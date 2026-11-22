# DXCTV Recorder

For the full setup and step-by-step operating instructions, see [`DXCTVRecorder_User_Guide.md`](DXCTVRecorder_User_Guide.md).

A small Python recorder for Tapo cameras. Add only the cameras you want to use in its local browser setup page. It records RTSP streams as MP4 segments on this computer and provides a local browser page for status, live view, and playback. The installer package includes a Windows self-extracting setup executable and executable setup commands for macOS and systemd-based Linux, including Ubuntu and Raspberry Pi OS.

Use `DXCTVRecorder-Windows-x64-Setup.exe` on Windows, `DXCTVRecorder-macOS-Installer.zip` on macOS, or `DXCTVRecorder-Ubuntu-Linux-Installer.zip` on Ubuntu/Linux. `DXCTVRecorder-Installers.zip` contains all platform options.

## Requirements

- Python 3.10 or later and FFmpeg installed on the target computer. The install scripts detect both and configure the FFmpeg path, but do not download or install those prerequisites.
- Windows 10 or 11, macOS with `launchd`, or Linux with a systemd user service. Linux ARM64 systems such as Raspberry Pi OS are supported when Python and FFmpeg are available for that system.
- Each camera and this computer connected to the same home network

## Configure the cameras

1. In the Tapo app, open each camera's settings and enable **Camera Account** under **Advanced Settings** (menu wording may vary by app version). Create a dedicated username and password for RTSP/ONVIF access. This is separate from your TP-Link ID.
2. Find each camera's local IP address in its Device Info or your router's connected-device list. Reserve those addresses in the router if possible so they do not change.
3. Install Python 3 and FFmpeg for your operating system. Confirm `python3 --version` and `ffmpeg -version` work (on Windows, use `python --version`).
4. Run the installer for your operating system below, then open the local web client at `http://127.0.0.1:8765` on the recording computer.
5. Use **Camera and storage setup** in the browser page. Select **Add camera** for each camera you want to connect, then enter its name, IP address, and Camera Account username/password. Use **Browse…** to navigate this computer's drives and select a recording folder, then set the segment length and web client port. Remove any camera you no longer want. Click **Save and apply**; configured camera recording restarts using the saved settings. If you change the port, the browser moves to the new local address automatically. You do not need to edit Python code or camera URLs by hand.

Recordings are saved under `recordings/<camera>/<date>/`. In the **Recordings** tab, select a camera and then choose one of its available dates to see that day's completed video files. Keep the PC powered on and prevent it from sleeping for 24/7 recording.

