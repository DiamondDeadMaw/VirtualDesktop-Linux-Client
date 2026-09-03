#pragma once
#include <cstdint>
#include <string>
#include <optional>

#include <X11/Xlib.h>
#include <X11/extensions/XShm.h>
#include <X11/extensions/Xfixes.h>

// XShm-based capture of a fixed (x,y,w,h) region inspired by ffmpegs x11grab
class X11Capture {
public:
    X11Capture(const std::string &display, const std::optional<std::string> &xauthority,
               int x, int y, int w, int h);
    ~X11Capture();

    X11Capture(const X11Capture &) = delete;
    X11Capture &operator=(const X11Capture &) = delete;

    // Captures one frame into the attached XShm segment and returns a pointer
    // to the raw pixel data (BGRX-family, whatever the X server's default
    // visual is) along with its pitch (bytes per row). Static till next capture()
    const uint8_t *capture(int *out_pitch);

    int width() const { return width_; }
    int height() const { return height_; }

private:
    Display *dpy_ = nullptr;
    Window root_ = 0;
    XImage *ximage_ = nullptr;
    XShmSegmentInfo shminfo_{};
    int x_, y_, width_, height_;
    bool shm_attached_ = false;
    XFixesCursorImage *cursor_ = nullptr;
};
