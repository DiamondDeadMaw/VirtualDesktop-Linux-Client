"""
Encodes and multiplexes monitors onto the video channel.
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time

from .. import logging_setup
from ..display import xsession
from ..protocol.constants import MAX_MONITORS
from ..protocol.messages import json_message
from ..protocol.video_structs import (
    CODEC_H264,
    build_frame_metadata,
    build_slice_header,
    build_video_format,
    frame_packet,
    slice_packet,
    video_format_packet,
)
from . import encoders, geometry, monitors, sources
from .access_units import SPS, DropGate, split_access_units

log = logging_setup.get("video")

_READ_CHUNK = 65536


class _NativeEncoderProc:
    def __init__(self, native):
        self._native = native

    def kill(self):
        self._native.stop()


def _reader(proc, index, out_q, alive, lock):
    buf = b""
    seen = 0
    gate = DropGate(index)
    try:
        while True:
            chunk = proc.stdout.read(_READ_CHUNK)
            if not chunk:
                try:
                    rc = proc.poll()
                except OSError:
                    rc = None
                log.info("[video] monitor %d: encoder closed its output after %d access "
                         "unit(s) (exit code %s) - the stream for this monitor ends "
                         "here; see the encoder's own messages above", index, seen, rc)
                break
            buf += chunk
            units, buf = split_access_units(buf)
            for au in units:
                seen += 1
                gate.offer(out_q, au, SPS in au)
    finally:
        with lock:
            alive[0] -= 1
            if alive[0] == 0:
                out_q.put(None)


def _native_reader(native, index, out_q, alive, lock):
    gate = DropGate(index)
    try:
        while True:
            au, is_sync = native.next_packet()
            gate.offer(out_q, au, is_sync)
    except Exception:
        pass
    finally:
        with lock:
            alive[0] -= 1
            if alive[0] == 0:
                out_q.put(None)


class VideoSession:
    def __init__(self, stream, config, session=None):
        self.stream = stream
        self.config = config
        self.session = session
        self.max_monitors = MAX_MONITORS

        video = config.video
        self.fps = video.fps
        self.encoder = video.encoder
        self.base_source = sources.resolve_source(video.source)

        self.zerocopy = (
            (video.source in ("auto", "desktop") and self.base_source == "x11"
             and self.encoder == "h264_vaapi" and sources.kmsgrab_viable())
            or self.base_source in ("kmsgrab", "kms")
        )
        if self.zerocopy:
            log.info("[video] zero-copy kmsgrab capture enabled (shared root "
                     "framebuffer, per-monitor GPU crop)")
        self.kms_device = sources.kms_card_device() if self.zerocopy else None
        self.cap_source = "kmsgrab" if self.zerocopy else self.base_source

        self.encoder, self.vaapi_device, self.vaapi_env = encoders.select_encoder(self.encoder)

        self.base_env, self.base_display = xsession.base_environment(self.base_source)
        if "LIBVA_DRIVER_NAME" in self.vaapi_env:
            self.base_env["LIBVA_DRIVER_NAME"] = self.vaapi_env["LIBVA_DRIVER_NAME"]
            os.environ["LIBVA_DRIVER_NAME"] = self.vaapi_env["LIBVA_DRIVER_NAME"]
        if self.base_source == "test" and len(video.monitors) > 1:
            log.info("[video] note: no X display, using distinct TEST patterns per monitor")

        # pin to primary output geometry so adding virtual monitors doesnt shift capture
        self.primary_region = (xsession.primary_geometry(self.base_env)
                               if self.base_source == "x11" else None)
        if self.primary_region:
            log.info("[video] primary output region %s", self.primary_region)

        self.width, self.height = geometry.resolve(
            video.width, video.height, self.primary_region)

        self.planner = monitors.CapturePlanner(
            width=self.width, height=self.height, base_source=self.base_source,
            cap_source=self.cap_source, base_display=self.base_display,
            base_env=self.base_env, primary_region=self.primary_region)

        self.plans: list[dict] = []
        self.procs: list = []
        self.frame_index: list[int] = []
        self.out_q: queue.Queue = queue.Queue(maxsize=config.tunables.video_queue_size)
        self.alive = [0]
        self.lock = threading.Lock()
        self.vd_encoder_ok: str | None = None

    def send_video_format(self, count: int) -> None:
        fmt = build_video_format(
            width=self.width, height=self.height, fps=self.fps, codec=CODEC_H264,
            bounds=monitors.bounds_for(count, self.width, self.height))
        self.stream.write_frame(video_format_packet(fmt))

    def send_setting(self, name: str, value) -> None:
        if self.session is None:
            return
        if value:
            self.session.streamer_settings[name] = value
        else:
            self.session.streamer_settings.pop(name, None)
        try:
            self.session.write_control(
                json_message("StreamerSettingsPropertyChanged", {name: value}))
        except (OSError, ConnectionError) as exc:
            log.warning("[video] failed to send %s=%s update: %r", name, value, exc)

    def _probe_vd_encoder(self) -> None:
        if not (self.zerocopy and self.encoder == "h264_vaapi"):
            return
        vd_path = encoders.vd_encoder_path()
        if vd_path is None:
            log.info("[video] native vd_encoder not built -- using ffmpeg (no cursor in "
                     "the stream). Build it with: make -C native install-cap")
            return
        probe_plan = self.plans[0]
        probe_cmd = encoders.vd_encoder_cmd(vd_path, probe_plan, self.width, self.height,
                                            self.fps, self.vaapi_device, self.kms_device)
        if encoders.vd_encoder_works(probe_cmd, probe_plan["env"]):
            self.vd_encoder_ok = vd_path
            log.info("[video] native vd_encoder active (zero-copy capture WITH cursor)")
        else:
            log.info("[video] falling back to ffmpeg (no cursor in the stream)")

    def _register(self, proc) -> None:
        self.procs.append(proc)
        self.frame_index.append(0)
        with self.lock:
            self.alive[0] += 1

    def start_encoder(self, index: int, plan: dict) -> None:
        if (encoders.native_module is not None and self.encoder == "h264_vaapi"
                and plan["source"] == "x11" and plan["region"] is not None):
            try:
                native = encoders.native_module.NativeEncoder(
                    plan["display"], plan["region"], self.width, self.height, self.fps,
                    self.vaapi_device or encoders.hardware.DEFAULT_VAAPI_DEVICE,
                    plan["env"].get("XAUTHORITY"))
                self._register(_NativeEncoderProc(native))
                threading.Thread(target=_native_reader,
                                 args=(native, index, self.out_q, self.alive, self.lock),
                                 daemon=True).start()
                log.info("[video] monitor %d: native VAAPI encoder path", index)
                return
            except Exception as exc:
                log.warning("[video] native encoder unavailable for monitor %d (%r); "
                            "falling back to ffmpeg", index, exc)

        if self.vd_encoder_ok and plan["source"] in ("kmsgrab", "kms"):
            cmd = encoders.vd_encoder_cmd(self.vd_encoder_ok, plan, self.width,
                                          self.height, self.fps, self.vaapi_device,
                                          self.kms_device)
            log.info("[video] monitor %d: %s", index, " ".join(cmd))
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0,
                                    env=plan["env"])
            self._register(proc)
            threading.Thread(target=_reader,
                                 args=(proc, index, self.out_q, self.alive, self.lock),
                                 daemon=True).start()
            return

        cmd = encoders.ffmpeg_cmd(plan["source"], plan["region"], index, self.width,
                                  self.height, self.fps, self.encoder, plan["display"],
                                  self.vaapi_device, kms_device=self.kms_device,
                                  crtc_id=plan.get("crtc_id"))
        log.info("[video] monitor %d: %s", index, " ".join(cmd))
        env = plan["env"]
        if self.encoder == "h264_vaapi" and "LIBVA_DRIVER_NAME" in self.vaapi_env and "LIBVA_DRIVER_NAME" not in env:
            env = dict(env, LIBVA_DRIVER_NAME=self.vaapi_env["LIBVA_DRIVER_NAME"])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                bufsize=0, env=env)
        self._register(proc)
        threading.Thread(target=_reader,
                         args=(proc, index, self.out_q, self.alive, self.lock),
                         daemon=True).start()

    def add_monitor(self) -> None:
        if len(self.plans) >= self.max_monitors:
            log.info("[video] AddMonitor requested but already at max monitors, ignoring")
            return
        index = len(self.plans)
        plan = self.planner.plan({"virtual": True})
        self.plans.append(plan)
        self.send_video_format(len(self.plans))
        log.info("[video] AddMonitor -> StreamCount=%d (monitor %d is a new virtual "
                 "display)", len(self.plans), index)
        self.start_encoder(index, plan)
        self.send_setting("CanRemoveMonitor", True)
        if len(self.plans) >= self.max_monitors:
            self.send_setting("CanAddMonitor", False)

    def remove_monitor(self) -> None:
        if len(self.plans) <= 1:
            log.info("[video] RemoveMonitor requested but only one monitor left, ignoring")
            return
        proc = self.procs.pop()
        self.frame_index.pop()
        plan = self.plans.pop()
        try:
            proc.kill()
        except OSError:
            pass
        monitors.teardown(plan, self.base_env)
        self.send_video_format(len(self.plans))
        log.info("[video] RemoveMonitor -> StreamCount=%d", len(self.plans))
        self.send_setting("CanAddMonitor", True)
        if len(self.plans) <= 1:
            self.send_setting("CanRemoveMonitor", False)

    def _start_command_forwarder(self) -> None:
        if self.session is None:
            return
        self.session.video_commands = queue.Queue()

        def forward():
            while True:
                cmd = self.session.video_commands.get()
                if cmd is None:
                    return
                self.out_q.put(("cmd", cmd))

        threading.Thread(target=forward, daemon=True).start()

    def run(self) -> None:
        self.plans = [self.planner.plan(m) for m in self.config.video.monitors]
        self._probe_vd_encoder()

        self.send_video_format(len(self.plans))
        log.info("[video] sent VideoFormat: H.264 %dx%d@%d StreamCount=%d (source=%s)",
                 self.width, self.height, self.fps, len(self.plans), self.base_source)

        for i, plan in enumerate(self.plans):
            self.start_encoder(i, plan)

        self._start_command_forwarder()

        sent = 0
        t0 = time.monotonic()
        try:
            while True:
                item = self.out_q.get()
                if item is None:
                    self._report_encoder_exits()
                    log.info("[video] all encoders ended")
                    break
                if item[0] == "cmd":
                    if item[1] == "AddMonitor":
                        self.add_monitor()
                    elif item[1] == "RemoveMonitor":
                        self.remove_monitor()
                    continue

                index, au, is_sync = item
                if index >= len(self.frame_index):
                    continue
                ts = int(time.monotonic() * 1_000_000)
                if index == 0:
                    meta = build_frame_metadata(
                        data_size=len(au), frame_index=self.frame_index[0],
                        is_sync=is_sync, width=self.width, height=self.height,
                        timestamp_us=ts)
                    self.stream.write_frame(frame_packet(meta, au))
                else:
                    hdr = build_slice_header(data_size=len(au), stream_index=index,
                                             timestamp_us=ts)
                    self.stream.write_frame(slice_packet(hdr, au))

                n = self.frame_index[index]
                self.frame_index[index] = n + 1
                sent += len(au)
                if index == 0 and (n < 3 or n % (self.fps * 2) == 0):
                    mbps = sent * 8 / 1e6 / max(1e-3, time.monotonic() - t0)
                    counts = "/".join(str(c) for c in self.frame_index)
                    log.info("[video] frames %s (%s) total %.1f Mbps",
                             counts, "SYNC" if is_sync else "delta", mbps)
        finally:
            self.shutdown()

    def _report_encoder_exits(self) -> None:
        for i, proc in enumerate(self.procs):
            if getattr(proc, "stderr", None) is None:
                continue
            err = (proc.stderr.read() or b"").decode("utf-8", "replace")[-300:]
            if err.strip():
                log.info("[video] monitor %d encoder stderr: %s", i, err)

    def shutdown(self) -> None:
        if self.session is not None:
            cmds = self.session.video_commands
            self.session.video_commands = None
            if cmds is not None:
                cmds.put(None)
        for proc in self.procs:
            try:
                proc.kill()
            except OSError:
                pass
        for plan in self.plans:
            monitors.teardown(plan, self.base_env)


def stream_video(stream, config, session=None) -> None:
    if not encoders.ffmpeg_available():
        log.warning("[video] ffmpeg not found on PATH -- cannot stream. Holding "
                    "channel open.")
        while True:
            time.sleep(config.tunables.idle_hold_seconds)

    VideoSession(stream, config, session=session).run()
