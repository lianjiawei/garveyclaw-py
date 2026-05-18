#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${WECLAW_INSTALL_DIR:-$HOME/.weclaw/weclaw}"
BIN_DIR="${WECLAW_BIN_DIR:-$HOME/.local/bin}"
KEEP_DATA="${WECLAW_KEEP_DATA:-0}"

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

assert_safe_install_dir() {
    local parent
    local resolved
    parent="$(dirname "$INSTALL_DIR")"
    if [ -d "$parent" ]; then
        resolved="$(cd "$parent" && pwd)/$(basename "$INSTALL_DIR")"
    else
        resolved="$INSTALL_DIR"
    fi
    case "$resolved" in
        "/"|"$HOME"|"$HOME/"|"$HOME/.weclaw"|"$HOME/.local"|"/opt"|"/usr"|"/usr/local"|"/tmp")
            fail "Refusing to remove broad directory: $resolved. Set WECLAW_INSTALL_DIR to the exact WeClaw install path."
            ;;
    esac
}

remove_file() {
    local path="$1"
    if [ -e "$path" ] || [ -L "$path" ]; then
        rm -f "$path"
        echo "Removed $path"
    fi
}

remove_dir() {
    local path="$1"
    if [ -d "$path" ]; then
        rm -rf "$path"
        echo "Removed $path"
    fi
}

main() {
    info "Uninstalling WeClaw"
    if [ "$KEEP_DATA" != "1" ]; then
        assert_safe_install_dir
    fi

    remove_file "$BIN_DIR/weclaw"
    remove_file "$BIN_DIR/weclaw-tui"
    remove_file "$BIN_DIR/weclaw-dashboard"
    remove_file "$BIN_DIR/weclaw-feishu"

    if [ "$KEEP_DATA" = "1" ]; then
        warn "Keeping install directory because WECLAW_KEEP_DATA=1: $INSTALL_DIR"
    else
        remove_dir "$INSTALL_DIR"
    fi

    parent_dir="$(dirname "$INSTALL_DIR")"
    if [ "$KEEP_DATA" != "1" ] && [ "$parent_dir" != "$HOME" ] && [ -d "$parent_dir" ]; then
        rmdir "$parent_dir" 2>/dev/null || true
    fi

    echo ""
    info "WeClaw uninstall complete"
    echo "If your shell still finds weclaw, open a new terminal or remove stale PATH entries manually."
}

main "$@"
