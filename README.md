# Virtual Desktop Streamer for Linux

Streams a Linux machines desktop to the Quest 3 via Virtual Desktop. 
---

Not implemented: audio output, mouse/keyboard input injection, switching active monitors (for dual monitor setups).

# Compatibility
Will work with hardware encoding on broadwell and haswell cpus (i5-4xxx, i5-5xxx, i7-4xxxx).
Skylake and up MAY use hardware (untested) otherwise will fall back to software.
Amd gpus not supported.
Non- debian distros will fail setup.
Non x11 window environments will fail.
Desktops cannot add new monitors.
No GPU support at all. 

## Requirements

Run the setup script on the streamer host:

```bash
./scripts/setup.sh          # interactive
./scripts/setup.sh --yes    # unattended
```

Checks and installs dependencies: Python 3 venv, `ffmpeg`, `xrandr`, `Xvfb`/`xauth`, i965 VAAPI driver with encode shaders, `render` group membership, `CAP_SYS_ADMIN` on ffmpeg for zero-copy kmsgrab, optional native encoders, and sudoers rules for virtual monitor scripts.

## Running

```bash
.venv/bin/python3 -m vdclient
.venv/bin/python3 -m vdclient --config config/vdclient.json --log-level DEBUG
```

Then open Virtual Desktop on the headset. The machine appears in the list.

## Configuration

JSON. Copy [`config/vdclient.example.json`](config/vdclient.example.json) to
`config/vdclient.json` and edit. Built-in defaults match the example file, so
an empty `{}` uses defaults.

Searched in order: `config/vdclient.json`,
`~/.config/vdclient/vdclient.json`, `/etc/vdclient/vdclient.json`.
Environment overrides use `VDCLIENT_<SECTION>_<KEY>`, e.g. `VDCLIENT_VIDEO_FPS=72`.

Common settings:

| Key | Default | Notes |
|---|---|---|
| `identity.name` | `"Linux Test PC"` | computer name shown in headset list |
| `video.width` / `video.height` | `null` | `null` detects primary output and aligns down to macroblocks |
| `video.fps` | `60` | target framerate |
| `video.encoder` | `"h264_vaapi"` | falls back to `libx264` if hardware encode fails |
| `video.source` | `"auto"` | `auto`, `x11`, `kmsgrab`, `test` |
| `video.monitors` | `[null]` | up to 3: `null`, `[x, y, w, h]`, or `{"virtual": true}` |

**Resolution must be a multiple of 16.** Otherwise, encoder padding causes black bars on decoders that ignore SPS cropping. Leaving dimensions as `null` auto-detects and aligns down to 16.

## Virtual monitors

`{"virtual": true}` in `video.monitors` adds an extra monitor. On X11, it forces a disconnected video connector connected to extend the desktop. If connector forcing fails, it falls back to an isolated `Xvfb` display (`DISPLAY=:50 some-app &`).

Add/Remove Monitor buttons in the Quest interface create and delete down virtual displays live.

## Tests

```bash
.venv/bin/python3 -m pytest
```

Runs offline. Tests verify `Computer` XML formatting against `DataContractSerializer` output, replay recorded Quest 3 discovery packets, validate video struct layouts, and test encoder command generation.


## Hardware

Currently targets Broadwell-era Intel iGPUs with an `eDP-1` panel. Hardware-specific defaults are defined in [`src/vdclient/hardware.py`](src/vdclient/hardware.py) 
