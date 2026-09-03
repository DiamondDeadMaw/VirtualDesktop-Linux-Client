#include "kms_source.h"

#include <cstdio>
#include <cstring>
#include <stdexcept>

#include <unistd.h>

extern "C" {
#include <libavdevice/avdevice.h>
#include <libavutil/opt.h>
}

namespace {

std::string av_err(int err) {
    char buf[AV_ERROR_MAX_STRING_SIZE] = {0};
    av_strerror(err, buf, sizeof(buf));
    return std::string(buf);
}

}  // namespace

KmsSource::KmsSource(VADisplay va_dpy, AVBufferRef *va_device,
                     const std::string &kms_device, int crtc_id,
                     std::tuple<int, int, int, int> crop,
                     int out_w, int out_h, int fps)
    : va_dpy_(va_dpy), va_device_(va_device),
      kms_device_(kms_device), crtc_id_(crtc_id), fps_(fps),
      out_w_(out_w), out_h_(out_h) {
    std::tie(crop_x_, crop_y_, crop_w_, crop_h_) = crop;

    avdevice_register_all();

    pkt_ = av_packet_alloc();
    drm_ = av_frame_alloc();
    va_ = av_frame_alloc();
    if (!pkt_ || !drm_ || !va_) throw std::runtime_error("av_frame_alloc failed");

    // VPP handles GPU scaling and BGRX to NV12 conversion
    VAStatus st = vaCreateConfig(va_dpy_, VAProfileNone, VAEntrypointVideoProc,
                                 nullptr, 0, &vpp_cfg_);
    if (st != VA_STATUS_SUCCESS) {
        throw std::runtime_error(std::string("vaCreateConfig(VPP) failed: ") + vaErrorStr(st));
    }
    // no render target bound here bc destination is a different pooled surface each frame
    st = vaCreateContext(va_dpy_, vpp_cfg_, out_w_, out_h_, VA_PROGRESSIVE,
                         nullptr, 0, &vpp_ctx_);
    if (st != VA_STATUS_SUCCESS) {
        throw std::runtime_error(std::string("vaCreateContext(VPP) failed: ") + vaErrorStr(st));
    }

    if (!openInput()) {
        throw std::runtime_error(
            "kmsgrab could not be opened (needs CAP_SYS_ADMIN: "
            "sudo setcap cap_sys_admin+ep <this binary>)");
    }
}

KmsSource::~KmsSource() {
    closeInput();
    if (va_) av_frame_free(&va_);
    if (drm_) av_frame_free(&drm_);
    if (pkt_) av_packet_free(&pkt_);
    if (vpp_ctx_ != VA_INVALID_ID) vaDestroyContext(va_dpy_, vpp_ctx_);
    if (vpp_cfg_ != VA_INVALID_ID) vaDestroyConfig(va_dpy_, vpp_cfg_);
}

bool KmsSource::openInput() {
    // cast for compatibility: ffmpeg 4.x takes non-const AVInputFormat*, 5.0+ takes const
    AVInputFormat *ifmt = (AVInputFormat *)av_find_input_format("kmsgrab");
    if (!ifmt) {
        fprintf(stderr, "[vd_encoder] this ffmpeg build has no kmsgrab demuxer\n");
        return false;
    }

    AVDictionary *opts = nullptr;
    av_dict_set(&opts, "device", kms_device_.c_str(), 0);
    av_dict_set_int(&opts, "framerate", fps_, 0);
    if (crtc_id_ > 0) av_dict_set_int(&opts, "crtc_id", crtc_id_, 0);
    int ret = avformat_open_input(&fmt_, "", ifmt, &opts);
    av_dict_free(&opts);
    if (ret < 0) {
        fprintf(stderr, "[vd_encoder] kmsgrab open failed: %s\n", av_err(ret).c_str());
        fmt_ = nullptr;
        return false;
    }
    if ((ret = avformat_find_stream_info(fmt_, nullptr)) < 0) {
        fprintf(stderr, "[vd_encoder] kmsgrab find_stream_info failed: %s\n", av_err(ret).c_str());
        closeInput();
        return false;
    }

    // kmsgrab emits WRAPPED_AVFRAME containing the DRM_PRIME descriptor
    const AVCodec *dec = avcodec_find_decoder(fmt_->streams[0]->codecpar->codec_id);
    if (!dec) {
        fprintf(stderr, "[vd_encoder] no decoder for the kmsgrab stream\n");
        closeInput();
        return false;
    }
    dec_ = avcodec_alloc_context3(dec);
    if (!dec_) {
        fprintf(stderr, "[vd_encoder] avcodec_alloc_context3 failed\n");
        closeInput();
        return false;
    }
    avcodec_parameters_to_context(dec_, fmt_->streams[0]->codecpar);
    if ((ret = avcodec_open2(dec_, dec, nullptr)) < 0) {
        fprintf(stderr, "[vd_encoder] kmsgrab decoder open failed: %s\n", av_err(ret).c_str());
        closeInput();
        return false;
    }
    return true;
}

void KmsSource::closeInput() {
    // va_frames_ derives from drm_ frames context, must be re-derived on reconnect
    av_frame_unref(va_);
    av_frame_unref(drm_);
    av_packet_unref(pkt_);
    if (va_frames_) av_buffer_unref(&va_frames_);
    if (dec_) avcodec_free_context(&dec_);
    if (fmt_) avformat_close_input(&fmt_);
    fmt_ = nullptr;
}

