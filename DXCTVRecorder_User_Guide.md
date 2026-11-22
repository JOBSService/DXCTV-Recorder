# DXCTV Recorder — User Guide

This guide covers installation, first-time setup, recording, Live View, playback, service operation, and common fixes. DXCTV Recorder saves video from configured Tapo RTSP camera streams to folders on the computer that runs the recorder.

## 1. What you need

- A Windows 10/11 PC, a macOS computer with `launchd`, or Linux with a systemd user service. Raspberry Pi OS is supported when compatible Python and FFmpeg packages are available (64-bit ARM is the recommended Pi setup).
- Python 3.10 or later.
- FFmpeg installed and available as `ffmpeg` in the command line.
- The recorder computer and each camera connected to the same home network.
- A Tapo **Camera Account** for each camera. This is a local camera login for RTSP/ONVIF and is different from your TP-Link ID and Tapo cloud account.
- Enough disk space for continuous video. The required space depends on camera quality, scene activity, and the number of cameras.

Install Python and FFmpeg before running the installer. The installers check for these programs but do not install them. Verify with `python --version` and `ffmpeg -version` on Windows, or `python3 --version` and `ffmpeg -version` on macOS/Linux.

## 2. Prepare each Tapo camera

Do this in the Tapo mobile app for each camera you plan to record:

1. Open the camera and its **Settings**.
2. Find **Advanced Settings → Camera Account** (the menu wording can vary by Tapo app or firmware version).
3. Enable Camera Account and create a username and password. Use these credentials in DXCTV Recorder; do not enter your TP-Link ID or Tapo account password here.
4. Find the camera's local IP address in its device/network information or in your router's connected-device list. A DHCP reservation in your router helps keep the address from changing.
5. Keep the camera connected to the same local network as the recorder computer.

Tapo model, firmware, and stream settings can affect RTSP availability. C500, C510W, and C200 models can be added individually when their firmware exposes the RTSP/Camera Account feature. A successful ping only confirms basic network reachability; it does not prove that RTSP is enabled or that its credentials are accepted.

## 3. Install DXCTV Recorder

Choose the matching installer package: `DXCTVRecorder-Windows-x64-Setup.exe` for Windows, `DXCTVRecorder-macOS-Installer.zip` for macOS, or `DXCTVRecorder-Ubuntu-Linux-Installer.zip` for Ubuntu and other compatible Linux systems. The combined `DXCTVRecorder-Installers.zip` contains all of them. Extract the macOS/Linux ZIP on the target computer and keep its files together. Packages do not include your camera configuration, passwords, or recordings.

### Windows

1. Confirm Python and FFmpeg are installed and `ffmpeg -version` works.
2. Double-click **`DXCTVRecorder-Windows-x64-Setup.exe`** and approve the Windows User Account Control (UAC) prompt. The self-extracting setup registers **DXCTV Recorder** as a Windows service, starts it, and configures it to start with Windows. The executable is unsigned, so Windows may identify its publisher as unknown in security prompts.
3. Open the browser client using the desktop shortcut, or visit `http://127.0.0.1:8765` on the recorder PC.
4. To confirm the service state, open Start, search for **Services**, open it, and find **DXCTV Recorder**. The browser is the control and viewing page; it is not the recording service.

The service runs in the background, so no command prompt needs to remain open. The PC must remain powered on and awake. The standard folder dialog is not available to a background Windows service; choose the folder in the app's browser-based folder selector. Use a local drive path, because Windows services may not see drive letters mapped in your signed-in desktop session.

### macOS

1. Confirm `python3 --version` is 3.10 or later and `ffmpeg -version` works. If you use Homebrew, FFmpeg can be installed with `brew install ffmpeg`.
2. Double-click the executable **`install-recorder-macos.command`**. If macOS blocks the downloaded command, use Finder's Control-click → **Open**, then confirm.
3. The installer creates a per-user `launchd` service, starts it, and opens the browser page. The service starts when that user signs in.
4. Open `http://127.0.0.1:8765` if the browser did not open automatically.

### Linux and Raspberry Pi OS

1. Install Python 3.10+ and FFmpeg using your distribution's package manager. Confirm `python3 --version` and `ffmpeg -version` work.
2. Extract the folder, open a terminal in it, and run `./install-recorder-linux.sh`. This executable installer works on Ubuntu and other compatible systemd Linux distributions. If executable permission is missing, run `bash install-recorder-linux.sh`.
3. The installer creates and starts a systemd **user** service. It may request your password to enable `loginctl linger`, which lets the service start before you sign in. If linger is unavailable or declined, the service starts after you sign in.
4. Open `http://127.0.0.1:8765` in a browser on the recorder computer. On a headless Raspberry Pi, use a browser on the Pi or a local display session.

### Port and local access

