"""Fetch the user-selected upstream only on request; verify before preparing source."""

import hashlib
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from shipment_bot.wechat.wxauto4_prepare import (
    MANIFEST,
    UPSTREAM_COMMIT,
    patch_update_deletion,
    verify,
)

ARCHIVE_SHA256 = "62c60b4354bbab73b1af45ef3b46832604010fcf3d0619e59c93356f89744103"
ARCHIVE_URL = f"https://codeload.github.com/FreeWisdom/wxauto-4.0/zip/{UPSTREAM_COMMIT}"


def prepare_archive(content: bytes, destination: Path):
    if hashlib.sha256(content).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("upstream archive checksum differs; do not execute")
    if destination.exists():
        verify(destination)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="wxauto4-stage-", dir=destination.parent))
    try:
        hashes, originals = {}, {}
        prefix = f"wxauto-4.0-{UPSTREAM_COMMIT}/"
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 16 * 1024 * 1024:
                raise ValueError("archive exceeds expected size")
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if not info.filename.startswith(prefix):
                    raise ValueError("unexpected archive root")
                name = info.filename[len(prefix) :]
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                    raise ValueError("invalid archive path")
                data = archive.read(info)
                originals[name] = hashlib.sha256(data).hexdigest()
                if name == "wxauto4/utils/tools.py":
                    data = patch_update_deletion(data.decode("utf-8")).encode("utf-8")
                target = temp / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                hashes[name] = hashlib.sha256(data).hexdigest()
        (temp / MANIFEST).write_text(
            json.dumps(
                {
                    "upstream_commit": UPSTREAM_COMMIT,
                    "patch": "disable-update-deletion-v1",
                    "original_sha256": originals,
                    "prepared_sha256": hashes,
                }
            ),
            encoding="utf-8",
        )
        verify(temp)
        temp.rename(destination)
        return destination
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def download_bundle(destination: Path):
    if destination.exists():
        verify(destination)
        return destination
    content = bytearray()
    with httpx.Client(timeout=60) as client, client.stream("GET", ARCHIVE_URL) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > 2 * 1024 * 1024:
                raise ValueError("archive download exceeds expected size")
    return prepare_archive(bytes(content), destination)
