#!/usr/bin/env bash
set -euo pipefail
systemctl --user disable --now dxctv-recorder.service >/dev/null 2>&1 || true
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/dxctv-recorder.service"
systemctl --user daemon-reload
systemctl --user reset-failed >/dev/null 2>&1 || true
echo "DXCTV Recorder's Linux service was removed. Settings and recordings were kept."
