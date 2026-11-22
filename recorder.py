from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit, urlunsplit

def app_root() -> Path:
    """Folder holding config.json, recordings and logs.

    DXCTV_HOME overrides it. A frozen (PyInstaller) build uses the executable's folder,
    because __file__ points into a temporary extraction folder that is deleted on exit.
    """
    override = os.environ.get("DXCTV_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = app_root()
ROOT.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = ROOT / "config.json"
STOP = threading.Event()
RETENTION_WAKE = threading.Event()
LOCK = threading.Lock()
CAMERAS: dict[str, dict] = {}
ACTIVE_OUTPUTS: dict[str, Path] = {}
LIVE_CAMERAS: set[str] = set()
HTTP_SERVER: ThreadingHTTPServer | None = None
HTTP_SERVER_LOCK = threading.Lock()
WORKER_STOPS: list[threading.Event] = []
WORKER_THREADS: list[threading.Thread] = []
RTSP_AUTH_PATTERN = re.compile(r"(?i)(rtsp://)[^/@\s]+@")
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        # First run of a standalone executable: start with no cameras.
        default = {"ffmpeg_path": "ffmpeg", "recordings_folder": "recordings", "segment_minutes": 10,
                   "retention_days": 30, "port": 8765, "cameras": []}
        CONFIG_FILE.write_text(json.dumps(default, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(CONFIG_FILE, 0o600)
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("ffmpeg_path", "ffmpeg")
    cfg.setdefault("segment_minutes", 10)
    cfg.setdefault("retention_days", 30)
    cfg.setdefault("cameras", [])
    return cfg


CONFIG = load_config()
RECORDINGS = (ROOT / CONFIG.get("recordings_folder", "recordings")).resolve()
RECORDINGS.mkdir(parents=True, exist_ok=True)


def camera_worker(camera: dict, worker_stop: threading.Event) -> None:
    name = camera["name"]
    status = {"name": name, "state": "No RTSP URL set", "last_started": None, "detail": ""}
    with LOCK:
        CAMERAS[name] = status
    url = camera.get("rtsp_url", "").strip()
    if not url or "CHANGE_ME" in url:
        return

    folder = RECORDINGS / name
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        with LOCK:
            status.update(state="storage error", detail=f"Cannot create the camera folder: {exc}")
        return
    while not STOP.is_set() and not worker_stop.is_set():
        parsed_url = urlsplit(url)
        camera_host = parsed_url.hostname
        camera_port = parsed_url.port or 554
        try:
            if not camera_host:
                raise OSError("The RTSP address has no camera host.")
            with socket.create_connection((camera_host, camera_port), timeout=3):
                pass
        except OSError:
            with LOCK:
                status.update(state="RTSP unavailable", detail="The camera's RTSP port is not accepting connections. Verify its IP/network and enable Camera Account in Tapo settings.")
            if worker_stop.wait(10) or STOP.is_set():
                break
            continue
        now = datetime.now()
        day_folder = folder / now.strftime("%Y-%m-%d")
        try:
            day_folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            with LOCK:
                status.update(state="storage error", detail=f"Cannot create today's folder: {exc}")
            return
        prefix = now.strftime("%Y%m%d_%H%M%S")
        output = str(day_folder / (prefix + "_%03d.mp4"))
        command = [
            CONFIG["ffmpeg_path"], "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
            "-i", url, "-map", "0:v:0", "-map", "0:a?", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-f", "segment", "-segment_time", str(max(1, int(CONFIG["segment_minutes"])) * 60),
            "-reset_timestamps", "1", output,
        ]
        with LOCK:
            status.update(state="connecting", detail="", last_started=datetime.now().isoformat(timespec="seconds"))
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    text=True, creationflags=flags)
            stderr_lines: deque[str] = deque(maxlen=12)

            def collect_stderr() -> None:
                if proc.stderr is None:
                    return
                for line in proc.stderr:
                    cleaned = RTSP_AUTH_PATTERN.sub(r"\1<credentials-redacted>@", line.strip())
                    if cleaned:
                        stderr_lines.append(cleaned)

            stderr_reader = threading.Thread(target=collect_stderr, daemon=True)
            stderr_reader.start()
            while proc.poll() is None and not STOP.is_set() and not worker_stop.wait(1):
                segments = list(day_folder.glob(prefix + "_*.mp4"))
                active_output = max(segments, key=lambda p: p.stat().st_mtime, default=None)
                with LOCK:
                    if active_output is not None:
                        ACTIVE_OUTPUTS[name] = active_output.resolve()
                    has_output = any(p.is_file() and p.stat().st_size > 0 for p in segments)
                    status.update(state="recording" if has_output else "connecting",
                                  detail="\n".join(stderr_lines)[-500:])
            error = ""
            if proc.poll() is None:
                try:
                    if proc.stdin is not None:
                        proc.stdin.write("q\n")
                        proc.stdin.flush()
                        proc.stdin.close()
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                except (BrokenPipeError, OSError):
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
            stderr_reader.join(timeout=1)
            error = "\n".join(stderr_lines)[-500:]
            with LOCK:
                ACTIVE_OUTPUTS.pop(name, None)
                status.update(state="stopped" if STOP.is_set() or worker_stop.is_set() else "reconnecting", detail=error)
        except FileNotFoundError:
            with LOCK:
                status.update(state="FFmpeg not found", detail="Install FFmpeg and make it available on PATH, or set ffmpeg_path in config.json.")
            return
        except PermissionError:
            with LOCK:
                status.update(state="FFmpeg access denied", detail="Windows blocked launching ffmpeg.exe. Check the FFmpeg installation and executable permissions.")
            return
        except Exception as exc:
            with LOCK:
                status.update(state="error", detail=str(exc))
        if not STOP.is_set() and not worker_stop.wait(5):
            continue


def start_workers() -> None:
    global WORKER_STOPS, WORKER_THREADS
    for stop_event in WORKER_STOPS:
        stop_event.set()
    for thread in WORKER_THREADS:
        thread.join(timeout=3)
    WORKER_STOPS, WORKER_THREADS = [], []
    with LOCK:
        CAMERAS.clear()
    for camera in CONFIG.get("cameras", []):
        stop_event = threading.Event()
        thread = threading.Thread(target=camera_worker, args=(camera, stop_event), daemon=True)
        WORKER_STOPS.append(stop_event)
        WORKER_THREADS.append(thread)
        thread.start()


def config_for_ui() -> dict:
    cameras = []
    for camera in CONFIG.get("cameras", []):
        parsed = urlsplit(camera.get("rtsp_url", ""))
        try:
            port = parsed.port or 554
        except ValueError:
            port = 554
        cameras.append({
            "name": camera.get("name", "Tapo Camera"),
            "ip": parsed.hostname or "",
            "username": unquote(parsed.username or ""),
            "has_password": parsed.password is not None,
            "path": parsed.path or "/stream1",
            "port": port,
        })
    return {"cameras": cameras, "recordings_folder": CONFIG.get("recordings_folder", "recordings"),
            "segment_minutes": CONFIG.get("segment_minutes", 10),
            "retention_days": CONFIG.get("retention_days", 30),
            "port": CONFIG.get("port", 8765)}


def save_config_from_ui(data: dict) -> None:
    global CONFIG, RECORDINGS
    cameras = []
    previous = {c.get("name"): c for c in CONFIG.get("cameras", [])}
    camera_items = data.get("cameras", [])
    if not isinstance(camera_items, list):
        raise ValueError("Camera settings must be a list.")
    for item in camera_items:
        if not isinstance(item, dict):
            raise ValueError("Invalid camera settings.")
        name = str(item.get("name", "")).strip()
        ip = str(item.get("ip", "")).strip()
        username = str(item.get("username", "")).strip()
        password = str(item.get("password", ""))
        path = str(item.get("path", "/stream1")).strip()
        if not name or not ip:
            raise ValueError("Enter a camera IP address for each camera.")
        if any(ch in ip for ch in "/@?#"):
            raise ValueError(f"The IP address for {name} is invalid.")
        if not path.startswith("/") or any(ch in path for ch in "?#"):
            raise ValueError(f"The RTSP stream path for {name} must start with / and cannot contain ? or #.")
        old = previous.get(name, {}).get("rtsp_url", "")
        if not password and old:
            password = unquote(urlsplit(old).password or "")
        if not username or not password:
            raise ValueError(f"Enter the camera account username and password for {name}.")
        safe_user, safe_password = quote(username, safe=""), quote(password, safe="")
        cameras.append({"name": name, "rtsp_url": f"rtsp://{safe_user}:{safe_password}@{ip}:554{path}"})
    if len({c["name"].casefold() for c in cameras}) != len(cameras):
        raise ValueError("Each camera must have a different name.")
    folder = str(data.get("recordings_folder", "recordings")).strip() or "recordings"
    segment = int(data.get("segment_minutes", 10))
    if not 1 <= segment <= 60:
        raise ValueError("Segment length must be between 1 and 60 minutes.")
    retention = int(data.get("retention_days", CONFIG.get("retention_days", 30)))
    if retention not in (30, 60, 90):
        raise ValueError("Recording retention must be 30, 60, or 90 days.")
    try:
        web_port = int(data.get("port", CONFIG.get("port", 8765)))
    except (TypeError, ValueError):
        raise ValueError("Web client port must be a number between 1024 and 65535.") from None
    if not 1024 <= web_port <= 65535:
        raise ValueError("Web client port must be between 1024 and 65535.")
    CONFIG = {**CONFIG, "cameras": cameras, "recordings_folder": folder,
              "segment_minutes": segment, "retention_days": retention,
              "port": web_port}
    CONFIG_FILE.write_text(json.dumps(CONFIG, indent=2), encoding="utf-8")
    RECORDINGS = (ROOT / folder).resolve()
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    RETENTION_WAKE.set()
    start_workers()


def has_mp4_index(path: Path) -> bool:
    try:
        size = path.stat().st_size
        position = 0
        found_moov = False
        with path.open("rb") as video:
            while position + 8 <= size:
                video.seek(position)
                header = video.read(8)
                box_size = int.from_bytes(header[:4], "big")
                box_type = header[4:8]
                header_size = 8
                if box_size == 1:
                    extended = video.read(8)
                    if len(extended) != 8:
                        return False
                    box_size = int.from_bytes(extended, "big")
                    header_size = 16
                elif box_size == 0:
                    box_size = size - position
                if box_size < header_size or position + box_size > size:
                    return False
                if box_type == b"moov":
                    found_moov = True
                position += box_size
        return found_moov and position == size
    except OSError:
        return False


def mp4_duration_seconds(path: Path) -> float | None:
    try:
        file_size = path.stat().st_size
        with path.open("rb") as video:
            position = 0
            while position + 8 <= file_size:
                video.seek(position)
                header = video.read(8)
                box_size = int.from_bytes(header[:4], "big")
                box_type = header[4:8]
                header_size = 8
                if box_size == 1:
                    extended = video.read(8)
                    if len(extended) != 8:
                        return None
                    box_size = int.from_bytes(extended, "big")
                    header_size = 16
                elif box_size == 0:
                    box_size = file_size - position
                if box_size < header_size or position + box_size > file_size:
                    return None
                if box_type == b"moov":
                    child = position + header_size
                    moov_end = position + box_size
                    while child + 8 <= moov_end:
                        video.seek(child)
                        child_header = video.read(8)
                        child_size = int.from_bytes(child_header[:4], "big")
                        child_type = child_header[4:8]
                        child_header_size = 8
                        if child_size == 1:
                            extended = video.read(8)
                            if len(extended) != 8:
                                return None
                            child_size = int.from_bytes(extended, "big")
                            child_header_size = 16
                        elif child_size == 0:
                            child_size = moov_end - child
                        if child_size < child_header_size or child + child_size > moov_end:
                            return None
                        if child_type == b"mvhd":
                            video.seek(child + child_header_size)
                            data = video.read(32)
                            if len(data) < 20:
                                return None
                            if data[0] == 1:
                                if len(data) < 32:
                                    return None
                                timescale = int.from_bytes(data[20:24], "big")
                                duration = int.from_bytes(data[24:32], "big")
                            else:
                                timescale = int.from_bytes(data[12:16], "big")
                                duration = int.from_bytes(data[16:20], "big")
                            return duration / timescale if timescale and duration < (1 << (64 if data[0] == 1 else 32)) - 1 else None
                        child += child_size
                    return None
                position += box_size
    except (OSError, ValueError):
        return None
    return None


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def list_videos() -> list[dict]:
    entries = []
    with LOCK:
        active_outputs = set(ACTIVE_OUTPUTS.values())
    for p in RECORDINGS.rglob("*.mp4"):
        try:
            stat = p.stat()
            if stat.st_size <= 0 or p.resolve() in active_outputs or not has_mp4_index(p):
                continue
            relative = p.relative_to(RECORDINGS)
            parts = relative.parts
            folder_date = parts[1] if len(parts) > 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1]) else ""
            recording_date = folder_date or datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
            entries.append({"name": p.name, "camera": parts[0], "date": recording_date,
                            "path": relative.as_posix(), "bytes": stat.st_size,
                            "duration": format_duration(mp4_duration_seconds(p)),
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
        except OSError:
            pass
    entries.sort(key=lambda item: item["modified"], reverse=True)
    return entries


def cleanup_expired_recordings() -> int:
    """Delete MP4 playback files older than the configured retention window."""
    days = int(CONFIG.get("retention_days", 30))
    cutoff = time.time() - days * 24 * 60 * 60
    with LOCK:
        active_outputs = set(ACTIVE_OUTPUTS.values())
    deleted = 0
    for video in RECORDINGS.rglob("*.mp4"):
        try:
            resolved = video.resolve()
            if not resolved.is_relative_to(RECORDINGS) or resolved in active_outputs:
                continue
            if video.stat().st_mtime < cutoff:
                video.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted


def retention_worker() -> None:
    while not STOP.is_set():
        cleanup_expired_recordings()
        if RETENTION_WAKE.wait(24 * 60 * 60):
            RETENTION_WAKE.clear()


def choose_windows_folder() -> str | None:
    if os.name != "nt":
        raise OSError("The native Windows folder picker is only available on Windows.")
    import ctypes
    from ctypes import wintypes

    class BrowseInfo(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", ctypes.c_void_p),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    owner = user32.GetForegroundWindow()
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    result = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    if result < 0:
        raise OSError(result, "Could not initialize the Windows folder picker.")
    try:
        display_name = ctypes.create_unicode_buffer(260)
        title = "Select a folder for camera recordings"
        info = BrowseInfo(
            owner, None, ctypes.cast(display_name, wintypes.LPWSTR), title,
            0x0001 | 0x0010 | 0x0040,  # filesystem folders, editable path, new-style dialog
            None, 0, 0,
        )
        shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BrowseInfo)]
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
        shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
        shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return None
        try:
            path = ctypes.create_unicode_buffer(260)
            if not shell32.SHGetPathFromIDListW(pidl, ctypes.cast(path, wintypes.LPWSTR)):
                raise OSError("Windows could not read the selected folder path.")
            return path.value or None
        finally:
            ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
    finally:
        ole32.CoUninitialize()


PAGE = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DXCTV Recorder</title><style>
*{box-sizing:border-box}body{font:15px system-ui,sans-serif;margin:0;background:#f3f5f8;color:#17202a}header{background:#12344a;color:white;padding:12px 20px}header h1{margin:0 0 3px;font-size:21px}main{width:100%;margin:0;padding:12px}.layout{display:grid;grid-template-columns:230px minmax(0,1fr);gap:14px;align-items:start}.sidebar,.panel{background:white;border-radius:10px;padding:12px;box-shadow:0 2px 9px #1223}.sidebar{align-self:start;position:sticky;top:12px}.camera-list{display:grid;gap:7px;margin:12px 0}.camera-item{display:flex;flex-direction:column;align-items:flex-start;text-align:left;width:100%;background:#f3f5f8;color:#17202a;border:1px solid #e1e7eb}.camera-item.active{background:#e5f3fa;border-color:#1477a8}.camera-item small{color:#607080;margin-top:3px}.add-button{width:100%}.content{min-width:0}.topline,.row,.tabs{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.topline{justify-content:space-between}.tabs{border-bottom:1px solid #dce3e8;margin:10px 0 14px;justify-content:space-between;min-height:44px}.tab-list,.tab-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.tab-list{gap:4px}.tab-meta{justify-content:flex-end}.tab-meta #selected-title{font-size:14px;white-space:nowrap}.selected-detail-inline{font-size:12px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tab-button{background:transparent;color:#526574;border-radius:6px 6px 0 0;border-bottom:3px solid transparent}.tab-button.active{color:#126b9c;border-bottom-color:#1477a8}.card{background:white;border-radius:10px;padding:16px;margin-bottom:14px;border:1px solid #e7ebef}.muted{color:#607080}.pill{padding:4px 10px;border-radius:20px;background:#e8f2ec;color:#17643a}.status-detail{overflow-wrap:anywhere}.settings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.settings-grid label,.shared-grid label{display:flex;flex-direction:column;gap:5px}.settings-grid input,.settings-grid select,.shared-grid input{width:100%;padding:8px;border:1px solid #c9d3db;border-radius:6px}.shared-grid{display:flex;gap:12px;align-items:end;flex-wrap:wrap}.shared-grid input{min-width:220px}.shared-grid .small-input{min-width:100px;width:120px}.shared-grid select{padding:8px;border:1px solid #c9d3db;border-radius:6px;background:white}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:10px;border-bottom:1px solid #e4e8ec}button{padding:8px 14px;border:0;background:#1477a8;color:white;border-radius:6px;cursor:pointer}button:disabled{opacity:.5;cursor:not-allowed}.empty{padding:22px;text-align:center;color:#607080}.live-controls{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin:0 0 10px}.panel-count{display:flex;align-items:center;gap:0}.panel-count-label{font-weight:600;margin-right:8px}.panel-count button{min-width:38px;border-radius:0;padding:7px 10px;border-right:1px solid #ffffff66}.panel-count button:first-of-type{border-radius:6px 0 0 6px}.panel-count button:last-child{border-radius:0 6px 6px 0;border-right:0}.panel-count button.active{background:#0b4f72;box-shadow:inset 0 0 0 2px #fff}.live-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.live-grid.panel-count-1{grid-template-columns:1fr}.live-grid.panel-count-2{height:calc(100vh - 235px);grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:1fr}.live-grid.panel-count-3{height:calc(100vh - 235px);grid-template-columns:minmax(0,1.45fr) minmax(0,1fr);grid-template-rows:repeat(2,minmax(0,1fr))}.live-grid.panel-count-4{height:calc(100vh - 235px);grid-template-rows:repeat(2,minmax(0,1fr))}.live-tile{height:calc((100vh - 255px)/2);min-height:220px;display:flex;flex-direction:column;background:#03080b;border:1px solid #355366;border-radius:8px;overflow:hidden}.live-grid.panel-count-1 .live-tile{height:calc(100vh - 235px);min-height:320px}.live-grid.panel-count-2 .live-tile,.live-grid.panel-count-4 .live-tile{height:100%;min-height:0}.live-grid.panel-count-3 .live-tile{height:100%;min-height:0}.live-grid.panel-count-3 .live-tile:first-child{grid-row:1/3}.live-tile-head{min-height:44px;padding:6px 9px;background:#183546;color:#eef4f7;display:flex;align-items:center;justify-content:space-between;gap:8px}.live-tile-head label{display:flex;align-items:center;gap:8px;font-weight:600}.live-camera-select{max-width:min(44vw,320px);padding:6px;border:1px solid #91a7b5;border-radius:5px;background:white;color:#17202a}.live-feed-holder{position:relative;flex:1;min-height:0;display:flex;align-items:center;justify-content:center}.live-feed{width:100%;height:100%;object-fit:contain}.live-feed-message{position:absolute;color:#d5e2e8;text-align:center;padding:18px}.live-feed-status{position:absolute;bottom:8px;left:8px;color:white;background:#0009;border-radius:4px;padding:4px 7px;font-size:12px}.live-grid:fullscreen{height:100vh;width:100vw;padding:8px;background:#101820}.live-grid:fullscreen .live-tile{height:calc((100vh - 34px)/2);min-height:0}.live-grid:fullscreen.panel-count-1 .live-tile{height:calc(100vh - 16px)}.live-grid:fullscreen.panel-count-2{grid-template-rows:1fr}.live-grid:fullscreen.panel-count-2 .live-tile{height:100%}.live-grid:fullscreen.panel-count-3{grid-template-columns:minmax(0,1.45fr) minmax(0,1fr);grid-template-rows:repeat(2,minmax(0,1fr))}.live-grid:fullscreen.panel-count-3 .live-tile{height:100%}.live-grid:fullscreen.panel-count-3 .live-tile:first-child{grid-row:1/3}.live-grid.panel-count-1:not(:fullscreen) .live-tile:nth-child(n+2),.live-grid.panel-count-2:not(:fullscreen) .live-tile:nth-child(n+3),.live-grid.panel-count-3:not(:fullscreen) .live-tile:nth-child(n+4){display:none}.live-view-panel{min-height:calc(100vh - 125px)}#folder-picker{display:none;position:fixed;inset:0;background:#0008;z-index:10;align-items:center;justify-content:center;padding:16px}.picker-card{width:min(700px,95vw);max-height:80vh;display:flex;flex-direction:column;background:white;border-radius:12px;padding:18px}.picker-list{overflow:auto;margin-top:12px}@media(max-width:760px){main{padding:8px}.layout{grid-template-columns:1fr}.sidebar{position:static}.camera-list{grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}.tabs{align-items:flex-start}.tab-meta{justify-content:flex-start}.live-grid.panel-count-2,.live-grid.panel-count-3,.live-grid.panel-count-4{height:auto;grid-template-columns:1fr;grid-template-rows:auto}.live-grid.panel-count-3 .live-tile:first-child{grid-row:auto}.live-tile,.live-grid.panel-count-1 .live-tile,.live-grid.panel-count-2 .live-tile,.live-grid.panel-count-3 .live-tile,.live-grid.panel-count-4 .live-tile{height:calc(100vh - 370px);min-height:220px}.live-view-panel{min-height:auto}.live-grid:fullscreen .live-tile,.live-grid:fullscreen.panel-count-1 .live-tile{height:calc(100vh - 34px);min-height:0}.live-grid.panel-count-1:not(:fullscreen) .live-tile{height:calc(100vh - 350px)}}
</style></head><body><header><h1>DXCTV Recorder</h1><div>Recordings stay on this computer. Keep it powered on and awake for continuous recording.</div></header><main>
<div class="layout"><aside class="sidebar"><div class="topline"><h2 style="margin:0">Cameras</h2><button id="refresh" type="button">Refresh</button></div><div id="camera-list" class="camera-list"><span class="muted">Loading…</span></div><button id="add-camera" class="add-button" type="button">＋ Add camera</button><p class="muted">Select a camera to view it, edit settings, or review recordings.</p></aside>
<section class="content"><div class="panel"><nav class="tabs"><div class="tab-list"><button class="tab-button active" data-tab="live" type="button">Live View</button><button class="tab-button" data-tab="settings" type="button">Settings</button><button class="tab-button" data-tab="recordings" type="button">Recordings <span id="recording-count"></span></button></div><div class="tab-meta"><strong id="selected-title">Select a camera</strong><span id="selected-status" class="status-badge">Not saved</span><span id="selected-detail" class="muted selected-detail-inline"></span><button id="live-view" type="button" disabled>Full screen</button></div></nav>
<div id="live-panel" class="live-view-panel"><div class="live-controls"><div class="panel-count" role="group" aria-label="Number of live camera panels"><span class="panel-count-label">Panels</span><button type="button" data-panel-count="1" class="active" aria-pressed="true">1</button><button type="button" data-panel-count="2" aria-pressed="false">2</button><button type="button" data-panel-count="3" aria-pressed="false">3</button><button type="button" data-panel-count="4" aria-pressed="false">4</button></div><span class="muted">Select a camera in each panel.</span></div><div id="multi-live-grid" class="live-grid panel-count-1"></div></div>
<div id="settings-panel" hidden><form id="settings"><div id="camera-fields"></div><section class="card"><h3 style="margin-top:0">Storage and recording</h3><div class="shared-grid"><label>Recording folder<input id="folder" placeholder="Choose a folder on this computer"></label><button id="browse-folder" type="button">Browse…</button><label>Segment length (minutes)<input id="segment" class="small-input" type="number" min="1" max="60" value="10"></label><label>Keep recordings<select id="retention" class="small-input"><option value="30">30 days</option><option value="60">60 days</option><option value="90">90 days</option></select></label><label>Web client port<input id="web-port" class="small-input" type="number" min="1024" max="65535" step="1" value="8765"></label></div><p class="muted">Completed recordings older than this are deleted automatically. Cleanup runs daily.</p><p class="muted">Only completed segments appear in playback. A segment becomes playable when it closes.</p><p class="muted">Changing the port moves the web client to the new address after you save.</p></section><div class="row"><button type="submit">Save and apply</button><button id="remove-camera" type="button" style="background:#a83232">Remove this camera</button><span id="save-result" class="muted"></span></div></form></div>
<div id="recordings-panel" hidden><div class="recording-filter" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0"><label for="recording-date" style="font-weight:600">Recording date</label><select id="recording-date" disabled style="padding:8px;border:1px solid #c9d3db;border-radius:6px;background:white"><option>No dates available</option></select><span id="recording-date-info" class="muted">Recordings are organized into camera/date folders.</span></div><p class="muted">Select a completed recording to open it in a separate playback window.</p><div style="overflow:auto"><table><thead><tr><th>Recording</th><th>Modified</th><th>Size</th><th>Length</th><th></th></tr></thead><tbody id="videos"></tbody></table></div></div>
</div><p class="muted">Local-only browser interface at 127.0.0.1. Do not expose this port directly to the internet.</p></section></div></main>
<div id="folder-picker"><section class="picker-card"><div class="topline"><h2 style="margin:0">Choose recording folder</h2><button id="close-folder" type="button">Close</button></div><p id="folder-current" style="overflow-wrap:anywhere" class="muted"></p><div class="row"><button id="folder-up" type="button">Up one level</button><button id="folder-use" type="button">Use this folder</button></div><div id="folder-list" class="picker-list"></div><p id="folder-error" class="muted"></p></section></div>
<script>
const fmt=n=>{if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';return (n/1048576).toFixed(1)+' MB'};
let cameraFormData=[],cameraStatuses=[],recordings=[],selectedIndex=-1,currentFolderPath='',activeTab='live',selectedRecordingDate='',livePanelCount=1,liveCameraSelections=[];
const byId=id=>document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function currentCamera(){return cameraFormData[selectedIndex]||null}
function stopLiveTile(tile){const feed=tile.querySelector('.live-feed');feed.removeAttribute('src');feed.hidden=true;feed.dataset.camera='';tile.querySelector('.live-feed-message').hidden=false;tile.querySelector('.live-feed-message').textContent='Select a camera for this panel.';tile.querySelector('.live-feed-status').textContent='Not selected'}
function startLiveTile(tile,camera){const feed=tile.querySelector('.live-feed'),message=tile.querySelector('.live-feed-message'),status=tile.querySelector('.live-feed-status');if(feed.dataset.camera===camera.name&&feed.getAttribute('src'))return;feed.removeAttribute('src');feed.dataset.camera=camera.name;message.hidden=false;message.textContent='Connecting to '+camera.name+'…';status.textContent='Connecting';feed.onload=()=>{feed.hidden=false;message.hidden=true;status.textContent='Live'};feed.onerror=()=>{feed.hidden=true;message.hidden=false;message.textContent='No live video. Check the camera connection and RTSP settings.';status.textContent='Not connected'};feed.hidden=false;feed.src='/live.mjpg?camera='+encodeURIComponent(camera.name)}
function renderLivePanels(){const grid=byId('multi-live-grid');if(!grid)return;grid.classList.remove('panel-count-1','panel-count-2','panel-count-3','panel-count-4');grid.classList.add('panel-count-'+livePanelCount);document.querySelectorAll('[data-panel-count]').forEach(button=>{const active=Number(button.dataset.panelCount)===livePanelCount;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active))});const used=new Set(liveCameraSelections.slice(0,livePanelCount).filter(Boolean));[...grid.querySelectorAll('.live-tile')].forEach((tile,index)=>{const select=tile.querySelector('.live-camera-select'),saved=liveCameraSelections[index]||'',camera=cameraFormData.find(item=>item.name===saved);if(saved&&!camera)liveCameraSelections[index]='';select.replaceChildren();const blank=document.createElement('option');blank.value='';blank.textContent='Select camera';select.appendChild(blank);for(const item of cameraFormData){const option=document.createElement('option');option.value=item.name;option.textContent=item.name;option.disabled=index<livePanelCount&&used.has(item.name)&&item.name!==saved;select.appendChild(option)}select.value=camera?saved:'';if(index>=livePanelCount||!camera)stopLiveTile(tile);else startLiveTile(tile,camera)})}
function setTab(tab){activeTab=tab;document.querySelectorAll('.tab-button').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));byId('live-panel').hidden=tab!=='live';byId('settings-panel').hidden=tab!=='settings';byId('recordings-panel').hidden=tab!=='recordings';if(tab==='live'){if(!liveCameraSelections[0]&&currentCamera())liveCameraSelections[0]=currentCamera().name;renderLivePanels()}else byId('multi-live-grid').querySelectorAll('.live-tile').forEach(stopLiveTile);renderRecordings()}
function initializeLivePanels(){const grid=byId('multi-live-grid');for(let index=0;index<4;index++){const tile=document.createElement('section');tile.className='live-tile';tile.innerHTML='<div class="live-tile-head"><label>Camera <select class="live-camera-select" aria-label="Camera for panel '+(index+1)+'"></select></label><span class="live-feed-status">Not selected</span></div><div class="live-feed-holder"><img class="live-feed" alt="Live camera feed" hidden><div class="live-feed-message">Select a camera for this panel.</div></div>';const select=tile.querySelector('.live-camera-select');select.addEventListener('change',()=>{const previous=liveCameraSelections[index]||'',name=select.value;if(name&&liveCameraSelections.some((used,other)=>other!==index&&other<livePanelCount&&used===name)){select.value=previous;return}liveCameraSelections[index]=name;if(name){const selected=cameraFormData.findIndex(item=>item.name===name);if(selected>=0)selectedIndex=selected}renderSidebar();renderCameraForm();renderLivePanels()});grid.appendChild(tile)}document.querySelectorAll('[data-panel-count]').forEach(button=>button.addEventListener('click',()=>{const count=Number(button.dataset.panelCount),used=new Set(liveCameraSelections.filter(Boolean));for(let index=0;index<count;index++){if(!liveCameraSelections[index]){const next=cameraFormData.find(item=>!used.has(item.name));if(next){liveCameraSelections[index]=next.name;used.add(next.name)}}}livePanelCount=count;renderLivePanels()}));renderLivePanels()}
function paintSelectedStatus(s){const badge=byId('selected-status'),label=s?s.state:'Not saved',state=label.toLowerCase(),colors=state==='recording'?['#e3f4e9','#17643a']:['connecting','reconnecting'].includes(state)?['#fff2d7','#805b00']:!s?['#e9edf1','#526574']:['#fde8e7','#9e2722'];badge.textContent=label;badge.style.cssText=`display:inline-block;margin-top:7px;padding:4px 10px;border-radius:20px;font-size:13px;font-weight:600;background:${colors[0]};color:${colors[1]}`;byId('selected-detail').textContent=s&&s.detail?s.detail:''}
function renderSidebar(){const nav=byId('camera-list');if(!cameraFormData.length){nav.innerHTML='<span class="muted">No cameras added yet.</span>';return}nav.innerHTML=cameraFormData.map((c,i)=>{const s=cameraStatuses.find(x=>x.name===c.name),label=s?s.state:'Not saved',state=label.toLowerCase(),colors=state==='recording'?['#e3f4e9','#17643a']:['connecting','reconnecting'].includes(state)?['#fff2d7','#805b00']:!s?['#e9edf1','#526574']:['#fde8e7','#9e2722'];return `<button type="button" class="camera-item ${i===selectedIndex?'active':''}" data-index="${i}"><span style="display:flex;align-items:center;justify-content:space-between;width:100%;gap:8px"><b>${esc(c.name||'Unnamed camera')}</b><span style="font-size:12px;font-weight:600;padding:3px 8px;border-radius:20px;white-space:nowrap;background:${colors[0]};color:${colors[1]}">${esc(label)}</span></span></button>`}).join('');nav.querySelectorAll('[data-index]').forEach(b=>b.addEventListener('click',()=>{selectedIndex=Number(b.dataset.index);if(activeTab==='live')liveCameraSelections[0]=currentCamera().name;byId('save-result').textContent='';renderAll()}))}
function renderCameraForm(){const c=currentCamera();byId('selected-title').textContent=c?(c.name||'Unnamed camera'):'No camera selected';const s=c&&cameraStatuses.find(x=>x.name===c.name);paintSelectedStatus(s);byId('remove-camera').disabled=!c;byId('live-view').disabled=!c;const root=byId('camera-fields');if(!c){root.innerHTML='<div class="empty">Add a camera from the left panel to configure it.</div>';return}root.innerHTML=`<section class="card"><h3 style="margin-top:0">Camera connection</h3><div class="settings-grid"><label>Camera name<input data-field="name" value="${esc(c.name)}" placeholder="e.g. Front door"></label><label>Camera IP<input data-field="ip" value="${esc(c.ip)}" placeholder="192.168.1.101"></label><label>Camera Account username<input data-field="username" value="${esc(c.username)}"></label><label>Password<input data-field="password" type="password" autocomplete="new-password" placeholder="${c.has_password?'Saved; blank keeps it':'Enter password'}"></label><label>Stream<select data-field="path"><option value="/stream1" ${c.path==='/stream1'?'selected':''}>High quality</option><option value="/stream2" ${c.path==='/stream2'?'selected':''}>Standard quality</option></select></label></div></section>`;root.querySelectorAll('[data-field]').forEach(el=>{el.addEventListener('input',()=>{c[el.dataset.field]=el.value;if(el.dataset.field==='name'){renderSidebar();byId('selected-title').textContent=el.value}});el.addEventListener('change',()=>{c[el.dataset.field]=el.value})})}
function renderRecordings(){const c=currentCamera(),items=c?recordings.filter(v=>v.camera===c.name):[];byId('recording-count').textContent=items.length?`(${items.length})`:'';const counts=new Map();items.forEach(v=>{const d=v.date||v.modified.slice(0,10);counts.set(d,(counts.get(d)||0)+1)});const dates=[...counts.keys()].sort().reverse(),dateSelect=byId('recording-date');if(!dates.includes(selectedRecordingDate))selectedRecordingDate=dates[0]||'';dateSelect.replaceChildren();for(const date of dates){const option=document.createElement('option');option.value=date;option.textContent=`${date} (${counts.get(date)} videos)`;dateSelect.appendChild(option)}dateSelect.disabled=!dates.length;dateSelect.value=selectedRecordingDate;const selected=items.filter(v=>(v.date||v.modified.slice(0,10))===selectedRecordingDate);byId('recording-date-info').textContent=dates.length?`${selected.length} video${selected.length===1?'':'s'} on ${selectedRecordingDate}`:'Recordings are organized into camera/date folders.';const body=byId('videos');body.replaceChildren();if(!selected.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=5;cell.className='empty';cell.textContent=!c?'Select or add a camera first.':items.length?'No completed recordings for this date.':'No completed recordings for this camera yet.';row.appendChild(cell);body.appendChild(row);return}for(const item of selected){const row=document.createElement('tr');for(const value of [item.name,item.modified.replace('T',' '),fmt(item.bytes),item.duration||'—']){const cell=document.createElement('td');cell.textContent=value;row.appendChild(cell)}const action=document.createElement('td'),button=document.createElement('button');button.type='button';button.textContent='Open playback';button.addEventListener('click',()=>openPlayback(item.path));action.appendChild(button);row.appendChild(action);body.appendChild(row)}}
function renderAll(){renderSidebar();renderCameraForm();setTab(activeTab)}
function addCamera(){cameraFormData.push({name:'Tapo camera '+(cameraFormData.length+1),ip:'',username:'',password:'',has_password:false,path:'/stream1'});selectedIndex=cameraFormData.length-1;renderAll();setTab('settings');byId('camera-fields').querySelector('[data-field="name"]')?.focus()}
function removeCamera(){if(selectedIndex<0)return;cameraFormData.splice(selectedIndex,1);selectedIndex=cameraFormData.length?Math.min(selectedIndex,cameraFormData.length-1):-1;byId('save-result').textContent='Camera removed from setup. Click Save and apply to confirm.';renderAll()}
async function loadConfig(){try{const r=await fetch('/api/config');if(!r.ok)throw new Error('Settings request failed ('+r.status+')');const c=await r.json();byId('folder').value=c.recordings_folder;byId('segment').value=c.segment_minutes;byId('retention').value=c.retention_days;byId('web-port').value=c.port||8765;cameraFormData=c.cameras;selectedIndex=cameraFormData.length?0:-1;liveCameraSelections=cameraFormData.slice(0,4).map(camera=>camera.name);renderAll()}catch(e){byId('save-result').textContent='Could not load settings: '+e.message}}
async function refresh(){try{const [s,v]=await Promise.all([fetch('/api/status').then(r=>r.json()),fetch('/api/videos').then(r=>r.json())]);cameraStatuses=s;recordings=v;renderSidebar();renderRecordings();const c=currentCamera(),state=c&&cameraStatuses.find(x=>x.name===c.name);byId('live-view').disabled=!c;paintSelectedStatus(state)}catch(e){byId('live-view').disabled=!currentCamera();paintSelectedStatus(null);byId('selected-detail').textContent='Recorder status is unavailable.'}}
async function loadFolderList(path){const err=byId('folder-error');err.textContent='Loading folders…';try{const r=await fetch('/api/folders?path='+encodeURIComponent(path||''));const d=await r.json();if(!r.ok)throw new Error(d.error||'Unable to list folders');currentFolderPath=d.path||'';byId('folder-current').textContent=currentFolderPath||'This PC — choose a drive';const up=byId('folder-up');up.disabled=!d.parent;up.onclick=()=>d.parent&&loadFolderList(d.parent);byId('folder-use').onclick=()=>{if(currentFolderPath){byId('folder').value=currentFolderPath;byId('save-result').textContent='Folder selected. Click Save and apply to use it.';byId('folder-picker').style.display='none'}};const list=byId('folder-list');list.replaceChildren();if(!d.items.length)list.textContent='No folders found here.';for(const item of d.items){const row=document.createElement('div'),b=document.createElement('button');row.style.padding='5px 0';b.type='button';b.textContent='Folder: '+item.name;b.onclick=()=>loadFolderList(item.path);row.appendChild(b);list.appendChild(row)}err.textContent=''}catch(e){err.textContent=e.message}}
function browseFolder(){byId('folder-error').textContent='';byId('folder-picker').style.display='flex';loadFolderList('')}
function openPlayback(path){window.open('/playback?path='+encodeURIComponent(path),'tapoPlayback','popup=yes,width=1100,height=780,resizable=yes,scrollbars=yes')}
async function openLiveView(){if(activeTab!=='live'){setTab('live');await new Promise(resolve=>requestAnimationFrame(resolve))}const screen=byId('multi-live-grid');if(screen.requestFullscreen){try{await screen.requestFullscreen()}catch(_){}}}
byId('refresh').addEventListener('click',refresh);byId('add-camera').addEventListener('click',addCamera);byId('remove-camera').addEventListener('click',removeCamera);byId('browse-folder').addEventListener('click',browseFolder);byId('live-view').addEventListener('click',openLiveView);byId('recording-date').addEventListener('change',e=>{selectedRecordingDate=e.target.value;renderRecordings()});byId('close-folder').addEventListener('click',()=>byId('folder-picker').style.display='none');document.querySelectorAll('.tab-button').forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.tab)));
byId('settings').addEventListener('submit',async e=>{e.preventDefault();const out=byId('save-result'),newPort=Number(byId('web-port').value),currentPort=Number(location.port||8765);out.textContent='Saving…';try{const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cameras:cameraFormData,recordings_folder:byId('folder').value,segment_minutes:byId('segment').value,retention_days:byId('retention').value,port:newPort})});const d=await r.json();out.textContent=r.ok?'Settings saved; configured cameras restarted.':(d.error||'Could not save settings.');if(r.ok){cameraFormData=cameraFormData.map(c=>({...c,password:'',has_password:true}));renderAll();refresh();if(newPort!==currentPort){out.textContent=`Port changed. Opening http://127.0.0.1:${newPort}/…`;setTimeout(()=>location.assign(`http://127.0.0.1:${newPort}/`),1200)}}}catch(err){out.textContent='Could not save settings: '+err}});
initializeLivePanels();loadConfig();refresh();setInterval(refresh,10000);
</script></body></html>'''

PLAYBACK_PAGE = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Camera playback</title><style>body{font:15px system-ui,sans-serif;margin:0;background:#101820;color:#eef4f7}header{padding:14px 20px;background:#12344a;overflow-wrap:anywhere}main{padding:16px}video{display:block;width:100%;height:calc(100vh - 100px);background:#05080a}p{color:#c5d4de}</style></head><body><header><b id="filename">Camera recording</b><p>Use the playback controls to pause, seek, or adjust volume.</p></header><main><video id="player" controls autoplay></video></main><script>const path=new URLSearchParams(location.search).get('path')||'';document.getElementById('filename').textContent=path.split('/').pop()||'Camera recording';document.getElementById('player').src='/video?path='+encodeURIComponent(path);</script></body></html>'''

