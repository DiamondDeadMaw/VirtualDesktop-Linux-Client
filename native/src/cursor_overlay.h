#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include <va/va.h>

typedef struct _XDisplay Display;

class CursorOverlay {
public:
    // (cap_x, cap_y, cap_w, cap_h) is the region of the X screen this stream captures
    // (out_w, out_h) is the encode surface it is scaled to
    // Cursor positions are mapped between the two.
    CursorOverlay(VADisplay va_dpy, const std::string &display,
                  const std::optional<std::string> &xauthority,
                  int cap_x, int cap_y, int cap_w, int cap_h,
                  int out_w, int out_h);
    ~CursorOverlay();

    CursorOverlay(const CursorOverlay &) = delete;
    CursorOverlay &operator=(const CursorOverlay &) = delete;

    // Composite the current pointer onto `surf`. Silently does nothing if the cursor is hidden, outside this 
    // streams region, or if the surface cannot
    // be mapped
    void composite(VASurfaceID surf);

private:
    // Refreshes bgra_/cw_/ch_/xhot_/yhot_ from XFixes, reusing the cached
    // bitmap when the shape has not changed. Returns false if there is no
    // visible cursor. Fills screen-space hotspot position in *sx, *sy.
    bool refresh(int *sx, int *sy);

    VADisplay va_dpy_;
    Display *xdpy_ = nullptr;
    int cap_x_, cap_y_, cap_w_, cap_h_, out_w_, out_h_;

    unsigned long cached_serial_ = 0;
    std::vector<uint8_t> bgra_;
    int cw_ = 0, ch_ = 0, xhot_ = 0, yhot_ = 0;

    bool warned_map_failure_ = false;
};