bool KmsSource::nextSurface(VASurfaceID *out) {
    if (!fmt_ || !dec_) return false;

    // bound spins to 256 to avoid infinite loop if demuxer hangs in EAGAIN
    for (int spins = 0; spins < 256; spins++) {
        av_frame_unref(drm_);
        int ret = avcodec_receive_frame(dec_, drm_);
        if (ret == AVERROR(EAGAIN)) {
            av_packet_unref(pkt_);
            ret = av_read_frame(fmt_, pkt_);
            if (ret == AVERROR(EAGAIN)) {
                usleep(1000);   // sleep 1ms so we don't burn CPU while waiting
                continue;
            }
            if (ret < 0) {
                fprintf(stderr, "[vd_encoder] kmsgrab read failed: %s\n", av_err(ret).c_str());
                return false;
            }
            if ((ret = avcodec_send_packet(dec_, pkt_)) < 0) {
                fprintf(stderr, "[vd_encoder] kmsgrab send_packet failed: %s\n",
                        av_err(ret).c_str());
                return false;
            }
            continue;
        }
        if (ret < 0) {
            fprintf(stderr, "[vd_encoder] kmsgrab receive_frame failed: %s\n",
                    av_err(ret).c_str());
            return false;
        }

        if (!va_frames_) {
            scanout_w_ = drm_->width;
            scanout_h_ = drm_->height;
            if (crop_w_ <= 0 || crop_h_ <= 0) {
                crop_x_ = 0;
                crop_y_ = 0;
                crop_w_ = scanout_w_;
                crop_h_ = scanout_h_;
            }
            ret = av_hwframe_ctx_create_derived(&va_frames_, AV_PIX_FMT_VAAPI, va_device_,
                                                drm_->hw_frames_ctx, AV_HWFRAME_MAP_READ);
            if (ret < 0) {
                fprintf(stderr, "[vd_encoder] av_hwframe_ctx_create_derived failed: %s\n",
                        av_err(ret).c_str());
                va_frames_ = nullptr;
                return false;
            }
        }

        av_frame_unref(va_);
        va_->format = AV_PIX_FMT_VAAPI;
        va_->hw_frames_ctx = av_buffer_ref(va_frames_);
        ret = av_hwframe_map(va_, drm_, AV_HWFRAME_MAP_READ);
        if (ret < 0) {
            fprintf(stderr, "[vd_encoder] av_hwframe_map failed: %s\n", av_err(ret).c_str());
            return false;
        }
        // libavutil vaapi stores VASurfaceID in data[3]
        *out = (VASurfaceID)(uintptr_t)va_->data[3];
        return true;
    }
    fprintf(stderr, "[vd_encoder] kmsgrab produced no frame in 256 attempts\n");
    return false;
}

bool KmsSource::captureInto(VASurfaceID dst) {
    VASurfaceID src = VA_INVALID_ID;

    while (!nextSurface(&src)) {
        if (++consecutive_failures_ > kMaxConsecutiveFailures) {
            fprintf(stderr, "[vd_encoder] capture failed %d times in a row; giving up\n",
                    consecutive_failures_);
            return false;
        }
        // reallocated scanout invalidates imported dma-buf and derived frames context
        // full demuxer reopen is required to import the new scanout buffer
        restarts_total_++;
        fprintf(stderr, "[vd_encoder] restarting kmsgrab capture "
                        "(failure %d/%d, %d restart(s) this session)\n",
                consecutive_failures_, kMaxConsecutiveFailures, restarts_total_);
        closeInput();
        usleep(100000);   // 100ms wait for DRM state to settle
        if (!openInput()) {
            closeInput();   // avoid half-open state if open fails
            usleep(400000);
        }
    }

    if (consecutive_failures_) {
        fprintf(stderr, "[vd_encoder] capture recovered after %d failure(s)\n",
                consecutive_failures_);
        consecutive_failures_ = 0;
    }

    VARectangle sr = { (int16_t)crop_x_, (int16_t)crop_y_,
                       (uint16_t)crop_w_, (uint16_t)crop_h_ };
    VARectangle dr = { 0, 0, (uint16_t)out_w_, (uint16_t)out_h_ };

    VAProcPipelineParameterBuffer pp;
    memset(&pp, 0, sizeof(pp));
    pp.surface = src;
    pp.surface_region = &sr;      // crop rectangle on the scanout
    pp.output_region = &dr;
    pp.output_background_color = 0xff000000;  // opaque black background
    pp.filter_flags = VA_FILTER_SCALING_DEFAULT;

    VABufferID buf;
    VAStatus st = vaCreateBuffer(va_dpy_, vpp_ctx_, VAProcPipelineParameterBufferType,
                                 sizeof(pp), 1, &pp, &buf);
    if (st != VA_STATUS_SUCCESS) {
        fprintf(stderr, "[vd_encoder] vaCreateBuffer(VPP) failed: %s\n", vaErrorStr(st));
        return false;
    }
    st = vaBeginPicture(va_dpy_, vpp_ctx_, dst);
    if (st == VA_STATUS_SUCCESS) st = vaRenderPicture(va_dpy_, vpp_ctx_, &buf, 1);
    if (st == VA_STATUS_SUCCESS) st = vaEndPicture(va_dpy_, vpp_ctx_);
    vaDestroyBuffer(va_dpy_, buf);
    if (st != VA_STATUS_SUCCESS) {
        fprintf(stderr, "[vd_encoder] VPP scale/convert failed: %s\n", vaErrorStr(st));
        return false;
    }
    // sync VPP writes before CPU cursor mapping and GPU encoder read dst surface
    vaSyncSurface(va_dpy_, dst);
    return true;
}
