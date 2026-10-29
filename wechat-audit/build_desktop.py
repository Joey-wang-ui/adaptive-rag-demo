"""Build a self-contained Windows folder; run on Windows with Python 3.12."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent


def main():
    if os.name != "nt":
        raise RuntimeError("Windows executable builds require Windows.")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "JiahuiGroupAudit",
        "--paths",
        str(ROOT),
        "--distpath",
        str(REPO / "dist"),
        "--workpath",
        str(REPO / "build/group-desktop"),
        "--specpath",
        str(REPO / "build/group-desktop"),
        "--hidden-import",
        "win32timezone",
        "--collect-submodules",
        "win32com",
    ]
    for package in [
        "wechatauto",
        "rapidocr_onnxruntime",
        "onnxruntime",
        "winsdk",
        "uiautomation",
        "comtypes",
        "cv2",
        "imageio_ffmpeg",
    ]:
        command.extend(["--collect-all", package])
    command.append(str(ROOT / "desktop.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    destination = REPO / "dist/JiahuiGroupAudit"
    shutil.copyfile(ROOT / "DESKTOP.md", destination / "使用说明.md")
    print(destination)


if __name__ == "__main__":
    main()
