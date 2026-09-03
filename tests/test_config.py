"""Config loading, validation and the geometry resolution it feeds."""
from __future__ import annotations

import json

import pytest

from vdclient import config as config_module
from vdclient.config import Config, ConfigError
from vdclient.video import geometry


def write(tmp_path, obj):
    path = tmp_path / "vdclient.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_defaults_reproduce_the_prototype():
    """An absent config file must behave exactly like the prototype's constants."""
    cfg = config_module.load(None)
    assert cfg.identity.name == "Linux Test PC"
    assert cfg.identity.os_name == "Windows"
    assert cfg.identity.region == "AmericaWest"
    assert cfg.identity.streamer_version == "1.34.22.0"
    assert cfg.identity.encrypt_local_traffic is True
    assert cfg.video.enabled is True
    assert cfg.video.fps == 60
    assert cfg.video.encoder == "h264_vaapi"
    assert cfg.video.source == "auto"
    assert cfg.video.monitors == [None]
    assert cfg.tunables.video_send_timeout == 5.0
    assert cfg.tunables.video_queue_size == 256


def test_example_config_equals_defaults():
    """The shipped example must be a no-op, so it documents without surprising."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "config" / "vdclient.example.json"
    assert config_module.load(example) == Config()


def test_file_overrides(tmp_path):
    cfg = config_module.load(write(tmp_path, {
        "identity": {"name": "Workshop PC"},
        "video": {"fps": 72, "encoder": "libx264"},
    }))
    assert cfg.identity.name == "Workshop PC"
    assert cfg.video.fps == 72
    assert cfg.video.encoder == "libx264"
    assert cfg.identity.os_name == "Windows"  # untouched


def test_comment_keys_are_ignored_at_every_level(tmp_path):
    cfg = config_module.load(write(tmp_path, {
        "$schema": "./schema.json",
        "_note": "top level",
        "video": {"_why": "because", "fps": 30},
    }))
    assert cfg.video.fps == 30


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="not a recognised setting"):
        config_module.load(write(tmp_path, {"video": {"fpz": 60}}))


def test_missing_explicit_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        config_module.load(tmp_path / "nope.json")


def test_invalid_json_is_reported(tmp_path):
    path = tmp_path / "vdclient.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        config_module.load(path)


def test_region_lists_become_tuples(tmp_path):
    """JSON has no tuples, but the video layer expects the prototype's shapes."""
    cfg = config_module.load(write(tmp_path, {
        "video": {"monitors": [None, [1600, 0, 1280, 720], {"virtual": True}]}
    }))
    assert cfg.video.monitors == [None, (1600, 0, 1280, 720), {"virtual": True}]


def test_malformed_region_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="x, y, width, height"):
        config_module.load(write(tmp_path, {"video": {"monitors": [[1, 2, 3]]}}))


def test_too_many_monitors_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="at most 3"):
        config_module.load(write(tmp_path, {"video": {"monitors": [None] * 4}}))


def test_empty_monitors_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="at least one monitor"):
        config_module.load(write(tmp_path, {"video": {"monitors": []}}))


def test_bad_os_name_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="Windows"):
        config_module.load(write(tmp_path, {"identity": {"os_name": "Linux"}}))


def test_non_macroblock_size_is_rejected(tmp_path):
    """900 is 56.25 macroblock rows: the encoder would pad to 912 and signal a
    crop the Quest's decoder may ignore, showing a black bar."""
    with pytest.raises(ConfigError, match="multiple of 16"):
        config_module.load(write(tmp_path, {"video": {"width": 1600, "height": 900}}))


def test_aligned_size_is_accepted(tmp_path):
    cfg = config_module.load(write(tmp_path, {"video": {"width": 1600, "height": 896}}))
    assert (cfg.video.width, cfg.video.height) == (1600, 896)


@pytest.mark.parametrize("half", [{"width": 1600}, {"height": 896}])
def test_half_specified_size_is_rejected(tmp_path, half):
    """One dimension configured and the other detected would pair unrelated
    numbers and distort the picture; demand both or neither."""
    with pytest.raises(ConfigError, match="set both"):
        config_module.load(write(tmp_path, {"video": half}))


def test_env_override(monkeypatch):
    monkeypatch.setenv("VDCLIENT_VIDEO_FPS", "90")
    monkeypatch.setenv("VDCLIENT_VIDEO_ENABLED", "false")
    cfg = config_module.load(None)
    assert cfg.video.fps == 90
    assert cfg.video.enabled is False


# --- geometry -------------------------------------------------------------

def test_geometry_prefers_explicit_config():
    assert geometry.resolve(1280, 720, (0, 0, 1920, 1080)) == (1280, 720)


def test_geometry_aligns_the_detected_panel_down():
    """The prototype's hardcoded 1600x896 came from a 1600x900 panel; detection
    must reproduce exactly that."""
    assert geometry.resolve(None, None, (0, 0, 1600, 900)) == (1600, 896)


@pytest.mark.parametrize("size,expected", [
    ((1920, 1080), (1920, 1072)),
    ((2560, 1440), (2560, 1440)),
    ((1366, 768), (1360, 768)),
])
def test_geometry_alignment_cases(size, expected):
    assert geometry.resolve(None, None, (0, 0, *size)) == expected


def test_geometry_falls_back_without_a_display():
    assert geometry.resolve(None, None, None) == geometry.FALLBACK_SIZE


def test_align_down_never_returns_zero():
    assert geometry.align_down(8) == 16
