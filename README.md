# DXCTV Recorder

A lightweight, self-hosted 24/7 recorder for **TP-Link Tapo** cameras. DXCTV Recorder captures each camera's RTSP stream as MP4 segments on your own computer and serves a local browser page for setup, live view, and playback. There is no cloud service and no subscription.

> **Note:** This is an independent project. It is not affiliated with or endorsed by TP-Link.

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Installation](#installation)
- [Using the web client](#using-the-web-client)
- [Standalone executables](#standalone-executables)
- [Building from source](#building-from-source)
- [Security and privacy](#security-and-privacy)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

For complete step-by-step operating instructions, see the [User Guide](DXCTVRecorder_User_Guide.md).

## Features

- **Continuous recording** of multiple Tapo cameras to MP4 segments with a configurable segment length.
- **Local web client** at `http://127.0.0.1:8765` for camera setup, status, live view, and playback.
- **Live view** with 1, 2, 3 or 4 panel layouts and full-screen mode.
- **Recordings browser** organised by camera and date.
- **Automatic retention cleanup** that keeps 30, 60 or 90 days of footage.
- **Runs as a background service** on Windows (Windows service), macOS (`launchd`), and Linux (systemd user service), including Raspberry Pi OS.
- **Standalone single-file executables** that run without Python installed.
- No code editing: cameras, storage folder and port are all set in the browser.

## How it works

```
Tapo camera(s) ──RTSP──▶ FFmpeg (per camera) ──▶ recordings/<camera>/<date>/*.mp4
                                   ▲
                       recorder.py (service)
                                   │
                    Local web client (127.0.0.1:8765)
```

`recorder.py` supervises one FFmpeg process per camera, restarts streams that drop, and serves the web client. Settings are stored in a local `config.json`.

## Requirements

| Component | Requirement |
|-----------|-------------|
| OS | Windows 10/11, macOS with `launchd`, or Linux with systemd (Ubuntu, Debian, Raspberry Pi OS, and others) |
| Python | 3.10 or later (not needed with the [standalone executables](#standalone-executables)) |
| FFmpeg | Installed and available on `PATH` |
| Network | The cameras and the recording computer on the same local network |
| Cameras | Tapo models whose firmware exposes **Camera Account** (RTSP/ONVIF), for example C200, C500 and C510W |

The installers detect Python and FFmpeg but do not download or install them.

## Quick start

1. **Enable RTSP on each camera.** In the Tapo app, open the camera's settings, then go to **Advanced Settings → Camera Account** and create a dedicated username and password. This is separate from your TP-Link ID.
2. **Find each camera's IP address** in Device Info or in your router's client list. Reserve those addresses in the router so they do not change.
3. **Install Python 3 and FFmpeg.** Confirm that `python3 --version` (or `python --version` on Windows) and `ffmpeg -version` both work.
4. **Run the installer** for your OS (see [Installation](#installation)).
5. **Open** `http://127.0.0.1:8765`, choose **Add camera**, enter the camera name, IP address and Camera Account credentials, pick a recordings folder, and click **Save and apply**.

## Installation

The installer packages are built with `build-installer-package.py` (see [Building from source](#building-from-source)). When releases are published, they are attached to the repository's Releases page.

| Platform | Package |
|----------|---------|
| Windows | `DXCTVRecorder-Windows-x64-Setup.exe` |
| macOS | `DXCTVRecorder-macOS-Installer.zip` |
| Ubuntu / Linux / Raspberry Pi OS | `DXCTVRecorder-Ubuntu-Linux-Installer.zip` |
| All platforms | `DXCTVRecorder-Installers.zip` |

Every installer keeps your existing settings and recordings when upgrading.

### Windows

1. Run `DXCTVRecorder-Windows-x64-Setup.exe` and approve the administrator prompt.
2. Setup installs the **DXCTV Recorder** Windows service. The service starts immediately and then starts with Windows, before anyone signs in.
3. Open `http://127.0.0.1:8765` or use the **DXCTV Recorder** desktop shortcut.
4. To uninstall, remove **DXCTV Recorder** from **Settings → Apps → Installed apps**. Settings and recordings are kept.

Notes:
- The service runs as **LocalSystem**. Choose the recordings folder with the browser's folder picker, and use local drive paths instead of mapped network drive letters.
- Setup downloads the [WinSW](https://github.com/winsw/winsw) service wrapper from its official GitHub release and verifies its SHA-256 checksum.
- The setup executable is not code-signed, so Windows may show an unknown-publisher warning.

### macOS

1. Extract the `DXCTVRecorder` folder from the ZIP.
2. Double-click `install-recorder-macos.command`. This installs a per-user `launchd` service, starts it and opens the browser page.
3. The service starts when this user signs in.
4. To uninstall, run:
   ```bash
   ~/Library/Application\ Support/DXCTVRecorder/uninstall-recorder.command
   ```

### Linux and Raspberry Pi OS

1. Extract the `DXCTVRecorder` folder from the ZIP.
2. In a terminal inside that folder, run:
   ```bash
   ./install-recorder-linux.sh    # or: bash install-recorder-linux.sh
   ```
3. The installer creates and starts a systemd **user** service. It may ask for `sudo` to enable `loginctl linger` so the service starts at boot; if you skip this, the service starts when you sign in.
4. To uninstall:
   ```bash
   bash "${XDG_DATA_HOME:-$HOME/.local/share}/dxctv-recorder/uninstall-recorder.sh"
   ```

On a headless Raspberry Pi, open `http://127.0.0.1:8765` from a browser on the Pi itself, because the web client only listens locally.

## Using the web client

- **Settings → Camera and storage setup:** add, edit or remove cameras; choose the recordings folder with **Browse…**; set the segment length, retention period (30/60/90 days) and web client port. If you leave a camera's password blank while editing, the saved password is kept.
- **Live View:** shows 1 to 4 panels with full-screen support. Preview uses each camera's lower-bandwidth `/stream2` feed while recording continues on the configured stream (`/stream1` is high quality, `/stream2` is standard).
- **Recordings:** select a camera and a date to play that day's completed MP4 files. Files are stored under `recordings/<camera>/<date>/`.

For uninterrupted 24/7 recording, keep the computer powered on and stop it from sleeping.

## Standalone executables

Single-file builds that run **without Python**:

| File | Target |
|------|--------|
| `DXCTVRecorder-Windows-x64.exe` | Windows x64 |
| `DXCTVRecorder-Linux-x64` | Linux x64 (glibc 2.35+: Ubuntu 22.04+, Debian 12) |
| `DXCTVRecorder-Linux-arm64` | Raspberry Pi OS 64-bit (Bookworm) |
| `DXCTVRecorder-macOS-arm64` | macOS on Apple silicon |
| `DXCTVRecorder-macOS-x64` | macOS on Intel |

Put the executable in its own folder and run it. On first run it creates `config.json` and `recordings/` next to itself. To keep data somewhere else, set the `DXCTV_HOME` environment variable. FFmpeg still has to be on `PATH`, or you can set `ffmpeg_path` in `config.json`.

The service installers above run `recorder.py` with Python. They do not use these executables.

## Building from source

```bash
git clone https://github.com/JOBSService/DXCTV-Recorder.git
cd DXCTV-Recorder

# Run directly (requires Python 3.10+ and FFmpeg)
python recorder.py

# Build a standalone executable for the current OS
python -m pip install pyinstaller
python build-executables.py          # output: dist/executables/

# Build the installer packages
python build-installer-package.py    # output: dist/
```

PyInstaller cannot cross-compile. The GitHub Actions workflow [`.github/workflows/build-executables.yml`](.github/workflows/build-executables.yml) builds every platform. Run it manually or push a `v*` tag to start it.

## Security and privacy

- The web client binds to **`127.0.0.1` only**, so it cannot be reached from other devices or the internet.
- Camera credentials are entered in the web client and saved **in plain text** in the local `config.json`. Restrict access to the application data folder, and never commit, email or share `config.json` or an unredacted backup.
- RTSP credentials are redacted from FFmpeg log output.
- `config.json`, recordings, logs and build output are excluded by `.gitignore`, and installer packages never include them.
- Use a dedicated Tapo **Camera Account**. Never enter your TP-Link ID password.

If you find a security issue, please report it privately through GitHub's **Security → Report a vulnerability** instead of opening a public issue.

## Limitations

- Accessible from the local machine only. There is no remote access or user accounts yet.
- No motion detection.
- No native NAS integration. Pointing the recordings folder at a mounted NAS path works.
- Cloud sync (for example OneDrive) of the recordings folder is possible but can use a lot of storage and upload bandwidth.
- Tapo Care, on-camera microSD/Hub storage and RTSP share the camera's stream resources. Check TP-Link's guidance before enabling all of them together.

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| FFmpeg not found or access denied | Make sure `ffmpeg -version` works for the account that runs the recorder, or set `ffmpeg_path` in `config.json`. |
| Camera stuck connecting or reconnecting | Check the camera IP, the Camera Account credentials, that the camera is on the same network, and the RTSP path. Try `/stream2` on weak Wi-Fi. |
| RTSP 401 Unauthorized | Re-enter the Camera Account username and password. This is not the TP-Link ID. |
| Ping works but the camera shows as unreachable | Ping does not test RTSP. Confirm that Camera Account is enabled and that the stream is not taken by other Tapo features. |
| No video in the Recordings tab | Only completed segments are listed. Wait for the current segment to finish. |

For more help, see the [User Guide](DXCTVRecorder_User_Guide.md) and [TP-Link's RTSP/ONVIF FAQ](https://www.tp-link.com/us/support/faq/2680/).

## Project structure

```
recorder.py                      Recorder service and local web client
build-executables.py             PyInstaller single-file build
build-installer-package.py       Builds the Windows/macOS/Linux installer packages
install-recorder.ps1             Windows service installer
install-recorder-macos.command   macOS launchd installer
install-recorder-linux.sh        Linux systemd user-service installer
uninstall-recorder-*.{sh,command}
DXCTVRecorder_User_Guide.md      Full end-user guide
.github/workflows/               CI builds for all platforms
```

## Contributing

Issues and pull requests are welcome. When you report a problem, include your OS, camera model and firmware, and the relevant log lines, **with any credentials or RTSP URLs removed**.

## License

Released under the [MIT License](LICENSE).
