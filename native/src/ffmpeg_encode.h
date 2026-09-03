#pragma once
#include <cstdint>
#include <vector>
#include <string>

extern "C" {
#include <va/va.h>
#include <libavcodec/avcodec.h>
#include <libavutil/hwcontext.h>
}

// inserts SPS/PPS extradata after AUD on keyframes if driver omitted them
// AUD stays first NAL so python reader can split access units
std::vector<uint8_t> ensure_parameter_sets(std::vector<uint8_t> raw, bool is_key,
                                           const uint8_t *extradata, int extradata_size);

// wraps h264_vaapi encoder on shared VADisplay using ffmpeg surface pool
class FfmpegEncoder {
public:
    FfmpegEncoder(VADisplay va_dpy, int width, int height, int fps);
    ~FfmpegEncoder();

    FfmpegEncoder(const FfmpegEncoder &) = delete;
    FfmpegEncoder &operator=(const FfmpegEncoder &) = delete;

    // gets pooled VASurface frame from AVHWFramesContext
    AVFrame *getPooledFrame();
    VASurfaceID surfaceOf(AVFrame *frame) const;

    // takes ownership, frees frame after sending
    void sendFrame(AVFrame *frame);

    // returns true if packet available, false if encoder needs more frames (EAGAIN)
    bool receivePacket(std::vector<uint8_t> *data, bool *is_keyframe);

    // flush remaining packets with a null frame
    void flush(std::vector<std::vector<uint8_t>> *out_packets);

    const uint8_t *extradata() const { return codec_ctx_->extradata; }
    int extradata_size() const { return codec_ctx_->extradata_size; }

private:
    AVBufferRef *hw_device_ctx_ = nullptr;
    AVBufferRef *hw_frames_ctx_ = nullptr;
    AVCodecContext *codec_ctx_ = nullptr;
    int64_t pts_ = 0;
};