LIVE_PAGE = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DXCTVRecorder live view</title><style>body{font:15px system-ui,sans-serif;margin:0;background:#101820;color:#eef4f7}header{padding:14px 20px;background:#12344a}main{padding:16px;text-align:center}img{display:block;width:100%;height:calc(100vh - 120px);object-fit:contain;background:#05080a}p{color:#c5d4de}</style></head><body><header><b id="camera-name">Live view</b><p>Live preview uses the camera's standard quality stream.</p></header><main><img id="feed" alt="Waiting for live video"><p id="message">Connecting to camera…</p></main><script>const q=new URLSearchParams(location.search),name=q.get('camera')||'Camera';document.title='Live view - '+name;document.getElementById('camera-name').textContent=name;const feed=document.getElementById('feed'),message=document.getElementById('message');feed.onload=()=>message.hidden=true;feed.onerror=()=>{message.hidden=false;message.textContent='Live view ended. Close this window and try again.'};feed.src='/live.mjpg?camera='+encodeURIComponent(name);setTimeout(()=>{if(!feed.naturalWidth){message.textContent='No video yet. Check that the camera is reachable and try again.'}},12000);</script></body></html>'''

MULTI_LIVE_PAGE = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DXCTV Recorder — Live view</title><style>
*{box-sizing:border-box}body{font:15px system-ui,sans-serif;margin:0;background:#101820;color:#eef4f7}header{min-height:48px;padding:6px 12px;background:#12344a;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}header h1{font-size:17px;margin:0}.header-tools{display:flex;align-items:center;gap:12px}.hint{font-size:12px;color:#c5d4de}.panel-count{display:flex;align-items:center;gap:4px}.panel-count span{font-size:13px;color:#d6e2e9;margin-right:3px}.panel-count button{min-width:30px;padding:5px 8px;border:1px solid #7b9caf;border-radius:4px;background:#183546;color:#eef4f7;cursor:pointer}.panel-count button[aria-pressed="true"]{background:#1683b5;border-color:#1683b5;font-weight:700}main{padding:10px}.live-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.live-grid.panel-count-1{grid-template-columns:1fr}.live-grid.panel-count-1 .live-tile{height:calc(100vh - 78px)}.live-grid.panel-count-2 .live-tile,.live-grid.panel-count-3 .live-tile,.live-grid.panel-count-4 .live-tile{height:calc((100vh - 100px)/2)}.live-grid:not(.expanded) .live-tile:nth-child(n+2){display:flex}.live-grid.panel-count-1:not(.expanded) .live-tile:nth-child(n+2),.live-grid.panel-count-2:not(.expanded) .live-tile:nth-child(n+3),.live-grid.panel-count-3:not(.expanded) .live-tile:nth-child(n+4){display:none}.live-tile{min-width:0;min-height:250px;display:flex;flex-direction:column;background:#071017;border:1px solid #355366;border-radius:8px;overflow:hidden}.tile-bar{min-height:44px;padding:6px 8px;background:#183546;display:flex;gap:8px;align-items:center;justify-content:space-between}.tile-bar label{display:flex;gap:8px;align-items:center;font-weight:600}.camera-select{max-width:min(42vw,300px);padding:6px;border-radius:5px;border:1px solid #8aa1af;background:white;color:#15212b}.tile-bar button{padding:6px 10px;border:0;border-radius:5px;background:#1683b5;color:white;cursor:pointer;white-space:nowrap}.feed-holder{position:relative;flex:1;min-height:0;display:flex;align-items:center;justify-content:center;background:#03080b}.feed{width:100%;height:100%;object-fit:contain}.placeholder{position:absolute;padding:16px;text-align:center;color:#a9bdc9}.feed-status{position:absolute;bottom:8px;left:10px;padding:4px 8px;border-radius:4px;background:#0009;color:white;font-size:12px}.live-grid.expanded{grid-template-columns:1fr}.live-grid.expanded .live-tile:not(.expanded){display:none}.live-tile.expanded{height:calc(100vh - 90px);min-height:300px;grid-column:1/-1}.live-tile:fullscreen{height:100vh;width:100vw;min-height:0;border:0;border-radius:0}.live-tile:fullscreen .feed-holder{height:calc(100vh - 48px)}@media(max-width:760px){main{padding:7px}.live-grid{grid-template-columns:1fr}.live-grid.panel-count-1 .live-tile{height:calc(100vh - 90px)}.live-grid.panel-count-2 .live-tile,.live-grid.panel-count-3 .live-tile,.live-grid.panel-count-4 .live-tile{height:calc((100vh - 100px)/2);min-height:190px}.camera-select{max-width:48vw}.live-grid.expanded .live-tile.expanded{height:calc(100vh - 82px)}}
</style></head><body><header><h1>DXCTV Recorder — Live View</h1><div class="header-tools"><div class="panel-count" role="group" aria-label="Number of live view panels"><span>Panels</span><button type="button" data-count="1" aria-pressed="true">1</button><button type="button" data-count="2" aria-pressed="false">2</button><button type="button" data-count="3" aria-pressed="false">3</button><button type="button" data-count="4" aria-pressed="false">4</button></div><div class="hint">Standard stream</div></div></header><main><div id="live-grid" class="live-grid panel-count-1"></div></main><script>
const grid=document.getElementById('live-grid'),initialCamera=new URLSearchParams(location.search).get('camera')||'';let cameras=[],panelCount=1;
function setPanelCount(count){panelCount=Math.max(1,Math.min(4,Number(count)||1));const tiles=[...grid.querySelectorAll('.live-tile')];tiles.forEach((tile,index)=>{const hidden=index>=panelCount;tile.hidden=hidden;tile.style.display=hidden?'none':'';if(hidden){const select=tile.querySelector('.camera-select');if(select.value){stopFeed(tile);select.value='';tile.querySelector('.feed-status').textContent='Not selected';tile.querySelector('.placeholder').textContent='Select a camera for this panel'}}});grid.classList.remove('panel-count-1','panel-count-2','panel-count-3','panel-count-4');grid.classList.add('panel-count-'+panelCount);document.querySelectorAll('.panel-count button').forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.count)===panelCount)));restoreGrid();refreshChoices()}
document.querySelectorAll('.panel-count button').forEach(button=>button.addEventListener('click',()=>setPanelCount(Number(button.dataset.count))));
function restoreGrid(){grid.classList.remove('expanded');grid.querySelectorAll('.live-tile').forEach(tile=>{tile.classList.remove('expanded');tile.querySelector('.size-button').textContent='Expand'})}
document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement)restoreGrid()});
function refreshChoices(){const selects=[...grid.querySelectorAll('.live-tile:not([hidden]) .camera-select')];for(const select of selects){for(const option of select.options){if(option.value)option.disabled=selects.some(other=>other!==select&&other.value===option.value)}}}
function stopFeed(tile){const feed=tile.querySelector('.feed');feed.removeAttribute('src');feed.hidden=true;tile.querySelector('.placeholder').hidden=false}
function startFeed(tile,name){const feed=tile.querySelector('.feed'),placeholder=tile.querySelector('.placeholder'),status=tile.querySelector('.feed-status');feed.onload=()=>{feed.hidden=false;placeholder.hidden=true;status.textContent='Live'};feed.onerror=()=>{feed.hidden=true;placeholder.hidden=false;placeholder.textContent='No live frames. Check the camera connection or stream limits.';status.textContent='Not connected'};placeholder.textContent='Connecting to '+name+'…';placeholder.hidden=false;status.textContent='Connecting';feed.hidden=false;feed.src='/live.mjpg?camera='+encodeURIComponent(name)}
async function setExpanded(tile){if(tile.classList.contains('expanded')){restoreGrid();if(document.fullscreenElement)await document.exitFullscreen();return}restoreGrid();grid.classList.add('expanded');tile.classList.add('expanded');tile.querySelector('.size-button').textContent='Back to panels';if(tile.requestFullscreen){try{await tile.requestFullscreen()}catch(_){}}}
async function initialize(){try{const response=await fetch('/api/config');if(!response.ok)throw new Error('Could not load configured cameras.');const config=await response.json();cameras=config.cameras||[];for(let i=0;i<4;i++){const tile=document.createElement('section');tile.className='live-tile';tile.innerHTML='<div class="tile-bar"><label>Camera <select class="camera-select"><option value="">Select camera</option></select></label><button class="size-button" type="button">Expand</button></div><div class="feed-holder"><img class="feed" alt="Live camera feed" hidden><div class="placeholder">Select a camera for this panel</div><span class="feed-status">Not selected</span></div>';const select=tile.querySelector('.camera-select');for(const camera of cameras){const option=document.createElement('option');option.value=camera.name;option.textContent=camera.name;select.appendChild(option)}select.addEventListener('change',()=>{const name=select.value;if(name&&[...grid.querySelectorAll('.live-tile:not([hidden]) .camera-select')].some(other=>other!==select&&other.value===name)){select.value='';stopFeed(tile);tile.querySelector('.feed-status').textContent='Not selected';tile.querySelector('.placeholder').textContent='That camera is already displayed in another panel.';refreshChoices();return}stopFeed(tile);refreshChoices();if(name)startFeed(tile,name)});tile.querySelector('.size-button').addEventListener('click',()=>setExpanded(tile));grid.appendChild(tile)}setPanelCount(1);const first=grid.querySelector('.camera-select'),firstCamera=initialCamera||cameras[0]?.name;if(firstCamera&&cameras.some(camera=>camera.name===firstCamera)){first.value=firstCamera;refreshChoices();startFeed(first.closest('.live-tile'),firstCamera)}if(!cameras.length)grid.querySelectorAll('.placeholder').forEach(p=>p.textContent='No cameras are configured. Add a camera in DXCTV Recorder settings.')}catch(error){grid.innerHTML='<div class="placeholder">'+error.message+'</div>'}}
initialize();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_bytes(self, data: bytes, content_type: str, status: int = 200, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def stream_live(self, camera_name: str) -> None:
        with LOCK:
            camera = next((c for c in CONFIG.get("cameras", []) if c.get("name") == camera_name), None)
            if not camera:
                self.send_error(404, "Camera not found")
                return
            if camera_name in LIVE_CAMERAS:
                self.send_error(409, "Live view is already open for this camera")
                return
            if len(LIVE_CAMERAS) >= 4:
                self.send_error(429, "Up to four camera live feeds can be open at once")
                return
            LIVE_CAMERAS.add(camera_name)
        proc = None
        try:
            rtsp_url = camera.get("rtsp_url", "").strip()
            parsed_rtsp = urlsplit(rtsp_url)
            if not rtsp_url or not parsed_rtsp.hostname:
                self.send_error(409, "This camera has no saved RTSP address")
                return
            if parsed_rtsp.path.rstrip("/").lower() == "/stream1":
                rtsp_url = urlunsplit(parsed_rtsp._replace(path="/stream2"))
            command = [
                CONFIG["ffmpeg_path"], "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
                "-i", rtsp_url, "-an", "-vf", "fps=5,scale=640:-2:flags=fast_bilinear",
                "-q:v", "6", "-f", "mpjpeg", "-boundary_tag", "dxctv", "pipe:1",
            ]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, bufsize=0, creationflags=flags)
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=dxctv")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Connection", "close")
            self.end_headers()
            while proc.poll() is None and proc.stdout is not None:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            with LOCK:
                LIVE_CAMERAS.discard(camera_name)

    def request_is_local(self) -> bool:
        """Reject DNS-rebinding requests: the Host header must name this computer."""
        host = urlsplit("//" + self.headers.get("Host", "")).hostname or ""
        if host.casefold() not in LOCAL_HOSTNAMES:
            self.send_error(403, "Open DXCTV Recorder at http://127.0.0.1")
            return False
        return True

    def do_GET(self):
        if not self.request_is_local():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(PAGE.encode(), "text/html; charset=utf-8")
        elif parsed.path == "/playback":
            self.send_bytes(PLAYBACK_PAGE.encode(), "text/html; charset=utf-8")
        elif parsed.path == "/live":
            self.send_bytes(MULTI_LIVE_PAGE.encode(), "text/html; charset=utf-8")
        elif parsed.path == "/live.mjpg":
            name = parse_qs(parsed.query).get("camera", [""])[0]
            self.stream_live(name)
        elif parsed.path == "/api/status":
            with LOCK:
                items = [dict(v) for v in CAMERAS.values()]
            self.send_bytes(json.dumps(items).encode(), "application/json")
        elif parsed.path == "/api/videos":
            self.send_bytes(json.dumps(list_videos()).encode(), "application/json")
        elif parsed.path == "/api/config":
            self.send_bytes(json.dumps(config_for_ui()).encode(), "application/json")
        elif parsed.path == "/api/folders":
            requested = parse_qs(parsed.query).get("path", [""])[0].strip()
            items = []
            parent = None
            current = ""
            if not requested:
                if os.name == "nt":
                    import string
                    import ctypes
                    mask = ctypes.windll.kernel32.GetLogicalDrives()
                    for index, drive in enumerate(string.ascii_uppercase):
                        if mask & (1 << index):
                            drive_path = f"{drive}:\\"
                            items.append({"name": drive_path, "path": drive_path})
                else:
                    items = [{"name": "/", "path": "/"}]
            else:
                try:
                    directory = Path(requested).expanduser().resolve(strict=True)
                    if not directory.is_dir():
                        raise ValueError("Choose a folder, not a file.")
                    current = str(directory)
                    parent_path = directory.parent
                    if parent_path != directory:
                        parent = str(parent_path)
                    for child in sorted(directory.iterdir(), key=lambda entry: entry.name.casefold()):
                        try:
                            if child.is_dir() and not child.is_symlink():
                                items.append({"name": child.name, "path": str(child)})
                        except OSError:
                            continue
                except PermissionError:
                    self.send_bytes(json.dumps({"error": "Windows denied access to this folder. Go up one level and choose a folder you can access, such as Documents or a local recordings folder."}).encode(), "application/json", 400)
                    return
                except (OSError, ValueError) as exc:
                    self.send_bytes(json.dumps({"error": f"Cannot open this folder: {exc}"}).encode(), "application/json", 400)
                    return
            self.send_bytes(json.dumps({"path": current, "parent": parent, "items": items}).encode(), "application/json")
        elif parsed.path == "/video":
            rel = unquote(parse_qs(parsed.query).get("path", [""])[0])
            target = (RECORDINGS / rel).resolve()
            if not target.is_relative_to(RECORDINGS) or not target.is_file() or target.suffix.lower() != ".mp4":
                self.send_error(404)
                return
            with LOCK:
                if target in ACTIVE_OUTPUTS.values():
                    self.send_bytes(b"This recording segment is still being written. Wait for the segment to finish, then refresh the recordings list.", "text/plain; charset=utf-8", 409)
                    return
            size = target.stat().st_size
            if size <= 0:
                self.send_error(404)
                return
            if not has_mp4_index(target):
                self.send_bytes(b"This MP4 is incomplete and cannot be played. Wait for recording to finish its segment.", "text/plain; charset=utf-8", 409)
                return
            range_header = self.headers.get("Range")
            start, end = 0, size - 1
            if range_header and range_header.startswith("bytes="):
                try:
                    a, b = range_header[6:].split("-", 1)
                    start = int(a) if a else max(0, size - int(b))
                    end = min(int(b), size - 1) if b else size - 1
                except ValueError:
                    self.send_error(416)
                    return
                if start > end or start >= size:
                    self.send_error(416)
                    return
            length = end - start + 1
            self.send_response(206 if range_header else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if range_header:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with target.open("rb") as f:
                f.seek(start)
                remaining = length
                while remaining:
                    block = f.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        else:
            self.send_error(404)

    def reject_post(self, status: int, message: str) -> None:
        # Read the body first; replying with unread data makes some clients see a reset connection.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if 0 < length <= 100_000:
            self.rfile.read(length)
        self.close_connection = True
        self.send_error(status, message)

    def do_POST(self):
        host = urlsplit("//" + self.headers.get("Host", "")).hostname or ""
        if host.casefold() not in LOCAL_HOSTNAMES:
            self.reject_post(403, "Open DXCTV Recorder at http://127.0.0.1")
            return
        # Block cross-site requests from other web pages. A JSON content type cannot be sent
        # cross-origin without a CORS preflight, which this server never approves.
        origin = self.headers.get("Origin")
        if origin is not None and (urlsplit(origin).hostname or "").casefold() not in LOCAL_HOSTNAMES:
            self.reject_post(403, "Cross-site requests are not allowed")
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            self.reject_post(415, "Requests must use Content-Type: application/json")
            return
        path = urlparse(self.path).path
        if path == "/api/pick-folder":
            try:
                selected = choose_windows_folder()
                self.send_bytes(json.dumps({"path": selected}).encode(), "application/json")
            except Exception as exc:
                self.send_bytes(json.dumps({"error": str(exc)}).encode(), "application/json", 500)
            return
        if path != "/api/config":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 100_000:
                raise ValueError("Invalid settings request.")
            data = json.loads(self.rfile.read(length))
            old_port = int(CONFIG.get("port", 8765))
            new_port = int(data.get("port", old_port))
            if not 1024 <= new_port <= 65535:
                raise ValueError("Web client port must be between 1024 and 65535.")
            replacement = None
            if new_port != old_port:
                try:
                    replacement = ThreadingHTTPServer(("127.0.0.1", new_port), Handler)
                    replacement.daemon_threads = True
                except OSError as exc:
                    raise ValueError(f"Port {new_port} is unavailable. Choose a different port.") from exc
            save_config_from_ui(data)
            if replacement is not None:
                switch_http_server(replacement)
            self.send_bytes(json.dumps({"ok": True, "port": new_port}).encode(), "application/json")
        except (ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
            if "replacement" in locals() and replacement is not None:
                replacement.server_close()
            self.send_bytes(json.dumps({"error": str(exc)}).encode(), "application/json", 400)


def close_http_server_later(server: ThreadingHTTPServer) -> None:
    time.sleep(2)
    server.shutdown()
    server.server_close()


def switch_http_server(server: ThreadingHTTPServer) -> None:
    global HTTP_SERVER
    with HTTP_SERVER_LOCK:
        old_server = HTTP_SERVER
        thread = threading.Thread(target=server.serve_forever, name="web-server", daemon=True)
        thread.start()
        HTTP_SERVER = server
    if old_server is not None:
        threading.Thread(target=close_http_server_later, args=(old_server,), daemon=True).start()


def main():
    global HTTP_SERVER
    server = ThreadingHTTPServer(("127.0.0.1", int(CONFIG.get("port", 8765))), Handler)
    server.daemon_threads = True
    with HTTP_SERVER_LOCK:
        HTTP_SERVER = server
    server_thread = threading.Thread(target=server.serve_forever, name="web-server", daemon=True)
    server_thread.start()
    start_workers()
    threading.Thread(target=retention_worker, name="recording-retention", daemon=True).start()
    print(f"DXCTVRecorder is running at http://127.0.0.1:{CONFIG.get('port', 8765)}", flush=True)
    print(f"Recordings folder: {RECORDINGS}", flush=True)
    try:
        while not STOP.wait(1):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        RETENTION_WAKE.set()
        for event in WORKER_STOPS:
            event.set()
        with HTTP_SERVER_LOCK:
            server = HTTP_SERVER
            HTTP_SERVER = None
        if server is not None:
            server.shutdown()
            server.server_close()
        for thread in WORKER_THREADS:
            thread.join(timeout=25)


if __name__ == "__main__":
    main()
