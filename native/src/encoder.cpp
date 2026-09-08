#include "encoder.h"

#include <stdexcept>
#include <cstdlib>
#include <cstring>
#include <unistd.h>
#include <fcntl.h>

#include <va/va_drm.h>

NativeEncoder::NativeEncoder(const std::string &display, std::tuple<int, int, int, int> region,
                             int out_width, int out_height, int fps, const std::string &vaapi_device,
                             const std::optional<std::string> &xauthority)
    : out_width_(out_width), out_height_(out_height) {
    auto [x, y, w, h] = region;

    drm_fd_ = open(vaapi_device.c_str(), O_RDWR);
    if (drm_fd_ < 0) {
        throw std::runtime_error("could not open " + vaapi_device);
    }
    va_dpy_ = vaGetDisplayDRM(drm_fd_);
    int major, minor;
    if (vaInitialize(va_dpy_, &major, &minor) != VA_STATUS_SUCCESS) {
        close(drm_fd_);
        drm_fd_ = -1;
        throw std::runtime_error("vaInitialize failed");
    }

    // raw members (not RAII): destructor won't run if constructor throws, clean up explicitly
    try {
        capture_ = std::make_unique<X11Capture>(display, xauthority, x, y, w, h);
        stage_ = std::make_unique<VaStage>(va_dpy_, w, h);
        encoder_ = std::make_unique<FfmpegEncoder>(va_dpy_, out_width, out_height, fps);

        // encode warm-up frames so initialization errors throw directly from constructor
        for (int attempt = 0; attempt < 5 && pending_.empty(); attempt++) {
            encode_one_real_frame();
            std::vector<uint8_t> data;
            bool is_key;
            while (encoder_->receivePacket(&data, &is_key)) {
                pending_.push_back(make_packet(data, is_key));
            }
        }
        if (pending_.empty()) {
            throw std::runtime_error("encoder produced no packets after 5 warm-up frames");
        }
    } catch (...) {
        encoder_.reset();
        stage_.reset();
        capture_.reset();
        vaTerminate(va_dpy_);
        va_dpy_ = nullptr;
        close(drm_fd_);
        drm_fd_ = -1;
        throw;
    }
}

NativeEncoder::~NativeEncoder() {
    try {
        stop();
    } catch (...) {

    }
}

void NativeEncoder::encode_one_real_frame() {
    int pitch;
    const uint8_t *px = capture_->capture(&pitch);
    stage_->write(px, pitch, capture_->height());

    AVFrame *frame = encoder_->getPooledFrame();
    VASurfaceID surf = encoder_->surfaceOf(frame);
    // wait for previous gpu encode to finish using this pooled surface as a ref
    vaSyncSurface(va_dpy_, surf);
    stage_->putInto(surf, out_width_, out_height_);
    // sync vaPutImage writes before encoder reads the surface on gpu
    vaSyncSurface(va_dpy_, surf);
    encoder_->sendFrame(frame);  // takes ownership
}

NativeEncoder::Packet NativeEncoder::make_packet(std::vector<uint8_t> raw, bool is_key) {
    return Packet{ensure_parameter_sets(std::move(raw), is_key,
                                        encoder_->extradata(),
                                        encoder_->extradata_size()),
                  is_key};
}

NativeEncoder::Packet NativeEncoder::next_packet() {
    if (!pending_.empty()) {
        Packet p = std::move(pending_.front());
        pending_.pop_front();
        return p;
    }

    encode_one_real_frame();
    std::vector<uint8_t> data;
    bool is_key;
    int attempts = 0;
    while (!encoder_->receivePacket(&data, &is_key)) {
        if (++attempts > 3) {
            throw std::runtime_error("encoder produced no packet after several frames");
        }
        usleep(2000);  // wait 2ms between capture attempts
        encode_one_real_frame();
    }
    Packet first = make_packet(data, is_key);

    // drain any remaining packets currently ready in encoder
    std::vector<uint8_t> extra_data;
    bool extra_key;
    while (encoder_->receivePacket(&extra_data, &extra_key)) {
        pending_.push_back(make_packet(extra_data, extra_key));
    }
    return first;
}

void NativeEncoder::stop() {
    if (stopped_) return;
    stopped_ = true;
    if (encoder_) {
        std::vector<std::vector<uint8_t>> leftover;
        try {
            encoder_->flush(&leftover);
        } catch (...) {
            
        }
    }
    encoder_.reset();
    stage_.reset();
    capture_.reset();
    if (va_dpy_) {
        vaTerminate(va_dpy_);
        va_dpy_ = nullptr;
    }
    if (drm_fd_ >= 0) {
        close(drm_fd_);
        drm_fd_ = -1;
    }
}