The **Live View** tab opens with the first configured camera. Choose **1**, **2**, **3**, or **4** panels and select cameras from each panel's dropdown. The layouts use a full-height view for one or two cameras, a large panel beside two stacked panels for three, and a 2×2 grid for four. Use **Full screen** to enlarge the display. Preview uses each camera's standard-quality `/stream2` feed while the recorder continues saving the configured stream. Camera stream availability can depend on other Tapo app views and storage features; see the [TP-Link RTSP guide](https://www.tp-link.com/us/support/faq/2680/).

In **Settings → Storage and recording**, choose to keep recordings for **30, 60, or 90 days**. Cleanup removes MP4 files older than the selected period and runs at service start, once daily, and after changing the retention setting.

## Install on Windows

1. Extract the complete folder to a location you can access.
2. Double-click `DXCTVRecorder-Windows-x64-Setup.exe` and approve the Windows administrator prompt. Setup installs the **DXCTV Recorder** Windows service, preserves existing settings and recordings, and starts the service immediately and automatically with Windows, including before you sign in.
3. Open the web client separately at `http://127.0.0.1:8765` or use the **DXCTV Recorder** desktop shortcut. The browser is only the client; the recorder service keeps running without it.
4. The service runs as **LocalSystem**. Use the browser-based folder selector to choose storage; the standard Windows folder dialog cannot appear from a background service. Use local drive paths rather than mapped network drive letters.
5. To remove the service, uninstall **DXCTV Recorder** from Windows Installed apps. Settings and recordings are retained.

Windows Installed apps lists the publisher as **DXCTV-Recorder**. Python and FFmpeg must remain installed on the PC. Setup downloads the WinSW service wrapper from its official GitHub release and verifies its SHA-256 checksum before registering the service.

## Install on macOS

1. Extract the complete `DXCTVRecorder` folder from the ZIP package.
2. Double-click `install-recorder-macos.command`. It installs a per-user `launchd` service, preserves existing settings and recordings, starts the service, and opens the browser page.
3. The service starts when this macOS user signs in. Run `~/Library/Application Support/DXCTVRecorder/uninstall-recorder.command` to remove the service; settings and recordings are kept.

Python 3 and FFmpeg must already be installed and available in the Terminal `PATH`. macOS may ask you to confirm running a downloaded command file.

## Install on Linux and Raspberry Pi OS

1. Extract the complete `DXCTVRecorder` folder from the ZIP package.
2. On Ubuntu or another Linux desktop, open a terminal in the extracted folder and run `./install-recorder-linux.sh`. If the executable bit was not preserved, run `bash install-recorder-linux.sh` instead.
3. The installer creates and starts a systemd user service, preserves existing settings and recordings, and opens the browser page when a desktop browser is available. It may ask for your password to enable the service at boot before sign-in; if that step is skipped, the service starts when you sign in.
4. Run `bash "${XDG_DATA_HOME:-$HOME/.local/share}/dxctv-recorder/uninstall-recorder.sh"` to remove the service while keeping settings and recordings.

Python 3, FFmpeg, and a systemd user session must be available. The installer does not install OS packages automatically. On headless Raspberry Pi systems, open `http://127.0.0.1:8765` from a browser on the Pi; this first version listens on the local computer only.

## Standalone executables (no Python needed)

`dist/executables/` holds single-file builds of the recorder that run without Python: `DXCTVRecorder-Windows-x64.exe`, `DXCTVRecorder-Linux-x64`, and the macOS builds. FFmpeg must still be installed and on `PATH` (or set `ffmpeg_path` in `config.json`).

Put the executable in its own folder and run it. On first run it creates `config.json` and `recordings/` beside itself; set the `DXCTV_HOME` environment variable to use a different data folder. Then open `http://127.0.0.1:8765`. The service installers above still run `recorder.py` with Python.

To rebuild for the current OS, run `python -m pip install pyinstaller` and then `python build-executables.py`. PyInstaller cannot cross-compile, so macOS builds must be made on a Mac or by the GitHub Actions workflow in `.github/workflows/build-executables.yml`, which builds Windows, Linux x64/ARM64, and macOS Intel/Apple silicon. The Linux x64 build here targets glibc 2.34+ (Ubuntu 22.04+, Debian 12, Raspberry Pi OS Bookworm).

## Privacy and limits

- This first version listens only on `127.0.0.1`, so the browser page is accessible from the recording PC only. It does not expose the cameras or app to the internet.
- Keep camera passwords private. `config.json` contains them in plain text; restrict access to this folder.
- OneDrive can sync the `recordings` folder, but continuous video may use substantial storage and upload bandwidth. OneDrive is not configured automatically.
- The app does not yet provide user accounts, remote access, motion detection, or NAS storage. NAS support can be implemented by changing the recordings folder to a mounted NAS path.
- Tapo Care, camera microSD/Hub storage, and RTSP/NVR recording have camera stream-resource limitations. Check TP-Link's current guidance before enabling all storage modes together.

## Troubleshooting

- **FFmpeg not found or access denied:** install a working FFmpeg build and make sure `ffmpeg -version` succeeds for the same Windows account running the recorder.
- **Connecting/reconnecting:** check the camera IP, Camera Account credentials, same-network connection, and RTSP path. Try the lower bandwidth `/stream2` path if upload or Wi-Fi capacity is limited.
- **No video in browser:** allow a few seconds for a segment to finish and appear; the app lists completed MP4 files.

TP-Link's RTSP/ONVIF instructions: https://www.tp-link.com/us/support/faq/2680/