The default web port is **8765**. The app listens on `127.0.0.1`, so the page is available on the recorder computer only. A different computer or a cloud server cannot open this page in this version. Do not create a router port-forward for it.

## 4. Add cameras and choose where recordings go

1. Open the web client on the recorder computer. The **Live View** page is the initial main view; the camera list is at the left.
2. Open **Camera and storage setup**. If you just clicked **New camera/Add camera**, the app should select the camera's **Settings** tab.
3. Choose **Add camera**. Enter a unique camera name (for example, `Living Room`), its local IP address, the Camera Account username, and Camera Account password.
4. Select the stream quality/path offered by the app: `/stream1` is the high-quality stream and `/stream2` is the standard/lower-bandwidth stream. Do not type the full RTSP URL into the username or password fields.
5. Save the camera. Add only cameras you actually want this recorder to use; the three example Tapo models are not preconfigured.
6. In storage settings, use **Browse** to select or create a recording folder. If folder browsing is unavailable, enter a folder path that exists and is writable by the recorder service.
7. Choose the segment duration (1–60 minutes; default 10 minutes), recording retention (30, 60, or 90 days), and web port (1024–65535; default 8765). Save and apply the settings.

When editing an existing camera, leaving its password field blank keeps the saved password. If you enter a new password, it replaces the saved one. Camera passwords are stored locally in the app configuration in plain text, so restrict access to the recorder computer and its application data folder.

### Recording folder structure

The app groups files by camera and date:

```text
<recording folder>/<camera name>/<YYYY-MM-DD>/<video segments>.mp4
```

For the default folder this is under the app's `recordings` directory. Changing the storage folder applies to new recording output; existing files remain where they were saved. Keep sufficient free space available.

### Retention cleanup

Choose 30, 60, or 90 days in **Settings → Storage and recording**. The app removes completed MP4 recordings older than the selected period during startup, daily cleanup, and after a retention change. Active recordings are skipped. Retention deletion is permanent; copy important video to another location before it expires.

## 5. Understand camera status

The left camera list shows each configured camera and its current status. A green status indicates the camera is recording. Connecting or reconnecting means the recorder is trying to open its RTSP stream. An unreachable/error status means no recording can be confirmed; the app may retry automatically.

If status is not green, check the camera IP and Camera Account credentials first. Camera names are labels in the app and output folder; they are not camera login names.

## 6. Use Live View

1. Select **Live View** in the main panel.
2. Choose **1**, **2**, **3**, or **4** at the top to set the number of display panels. One camera is shown first by default when one is configured.
3. Use the dropdown on each panel to choose which active camera to display. A camera already selected in another panel is unavailable there, so the same camera is not shown twice. You can show up to four panels at once.
4. The layout resizes to use the available viewing area: one panel fills it; two panels share it; three panels use one large panel and two smaller stacked panels; four panels use a 2×2 grid.
5. Select **Full screen** on a camera panel to enlarge it. Use the on-screen return control or press **Esc** to return to the previous layout. If you reduce the panel count, feeds in panels that are removed from view are stopped and their camera selections are cleared.

Live preview uses the camera's standard-quality `/stream2` feed. Recording continues with the configured recording stream. Camera firmware and simultaneous Tapo app, microSD, hub, or cloud viewing/recording can limit available video streams. If a preview does not start, close other live camera views and wait briefly before retrying.

## 7. Find and play recordings

1. Select the camera in the left panel.
2. Open the **Recordings** tab.
3. Select a date to see the completed MP4 files for that camera and day. The files are stored in the matching camera/date folders described above.
4. Review the file's size and length, then select **Play/Open**. Playback opens in a separate browser window or tab.

The list contains completed segments. A segment appears after FFmpeg closes/finalizes it, so a new recording may take up to the configured segment duration to appear. A recording that is still active may not yet be playable. If a completed file does not play, confirm it has nonzero size and duration and try a current browser or a desktop media player such as VLC.

## 8. Service operation and computer sleep

The recorder service and browser client are separate. Closing the browser does not stop the recorder. If the computer sleeps, shuts down, loses power, or disconnects from the camera network, recording stops until the computer and service are available again.

- **Windows:** use the Services app to check or start **DXCTV Recorder**. It is configured to start with Windows, including before a desktop sign-in. The installer also creates a browser shortcut.
- **macOS:** the per-user `launchd` service starts at that user's sign-in. Keep that account active and prevent sleep for continuous recording.
- **Linux:** use `systemctl --user status dxctv-recorder` to inspect the service. If linger is enabled, it can start before sign-in; otherwise sign in to start the user service.

Keep Python and FFmpeg installed. When changing the service port or storage folder in Settings, save/apply and use the new page address if the port changed. The port is for the local web client; camera RTSP uses camera port 554.

## 9. OneDrive and NAS storage

