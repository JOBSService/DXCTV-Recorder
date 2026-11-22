from __future__ import annotations

import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path
from shutil import copy2


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT.parent if ROOT.name == "TapoLocalRecorder" and ROOT.parent.name == "outputs" else ROOT / "dist"
OUTPUT = OUTPUT_DIR / "DXCTVRecorder-Installers.zip"
WINDOWS_EXE = OUTPUT_DIR / "DXCTVRecorder-Windows-x64-Setup.exe"
PACKAGE_FILES = (
    "README.md",
    "DXCTVRecorder_User_Guide.md",
    "recorder.py",
    "install-recorder.ps1",
    "install-recorder-macos.command",
    "uninstall-recorder-macos.command",
    "install-recorder-linux.sh",
    "uninstall-recorder-linux.sh",
)
EXECUTABLES = {
    "install-recorder-macos.command",
    "uninstall-recorder-macos.command",
    "install-recorder-linux.sh",
    "uninstall-recorder-linux.sh",
}
WINDOWS_PAYLOAD = (
    "recorder.py",
    "install-recorder.ps1",
    "README.md",
    "DXCTVRecorder_User_Guide.md",
)
MACOS_FILES = (
    "README.md",
    "DXCTVRecorder_User_Guide.md",
    "recorder.py",
    "install-recorder-macos.command",
    "uninstall-recorder-macos.command",
)
LINUX_FILES = (
    "README.md",
    "DXCTVRecorder_User_Guide.md",
    "recorder.py",
    "install-recorder-linux.sh",
    "uninstall-recorder-linux.sh",
)


def build_windows_installer() -> None:
    iexpress = Path(r"C:\Windows\System32\iexpress.exe")
    if not iexpress.is_file():
        raise SystemExit("Windows IExpress was not found; cannot build the Windows setup executable.")

    with tempfile.TemporaryDirectory(prefix="dxctv-installer-") as temporary:
        stage = Path(temporary) / "payload"
        stage.mkdir()
        for name in WINDOWS_PAYLOAD:
            copy2(ROOT / name, stage / name)

        sed_path = Path(temporary) / "DXCTVRecorder.sed"
        strings = "\n".join(f'FILE{i}="{name}"' for i, name in enumerate(WINDOWS_PAYLOAD))
        files = "\n".join(f"%FILE{i}%=" for i in range(len(WINDOWS_PAYLOAD)))
        sed_path.write_text(
            "\n".join(
                [
                    "[Version]",
                    "Class=IEXPRESS",
                    "SEDVersion=3",
                    "[Options]",
                    "PackagePurpose=InstallApp",
                    "ShowInstallProgramWindow=1",
                    "HideExtractAnimation=1",
                    "UseLongFileName=1",
                    "InsideCompressed=1",
                    "CAB_FixedSize=0",
                    "CAB_ResvCodeSigning=0",
                    "RebootMode=N",
                    "InstallPrompt=%InstallPrompt%",
                    "DisplayLicense=%DisplayLicense%",
                    f'TargetName="{WINDOWS_EXE}"',
                    "FriendlyName=%FriendlyName%",
                    "AppLaunched=%AppLaunched%",
                    "PostInstallCmd=%PostInstallCmd%",
                    "AdminQuietInstCmd=%AdminQuietInstCmd%",
                    "UserQuietInstCmd=%UserQuietInstCmd%",
                    "SourceFiles=SourceFiles",
                    "[Strings]",
                    "InstallPrompt=",
                    "DisplayLicense=",
                    "FriendlyName=DXCTV Recorder Windows Setup",
                    "AppLaunched=powershell.exe -NoProfile -ExecutionPolicy Bypass -File install-recorder.ps1",
                    "PostInstallCmd=<None>",
                    "AdminQuietInstCmd=",
                    "UserQuietInstCmd=",
                    strings,
                    "[SourceFiles]",
                    f"SourceFiles0={stage}\\",
                    "[SourceFiles0]",
                    files,
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        result = subprocess.run([str(iexpress), "/N", "/Q", str(sed_path)], capture_output=True, text=True)
        if result.returncode != 0 or not WINDOWS_EXE.is_file():
            WINDOWS_EXE.unlink(missing_ok=True)
            raise SystemExit(
                "IExpress failed to create the Windows installer. "
                + (result.stderr.strip() or result.stdout.strip() or f"Exit code {result.returncode}.")
            )


def write_platform_zip(path: Path, files: tuple[str, ...]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            info = zipfile.ZipInfo(f"DXCTVRecorder/{name}")
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = 0o755 if name in EXECUTABLES else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, (ROOT / name).read_bytes())


def main() -> None:
    missing = [name for name in PACKAGE_FILES if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit("Missing installer package files: " + ", ".join(missing))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_windows_installer()
    macos_zip = OUTPUT_DIR / "DXCTVRecorder-macOS-Installer.zip"
    linux_zip = OUTPUT_DIR / "DXCTVRecorder-Ubuntu-Linux-Installer.zip"
    write_platform_zip(macos_zip, MACOS_FILES)
    write_platform_zip(linux_zip, LINUX_FILES)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(WINDOWS_EXE, f"DXCTVRecorder/{WINDOWS_EXE.name}")
        for name in PACKAGE_FILES:
            data = (ROOT / name).read_bytes()
            info = zipfile.ZipInfo(f"DXCTVRecorder/{name}")
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = 0o755 if name in EXECUTABLES else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, data)

    print(f"Created {OUTPUT}")
    print(f"Created {WINDOWS_EXE}")
    print(f"Created {macos_zip}")
    print(f"Created {linux_zip}")
    print("The package excludes config.json, recordings, and camera credentials.")


if __name__ == "__main__":
    main()
