#!/usr/bin/env bash
# Checks (and optionally installs) everything this streamer needs: Python 3, the
# venv and its packages, the system tools used for capture and virtual monitors,
# hardware video encoding, and the scoped sudo rule the Add Monitor button needs.
#
# It finishes with a readiness report that verifies what is actually in place,
# rather than assuming an install step that printed no error succeeded.
#
# Usage:
#   ./scripts/setup.sh          interactive (asks before each install)
#   ./scripts/setup.sh --yes    answer yes to everything (for provisioning)
#   ./scripts/setup.sh --check  install nothing, just print the readiness report
#
# Exit status: 0 if every essential component is present, 1 otherwise.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python3"

ASSUME_YES=0
CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y) ASSUME_YES=1 ;;
        --check|-n) CHECK_ONLY=1 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

if [ "$ASSUME_YES" = "1" ] && [ "$CHECK_ONLY" = "1" ]; then
    echo "--yes and --check are contradictory" >&2
    exit 2
fi

ask() {
    # ask "prompt" -> 0 (yes) or 1 (no). Defaults to YES: this is an installer,
    # and a bare Enter silently skipping the step is how you end up with a
    # half-provisioned box and no idea why.
    if [ "$CHECK_ONLY" = "1" ]; then
        return 1   # --check installs nothing; decline every offer silently
    fi
    if [ "$ASSUME_YES" = "1" ]; then
        echo "$1 [Y/n] y"
        return 0
    fi
    read -r -p "$1 [Y/n] " reply
    case "$reply" in
        [Nn]|[Nn][Oo]) return 1 ;;
        *) return 0 ;;
    esac
}

pkg_install() {
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y "$@"
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y "$@"
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm "$@"
    else
        echo "  No known package manager (apt/dnf/pacman) found -- install $* manually."
        return 1
    fi
}

BUILD_LOG="$ROOT_DIR/native/build.log"

step() {
    # step "message" cmd... -- run quietly, print one line, show the tail on error.
    local msg="$1"; shift
    printf '  %-38s' "$msg..."
    if "$@" >>"$BUILD_LOG" 2>&1; then
        echo "OK"
        return 0
    fi
    echo "FAILED"
    tail -8 "$BUILD_LOG" | sed 's/^/      /'
    echo "      full log: $BUILD_LOG"
    return 1
}

build_native_module() {
    (cd "$ROOT_DIR/native" && "$VENV_PY" setup.py build_ext --inplace) || return 1
    ls "$ROOT_DIR"/native/native_encoder*.so >/dev/null 2>&1
}

build_vd_encoder() {
    (cd "$ROOT_DIR/native" && make vd_encoder) || return 1
    [ -x "$ROOT_DIR/native/vd_encoder" ]
}

check_cmd() {
    # check_cmd <human name> <command> <package> [optional=1]
    local name="$1" cmd="$2" pkg="$3" optional="${4:-0}"
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "$name Exists, OK."
        return 0
    fi
    echo "$name is missing."
    if ask "Install $pkg now?"; then
        pkg_install "$pkg"
        if command -v "$cmd" >/dev/null 2>&1; then
            echo "$name Exists, OK. (installed)"
        else
            echo "$name install failed or '$cmd' still not on PATH -- check manually."
        fi
    elif [ "$optional" = "1" ]; then
        echo "Skipping $name (optional)."
    else
        echo "Skipping $name -- related features will not work until it's installed."
    fi
}

echo "== Virtual Desktop Linux client: environment check =="
echo

# --- Python --------------------------------------------------------------
echo "-- Python --"
if command -v python3 >/dev/null 2>&1; then
    echo "Python 3 ($(python3 --version 2>&1)) Exists, OK."
else
    echo "Python 3 is missing."
    if ask "Install python3 now?"; then
        pkg_install python3 python3-venv python3-pip
    elif [ "$CHECK_ONLY" = "1" ]; then
        :   # --check never bails early; the report below says what is missing
    else
        echo "Nothing else here can proceed without it."
        exit 1
    fi
fi

# --- venv ----------------------------------------------------------------
echo
echo "-- Virtual environment ($VENV_DIR) --"
if [ -x "$VENV_PY" ]; then
    echo "venv Exists, OK."
