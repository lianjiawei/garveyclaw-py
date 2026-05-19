#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="${WECLAW_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
BIN_DIR="${WECLAW_BIN_DIR:-$HOME/.local/bin}"

info() {
    echo "[weclaw] $*"
}

fail() {
    echo "[weclaw] ERROR: $*" >&2
    exit 1
}

write_wrapper() {
    local name="$1"
    local target="$BIN_DIR/$name"
    local executable="$INSTALL_DIR/.venv/bin/$name"

    mkdir -p "$BIN_DIR"
    [ -x "$executable" ] || fail "Missing executable: $executable. Run: cd \"$INSTALL_DIR\" && python -m pip install -e ."

    cat > "$target" <<EOF
#!/usr/bin/env bash
set -e
export WECLAW_INSTALL_DIR="$INSTALL_DIR"
cd "$INSTALL_DIR"
exec "$executable" "\$@"
EOF
    chmod +x "$target"
    info "Installed $target -> $executable"
}

write_wrapper weclaw
write_wrapper weclaw-tui
write_wrapper weclaw-dashboard
write_wrapper weclaw-feishu
write_wrapper weclaw-weixin

info "Command wrappers installed in $BIN_DIR"
info "If 'weclaw' is not found, add this to your shell profile:"
info "  export PATH=\"$BIN_DIR:\$PATH\""
