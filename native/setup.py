"""
Build script for the native_encoder pybind11 extension.
Build: python3 setup.py build_ext --inplace
"""
import subprocess

from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

PKGS = ["x11", "xext", "xfixes", "libva", "libva-drm", "libavcodec", "libavutil"]


def pkg_config(flag):
    out = subprocess.check_output(["pkg-config", flag] + PKGS, text=True)
    return out.split()


ext = Pybind11Extension(
    "native_encoder",
    [
        "src/module.cpp",
        "src/encoder.cpp",
        "src/x11_capture.cpp",
        "src/va_stage.cpp",
        "src/ffmpeg_encode.cpp",
    ],
    extra_compile_args=["-std=c++17"] + pkg_config("--cflags"),
    extra_link_args=pkg_config("--libs"),
)

setup(
    name="native_encoder",
    version="1.0",
    ext_modules=[ext],
    cmdclass={"build_ext": build_ext},
)