You can select a local folder inside your OneDrive sync folder to let OneDrive upload completed recordings. DXCTV Recorder does not configure OneDrive itself. Continuous camera video can consume substantial disk space, upload bandwidth, and cloud quota. Wait for files to finish recording before relying on their cloud copies.

A NAS folder may be used when it is mounted as a local path and the recorder service account has permission to read/write it. On Windows, mapped drive letters are often unavailable to services; use a suitable local path or service-visible mount. NAS support and remote browsing are not built into this version.

## 10. Privacy and security

- This version's web server listens only on `127.0.0.1`; it is not an internet streaming service and has no remote user accounts.
- Do not expose the recorder port with router port forwarding. The configurable port does not change the local-only network binding.
- Camera Account credentials are stored in the local configuration file as plain text. Do not email or share that file or an unredacted backup.
- The installer ZIP excludes `config.json`, recordings, and credentials. Keep the extracted installer folder or ZIP if you may need to reinstall; you can delete your downloaded copy yourself after installation. The Windows setup executable is not code-signed, so Windows may show an unknown publisher warning.

## 11. Troubleshooting

| Symptom | What to check |
|---|---|
| Installer says Python is missing or too old | Install Python 3.10 or later, open a fresh terminal, confirm `python --version` (Windows) or `python3 --version` (macOS/Linux), then rerun setup. |
| FFmpeg not found | Install FFmpeg and make sure `ffmpeg -version` succeeds. The service installer uses the FFmpeg executable found during installation. |
| Windows says FFmpeg access denied | Confirm Windows allows the selected FFmpeg executable to run and that its folder is readable/executable by the service. Reinstall a trusted FFmpeg build to a normal application folder if needed. |
| Camera Account error / RTSP 401 Unauthorized | Re-enter the camera's Camera Account username/password exactly. This is not the TP-Link ID. Confirm Camera Account is enabled. To isolate the camera, test the stream in VLC using `rtsp://USERNAME:PASSWORD@CAMERA_IP:554/stream1`; do not share a URL containing a real password. |
| Ping succeeds but the app says unreachable | Ping does not test RTSP. Check same-network connectivity, current camera IP, RTSP/Camera Account support, credentials, and whether another Tapo view or storage feature is using the stream. |
| Camera status is green but no file appears yet | Wait for the current segment to finish; the list shows completed segments. Check the selected recording folder, available disk space, and service write permissions. |
| Recording file appears but will not play | Check that the file has nonzero size and a completed duration. Try VLC. An interrupted or still-open segment may not have been finalized. |
| Live View says waiting for video | Check camera status and stream availability, close competing Tapo live views, try another camera/stream, and allow time for the preview to connect. Restarting the camera can release a stuck stream if other checks do not help. |
| Web page does not open | On the recorder computer, visit `http://127.0.0.1:<port>/`. Check the configured port in Settings and confirm the service is running. A different device cannot connect in this local-only version. |
| Port is already in use | Close an old recorder process/service if one is still running, or choose another unused port in Settings. A system message that identifies PID 0 may be a Windows reporting limitation; do not try to close the System Idle Process. |
| Folder picker cannot access a drive | Choose a folder on a local drive and check write permission. Windows background services generally cannot use drive letters mapped only in the desktop sign-in. |
| Service is installed but not recording | Check camera status, verify Python and FFmpeg remain installed, and inspect the app's `logs` folder for startup/stream errors. |
| OneDrive files are not uploading | Confirm the chosen folder is actually inside the OneDrive sync location, OneDrive is signed in/running, and the account has enough quota and upload bandwidth. |

## 12. Upgrade and uninstall

Re-run the platform installer from a newer complete package to update the app. The installer preserves the existing camera configuration and recordings. Keep Python and FFmpeg installed.

- **Windows:** uninstall **DXCTV Recorder** from Windows Installed apps. This removes the service and app program files while retaining configuration and recordings.
- **macOS:** run `~/Library/Application Support/DXCTVRecorder/uninstall-recorder.command`.
- **Linux:** run `bash "${XDG_DATA_HOME:-$HOME/.local/share}/dxctv-recorder/uninstall-recorder.sh"`.

The macOS and Linux uninstallers remove the service and retain configuration and recordings. Back up recordings before removing their folders manually.

## Quick start checklist

- [ ] Install Python 3.10+ and FFmpeg.
- [ ] Enable Camera Account for each Tapo camera and note its local IP.
- [ ] Install DXCTV Recorder for your operating system.
- [ ] Open the local web page and add only the cameras you want.
- [ ] Set a writable recording folder, segment length, retention, and port.
- [ ] Confirm each camera turns green and a completed recording appears.
- [ ] Select a camera/date in Recordings and open a playback file.
- [ ] Keep the computer powered, awake, and connected to the camera network.
