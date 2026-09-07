# Builds vd_encoder, the standalone capture+cursor+encode binary the video
# session spawns in place of ffmpeg on the zero-copy kmsgrab path.
#
# The pybind11 module (setup.py) is a separate build covering the X11 capture
# path; both share ffmpeg_encode.cpp.
#
# After building, grant kmsgrab the capability it needs -- once, and to this
# binary rather than to the Python interpreter:
#     sudo setcap cap_sys_admin+ep ./vd_encoder

PKGS := libva libva-drm libavdevice libavformat libavcodec libavutil x11 xfixes

CXXFLAGS := -std=c++17 -O2 -Wall -Wextra $(shell pkg-config --cflags $(PKGS))
LDLIBS   := $(shell pkg-config --libs $(PKGS))

SRC := src/vd_encoder.cpp src/kms_source.cpp src/cursor_overlay.cpp src/ffmpeg_encode.cpp
HDR := src/kms_source.h src/cursor_overlay.h src/ffmpeg_encode.h

vd_encoder: $(SRC) $(HDR)
	$(CXX) $(CXXFLAGS) -o $@ $(SRC) $(LDLIBS)

# Convenience: build and grant the capability in one step (prompts for sudo).
install-cap: vd_encoder
	sudo setcap cap_sys_admin+ep ./vd_encoder
	@echo "vd_encoder is ready; the video session will pick it up automatically"

clean:
	rm -f vd_encoder

.PHONY: clean install-cap
