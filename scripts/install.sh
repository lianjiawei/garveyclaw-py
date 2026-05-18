#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${WECLAW_REPO_URL:-https://github.com/lianjiawei/weclaw.git}"
BRANCH="${WECLAW_BRANCH:-master}"
INSTALL_DIR="${WECLAW_INSTALL_DIR:-$HOME/.weclaw/weclaw}"
BIN_DIR="${WECLAW_BIN_DIR:-$HOME/.local/bin}"
PYTHON_BIN="${PYTHON:-}"

info() {
    printf '\033[1;36m==>\033[0m %s\n' "$1"
}

warn() {
    printf '\033[1;33mWarning:\033[0m %s\n' "$1"
}

fail() {
    printf '\033[1;31mError:\033[0m %s\n' "$1" >&2
    exit 1
}

run_elevated() {
    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        warn "Need root privileges to run: $*"
        return 1
    fi
}

find_python() {
    if [ -n "$PYTHON_BIN" ]; then
        command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "PYTHON=$PYTHON_BIN was not found."
        printf '%s' "$PYTHON_BIN"
        return
    fi
    for candidate in python3.12 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
            then
                printf '%s' "$candidate"
                return
            fi
        fi
    done
    fail "Python 3.12+ is required. Install Python 3.12 first, or set PYTHON=/path/to/python."
}

ensure_git() {
    command -v git >/dev/null 2>&1 || fail "git is required. Install git first."
}

install_ffmpeg() {
    if command -v ffmpeg >/dev/null 2>&1; then
        info "ffmpeg is already installed"
        return
    fi

    info "Installing ffmpeg for local voice transcription"
    if command -v apt-get >/dev/null 2>&1; then
        run_elevated apt-get update || return
        run_elevated apt-get install -y ffmpeg || return
    elif command -v dnf >/dev/null 2>&1; then
        run_elevated dnf install -y ffmpeg || return
    elif command -v yum >/dev/null 2>&1; then
        run_elevated yum install -y ffmpeg || return
    elif command -v pacman >/dev/null 2>&1; then
        run_elevated pacman -Sy --noconfirm ffmpeg || return
    elif command -v brew >/dev/null 2>&1; then
        brew install ffmpeg
    else
        warn "ffmpeg was not found and no supported package manager was detected. Voice transcription needs ffmpeg; install it manually if voice messages fail."
        return
    fi

    if ! command -v ffmpeg >/dev/null 2>&1; then
        warn "ffmpeg installation command finished, but ffmpeg is still not on PATH. Voice transcription may fail until ffmpeg is available."
    fi
}

install_repo() {
    mkdir -p "$(dirname "$INSTALL_DIR")"
    if [ -d "$INSTALL_DIR/.git" ]; then
        info "Updating WeClaw at $INSTALL_DIR"
        git -C "$INSTALL_DIR" fetch origin "$BRANCH"
        git -C "$INSTALL_DIR" checkout "$BRANCH"
        git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
    elif [ -e "$INSTALL_DIR" ]; then
        fail "$INSTALL_DIR already exists but is not a git repository. Set WECLAW_INSTALL_DIR to another path."
    else
        info "Cloning WeClaw into $INSTALL_DIR"
        git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    fi
}

create_venv() {
    local python_cmd="$1"
    info "Preparing Python environment"
    "$python_cmd" -m venv "$INSTALL_DIR/.venv"
    "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
    "$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR"
}

build_core_dashboard() {
    if ! command -v npm >/dev/null 2>&1; then
        warn "npm was not found. /core dashboard will be built later if npm is installed."
        return
    fi
    if [ ! -f "$INSTALL_DIR/pixel-office-core/package.json" ]; then
        return
    fi
    info "Building pixel-office-core dashboard"
    (
        cd "$INSTALL_DIR/pixel-office-core"
        if [ -f package-lock.json ]; then
            npm ci
        else
            npm install
        fi
        npm run build
    )
}

write_wrapper() {
    local name="$1"
    mkdir -p "$BIN_DIR"
    cat > "$BIN_DIR/$name" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/.venv/bin/$name" "\$@"
EOF
    chmod +x "$BIN_DIR/$name"
}

write_wrappers() {
    info "Installing command wrappers into $BIN_DIR"
    write_wrapper weclaw
    write_wrapper weclaw-tui
    write_wrapper weclaw-dashboard
    write_wrapper weclaw-feishu
    write_wrapper weclaw-weixin
}

print_next_steps() {
    echo ""
    info "WeClaw installed successfully"
    echo ""
    echo "Next steps:"
    echo "  weclaw setup"
    echo "  weclaw doctor"
    echo "  weclaw model list"
    echo "  weclaw channel setup telegram   # optional, configure later"
    echo "  weclaw start   # background mode on Linux/macOS/WSL2"
    echo "  weclaw run     # foreground mode"
    echo ""
    echo "If 'weclaw' is not found, add this to your shell profile:"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
    echo ""
    echo "If you are inside the source directory, this also works:"
    echo "  cd \"$INSTALL_DIR\" && python -m weclaw doctor"
    echo ""
    echo "Install path:"
    echo "  $INSTALL_DIR"
}

main() {
    ensure_git
    local python_cmd
    python_cmd="$(find_python)"
    info "Using Python: $python_cmd"
    install_repo
    install_ffmpeg
    create_venv "$python_cmd"
    build_core_dashboard
    write_wrappers
    print_next_steps
}

main "$@"
