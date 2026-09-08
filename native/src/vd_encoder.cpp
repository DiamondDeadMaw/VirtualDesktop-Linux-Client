// frames: get pooled surface -> import scanout via drmPrime and VPP-scale -> composite X cursor -> encode with h264_vaapi to stdout
#include <cerrno>
#include <ctime>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <unistd.h>
#include <fcntl.h>
#include <signal.h>

#include <va/va.h>
#include <va/va_drm.h>

extern "C" {
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_vaapi.h>
}

#include "ffmpeg_encode.h"
#include "kms_source.h"
#include "cursor_overlay.h"

namespace {

struct Options {
    std::string kms_device = "/dev/dri/card0";
    std::string vaapi_device = "/dev/dri/renderD128";
    std::string display = ":0";
    std::optional<std::string> xauthority;
    int crtc_id = 0;                       // 0 = let kmsgrab choose
    int crop_x = 0, crop_y = 0, crop_w = 0, crop_h = 0;   // w<=0 = whole scanout
    int out_w = 1600, out_h = 896;
    int fps = 60;
    bool cursor = true;
    bool probe = false;
};

void usage(const char *argv0) {
    fprintf(stderr,
        "usage: %s [options]   (writes Annex-B H.264 to stdout)\n"
        "  --kms-device PATH     DRM card node for kmsgrab (default /dev/dri/card0)\n"
        "  --vaapi-device PATH   render node for VAAPI     (default /dev/dri/renderD128)\n"
        "  --crtc-id N           specific CRTC to capture  (default: kmsgrab picks)\n"
        "  --crop W:H:X:Y        this monitor's rect on the scanout (default: all)\n"
        "  --size WxH            encode size, both multiples of 16 (default 1600x896)\n"
        "  --fps N               capture/encode rate       (default 60)\n"
        "  --display :N          X display for the cursor  (default :0)\n"
        "  --xauthority PATH     X auth cookie, if not the default\n"
        "  --no-cursor           do not composite the cursor\n"
        "  --probe               encode one frame, write nothing, exit 0 on success\n"
        "                        (the video session uses this to decide whether to\n"
        "                         spawn us or fall back to ffmpeg)\n", argv0);
}

bool parse_args(int argc, char **argv, Options *o) {
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        auto next = [&](const char *what) -> const char * {
            if (i + 1 >= argc) {
                fprintf(stderr, "%s needs a value\n", what);
                exit(2);
            }
            return argv[++i];
        };
        if (a == "--kms-device") o->kms_device = next("--kms-device");
        else if (a == "--vaapi-device") o->vaapi_device = next("--vaapi-device");
        else if (a == "--display") o->display = next("--display");
        else if (a == "--xauthority") o->xauthority = std::string(next("--xauthority"));
        else if (a == "--crtc-id") o->crtc_id = atoi(next("--crtc-id"));
        else if (a == "--fps") o->fps = atoi(next("--fps"));
        else if (a == "--no-cursor") o->cursor = false;
        else if (a == "--probe") o->probe = true;
        else if (a == "--crop") {
            // W:H:X:Y matches ffmpeg crop filter order
            if (sscanf(next("--crop"), "%d:%d:%d:%d",
                       &o->crop_w, &o->crop_h, &o->crop_x, &o->crop_y) != 4) {
                fprintf(stderr, "--crop wants W:H:X:Y\n");
                return false;
            }
        } else if (a == "--size") {
            if (sscanf(next("--size"), "%dx%d", &o->out_w, &o->out_h) != 2) {
                fprintf(stderr, "--size wants WxH\n");
                return false;
            }
        } else if (a == "-h" || a == "--help") {
            usage(argv[0]);
            exit(0);
        } else {
            fprintf(stderr, "unknown argument: %s\n", a.c_str());
            usage(argv[0]);
            return false;
        }
    }
    if (o->out_w % 16 || o->out_h % 16) {
        // non-multiple of 16 forces encoder padding, decoders that ignore crop show black bars
        fprintf(stderr, "[warn] %dx%d is not a multiple of 16; expect a black bar "
                        "on edges the decoder does not crop\n", o->out_w, o->out_h);
    }
    return true;
}

double now_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1.0e6;
}

// quest drops streams if frames pause too long, warn if a frame takes over 750ms
constexpr double kStallWarnMs = 750.0;

bool write_all(const uint8_t *p, size_t n) {
    while (n) {
        ssize_t w = write(STDOUT_FILENO, p, n);
        if (w < 0) {
            if (errno == EINTR) continue;
            return false;      // reader closed stdout (EPIPE)
        }
        p += w;
        n -= (size_t)w;
    }
    return true;
}

}  // namespace

