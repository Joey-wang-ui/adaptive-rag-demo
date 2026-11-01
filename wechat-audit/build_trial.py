"""Build the independently usable Windows trial without bundling vendor wxauto4 source."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent


def main():
    if os.name != "nt":
        raise RuntimeError("Windows builds require Windows")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "JiahuiAuditTrial",
        "--paths",
        str(ROOT),
        "--distpath",
        str(REPO / "dist"),
        "--workpath",
        str(REPO / "build/trial"),
        "--specpath",
        str(REPO / "build/trial"),
        "--hidden-import",
        "win32timezone",
    ]
    for package in [
        "PIL",
        "rapidocr_onnxruntime",
        "onnxruntime",
        "cv2",
        "comtypes",
        "uiautomation",
        "pyperclip",
        "psutil",
        "colorama",
        "tenacity",
        "win32com",
        "httpx",
        "httpcore",
        "certifi",
    ]:
        command.extend(["--collect-all", package])
    for module in [
        "win32gui",
        "win32api",
        "win32con",
        "win32process",
        "win32clipboard",
        "win32ui",
        "pythoncom",
        "pywintypes",
    ]:
        command.extend(["--hidden-import", module])
    command.append(str(ROOT / "trial_desktop.py"))
    subprocess.run(command, check=True, cwd=ROOT)
    destination = REPO / "dist/JiahuiAuditTrial"
    shutil.copyfile(ROOT / "TRIAL-QUICKSTART.md", destination / "使用说明.md")
    shutil.copyfile(REPO / "LICENSE", destination / "LICENSE.txt")
    for guide in ["WXAUTO4-EVALUATION.md", "WXAUTO4-TRIAL.md"]:
        shutil.copyfile(ROOT / guide, destination / guide)
    print(destination)


if __name__ == "__main__":
    main()
