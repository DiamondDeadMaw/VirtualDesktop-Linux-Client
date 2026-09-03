#include "ffmpeg_encode.h"

#include <stdexcept>
#include <cstring>
#include <cerrno>

extern "C" {
#include <libavutil/hwcontext_vaapi.h>
#include <libavutil/opt.h>
#include <libavutil/pixfmt.h>
}

namespace {
std::string av_err(int err) {
    char buf[AV_ERROR_MAX_STRING_SIZE] = {0};
    av_strerror(err, buf, sizeof(buf));
    return std::string(buf);
}

// 3-byte start code + NAL header 0x67 (type 7 SPS)
const uint8_t kSpsStartCode[] = {0x00, 0x00, 0x01, 0x67};

bool contains_sps(const std::vector<uint8_t> &data) {
    if (data.size() < sizeof(kSpsStartCode)) return false;
    for (size_t i = 0; i + sizeof(kSpsStartCode) <= data.size(); i++) {
        if (memcmp(data.data() + i, kSpsStartCode, sizeof(kSpsStartCode)) == 0) return true;
    }
    return false;
}
}  // namespace

std::vector<uint8_t> ensure_parameter_sets(std::vector<uint8_t> raw, bool is_key,
                                           const uint8_t *extradata, int extradata_size) {
    if (!is_key || extradata_size <= 0 || contains_sps(raw)) return raw;

    // H.264 Annex B requires AUD as first NAL
    // vaapi puts 6-byte AUD at start (00 00 00 01 09 xx), insert SPS/PPS right after it
    static const uint8_t kAudStart[] = {0x00, 0x00, 0x00, 0x01, 0x09};
    size_t aud_end = 0;
    if (raw.size() >= 6 && memcmp(raw.data(), kAudStart, 5) == 0) {
        aud_end = 6;  // 5-byte start code + 1-byte AUD payload
    }
    std::vector<uint8_t> out;
    out.reserve(raw.size() + extradata_size);
    out.insert(out.end(), raw.begin(), raw.begin() + aud_end);          // AUD first
    out.insert(out.end(), extradata, extradata + extradata_size);       // SPS/PPS
    out.insert(out.end(), raw.begin() + aud_end, raw.end());            // video slices
    return out;
}

FfmpegEncoder::FfmpegEncoder(VADisplay va_dpy, int width, int height, int fps) {
    // reuse existing VADisplay so vaPutImage writes to surfaces on the same display
    hw_device_ctx_ = av_hwdevice_ctx_alloc(AV_HWDEVICE_TYPE_VAAPI);
    if (!hw_device_ctx_) {
        throw std::runtime_error("av_hwdevice_ctx_alloc(VAAPI) failed");
    }
    auto *dev_ctx = (AVHWDeviceContext *)hw_device_ctx_->data;
    auto *vaapi_ctx = (AVVAAPIDeviceContext *)dev_ctx->hwctx;
    vaapi_ctx->display = va_dpy;
    int ret = av_hwdevice_ctx_init(hw_device_ctx_);
    if (ret < 0) {
        av_buffer_unref(&hw_device_ctx_);
        throw std::runtime_error("av_hwdevice_ctx_init failed: " + av_err(ret));
    }

    hw_frames_ctx_ = av_hwframe_ctx_alloc(hw_device_ctx_);
    if (!hw_frames_ctx_) {
        av_buffer_unref(&hw_device_ctx_);
        throw std::runtime_error("av_hwframe_ctx_alloc failed");
    }
    auto *frames_ctx = (AVHWFramesContext *)hw_frames_ctx_->data;
    frames_ctx->format = AV_PIX_FMT_VAAPI;
    frames_ctx->sw_format = AV_PIX_FMT_NV12;
    frames_ctx->width = width;
    frames_ctx->height = height;
    // pool headroom: surfaces are written outside ffmpeg, and gpu encode may still
    // be reading previous frames as p-frame refs after cpu refcount drops
    frames_ctx->initial_pool_size = 16;
    ret = av_hwframe_ctx_init(hw_frames_ctx_);
    if (ret < 0) {
        av_buffer_unref(&hw_frames_ctx_);
        av_buffer_unref(&hw_device_ctx_);
        throw std::runtime_error("av_hwframe_ctx_init failed: " + av_err(ret));
    }

    const AVCodec *codec = avcodec_find_encoder_by_name("h264_vaapi");
    if (!codec) {
        av_buffer_unref(&hw_frames_ctx_);
        av_buffer_unref(&hw_device_ctx_);
        throw std::runtime_error("h264_vaapi encoder not found (ffmpeg build lacks VAAPI support?)");
    }
    codec_ctx_ = avcodec_alloc_context3(codec);
    codec_ctx_->width = width;
    codec_ctx_->height = height;
    codec_ctx_->time_base = AVRational{1, fps};
    codec_ctx_->framerate = AVRational{fps, 1};
    codec_ctx_->pix_fmt = AV_PIX_FMT_VAAPI;
    codec_ctx_->gop_size = fps;
    codec_ctx_->max_b_frames = 0;  // zero b-frames for low latency
    codec_ctx_->profile = FF_PROFILE_H264_MAIN;  // broadwell vaapi lacks constrained_baseline entrypoint
    codec_ctx_->hw_frames_ctx = av_buffer_ref(hw_frames_ctx_);

    // h264_vaapi doesn't emit AUDs by default, force them on for quest decoder
    av_opt_set(codec_ctx_->priv_data, "aud", "1", 0);

    ret = avcodec_open2(codec_ctx_, codec, nullptr);
    if (ret < 0) {
        avcodec_free_context(&codec_ctx_);
        av_buffer_unref(&hw_frames_ctx_);
        av_buffer_unref(&hw_device_ctx_);
        throw std::runtime_error("avcodec_open2(h264_vaapi) failed: " + av_err(ret));
    }
}

