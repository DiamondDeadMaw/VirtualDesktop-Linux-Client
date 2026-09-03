#pragma once
#include <string>
#include <tuple>

#include <va/va.h>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/hwcontext.h>
}

// zero-copy desktop capture: imports primary DRM plane via dma-buf and scales
// into caller's NV12 surface using VA VPP
// automatically reopens demuxer if scanout reallocates (e.g. unredirect or mode changes)
class KmsSource {
public:
    // va_device must wrap va_dpy for dma-buf import
    // crop is (x, y, w, h) on scanout, pass w <= 0 for full screen
    KmsSource(VADisplay va_dpy, AVBufferRef *va_device,
              const std::string &kms_device, int crtc_id,
              std::tuple<int, int, int, int> crop,
              int out_w, int out_h, int fps);
    ~KmsSource();

    KmsSource(const KmsSource &) = delete;
    KmsSource &operator=(const KmsSource &) = delete;

    // captures scanout frame and renders into dst NV12 surface
    // restarts on transient failure, returns false if capture permanently fails
    bool captureInto(VASurfaceID dst);

    int scanoutWidth() const { return scanout_w_; }
    int scanoutHeight() const { return scanout_h_; }

    // total restarts this session
    int restartCount() const { return restarts_total_; }

private:
    bool openInput();
    void closeInput();                    // safe to call on half-open state
    bool nextSurface(VASurfaceID *out);   // captures and imports dma-buf without scaling

    // max consecutive close/reopen cycles before giving up
    static constexpr int kMaxConsecutiveFailures = 20;

    VADisplay va_dpy_;
    AVBufferRef *va_device_ = nullptr;    // borrowed, not owned
    AVBufferRef *va_frames_ = nullptr;    // derived from DRM frames context
    AVFormatContext *fmt_ = nullptr;
    AVCodecContext *dec_ = nullptr;
    AVPacket *pkt_ = nullptr;
    AVFrame *drm_ = nullptr;
    AVFrame *va_ = nullptr;

    VAConfigID vpp_cfg_ = VA_INVALID_ID;
    VAContextID vpp_ctx_ = VA_INVALID_ID;

    // stored to reopen demuxer with same settings
    std::string kms_device_;
    int crtc_id_ = 0;
    int fps_ = 60;

    int crop_x_, crop_y_, crop_w_, crop_h_;
    int out_w_, out_h_;
    int scanout_w_ = 0, scanout_h_ = 0;
    int consecutive_failures_ = 0;
    int restarts_total_ = 0;
};
