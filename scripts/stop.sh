#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_DIR/data/weclaw.pid"

cd "$PROJECT_DIR"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found, searching for process..."
    FOUND=""
    # 匹配两种启动方式：模块启动 (python -m weclaw) 和脚本入口 (bin/weclaw)
    FOUND=$(pgrep -f "bin/weclaw" 2>/dev/null || true)
    if [ -z "$FOUND" ]; then
        FOUND=$(pgrep -f "python -m weclaw" 2>/dev/null || true)
    fi
    if [ -n "$FOUND" ]; then
        for pid in $FOUND; do
            echo "Killing PID $pid..."
            kill "$pid" 2>/dev/null || true
        done
        sleep 2
        for pid in $FOUND; do
            if kill -0 "$pid" 2>/dev/null; then
                echo "PID $pid still alive, force killing..."
                kill -9 "$pid" 2>/dev/null || true
            fi
        done
        echo "Stopped."
    else
        echo "No running WeClaw process found."
    fi
    exit 0
fi

PID=$(cat "$PID_FILE")

if kill -0 "$PID" 2>/dev/null; then
    echo "Stopping WeClaw (PID $PID)..."
    kill "$PID"
    sleep 2
    if kill -0 "$PID" 2>/dev/null; then
        echo "Process still alive, force killing..."
        kill -9 "$PID"
    fi
    echo "Stopped."
else
    echo "PID $PID not running."
fi

rm -f "$PID_FILE"