else
    echo "venv is missing."
    if ask "Create venv at $VENV_DIR now?"; then
        python3 -m venv "$VENV_DIR"
        if [ -x "$VENV_PY" ]; then
            echo "venv Exists, OK. (created)"
        else
            echo "venv creation failed -- you may need: sudo apt-get install python3-venv"
        fi
    else
        echo "Skipping -- can't install the package without it."
    fi
fi

# --- the package itself ---------------------------------------------------
echo
echo "-- vdclient package --"
if [ -x "$VENV_PY" ]; then
    if "$VENV_PY" -c "import vdclient" >/dev/null 2>&1; then
        echo "vdclient Exists, OK."
    else
        echo "vdclient is not installed in the venv."
        if ask "Install it (editable) now?"; then
            "$VENV_PY" -m pip install -e "$ROOT_DIR"
        else
            echo "Skipping -- run it with PYTHONPATH=$ROOT_DIR/src instead."
        fi
    fi
else
    echo "Skipping (no venv)."
fi

# --- system tools ----------------------------------------------------------
echo
echo "-- System tools --"
check_cmd "ffmpeg"  ffmpeg  ffmpeg
check_cmd "xrandr"  xrandr  x11-xserver-utils   # extend the desktop onto a virtual monitor
check_cmd "Xvfb"    Xvfb    xvfb                # isolated virtual display fallback
check_cmd "xauth"   xauth   xauth
check_cmd "openbox" openbox openbox 1           # optional WM for the Xvfb fallback

# --- hardware video encode (VAAPI) -----------------------------------------
echo
echo "-- Hardware encoding (VAAPI, the default; far less CPU) --"
VAAPI_DEV=""
for d in /dev/dri/renderD128 /dev/dri/renderD129 /dev/dri/card0; do
    [ -e "$d" ] && VAAPI_DEV="$d" && break
done
if [ -z "$VAAPI_DEV" ]; then
    echo "No /dev/dri render device found -- no supported iGPU, or the driver isn't"
    echo "loaded. The streamer falls back to software encoding automatically."
else
    echo "$VAAPI_DEV Exists, OK."
    # Two gotchas on older Intel iGPUs (Broadwell/HD 5xxx):
    #  1. The default iHD driver (intel-media-driver) is DECODE-ONLY there -- it
    #     exposes no H.264 encode entrypoint ("No usable encoding entrypoint
    #     found"). The legacy i965 driver provides the encoder, which is why
    #     vdclient.hardware forces LIBVA_DRIVER_NAME=i965.
    #  2. The base i965-va-driver package ships WITHOUT the encode shader
    #     kernels, so h264_vaapi aborts inside the driver ("i965_encoder.c:
    #     Assertion `encoder_context->mfc_context' failed"). Those shaders live
    #     in i965-va-driver-shaders (non-free/multiverse).
    I965_DRV=""
    for d in /usr/lib/x86_64-linux-gnu/dri/i965_drv_video.so \
             /usr/lib/dri/i965_drv_video.so /usr/lib64/dri/i965_drv_video.so; do
        [ -e "$d" ] && I965_DRV="$d" && break
    done
    if [ -z "$I965_DRV" ]; then
        echo "i965 VAAPI driver is missing (needed for hardware H.264 ENCODE on older"
        echo "Intel iGPUs; the default iHD driver only decodes on those chips)."
        if ask "Install i965-va-driver-shaders (driver + encode shaders) now?"; then
            pkg_install i965-va-driver-shaders
        else
            echo "Skipping -- hardware encoding will fall back to software."
        fi
    elif command -v dpkg >/dev/null 2>&1 && \
         ! dpkg -s i965-va-driver-shaders >/dev/null 2>&1; then
        echo "i965 VAAPI driver Exists, OK. (but the encode shaders look missing)"
        echo "Without them h264_vaapi aborts with an mfc_context assertion."
        if ask "Install i965-va-driver-shaders now?"; then
            pkg_install i965-va-driver-shaders
        else
            echo "Skipping -- hardware encoding will fall back to software."
        fi
    else
        echo "i965 VAAPI driver + encode shaders Exist, OK."
    fi
    if [ -r "$VAAPI_DEV" ] && [ -w "$VAAPI_DEV" ]; then
        echo "Permission to use $VAAPI_DEV Exists, OK."
    else
        echo "This user has no read/write permission on $VAAPI_DEV. That's a group"
        echo "membership fix, not a package: you need to be in 'render' or 'video'."
        if ask "Add $(whoami) to the 'render' group now?"; then
            sudo usermod -aG render "$(whoami)"
            echo "Added -- log out and back in (or reboot) for it to take effect."
        else
            echo "Skipping -- ffmpeg will fail to open $VAAPI_DEV and fall back to software."
        fi
    fi
