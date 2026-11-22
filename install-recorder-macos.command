#!/bin/bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/Library/Application Support/DXCTVRecorder"
PLIST_PATH="$HOME/Library/LaunchAgents/dxctv-recorder.plist"
PYTHON="$(command -v python3 || true)"
FFMPEG="$(command -v ffmpeg || true)"

if [[ -z "$PYTHON" ]]; then
  echo "Python 3 is required. Install Python 3, then run this installer again."
  exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 or later is required. Update Python, then run this installer again."
  exit 1
fi
if [[ -z "$FFMPEG" ]]; then
  echo "FFmpeg is required. Install it (for example, with Homebrew: brew install ffmpeg), then run this installer again."
  exit 1
fi
if [[ ! -f "$SOURCE_DIR/recorder.py" ]]; then
  echo "recorder.py was not found beside this installer. Extract the complete DXCTV Recorder folder first."
  exit 1
fi

mkdir -p "$APP_DIR" "$HOME/Library/LaunchAgents"
cp "$SOURCE_DIR/recorder.py" "$APP_DIR/recorder.py"
if [[ -f "$SOURCE_DIR/uninstall-recorder-macos.command" ]]; then cp "$SOURCE_DIR/uninstall-recorder-macos.command" "$APP_DIR/uninstall-recorder.command"; chmod 0755 "$APP_DIR/uninstall-recorder.command"; fi
cp "$SOURCE_DIR/README.md" "$APP_DIR/README.md" 2>/dev/null || true
cp "$SOURCE_DIR/DXCTVRecorder_User_Guide.md" "$APP_DIR/DXCTVRecorder_User_Guide.md" 2>/dev/null || true
if [[ ! -f "$APP_DIR/config.json" ]]; then
  "$PYTHON" - "$APP_DIR/config.json" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"ffmpeg_path":"ffmpeg","recordings_folder":"recordings","segment_minutes":10,"retention_days":30,"port":8765,"cameras":[]}, indent=2) + "\n", encoding="utf-8")
PY
fi
"$PYTHON" - "$APP_DIR/config.json" "$FFMPEG" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["ffmpeg_path"] = sys.argv[2]
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

"$PYTHON" - "$PLIST_PATH" "$PYTHON" "$APP_DIR/recorder.py" "$APP_DIR" "$PATH" <<'PY'
import os, plistlib, sys
path, python, script, workdir, search_path = sys.argv[1:]
plist = {
    "Label": "dxctv-recorder",
    "ProgramArguments": [python, script],
    "WorkingDirectory": workdir,
    "RunAtLoad": True,
    "KeepAlive": True,
    "EnvironmentVariables": {"PATH": search_path},
    "StandardOutPath": os.path.join(workdir, "dxctv-recorder.log"),
    "StandardErrorPath": os.path.join(workdir, "dxctv-recorder-error.log"),
}
with open(path, "wb") as f:
    plistlib.dump(plist, f)
PY

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/dxctv-recorder"
PORT="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("port",8765))' "$APP_DIR/config.json")"
echo "DXCTV Recorder is installed and running as a macOS login service."
echo "Open http://127.0.0.1:${PORT}/ to configure cameras."
if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:${PORT}/" || true; fi
