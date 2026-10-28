"""Download the official 3B model when Ollama rejects a proxy's Fake-IP DNS.

Uses verified HTTPS and the Windows system proxy (urllib default). Only official
registry/CDN redirects are accepted. No system DNS, hosts or TLS settings change.
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REGISTRY = "https://registry.ollama.ai/v2/library/qwen2.5"
MODEL = "qwen2.5:3b"
MANIFEST_LIMIT = 1024 * 1024


def official_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and parsed.port in (None, 443)
        and (
            host in {"registry.ollama.ai", "registry.ollama.com"}
            or host.endswith(".r2.cloudflarestorage.com")
            or host.endswith(".ollama.ai")
            or host.endswith(".ollama.com")
        )
    )


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not official_url(newurl):
            raise RuntimeError("Download stopped: redirect is not an official HTTPS model host.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def descriptors(raw: bytes) -> list[dict]:
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
        raise ValueError("Unsupported official model manifest.")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not 1 <= len(layers) <= 32:
        raise ValueError("Unsupported official model layers.")
    entries = [manifest.get("config"), *layers]
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("digest"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", entry["digest"])
            or type(entry.get("size")) is not int
            or not 0 < entry["size"] <= 10 * 1024**3
        ):
            raise ValueError("Invalid model digest or size in official manifest.")
    return entries


def verified(path: Path, entry: dict) -> bool:
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    with path.open("rb") as file:
        return "sha256:" + hashlib.file_digest(file, "sha256").hexdigest() == entry["digest"]


def download_blob(opener, folder: Path, entry: dict) -> None:
    target = folder / entry["digest"].replace(":", "-")
    if verified(target, entry):
        print(f"Already verified: {target.name[:23]}", flush=True)
        return
    partial = target.with_suffix(".repair-part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset >= entry["size"]:
        if verified(partial, entry):
            partial.replace(target)
            return
        partial.unlink()
        offset = 0
    request = urllib.request.Request(REGISTRY + "/blobs/" + entry["digest"])
    if offset:
        request.add_header("Range", f"bytes={offset}-")
    with opener.open(request, timeout=60) as response:
        if response.status == 206:
            match = re.fullmatch(
                r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
            )
            if not match or int(match[1]) != offset or int(match[3]) != entry["size"]:
                raise RuntimeError("Invalid resumed download range; model was not installed.")
        elif response.status == 200:
            offset = 0  # Server ignored Range; restart instead of appending duplicate bytes.
        else:
            raise RuntimeError(f"Unexpected download status: {response.status}")
        print(f"Downloading {target.name[:23]} ({entry['size'] / 1024**2:.1f} MiB)...", flush=True)
        previous = -1
        with partial.open("ab" if offset else "wb") as file:
            while chunk := response.read(1024 * 1024):
                offset += len(chunk)
                if offset > entry["size"]:
                    raise RuntimeError("Downloaded file exceeds official size.")
                file.write(chunk)
                progress = offset * 100 // entry["size"]
                if progress // 5 != previous:
                    print(f"  {progress}%", flush=True)
                    previous = progress // 5
    if not verified(partial, entry):
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            "SHA-256 or size check failed; model was not installed. Retry the repair."
        )
    partial.replace(target)


def install(model_dir: Path) -> None:
    opener = urllib.request.build_opener(OfficialRedirects())
    request = urllib.request.Request(
        REGISTRY + "/manifests/3b",
        headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
    )
    print(f"Fetching official {MODEL} manifest over HTTPS...", flush=True)
    with opener.open(request, timeout=60) as response:
        raw = response.read(MANIFEST_LIMIT + 1)
    if len(raw) > MANIFEST_LIMIT:
        raise ValueError("Official model manifest is unexpectedly large.")
    entries = descriptors(raw)
    blobs = model_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    print(f"Ollama model directory: {model_dir}", flush=True)
    for entry in entries:
        download_blob(opener, blobs, entry)
    manifest = model_dir / "manifests/registry.ollama.ai/library/qwen2.5/3b"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_suffix(".repair-tmp")
    temporary.write_bytes(raw)
    temporary.replace(manifest)  # Publish only after every blob has passed SHA-256.
    print("Official model files verified. Next: check real Ollama generation.", flush=True)


if __name__ == "__main__":
    try:
        model_dir = Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama/models")
        install(model_dir)
    except KeyboardInterrupt:
        print("\nStopped. Partial downloads can resume on the next repair run.")
        raise SystemExit(1) from None
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        # Avoid dumping signed CDN query strings or proxy credentials.
        print(f"\nDownload failed ({type(error).__name__}). Model readiness is NOT confirmed.")
        if isinstance(error, urllib.error.HTTPError):
            print(f"HTTP status: {error.code}")
        elif isinstance(error, RuntimeError | ValueError):
            print(str(error))
        print("Check your network/system proxy, then rerun repair-model-download.cmd.")
        raise SystemExit(1) from None