int main(int argc, char **argv) {
    Options opt;
    if (!parse_args(argc, argv, &opt)) return 2;

    // handle closed stdout via write() error rather than dying to SIGPIPE
    signal(SIGPIPE, SIG_IGN);

    int drm_fd = open(opt.vaapi_device.c_str(), O_RDWR);
    if (drm_fd < 0) {
        fprintf(stderr, "cannot open %s: %s\n", opt.vaapi_device.c_str(), strerror(errno));
        return 1;
    }
    VADisplay va_dpy = vaGetDisplayDRM(drm_fd);
    int major, minor;
    if (vaInitialize(va_dpy, &major, &minor) != VA_STATUS_SUCCESS) {
        fprintf(stderr, "vaInitialize failed on %s\n", opt.vaapi_device.c_str());
        close(drm_fd);
        return 1;
    }
    fprintf(stderr, "[vd_encoder] VAAPI %d.%d on %s (%s)\n", major, minor,
            opt.vaapi_device.c_str(), vaQueryVendorString(va_dpy));

    // wrap our VADisplay in an ffmpeg hw context for dma-buf import
    // manual alloc keeps display ownership so libavutil doesnt call vaTerminate early
    AVBufferRef *va_device = av_hwdevice_ctx_alloc(AV_HWDEVICE_TYPE_VAAPI);
    if (!va_device) {
        fprintf(stderr, "av_hwdevice_ctx_alloc failed\n");
        return 1;
    }
    ((AVVAAPIDeviceContext *)((AVHWDeviceContext *)va_device->data)->hwctx)->display = va_dpy;
    if (av_hwdevice_ctx_init(va_device) < 0) {
        fprintf(stderr, "av_hwdevice_ctx_init failed\n");
        return 1;
    }

    int rc = 0;
    try {
        auto encoder = std::make_unique<FfmpegEncoder>(va_dpy, opt.out_w, opt.out_h, opt.fps);
        auto source = std::make_unique<KmsSource>(
            va_dpy, va_device, opt.kms_device, opt.crtc_id,
            std::make_tuple(opt.crop_x, opt.crop_y, opt.crop_w, opt.crop_h),
            opt.out_w, opt.out_h, opt.fps);

        std::unique_ptr<CursorOverlay> cursor;
        if (opt.cursor) {
            // stream without cursor if X is unavailable
            try {
                cursor = std::make_unique<CursorOverlay>(
                    va_dpy, opt.display, opt.xauthority,
                    opt.crop_x, opt.crop_y,
                    opt.crop_w > 0 ? opt.crop_w : opt.out_w,
                    opt.crop_h > 0 ? opt.crop_h : opt.out_h,
                    opt.out_w, opt.out_h);
            } catch (const std::exception &e) {
                fprintf(stderr, "[vd_encoder] no cursor overlay (%s)\n", e.what());
            }
        }

        bool running = true;
        bool sized_cursor = false;
        bool probe_ok = false;
        long frames = 0;
        long stalls = 0;
        const char *stop_reason = "stdout closed";
        while (running) {
            // track stage timings to identify which call blocked if a stall happens
            double t0 = now_ms();
            AVFrame *frame = encoder->getPooledFrame();
            VASurfaceID surf = encoder->surfaceOf(frame);
            // wait for previous gpu encode to finish using this pooled surface as a ref
            vaSyncSurface(va_dpy, surf);
            double t_pool = now_ms();

            if (!source->captureInto(surf)) {
                av_frame_free(&frame);
                stop_reason = "capture ended (see the kmsgrab error above)";
                break;
            }
            double t_cap = now_ms();
            if (cursor) {
                if (!sized_cursor && opt.crop_w <= 0) {
                    // full screen scanout dimensions are only known after frame 1
                    sized_cursor = true;
                    cursor = std::make_unique<CursorOverlay>(
                        va_dpy, opt.display, opt.xauthority, 0, 0,
                        source->scanoutWidth(), source->scanoutHeight(),
                        opt.out_w, opt.out_h);
                }
                cursor->composite(surf);
            }
            // sync cpu cursor writes before the encoder reads the surface on gpu
            vaSyncSurface(va_dpy, surf);
            double t_cur = now_ms();

            encoder->sendFrame(frame);   // takes ownership
            frames++;

            std::vector<uint8_t> data;
            bool is_key;
            while (encoder->receivePacket(&data, &is_key)) {
                std::vector<uint8_t> au = ensure_parameter_sets(
                    std::move(data), is_key, encoder->extradata(), encoder->extradata_size());
                if (opt.probe) {
                    // successfully produced an AU, pipeline works so exit cleanly
                    fprintf(stderr, "[vd_encoder] probe ok (%zu-byte access unit)\n", au.size());
                    probe_ok = true;
                    running = false;
                    break;
                }
                if (!write_all(au.data(), au.size())) {
                    running = false;
                    break;
                }
            }
            double t_end = now_ms();

            if (t_end - t0 > kStallWarnMs) {
                // log breakdown of which stage blocked (pipe write, kmsgrab capture, or surface pool)
                stalls++;
                fprintf(stderr,
                        "[vd_encoder] STALL at frame %ld: %.0f ms total "
                        "(pool+sync %.0f, capture %.0f, cursor+sync %.0f, encode+write %.0f)\n",
                        frames, t_end - t0, t_pool - t0, t_cap - t_pool,
                        t_cur - t_cap, t_end - t_cur);
            }
        }
        if (opt.probe) {
            if (!probe_ok) {
                fprintf(stderr, "[vd_encoder] probe failed: no access unit produced\n");
                rc = 1;
            }
        } else {
            // log shutdown reason so python runner can distinguish errors from normal exit
            fprintf(stderr, "[vd_encoder] stopped: %s (%ld frames, %d capture restart(s), "
                            "%ld stall(s))\n",
                    stop_reason, frames, source->restartCount(), stalls);
        }
    } catch (const std::exception &e) {
        fprintf(stderr, "[vd_encoder] %s\n", e.what());
        rc = 1;
    }

    av_buffer_unref(&va_device);
    vaTerminate(va_dpy);
    close(drm_fd);
    return rc;
}
