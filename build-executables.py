"""Build a standalone DXCTV Recorder executable for the current OS with PyInstaller.

PyInstaller cannot cross-compile: run this on Windows for the .exe, on Linux for the
Linux binary, and on macOS for the macOS binary (or use .github/workflows/build-executables.yml).

    python -m pip install pyinstaller
    python build-executables.py

Output: dist/executables/DXCTVRecorder-<platform>-<arch>[.exe]
FFmpeg is not bundled; it must still be installed on the target computer.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "executables"


def target_name() -> str:
    system = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return f"DXCTVRecorder-{system}-{arch}"


def main() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or later is required to build DXCTV Recorder.")
    name = target_name()
    DIST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dxctv-build-") as work:
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
            "--name", name, "--distpath", str(DIST), "--workpath", str(Path(work) / "build"),
            "--specpath", work, str(ROOT / "recorder.py"),
        ]
        subprocess.run(command, check=True)
    built = DIST / (name + (".exe" if sys.platform == "win32" else ""))
    if not built.is_file():
        raise SystemExit(f"PyInstaller finished but {built} was not created.")
    if sys.platform != "win32":
        built.chmod(0o755)
    shutil.rmtree(ROOT / "__pycache__", ignore_errors=True)
    print(f"Created {built}")


if __name__ == "__main__":
    main()