fi

# --- zero-copy capture privilege ------------------------------------------
echo
echo "-- Zero-copy capture (kmsgrab) --"
if command -v ffmpeg >/dev/null 2>&1; then
    FFMPEG_BIN="$(readlink -f "$(command -v ffmpeg)")"
    if command -v getcap >/dev/null 2>&1 && \
       getcap "$FFMPEG_BIN" 2>/dev/null | grep -qi cap_sys_admin; then
        echo "ffmpeg has CAP_SYS_ADMIN, OK."
    else
        echo "ffmpeg lacks CAP_SYS_ADMIN, so kmsgrab can't read the scanout framebuffer"
        echo "and capture falls back to x11grab+hwupload (roughly 3x the CPU)."
        if ask "Grant it now (setcap cap_sys_admin+ep $FFMPEG_BIN)?"; then
            sudo setcap cap_sys_admin+ep "$FFMPEG_BIN"
        else
            echo "Skipping -- x11grab will be used instead."
        fi
    fi
fi

# --- native encoder (optional) ---------------------------------------------
echo
echo "-- Native encoders --"
if [ -x "$VENV_PY" ]; then
    if ask "Build the native encoders now?"; then
        : > "$BUILD_LOG"
        step "Installing build dependencies" \
            pkg_install build-essential pkg-config python3-dev \
                libx11-dev libxext-dev libxfixes-dev libva-dev libva-drm2 \
                libavcodec-dev libavutil-dev libavformat-dev libavdevice-dev
        step "Installing pybind11" "$VENV_PY" -m pip install -q pybind11
        step "Building native module" build_native_module
        step "Building vd_encoder" build_vd_encoder
        # setcap needs root. sudo prompts on the tty, so it stays visible even
        # though the command's own output goes to the log.
        step "Granting vd_encoder cap_sys_admin" \
            sudo setcap cap_sys_admin+ep "$ROOT_DIR/native/vd_encoder"
    else
        echo "Skipping -- the ffmpeg path will be used (higher CPU, no cursor on kmsgrab)."
    fi
else
    echo "Skipping (no venv) -- see the venv section above first."
fi

# --- passwordless sudo for the virtual-monitor scripts ---------------------
echo
echo "-- Virtual monitor (force-connected output) --"
ENABLE_SH="$SCRIPT_DIR/enable_virtual_monitor.sh"
DISABLE_SH="$SCRIPT_DIR/disable_virtual_monitor.sh"
chmod +x "$ENABLE_SH" "$DISABLE_SH" 2>/dev/null
SUDOERS_FILE="/etc/sudoers.d/vdclient-virtual-monitor"
RULE="$(whoami) ALL=(root) NOPASSWD: $ENABLE_SH, $DISABLE_SH"
if sudo grep -qF "$RULE" "$SUDOERS_FILE" 2>/dev/null; then
    echo "Passwordless sudo rule for virtual monitors Exists, OK."
else
    echo "Add Monitor needs root to force a connector on. Installs a NOPASSWD rule"
    echo "scoped to only these two scripts:"
    echo "  $RULE"
    if ask "Install that sudoers rule now?"; then
        echo "$RULE" | sudo tee "$SUDOERS_FILE" >/dev/null
        sudo chmod 0440 "$SUDOERS_FILE"
        if sudo visudo -cf "$SUDOERS_FILE" >/dev/null 2>&1; then
            echo "Passwordless sudo rule Exists, OK. (installed)"
        else
            echo "sudoers rule failed validation -- removing it to be safe."
            sudo rm -f "$SUDOERS_FILE"
        fi
    else
        echo "Skipping -- Add Monitor falls back to an isolated Xvfb display."
    fi
fi

# --- config ----------------------------------------------------------------
echo
echo "-- Configuration --"
if [ -f "$ROOT_DIR/config/vdclient.json" ]; then
    echo "config/vdclient.json Exists, OK."
else
    echo "No config/vdclient.json (the built-in defaults will be used)."
    if ask "Copy the commented example to config/vdclient.json now?"; then
        cp "$ROOT_DIR/config/vdclient.example.json" "$ROOT_DIR/config/vdclient.json"
        echo "Created config/vdclient.json -- edit it to taste."
    fi
fi

