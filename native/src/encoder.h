#pragma once
#include <cstdint>
#include <vector>
#include <string>
#include <optional>
#include <memory>
#include <deque>

#include <va/va.h>

#include "x11_capture.h"
#include "va_stage.h"
#include "ffmpeg_encode.h"

// Does capture -> stage -> vaPutImage -> encode for one monitor
class NativeEncoder {
public:
    struct Packet {
        std::vector<uint8_t> data;
        bool is_key;
    };

    // vaapi_device is a /dev/dri/renderD* path.
    NativeEncoder(const std::string &display, std::tuple<int, int, int, int> region,
                 int out_width, int out_height, int fps, const std::string &vaapi_device,
                 const std::optional<std::string> &xauthority);
    ~NativeEncoder();

    NativeEncoder(const NativeEncoder &) = delete;
    NativeEncoder &operator=(const NativeEncoder &) = delete;

    // Blocking- captures/encodes as needed and returns the next access unit.
    Packet next_packet();

    void stop();

private:
    Packet make_packet(std::vector<uint8_t> raw, bool is_key);
    void encode_one_real_frame();

    std::unique_ptr<X11Capture> capture_;
    std::unique_ptr<FfmpegEncoder> encoder_;
    std::unique_ptr<VaStage> stage_;
    VADisplay va_dpy_ = nullptr;
    int drm_fd_ = -1;
    int out_width_, out_height_;
    bool stopped_ = false;
    std::deque<Packet> pending_;
};
