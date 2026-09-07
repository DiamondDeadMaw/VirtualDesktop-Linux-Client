#include "va_stage.h"

#include <stdexcept>
#include <cstring>
#include <vector>
#include <string>

namespace {
// find first supported BGRX/BGRA/ARGB/XRGB format exposed by driver
VAImageFormat pick_bgrx_format(VADisplay dpy) {
    int n = vaMaxNumImageFormats(dpy);
    std::vector<VAImageFormat> formats(n);
    if (vaQueryImageFormats(dpy, formats.data(), &n) != VA_STATUS_SUCCESS) {
        throw std::runtime_error("vaQueryImageFormats failed");
    }
    for (int i = 0; i < n; i++) {
        uint32_t f = formats[i].fourcc;
        if (f == VA_FOURCC_BGRX || f == VA_FOURCC_BGRA ||
            f == VA_FOURCC_ARGB || f == VA_FOURCC_XRGB) {
            return formats[i];
        }
    }
    throw std::runtime_error("driver supports no BGRX/BGRA/ARGB/XRGB image format");
}
} 

VaStage::VaStage(VADisplay dpy, int width, int height) : dpy_(dpy), width_(width), height_(height) {
    VAImageFormat fmt = pick_bgrx_format(dpy_);
    if (vaCreateImage(dpy_, &fmt, width, height, &image_) != VA_STATUS_SUCCESS) {
        throw std::runtime_error("vaCreateImage (staging BGRX image) failed");
    }
}

VaStage::~VaStage() {
    vaDestroyImage(dpy_, image_.image_id);
}

void VaStage::write(const uint8_t *src, int src_pitch, int height) {
    void *ptr;
    if (vaMapBuffer(dpy_, image_.buf, &ptr) != VA_STATUS_SUCCESS) {
        throw std::runtime_error("vaMapBuffer (staging image) failed");
    }
    uint32_t dst_pitch = image_.pitches[0];
    int row_bytes = src_pitch < (int)dst_pitch ? src_pitch : (int)dst_pitch;
    uint8_t *d = (uint8_t *)ptr + image_.offsets[0];
    const uint8_t *s = src;
    for (int y = 0; y < height && y < (int)image_.height; y++) {
        memcpy(d, s, row_bytes);
        d += dst_pitch;
        s += src_pitch;
    }
    vaUnmapBuffer(dpy_, image_.buf);
}

void VaStage::putInto(VASurfaceID dst, int dst_width, int dst_height) {
    VAStatus st = vaPutImage(dpy_, dst, image_.image_id, 0, 0, width_, height_,
                             0, 0, dst_width, dst_height);
    if (st != VA_STATUS_SUCCESS) {
        throw std::runtime_error(std::string("vaPutImage failed: ") + vaErrorStr(st));
    }
}
