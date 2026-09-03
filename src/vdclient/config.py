"""
Configuration loading with precedence: JSON file -> env -> CLI.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from . import hardware

# Search order when --config is not given.
DEFAULT_CONFIG_PATHS = (
    Path("config/vdclient.json"),
    Path("~/.config/vdclient/vdclient.json").expanduser(),
    Path("/etc/vdclient/vdclient.json"),
)

ENV_PREFIX = "VDCLIENT_"


class ConfigError(ValueError):
    """Raised for a config file that parses but does not make sense."""


@dataclass
class IdentityConfig:
    name: str = "Linux Test PC"
    description: str = "Virtual Desktop (Linux)"
    # quest checks this against app version for update prompts
    streamer_version: str = "1.34.22.0"
    # quest only accepts Windows or MacOS
    os_name: str = "Windows"
    region: str = "AmericaWest"
    allow_remote_connections: bool = False
    encrypt_local_traffic: bool = True
    encrypt_remote_traffic: bool = True
    # quest expects a valid mac in PrivateAdapters for network checks
    adapter_mac: str = "02:00:00:00:00:01"
    adapter_wireless: bool = True
    adapter_gigabit: bool = True

    def validate(self) -> None:
        if self.os_name not in ("Windows", "MacOS"):
            raise ConfigError(
                f"identity.os_name must be 'Windows' or 'MacOS' (got {self.os_name!r}); "
                "the Quest's deserializer rejects anything else and silently drops "
                "the computer from its list"
            )


@dataclass
class VideoConfig:
    enabled: bool = True
    # None detects primary output and aligns down to macroblocks
    width: int | None = None
    height: int | None = None
    fps: int = 60
    # h264_vaapi (iGPU) or libx264 (software fallback)
    encoder: str = "h264_vaapi"
    source: str = "auto"
    # entries: null (full primary), [x, y, w, h] (region), or {"virtual": true}
    monitors: list[Any] = field(default_factory=lambda: [None])

    def validate(self) -> None:
        from .protocol.constants import MAX_MONITORS

        if not self.monitors:
            raise ConfigError("video.monitors must list at least one monitor")
        if len(self.monitors) > MAX_MONITORS:
            raise ConfigError(
                f"video.monitors has {len(self.monitors)} entries but the Quest "
                f"configures at most {MAX_MONITORS} decoders"
            )
        if (self.width is None) != (self.height is None):
            given, missing = (("width", "height") if self.width is not None
                              else ("height", "width"))
            raise ConfigError(
                f"video.{given} is set but video.{missing} is not; set both to pin "
                "the encode size, or neither to detect it from the primary output. "
                "Mixing the two would pair one configured dimension with an "
                "unrelated detected or fallback one and distort the picture"
            )
        for dim, name in ((self.width, "width"), (self.height, "height")):
            if dim is None:
                continue
            if dim <= 0:
                raise ConfigError(f"video.{name} must be positive")
            if dim % hardware.MACROBLOCK:
                raise ConfigError(
                    f"video.{name}={dim} is not a multiple of {hardware.MACROBLOCK}; "
                    "the encoder would pad and signal a crop the Quest's decoder may "
                    "ignore, leaving a black bar along one edge"
                )
        if self.fps <= 0:
            raise ConfigError("video.fps must be positive")


@dataclass
class TunablesConfig:
    # if quest stops reading stream, timeout disconnects instead of hanging writer
    video_send_timeout: float = 5.0
    # drops frames on overflow to avoid stalling encoder
    video_queue_size: int = 256
    # sleep interval for idle channels
    idle_hold_seconds: float = 3600.0


@dataclass
class LoggingConfig:
    level: str = "INFO"
    # prefix logs with HH:MM:SS
    timestamps: bool = True


@dataclass
class Config:
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    tunables: TunablesConfig = field(default_factory=TunablesConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def validate(self) -> None:
        self.identity.validate()
        self.video.validate()


def _coerce(value: Any, current: Any, path: str) -> Any:
    # uses default value type bc annotations become strings under future annotations
    if isinstance(current, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"{path}: expected true or false, got {value!r}")
        return value
    for target in (int, float, str):
        if isinstance(current, target):
            if isinstance(value, bool):
                raise ConfigError(f"{path}: expected {target.__name__}, got a boolean")
            try:
                return target(value)
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    f"{path}: expected {target.__name__}, got {value!r}"
                ) from exc
    return value


def _apply(section: Any, values: dict, path: str) -> None:
    known = {f.name: f for f in fields(section)}
    for key, value in values.items():
        # ignore comments and schema keys
        if key.startswith(("_", "$")):
            continue
        if key not in known:
            raise ConfigError(
                f"{path}.{key} is not a recognised setting "
                f"(known: {', '.join(sorted(known))})"
            )
        current = getattr(section, known[key].name)
        if is_dataclass(current) and isinstance(value, dict):
            _apply(current, value, f"{path}.{key}")
        else:
            setattr(section, known[key].name, _coerce(value, current, f"{path}.{key}"))


def _normalise_monitors(monitors: list[Any]) -> list[Any]:
    # convert [x, y, w, h] lists to tuples for the video layer
    out: list[Any] = []
    for i, m in enumerate(monitors):
        if m is None or isinstance(m, dict):
            out.append(m)
        elif isinstance(m, (list, tuple)):
            if len(m) != 4:
                raise ConfigError(
                    f"video.monitors[{i}]: a capture region needs exactly "
                    f"[x, y, width, height], got {list(m)!r}"
                )
            out.append(tuple(int(v) for v in m))
        else:
            raise ConfigError(
                f"video.monitors[{i}]: expected null, [x, y, w, h], or "
                f'{{"virtual": true}}, got {m!r}'
            )
    return out


def _from_env(cfg: Config) -> None:
    # apply VDCLIENT_* environment variable overrides
    for section_field in fields(cfg):
        section = getattr(cfg, section_field.name)
        for f in fields(section):
            var = f"{ENV_PREFIX}{section_field.name.upper()}_{f.name.upper()}"
            raw = os.environ.get(var)
            if raw is None:
                continue
            if isinstance(getattr(section, f.name), bool):
                setattr(section, f.name, raw.strip().lower() in ("1", "true", "yes", "on"))
            else:
                try:
                    setattr(section, f.name, json.loads(raw))
                except json.JSONDecodeError:
                    setattr(section, f.name, raw)


def find_config_file(explicit: str | os.PathLike | None = None) -> Path | None:
    if explicit is not None:
        path = Path(explicit)
        if not path.exists():
            raise ConfigError(f"config file not found: {path}")
        return path
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    return None


def load(path: str | os.PathLike | None = None, overrides: dict | None = None) -> Config:
    """Build a Config from the JSON file (if any), then env, then `overrides`."""
    cfg = Config()

    config_path = find_config_file(path)
    if config_path is not None:
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{config_path}: invalid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{config_path}: top level must be a JSON object")
        _apply(cfg, raw, "config")

    _from_env(cfg)

    if overrides:
        _apply(cfg, overrides, "config")

    cfg.video.monitors = _normalise_monitors(cfg.video.monitors)
    cfg.validate()
    return cfg
