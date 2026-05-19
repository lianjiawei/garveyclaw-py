#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_DIR/data/weclaw.pid"

cd "$PROJECT_DIR"

is_alive() {
    local pid="$1"
    kill -0 "$pid" 2>/dev/null
}

wait_for_exit() {
    local pid="$1"
    local tries="${2:-20}"
    for _ in $(seq 1 "$tries"); do
        if ! is_alive "$pid"; then
            return 0
        fi
        sleep 0.2
    done
    return 1
}

stop_pid() {
    local pid="$1"
    if ! is_alive "$pid"; then
        return 0
    fi

    echo "Stopping WeClaw (PID $pid)..."
    kill "$pid" 2>/dev/null || true
    if wait_for_exit "$pid" 25; then
        return 0
    fi

    echo "PID $pid still alive, force killing..."
    kill -9 "$pid" 2>/dev/null || true
    wait_for_exit "$pid" 10 || true
}

find_background_pids() {
    # Only match the long-running background app started by scripts/start.sh.
    # A broader "bin/weclaw" match can catch the current "weclaw stop" wrapper
    # and make the shell print a confusing "Terminated" message.
    pgrep -f "python.* -m weclaw" 2>/dev/null || true
}

if [ -f "$PID_FILE" ]; then
    PID=$(tr -d '[:space:]' < "$PID_FILE")
    if [ -n "$PID" ] && is_alive "$PID"; then
        stop_pid "$PID"
        rm -f "$PID_FILE"
        echo "Stopped."
        exit 0
    fi

    echo "PID file is stale or invalid, cleaning up: $PID_FILE"
    rm -f "$PID_FILE"
fi

echo "No active PID file found, searching for background WeClaw process..."
FOUND=$(find_background_pids)

if [ -z "$FOUND" ]; then
    echo "No running WeClaw process found."
    exit 0
fi

for pid in $FOUND; do
    stop_pid "$pid"
done

rm -f "$PID_FILE"
echo "Stopped."
