#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/dxctv-recorder"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/dxctv-recorder.service"
PYTHON="$(command -v python3 || true)"
FFMPEG="$(command -v ffmpeg || true)"

if [[ -z "$PYTHON" ]]; then echo "Python 3 is required. Install Python 3, then rerun this installer."; exit 1; fi
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 or later is required. Update Python, then run this installer again."
  exit 1
fi
if [[ -z "$FFMPEG" ]]; then echo "FFmpeg is required. Install FFmpeg with your distribution's package manager, then rerun this installer."; exit 1; fi
if [[ ! -f "$SOURCE_DIR/recorder.py" ]]; then echo "recorder.py was not found beside this installer. Extract the complete DXCTV Recorder folder first."; exit 1; fi
if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "A systemd user session is required for the Linux service installer."
  exit 1
fi

mkdir -p "$APP_DIR" "$UNIT_DIR"
install -m 0644 "$SOURCE_DIR/recorder.py" "$APP_DIR/recorder.py"
if [[ -f "$SOURCE_DIR/uninstall-recorder-linux.sh" ]]; then install -m 0755 "$SOURCE_DIR/uninstall-recorder-linux.sh" "$APP_DIR/uninstall-recorder.sh"; fi
if [[ -f "$SOURCE_DIR/README.md" ]]; then install -m 0644 "$SOURCE_DIR/README.md" "$APP_DIR/README.md"; fi
if [[ -f "$SOURCE_DIR/DXCTVRecorder_User_Guide.md" ]]; then install -m 0644 "$SOURCE_DIR/DXCTVRecorder_User_Guide.md" "$APP_DIR/DXCTVRecorder_User_Guide.md"; fi
if [[ ! -f "$APP_DIR/config.json" ]]; then
  "$PYTHON" - "$APP_DIR/config.json" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"ffmpeg_path":"ffmpeg","recordings_folder":"recordings","segment_minutes":10,"retention_days":30,"port":8765,"cameras":[]}, indent=2) + "\n", encoding="utf-8")
PY
  chmod 600 "$APP_DIR/config.json"
fi
"$PYTHON" - "$APP_DIR/config.json" "$FFMPEG" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["ffmpeg_path"] = sys.argv[2]
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
"$PYTHON" - "$UNIT_PATH" "$PYTHON" "$APP_DIR/recorder.py" "$APP_DIR" <<'PY'
import pathlib, sys
unit_path, python, script, workdir = sys.argv[1:]
def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
unit = f"""[Unit]
Description=DXCTV Recorder local CCTV recording service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={quote(workdir)}
ExecStart={quote(python)} {quote(script)}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""
pathlib.Path(unit_path).write_text(unit, encoding="utf-8")
PY

systemctl --user daemon-reload
systemctl --user enable --now dxctv-recorder.service
if command -v loginctl >/dev/null 2>&1 && loginctl show-user "$USER" -p Linger --value 2>/dev/null | grep -qx no; then
  echo "Enabling the user service at boot (sudo may ask for your password)..."
  if command -v sudo >/dev/null 2>&1; then sudo loginctl enable-linger "$USER" || echo "Could not enable start-before-login. The service will start when you sign in."; fi
fi
PORT="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("port",8765))' "$APP_DIR/config.json")"
echo "DXCTV Recorder is installed and running as a systemd user service."
echo "Open http://127.0.0.1:${PORT}/ to configure cameras."
if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || true; fi
