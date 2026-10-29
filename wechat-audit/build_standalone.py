"""Build only the standalone audit; never install/import the WeChat adapter."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build_command():
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "JiahuiShipmentAudit",
        "--paths",
        str(ROOT),
        "--distpath",
        str(ROOT.parent / "dist"),
        "--workpath",
        str(ROOT.parent / "build/standalone"),
        "--specpath",
        str(ROOT.parent / "build/standalone"),
    ]
    for package in ("rapidocr_onnxruntime", "onnxruntime", "cv2"):
        command.extend(["--collect-all", package])
    for package in (
        "wechatauto",
        "pyautogui",
        "uiautomation",
        "shipment_bot.group_runtime",
        "shipment_bot.group_audit",
        "shipment_bot.group_ui",
        "shipment_bot.desktop_state",
    ):
        command.extend(["--exclude-module", package])
    return command + [str(ROOT / "standalone_desktop.py")]


def main():
    if os.name != "nt":
        raise RuntimeError("Windows 软件包必须在 Windows 构建。")
    subprocess.run(build_command(), cwd=ROOT, check=True)
    destination = ROOT.parent / "dist/JiahuiShipmentAudit"
    shutil.copyfile(ROOT / "STANDALONE.md", destination / "使用说明.md")
    shutil.copyfile(ROOT.parent / "LICENSE", destination / "LICENSE.txt")


if __name__ == "__main__":
    main()