# --- systemd service (optional) --------------------------------------------
echo
echo "-- systemd service --"
UNIT_SRC="$ROOT_DIR/systemd/vdclient.service"
UNIT_DST="/etc/systemd/system/vdclient.service"
if [ -f "$UNIT_DST" ]; then
    echo "vdclient.service Exists, OK."
else
    echo "Starts the streamer at boot as $(whoami). Not needed to run it by hand."
    if ask "Install $UNIT_DST now?"; then
        sed -e "s|__ROOT_DIR__|$ROOT_DIR|g" -e "s|__USER__|$(whoami)|g" "$UNIT_SRC" \
            | sudo tee "$UNIT_DST" >/dev/null
        sudo systemctl daemon-reload
        echo "Installed. Start it with: sudo systemctl enable --now vdclient"
    fi
fi

# --- readiness report ------------------------------------------------------
# Verify what is actually in place. An install step that printed no error is not
# evidence that it worked -- a failed compile or a failed setcap otherwise
# surfaces days later as "no cursor in the stream".
if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_BAD=$'\033[31m'
    C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_OK=""; C_WARN=""; C_BAD=""; C_DIM=""; C_OFF=""
fi

MISSING_ESSENTIAL=0
MISSING_OPTIONAL=0

row() {
    # row <ok|no> <essential|optional> <label> <detail> [fix command]
    # A row that is not OK always prints the command that fixes it.
    local state="$1" kind="$2" label="$3" detail="$4" fix="${5:-}"
    if [ "$state" = "ok" ]; then
        printf '  [ %sOK%s ]  %-22s %s%s%s\n' "$C_OK" "$C_OFF" "$label" "$C_DIM" "$detail" "$C_OFF"
        return
    fi
    if [ "$kind" = "essential" ]; then
        printf '  [%sFAIL%s]  %-22s %s\n' "$C_BAD" "$C_OFF" "$label" "$detail"
        MISSING_ESSENTIAL=$((MISSING_ESSENTIAL + 1))
    else
        printf '  [%sWARN%s]  %-22s %s\n' "$C_WARN" "$C_OFF" "$label" "$detail"
        MISSING_OPTIONAL=$((MISSING_OPTIONAL + 1))
    fi
    [ -n "$fix" ] && printf '          %sfix:%s %s\n' "$C_DIM" "$C_OFF" "$fix"
}

have() { command -v "$1" >/dev/null 2>&1; }
venv_has() { [ -x "$VENV_PY" ] && "$VENV_PY" -c "import $1" >/dev/null 2>&1; }

echo
echo "=============================== READINESS ==============================="
echo "${C_DIM}essential -- the streamer will not start without these${C_OFF}"

if have python3; then row ok essential "python3" "$(python3 --version 2>&1 | cut -d' ' -f2)"
else row no essential "python3" "not on PATH" "sudo apt install python3 python3-venv"; fi

if [ -x "$VENV_PY" ]; then row ok essential "venv" "$VENV_DIR"
else row no essential "venv" "no interpreter at $VENV_PY" "python3 -m venv $VENV_DIR"; fi

if venv_has vdclient; then row ok essential "vdclient package" "importable"
else row no essential "vdclient package" "not in the venv -- pip install -e ." "$VENV_PY -m pip install -e $ROOT_DIR"; fi

if venv_has Crypto; then row ok essential "pycryptodome" "present"
else row no essential "pycryptodome" "missing -- every channel is AES-wrapped" "$VENV_PY -m pip install -e $ROOT_DIR"; fi

if have ffmpeg; then row ok essential "ffmpeg" "$(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)"
else row no essential "ffmpeg" "not on PATH" "sudo apt install ffmpeg"; fi

echo
echo "${C_DIM}optional -- each of these degrades to a working fallback${C_OFF}"

if have xrandr; then row ok optional "xrandr" "primary output detection"
else row no optional "xrandr" "encode size falls back to 1920x1072" "sudo apt install x11-xserver-utils"; fi

if have Xvfb; then row ok optional "Xvfb" "isolated virtual display fallback"
else row no optional "Xvfb" "Add Monitor has no fallback if xrandr fails" "sudo apt install xvfb"; fi

if have xauth; then row ok optional "xauth" "present"
else row no optional "xauth" "needed by the Xvfb fallback" "sudo apt install xauth"; fi

