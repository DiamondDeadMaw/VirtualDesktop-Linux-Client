#pragma once
#include <cstdint>

#include <va/va.h>

// CPU staging buffer that uploads BGRX frames to a VASurface
class VaStage {
public:
    VaStage(VADisplay dpy, int width, int height);
    ~VaStage();

    VaStage(const VaStage &) = delete;
    VaStage &operator=(const VaStage &) = delete;

    // copies pixel rows into mapped VAImage buffer
    void write(const uint8_t *src, int src_pitch, int height);

    // uploads and scales staged image into dst VASurface via vaPutImage
    void putInto(VASurfaceID dst, int dst_width, int dst_height);

    int width() const { return width_; }
    int height() const { return height_; }

private:
    VADisplay dpy_;
    VAImage image_{};
    int width_, height_;
};