FfmpegEncoder::~FfmpegEncoder() {
    if (codec_ctx_) avcodec_free_context(&codec_ctx_);
    if (hw_frames_ctx_) av_buffer_unref(&hw_frames_ctx_);
    if (hw_device_ctx_) av_buffer_unref(&hw_device_ctx_);
}

AVFrame *FfmpegEncoder::getPooledFrame() {
    AVFrame *frame = av_frame_alloc();
    if (!frame) throw std::runtime_error("av_frame_alloc failed");
    int ret = av_hwframe_get_buffer(hw_frames_ctx_, frame, 0);
    if (ret < 0) {
        av_frame_free(&frame);
        throw std::runtime_error("av_hwframe_get_buffer failed (surface pool exhausted?): " + av_err(ret));
    }
    return frame;
}

VASurfaceID FfmpegEncoder::surfaceOf(AVFrame *frame) const {
    // libavutil vaapi stores VASurfaceID in data[3]
    return (VASurfaceID)(uintptr_t)frame->data[3];
}

void FfmpegEncoder::sendFrame(AVFrame *frame) {
    frame->pts = pts_++;
    int ret = avcodec_send_frame(codec_ctx_, frame);
    av_frame_free(&frame);
    if (ret < 0) {
        throw std::runtime_error("avcodec_send_frame failed: " + av_err(ret));
    }
}

bool FfmpegEncoder::receivePacket(std::vector<uint8_t> *data, bool *is_keyframe) {
    AVPacket *pkt = av_packet_alloc();
    int ret = avcodec_receive_packet(codec_ctx_, pkt);
    if (ret == AVERROR(EAGAIN) || ret == AVERROR_EOF) {
        av_packet_free(&pkt);
        return false;
    }
    if (ret < 0) {
        av_packet_free(&pkt);
        throw std::runtime_error("avcodec_receive_packet failed: " + av_err(ret));
    }
    data->assign(pkt->data, pkt->data + pkt->size);
    *is_keyframe = (pkt->flags & AV_PKT_FLAG_KEY) != 0;
    av_packet_free(&pkt);
    return true;
}

void FfmpegEncoder::flush(std::vector<std::vector<uint8_t>> *out_packets) {
    avcodec_send_frame(codec_ctx_, nullptr);
    std::vector<uint8_t> data;
    bool is_key;
    while (receivePacket(&data, &is_key)) {
        out_packets->push_back(data);
    }
}
