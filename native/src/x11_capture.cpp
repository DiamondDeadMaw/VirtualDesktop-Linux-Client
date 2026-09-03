#include "x11_capture.h"

#include <stdexcept>
#include <cstdlib>
#include <sys/ipc.h>
#include <sys/shm.h>

namespace {
// Wrapper around xdestroyimage(), bc it doesnt resolve for some reason
void destroy_ximage(XImage *img) {
    if (img && img->f.destroy_image) {
        img->f.destroy_image(img);
    }
}
}

X11Capture::X11Capture(const std::string &display, const std::optional<std::string> &xauthority,
                       int x, int y, int w, int h)
    : x_(x), y_(y), width_(w), height_(h) {
    // Xlib reads XAUTHORITY from the process environment at XOpenDisplay time,
    // so set it (if given) before opening
    if (xauthority) {
        setenv("XAUTHORITY", xauthority->c_str(), 1);
    }

    dpy_ = XOpenDisplay(display.c_str());
    if (!dpy_) {
        throw std::runtime_error("XOpenDisplay(" + display + ") failed");
    }

    int shm_event, shm_error;
    if (!XShmQueryExtension(dpy_)) {
        throw std::runtime_error("X server does not support the MIT-SHM (XShm) extension");
    }
    (void)shm_event; (void)shm_error;

    int screen = DefaultScreen(dpy_);
    root_ = DefaultRootWindow(dpy_);
    Visual *visual = DefaultVisual(dpy_, screen);
    int depth = DefaultDepth(dpy_, screen);

    ximage_ = XShmCreateImage(dpy_, visual, depth, ZPixmap, nullptr, &shminfo_, w, h);
    if (!ximage_) {
        XCloseDisplay(dpy_);
        dpy_ = nullptr;
        throw std::runtime_error("XShmCreateImage failed");
    }

    shminfo_.shmid = shmget(IPC_PRIVATE, (size_t)ximage_->bytes_per_line * ximage_->height,
                            IPC_CREAT | 0600);
    if (shminfo_.shmid < 0) {
        destroy_ximage(ximage_);
        XCloseDisplay(dpy_);
        dpy_ = nullptr;
        throw std::runtime_error("shmget failed");
    }
    shminfo_.shmaddr = ximage_->data = (char *)shmat(shminfo_.shmid, nullptr, 0);
    shminfo_.readOnly = False;

    if (shminfo_.shmaddr == (char *)-1) {
        shmctl(shminfo_.shmid, IPC_RMID, nullptr);
        destroy_ximage(ximage_);
        XCloseDisplay(dpy_);
        dpy_ = nullptr;
        throw std::runtime_error("shmat failed");
    }

    if (!XShmAttach(dpy_, &shminfo_)) {
        shmdt(shminfo_.shmaddr);
        shmctl(shminfo_.shmid, IPC_RMID, nullptr);
        destroy_ximage(ximage_);
        XCloseDisplay(dpy_);
        dpy_ = nullptr;
        throw std::runtime_error("XShmAttach failed");
    }
    shm_attached_ = true;
    XSync(dpy_, False);

    // Mark the shm segment for removal once detached in case we crash
    shmctl(shminfo_.shmid, IPC_RMID, nullptr);
}

X11Capture::~X11Capture() {
    if (dpy_) {
        if (shm_attached_) {
            XShmDetach(dpy_, &shminfo_);
        }
        if (ximage_) {
            // NOTe- shminfo shmaddr is also unmapped
            destroy_ximage(ximage_);
        }
        if (shminfo_.shmaddr && shminfo_.shmaddr != (char *)-1) {
            shmdt(shminfo_.shmaddr);
        }
        if (cursor_) {
            XFree(cursor_);
        }
        XCloseDisplay(dpy_);
    }
}

const uint8_t *X11Capture::capture(int *out_pitch) {
    if (!XShmGetImage(dpy_, root_, ximage_, x_, y_, AllPlanes)) {
        throw std::runtime_error("XShmGetImage failed");
    }
    
    if (cursor_) {
        XFree(cursor_);
    }
    cursor_ = XFixesGetCursorImage(dpy_);
    if (cursor_) {
        int cx = cursor_->x - cursor_->xhot - x_;
        int cy = cursor_->y - cursor_->yhot - y_;
        
        // Alpha blend into image
        uint32_t *dst_data = reinterpret_cast<uint32_t *>(ximage_->data);
        int dst_stride = ximage_->bytes_per_line / 4;
        
        for (int y = 0; y < cursor_->height; ++y) {
            int dst_y = cy + y;
            if (dst_y < 0 || dst_y >= height_) continue;
            
            for (int x = 0; x < cursor_->width; ++x) {
                int dst_x = cx + x;
                if (dst_x < 0 || dst_x >= width_) continue;
                
                uint32_t src_pixel = cursor_->pixels[y * cursor_->width + x];
                uint8_t alpha = (src_pixel >> 24) & 0xFF;
                if (alpha == 0) continue;
                
                uint32_t *dst_pixel = &dst_data[dst_y * dst_stride + dst_x];
                if (alpha == 255) {
                    *dst_pixel = src_pixel;
                } else {
                    uint8_t src_r = (src_pixel >> 16) & 0xFF;
                    uint8_t src_g = (src_pixel >> 8) & 0xFF;
                    uint8_t src_b = src_pixel & 0xFF;
                    
                    uint32_t dp = *dst_pixel;
                    uint8_t dst_r = (dp >> 16) & 0xFF;
                    uint8_t dst_g = (dp >> 8) & 0xFF;
                    uint8_t dst_b = dp & 0xFF;
                    
                    uint8_t out_r = ((src_r * alpha) + (dst_r * (255 - alpha))) / 255;
                    uint8_t out_g = ((src_g * alpha) + (dst_g * (255 - alpha))) / 255;
                    uint8_t out_b = ((src_b * alpha) + (dst_b * (255 - alpha))) / 255;
                    
                    *dst_pixel = (dp & 0xFF000000) | (out_r << 16) | (out_g << 8) | out_b;
                }
            }
        }
    }

    *out_pitch = ximage_->bytes_per_line;
    return reinterpret_cast<const uint8_t *>(ximage_->data);
}
