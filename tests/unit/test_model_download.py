import hashlib
import io
import json
import urllib.request

import pytest

from scripts import download_ollama_model as downloader


def entry(data):
    return {"digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)}


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class Opener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, **kwargs):
        self.requests.append(request)
        return next(self.responses)


@pytest.mark.parametrize(
    "url",
    [
        "http://registry.ollama.ai/blob",
        "https://127.0.0.1/blob",
        "https://evilcloudflarestorage.com/blob",
        "https://x.r2.cloudflarestorage.com.evil.test/blob",
        "https://user:password@registry.ollama.ai/blob",
        "https://registry.ollama.ai:8080/blob",
    ],
)
def test_repair_rejects_unofficial_or_insecure_redirects(url):
    request = urllib.request.Request(downloader.REGISTRY + "/manifests/3b")
    with pytest.raises(RuntimeError, match="official HTTPS"):
        downloader.OfficialRedirects().redirect_request(request, None, 307, "", {}, url)


def test_repair_accepts_official_cdn_redirect():
    url = "https://dd20bb891979d25aebc8bec07b2b3bbc.r2.cloudflarestorage.com/model"
    request = urllib.request.Request(downloader.REGISTRY + "/manifests/3b")
    redirected = downloader.OfficialRedirects().redirect_request(request, None, 307, "", {}, url)
    assert redirected.full_url == url


def test_repair_rejects_manifest_path_traversal():
    manifest = {"schemaVersion": 2, "config": entry(b"config"), "layers": [entry(b"weights")]}
    manifest["layers"][0]["digest"] = "../../arbitrary-file"
    with pytest.raises(ValueError, match="digest"):
        downloader.descriptors(json.dumps(manifest).encode())


@pytest.mark.parametrize("resumed", [True, False])
def test_repair_resumes_or_restarts_when_range_is_ignored(tmp_path, resumed):
    data = b"example-model-weights"
    descriptor = entry(data)
    target = tmp_path / descriptor["digest"].replace(":", "-")
    target.with_suffix(".repair-part").write_bytes(data[:4])
    response = (
        Response(data[4:], 206, {"Content-Range": f"bytes 4-{len(data) - 1}/{len(data)}"})
        if resumed
        else Response(data)
    )
    opener = Opener([response])
    downloader.download_blob(opener, tmp_path, descriptor)
    assert target.read_bytes() == data
    assert opener.requests[0].get_header("Range") == "bytes=4-"
    assert not target.with_suffix(".repair-part").exists()


def test_repair_does_not_publish_model_after_bad_checksum(monkeypatch, tmp_path):
    config, weights = b"config", b"weights"
    manifest = {"schemaVersion": 2, "config": entry(config), "layers": [entry(weights)]}
    opener = Opener(
        [Response(json.dumps(manifest).encode()), Response(config), Response(b"invalid")]
    )
    monkeypatch.setattr(downloader.urllib.request, "build_opener", lambda *args: opener)
    target = tmp_path / "manifests/registry.ollama.ai/library/qwen2.5/3b"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous manifest")
    with pytest.raises(RuntimeError, match="SHA-256"):
        downloader.install(tmp_path)
    assert target.read_bytes() == b"previous manifest"


def test_repair_publishes_exact_manifest_only_after_verified_blobs(monkeypatch, tmp_path):
    config, weights = b"config", b"weights"
    raw = json.dumps(
        {"schemaVersion": 2, "config": entry(config), "layers": [entry(weights)]}
    ).encode()
    opener = Opener([Response(raw), Response(config), Response(weights)])
    monkeypatch.setattr(downloader.urllib.request, "build_opener", lambda *args: opener)
    downloader.install(tmp_path)
    assert (tmp_path / "manifests/registry.ollama.ai/library/qwen2.5/3b").read_bytes() == raw
    for data in (config, weights):
        descriptor = entry(data)
        assert downloader.verified(
            tmp_path / "blobs" / descriptor["digest"].replace(":", "-"), descriptor
        )


def test_repair_reuses_verified_blobs_without_download(tmp_path):
    data = b"weights"
    descriptor = entry(data)
    target = tmp_path / descriptor["digest"].replace(":", "-")
    target.write_bytes(data)
    opener = Opener([])
    downloader.download_blob(opener, tmp_path, descriptor)
    assert opener.requests == []


def test_repair_rejects_wrong_resume_offset(tmp_path):
    descriptor = entry(b"weights")
    partial = (tmp_path / descriptor["digest"].replace(":", "-")).with_suffix(".repair-part")
    partial.write_bytes(b"we")
    opener = Opener([Response(b"ights", 206, {"Content-Range": "bytes 0-4/7"})])
    with pytest.raises(RuntimeError, match="range"):
        downloader.download_blob(opener, tmp_path, descriptor)
    assert partial.read_bytes() == b"we"
