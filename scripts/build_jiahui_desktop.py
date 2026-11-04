"""Build a Windows x64 application with Python runtime, dependencies and React assets."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.name != "nt":
        raise RuntimeError("Windows desktop builds must run on Windows")
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise RuntimeError("Build frontend before packaging")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "JiahuiAssistant",
        "--paths",
        str(ROOT),
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build/desktop"),
        "--specpath",
        str(ROOT / "build/desktop"),
        "--add-data",
        str(ROOT / "frontend/dist") + ";frontend/dist",
    ]
    for package in [
        "app",
        "webview",
        "pythonnet",
        "clr_loader",
        "jieba",
        "pypdf",
        "pdfplumber",
        "pdfminer",
        "pypdfium2",
        "uvicorn",
        "sse_starlette",
        "certifi",
        "qdrant_client",
        "langgraph",
        "langgraph_sdk",
        "langchain_core",
    ]:
        command += ["--collect-all", package]
    for package in [
        "adaptive-rag-cs",
        "langgraph",
        "langgraph-checkpoint",
        "langgraph-sdk",
        "langchain-core",
        "langsmith",
    ]:
        command += ["--copy-metadata", package]
    for module in [
        "scripts.jiahui_desktop_selftest",
        "scripts.jiahui_pdf_fixture",
        "aiosqlite",
        "asyncpg",
        "sqlalchemy.dialects.sqlite.aiosqlite",
        "uvicorn.logging",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on",
    ]:
        command += ["--hidden-import", module]
    command += [
        "--exclude-module",
        "torch",
        "--exclude-module",
        "sentence_transformers",
        "--exclude-module",
        "pytest",
        str(ROOT / "scripts/jiahui_desktop.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    destination = ROOT / "dist/JiahuiAssistant"
    shutil.copyfile(ROOT / "desktop/使用说明.md", destination / "使用说明.md")
    shutil.copyfile(ROOT / "LICENSE", destination / "LICENSE.txt")
    shutil.copytree(
        ROOT / "docs/jiahui",
        destination / "资料模板",
        ignore=shutil.ignore_patterns("验证结果.json", "浏览器验证结果.json"),
    )
    (destination / "START.cmd").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\nstart "" "%~dp0JiahuiAssistant.exe"\r\n', encoding="ascii"
    )
    (destination / "恢复账号.cmd").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\nstart "" "%~dp0JiahuiAssistant.exe" --recover-account\r\n',
        encoding="ascii",
    )
    (destination / "检查账号和资料目录.cmd").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\nstart "" "%~dp0JiahuiAssistant.exe" --select-data\r\n',
        encoding="ascii",
    )
    print(destination)


if __name__ == "__main__":
    main()
