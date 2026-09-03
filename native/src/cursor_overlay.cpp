#include "cursor_overlay.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>

#include <X11/Xlib.h>
#include <X11/extensions/Xfixes.h>

namespace {

int clamp255(int v) {
    if (v < 0) return 0;
    if (v > 255) return 255;
    return v;
}

// BT.601 full-range, matching what the VPP stage produces for desktop pixels
void bgr_to_yuv(int b, int g, int r, int *y, int *u, int *v) {
    *y = clamp255(( 299 * r + 587 * g + 114 * b) / 1000);
    *u = clamp255((-169 * r - 331 * g + 500 * b) / 1000 + 128);
    *v = clamp255(( 500 * r - 419 * g -  81 * b) / 1000 + 128);
}

}  // namespace

CursorOverlay::CursorOverlay(VADisplay va_dpy, const std::string &display,
                             const std::optional<std::string> &xauthority,
                             int cap_x, int cap_y, int cap_w, int cap_h,
                             int out_w, int out_h)
    : va_dpy_(va_dpy), cap_x_(cap_x), cap_y_(cap_y), cap_w_(cap_w), cap_h_(cap_h),
      out_w_(out_w), out_h_(out_h) {
    if (xauthority) setenv("XAUTHORITY", xauthority->c_str(), 1);
    xdpy_ = XOpenDisplay(display.empty() ? nullptr : display.c_str());
    if (!xdpy_) throw std::runtime_error("CursorOverlay: XOpenDisplay(" + display + ") failed");
    int evb, erb;
    if (!XFixesQueryExtension(xdpy_, &evb, &erb)) {
        XCloseDisplay(xdpy_);
        xdpy_ = nullptr;
        throw std::runtime_error("CursorOverlay: XFixes extension not available");
    }
}

CursorOverlay::~CursorOverlay() {
    if (xdpy_) XCloseDisplay(xdpy_);
}

bool CursorOverlay::refresh(int *sx, int *sy) {
    XFixesCursorImage *ci = XFixesGetCursorImage(xdpy_);
    if (!ci) return false;
    *sx = ci->x;
    *sy = ci->y;

    // cursor only changes when moving b/w widgets, so just keep the smae bitmap 
    if (ci->cursor_serial != cached_serial_ || bgra_.empty()) {
        cached_serial_ = ci->cursor_serial;
        cw_ = ci->width;
        ch_ = ci->height;
        xhot_ = ci->xhot;
        yhot_ = ci->yhot;
        bgra_.resize((size_t)cw_ * ch_ * 4);
        for (int i = 0; i < cw_ * ch_; i++) {
            // xfixescursorimage::pixels is a u long ptr, 64 bits. need to read lower half
            uint32_t px = (uint32_t)ci->pixels[i];
            int a = (px >> 24) & 0xff;
            int r = (px >> 16) & 0xff, g = (px >> 8) & 0xff, b = px & 0xff;
            // XFixes gives PREMULTIPLIED alpha
            if (a > 0 && a < 255) {
                r = clamp255(r * 255 / a);
                g = clamp255(g * 255 / a);
                b = clamp255(b * 255 / a);
            }
            uint8_t *p = bgra_.data() + (size_t)i * 4;
            p[0] = (uint8_t)b;
            p[1] = (uint8_t)g;
            p[2] = (uint8_t)r;
            p[3] = (uint8_t)a;
        }
    }
    XFree(ci);
    return cw_ > 0 && ch_ > 0;
}

