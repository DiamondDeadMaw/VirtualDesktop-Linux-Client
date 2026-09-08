"""
Encoder selection and command construction for ffmpeg, vd_encoder, and native module.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .. import hardware, logging_setup

log = logging_setup.get("video")

try:
    import native_encoder as native_module
except ImportError:
    native_module = None

_PROBE_TIMEOUT = 20

TEST_PATTERNS = ["testsrc2", "smptebars", "rgbtestsrc"]


def vaapi_device() -> str | None:
    for path in hardware.VAAPI_DEVICE_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def vaapi_environment() -> dict:
    return os.environ.copy()


def vaapi_encode_works(device: str, env: dict) -> bool:
    # on ol i965 hardware, hardware encode straight up doesnt work. need to use different drivers, so test first
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=5", "-frames:v", "5",
           "-vaapi_device", device, "-vf", "format=nv12,hwupload",
           "-c:v", "h264_vaapi", "-profile:v", hardware.VAAPI_PROFILE,
           "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("[video] VAAPI probe could not run (%r)", exc)
        return False
    if r.returncode == 0:
        return True
    tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    log.info("[video] VAAPI hardware encode probe failed: %s",
             tail[-1] if tail else "(no output)")
    return False


def vd_encoder_path() -> str | None:
    root = Path(__file__).resolve().parents[3]
    for candidate in (root / "native" / "vd_encoder",
                      Path(__file__).resolve().parent / "vd_encoder"):
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def vd_encoder_works(cmd: list[str], env: dict) -> bool:
    # kmsgrab needs cap sys admin
    try:
        r = subprocess.run(cmd + ["--probe"], env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("[video] vd_encoder probe could not run (%r)", exc)
        return False
    if r.returncode == 0:
        return True
    tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    log.info("[video] vd_encoder probe failed: %s", tail[-1] if tail else "(no output)")
    if any("cap_sys_admin" in line.lower() for line in tail):
        log.info("[video] fix once with: sudo setcap cap_sys_admin+ep %s   "
                 "(same one-off already applied to ffmpeg)", cmd[0])
    return False


def vd_encoder_cmd(vd_path: str, plan, width: int, height: int, fps: int,
                   device: str | None, kms_device: str | None,
                   cursor: bool = True) -> list[str]:
    cmd = [vd_path,
           "--kms-device", kms_device or hardware.DEFAULT_KMS_DEVICE,
           "--vaapi-device", device or hardware.DEFAULT_VAAPI_DEVICE,
           "--size", f"{width}x{height}",
           "--fps", str(fps),
           "--display", plan["display"]]
    if plan.get("region"):
        x, y, w, h = plan["region"]
        cmd += ["--crop", f"{w}:{h}:{x}:{y}"]
    if plan.get("crtc_id") is not None:
        cmd += ["--crtc-id", str(plan["crtc_id"])]
    xauth = plan["env"].get("XAUTHORITY")
    if xauth:
        cmd += ["--xauthority", xauth]
    if not cursor:
        cmd += ["--no-cursor"]
    return cmd


def input_args(source: str, region, index: int, width: int, height: int,
               fps: int, display: str, kms_device: str | None = None,
               crtc_id: int | None = None) -> list[str]:
    if source == "x11":
        if region is None:
            return ["-f", "x11grab", "-draw_mouse", "1", "-framerate", str(fps),
                    "-i", display]
        x, y, w, h = region
        return ["-f", "x11grab", "-draw_mouse", "1", "-framerate", str(fps),
                "-video_size", f"{w}x{h}", "-i", f"{display}+{x},{y}"]
    if source in ("kmsgrab", "kms"):
        args: list[str] = []
        if kms_device:
            args += ["-device", kms_device]
        args += ["-f", "kmsgrab", "-framerate", str(fps)]
        if crtc_id is not None:
            args += ["-crtc_id", str(crtc_id)]
        return args + ["-i", "-"]
    pat = TEST_PATTERNS[index % len(TEST_PATTERNS)]
    return ["-re", "-f", "lavfi", "-i", f"{pat}=size={width}x{height}:rate={fps}"]


def ffmpeg_cmd(source, region, index, width, height, fps, encoder, display,
               device=None, kms_device=None, crtc_id=None) -> list[str]:
    common = ["ffmpeg", "-hide_banner", "-loglevel", "warning"] + \
        input_args(source, region, index, width, height, fps, display,
                   kms_device=kms_device, crtc_id=crtc_id)
    scale = f"scale={width}:{height}"
    zerocopy = source in ("kmsgrab", "kms")

    if encoder == "h264_vaapi" and zerocopy:
        # derive_device sets the vaapi device, setting -vaapi_device conflicts
        filters = ["hwmap=derive_device=vaapi"]
        if region is not None:
            x, y, w, h = region
            filters.append(f"crop={w}:{h}:{x}:{y}")
        filters.append(f"scale_vaapi=w={width}:h={height}:format=nv12")
        enc = ["-vf", ",".join(filters), "-c:v", "h264_vaapi",
               "-profile:v", hardware.VAAPI_PROFILE,
               "-aud", "1", "-g", str(fps), "-bf", "0"]
    elif encoder == "h264_vaapi":
        vf = f"hwupload,scale_vaapi=w={width}:h={height}:format=nv12"
        enc = ["-vaapi_device", device or hardware.DEFAULT_VAAPI_DEVICE, "-vf", vf,
               "-c:v", "h264_vaapi", "-profile:v", hardware.VAAPI_PROFILE,
               "-aud", "1", "-g", str(fps), "-bf", "0"]
    else:
        vf = ",".join([scale, "format=yuv420p"])
        enc = ["-vf", vf, "-c:v", "libx264", "-profile:v", "baseline",
               "-preset", "ultrafast", "-tune", "zerolatency",
               "-x264-params",
               f"aud=1:repeat-headers=1:keyint={fps}:min-keyint={fps}:scenecut=0"]

    return common + enc + ["-f", "h264", "-"]


def select_encoder(requested: str) -> tuple[str, str | None, dict]:
    env = vaapi_environment()
    if requested != "h264_vaapi":
        return requested, None, env

    device = vaapi_device()
    if device is None:
        log.info("[video] h264_vaapi requested but no /dev/dri/render* device found "
                 "(no iGPU, or this user isn't in the 'render'/'video' group) -- "
                 "falling back to software libx264")
        return "libx264", None, env

    if "LIBVA_DRIVER_NAME" in env:
        if vaapi_encode_works(device, env):
            log.info("[video] hardware encoding via VAAPI on %s (driver=%s)",
                     device, env["LIBVA_DRIVER_NAME"])
            return "h264_vaapi", device, env
    else:
        # try default driver first, then fall back to i965 for older intel igpus
        if vaapi_encode_works(device, env):
            log.info("[video] hardware encoding via VAAPI on %s (default driver)", device)
            return "h264_vaapi", device, env

        fallback_env = dict(env, LIBVA_DRIVER_NAME=hardware.LIBVA_DRIVER)
        if vaapi_encode_works(device, fallback_env):
            log.info("[video] hardware encoding via VAAPI on %s (driver=%s)",
                     device, hardware.LIBVA_DRIVER)
            return "h264_vaapi", device, fallback_env

    log.info("[video] VAAPI is present but hardware H.264 encode is not usable on this "
             "GPU/driver -- falling back to software libx264")
    return "libx264", None, env


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None