VAAPI_DEV=""
for d in /dev/dri/renderD128 /dev/dri/renderD129 /dev/dri/card0; do
    [ -e "$d" ] && VAAPI_DEV="$d" && break
done
if [ -z "$VAAPI_DEV" ]; then
    row no optional "VAAPI device" "none found -- software encoding only" "nothing to do: this machine exposes no GPU render node"
elif [ -r "$VAAPI_DEV" ] && [ -w "$VAAPI_DEV" ]; then
    row ok optional "VAAPI device" "$VAAPI_DEV (read/write)"
else
    row no optional "VAAPI device" "$VAAPI_DEV not writable -- join the 'render' group" "sudo usermod -aG render $(whoami)   # then log out and back in"
fi

I965=""
for d in /usr/lib/x86_64-linux-gnu/dri/i965_drv_video.so /usr/lib/dri/i965_drv_video.so \
         /usr/lib64/dri/i965_drv_video.so; do
    [ -e "$d" ] && I965="$d" && break
done
if [ -z "$I965" ]; then
    row no optional "i965 VAAPI driver" "missing -- hardware H.264 encode unavailable" "sudo apt install i965-va-driver-shaders"
elif have dpkg && ! dpkg -s i965-va-driver-shaders >/dev/null 2>&1; then
    row no optional "i965 VAAPI driver" "present but encode shaders look missing" "sudo apt install i965-va-driver-shaders"
else
    row ok optional "i965 VAAPI driver" "driver + encode shaders"
fi

if have ffmpeg && have getcap && \
   getcap "$(readlink -f "$(command -v ffmpeg)")" 2>/dev/null | grep -qi cap_sys_admin; then
    row ok optional "ffmpeg cap_sys_admin" "zero-copy kmsgrab enabled"
else
    row no optional "ffmpeg cap_sys_admin" "falls back to x11grab (~3x the CPU)" "sudo setcap cap_sys_admin+ep $(readlink -f $(command -v ffmpeg))"
fi

VD_BIN="$ROOT_DIR/native/vd_encoder"
if [ ! -x "$VD_BIN" ]; then
    row no optional "vd_encoder" "not built -- no cursor on the kmsgrab path" "make -C $ROOT_DIR/native && sudo setcap cap_sys_admin+ep $VD_BIN"
elif have getcap && getcap "$VD_BIN" 2>/dev/null | grep -qi cap_sys_admin; then
    row ok optional "vd_encoder" "built, cap_sys_admin set (cursor in the stream)"
else
    row no optional "vd_encoder" "built but no cap_sys_admin -- it will produce nothing" "sudo setcap cap_sys_admin+ep $VD_BIN"
fi

if ls "$ROOT_DIR"/native/native_encoder*.so >/dev/null 2>&1; then
    row ok optional "native module" "lower-CPU X11 capture path"
else
    row no optional "native module" "not built -- the X11 path uses ffmpeg hwupload" "cd $ROOT_DIR/native && $VENV_PY setup.py build_ext --inplace"
fi

if sudo -n true 2>/dev/null && sudo -n grep -qF "$ENABLE_SH" "$SUDOERS_FILE" 2>/dev/null; then
    row ok optional "virtual-monitor sudo" "Add Monitor can force a connector on"
elif [ -f "$SUDOERS_FILE" ]; then
    row ok optional "virtual-monitor sudo" "rule installed"
else
    row no optional "virtual-monitor sudo" "Add Monitor falls back to an isolated Xvfb" "re-run ./scripts/setup.sh and answer yes at the virtual monitor step"
fi

if [ -f "$ROOT_DIR/config/vdclient.json" ]; then
    row ok optional "config file" "config/vdclient.json"
else
    row ok optional "config file" "none -- built-in defaults (this is fine)"
fi

echo "========================================================================="
if [ "$MISSING_ESSENTIAL" -gt 0 ]; then
    echo "  ${C_BAD}NOT READY${C_OFF} -- $MISSING_ESSENTIAL essential item(s) missing."
    [ "$CHECK_ONLY" = "1" ] && echo "  Re-run without --check to install them."
    exit 1
fi
if [ "$MISSING_OPTIONAL" -gt 0 ]; then
    echo "  ${C_OK}READY${C_OFF} -- $MISSING_OPTIONAL optional item(s) missing, each with a fallback."
else
    echo "  ${C_OK}READY${C_OFF} -- everything is in place."
fi
echo
echo "  Run it with:  $VENV_PY -m vdclient"
exit 0
