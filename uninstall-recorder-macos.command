#!/bin/bash
set -euo pipefail
PLIST_PATH="$HOME/Library/LaunchAgents/dxctv-recorder.plist"
launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"
launchctl remove dxctv-recorder >/dev/null 2>&1 || true
echo "DXCTV Recorder's macOS login service was removed. Settings and recordings were kept."