void CursorOverlay::composite(VASurfaceID surf) {
    int sx, sy;
    if (!refresh(&sx, &sy)) return;

    // screen space -> this stream's captured region -> encode surface
    int rx = sx - xhot_ - cap_x_;
    int ry = sy - yhot_ - cap_y_;
    if (cap_w_ <= 0 || cap_h_ <= 0) return;
    // pointer on another monitor
    if (rx + cw_ <= 0 || ry + ch_ <= 0 || rx >= cap_w_ || ry >= cap_h_) {
        return;
    }
    int dx = (int)((int64_t)rx * out_w_ / cap_w_);
    int dy = (int)((int64_t)ry * out_h_ / cap_h_);

    VAImage img;
    if (vaDeriveImage(va_dpy_, surf, &img) != VA_STATUS_SUCCESS) {
        if (!warned_map_failure_) {
            warned_map_failure_ = true;
            fprintf(stderr, "[cursor] vaDeriveImage failed; streaming without a cursor\n");
        }
        return;
    }
    if (img.format.fourcc != VA_FOURCC_NV12 || img.num_planes < 2) {
        vaDestroyImage(va_dpy_, img.image_id);
        if (!warned_map_failure_) {
            warned_map_failure_ = true;
            fprintf(stderr, "[cursor] surface is not 2-plane NV12; streaming without a cursor\n");
        }
        return;
    }

    void *base = nullptr;
    if (vaMapBuffer(va_dpy_, img.buf, &base) == VA_STATUS_SUCCESS) {
        uint8_t *yp = (uint8_t *)base + img.offsets[0];
        uint8_t *uvp = (uint8_t *)base + img.offsets[1];
        int y_pitch = img.pitches[0], uv_pitch = img.pitches[1];

        // Brightness-----
        // Skipi transparent pixels, and dont read opaque ones to reduce mermory cache misses
        for (int y = 0; y < ch_; y++) {
            int fy = dy + y;
            if (fy < 0 || fy >= out_h_) continue;
            uint8_t *yrow = yp + (size_t)fy * y_pitch;
            for (int x = 0; x < cw_; x++) {
                int fx = dx + x;
                if (fx < 0 || fx >= out_w_) continue;
                const uint8_t *s = bgra_.data() + ((size_t)y * cw_ + x) * 4;
                int a = s[3];
                if (a == 0) continue;
                int cy, cu, cv;
                bgr_to_yuv(s[0], s[1], s[2], &cy, &cu, &cv);
                yrow[fx] = (a == 255)
                    ? (uint8_t)cy
                    : (uint8_t)((cy * a + yrow[fx] * (255 - a)) / 255);
            }
        }
        // Color-----------
        // nv12 shape has 1 u,v pair per 2x2 luma block, avg cursor color over each block
        for (int y = 0; y < ch_; y += 2) {
            int fy = dy + y;
            if (fy < 0 || fy + 1 >= out_h_) continue;
            uint8_t *uvrow = uvp + (size_t)(fy / 2) * uv_pitch;
            for (int x = 0; x < cw_; x += 2) {
                int fx = dx + x;
                if (fx < 0 || fx + 1 >= out_w_) continue;
                int sa = 0, su = 0, sv = 0, n = 0;
                for (int yy = 0; yy < 2; yy++) {
                    for (int xx = 0; xx < 2; xx++) {
                        if (y + yy >= ch_ || x + xx >= cw_) continue;
                        const uint8_t *s = bgra_.data() + ((size_t)(y + yy) * cw_ + (x + xx)) * 4;
                        int cy, cu, cv;
                        bgr_to_yuv(s[0], s[1], s[2], &cy, &cu, &cv);
                        sa += s[3];
                        su += cu;
                        sv += cv;
                        n++;
                    }
                }
                if (n == 0) continue;
                int a = sa / n;
                if (a == 0) continue;
                uint8_t *p = uvrow + (fx & ~1);
                p[0] = (a == 255) ? (uint8_t)(su / n)
                                  : (uint8_t)(((su / n) * a + p[0] * (255 - a)) / 255);
                p[1] = (a == 255) ? (uint8_t)(sv / n)
                                  : (uint8_t)(((sv / n) * a + p[1] * (255 - a)) / 255);
            }
        }
        vaUnmapBuffer(va_dpy_, img.buf);
    }
    
    vaDestroyImage(va_dpy_, img.image_id);
}
