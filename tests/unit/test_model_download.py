import hashlib
import http.client
import io
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    assert opener.requests[0].get_header("Range") == f"bytes=4-{len(data) - 1}"
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


def test_short_response_retries_from_saved_bytes_instead_of_deleting(monkeypatch, tmp_path):
    data = b"weights-that-were-interrupted"
    descriptor = entry(data)
    opener = Opener(
        [
            Response(data[:10]),
            Response(data[10:], 206, {"Content-Range": f"bytes 10-{len(data) - 1}/{len(data)}"}),
        ]
    )
    monkeypatch.setattr(downloader.time, "sleep", lambda seconds: None)
    downloader.download_blob(opener, tmp_path, descriptor)
    assert opener.requests[1].get_header("Range") == f"bytes=10-{len(data) - 1}"
    assert (tmp_path / descriptor["digest"].replace(":", "-")).read_bytes() == data


def test_exhausted_retries_preserve_partial_and_next_run_resumes(monkeypatch, tmp_path):
    data = b"weights-that-were-interrupted"
    descriptor = entry(data)
    partial = (tmp_path / descriptor["digest"].replace(":", "-")).with_suffix(".repair-part")
    monkeypatch.setattr(downloader, "MAX_INTERRUPTS", 2)
    monkeypatch.setattr(downloader.time, "sleep", lambda seconds: None)
    opener = Opener(
        [
            Response(data[:8]),
            Response(b"", 206, {"Content-Range": f"bytes 8-{len(data) - 1}/{len(data)}"}),
            Response(b"", 206, {"Content-Range": f"bytes 8-{len(data) - 1}/{len(data)}"}),
        ]
    )
    with pytest.raises(RuntimeError, match="Progress is saved"):
        downloader.download_blob(opener, tmp_path, descriptor)
    assert partial.read_bytes() == data[:8]
    resumed = Opener(
        [Response(data[8:], 206, {"Content-Range": f"bytes 8-{len(data) - 1}/{len(data)}"})]
    )
    downloader.download_blob(resumed, tmp_path, descriptor)
    assert resumed.requests[0].get_header("Range") == f"bytes=8-{len(data) - 1}"
    assert not partial.exists()


def test_downloads_bounded_ranges_until_whole_blob_verified(monkeypatch, tmp_path):
    data = b"weights-from-multiple-ranges"
    descriptor = entry(data)
    monkeypatch.setattr(downloader, "RANGE_BYTES", 8)
    responses = []
    ranges = []
    for offset in range(0, len(data), 8):
        end = min(offset + 8, len(data)) - 1
        ranges.append(f"bytes={offset}-{end}")
        responses.append(
            Response(
                data[offset : end + 1], 206, {"Content-Range": f"bytes {offset}-{end}/{len(data)}"}
            )
        )
    opener = Opener(responses)
    downloader.download_blob(opener, tmp_path, descriptor)
    assert [request.get_header("Range") for request in opener.requests] == ranges
    assert (tmp_path / descriptor["digest"].replace(":", "-")).read_bytes() == data


def test_incomplete_read_keeps_its_bytes_before_retry(monkeypatch, tmp_path):
    data = b"weights-after-an-incomplete-read"
    descriptor = entry(data)

    class IncompleteResponse(Response):
        def read(self, size=-1):
            raise http.client.IncompleteRead(data[:5], len(data) - 5)

    opener = Opener(
        [
            IncompleteResponse(b""),
            Response(data[5:], 206, {"Content-Range": f"bytes 5-{len(data) - 1}/{len(data)}"}),
        ]
    )
    monkeypatch.setattr(downloader.time, "sleep", lambda seconds: None)
    downloader.download_blob(opener, tmp_path, descriptor)
    assert opener.requests[1].get_header("Range") == f"bytes=5-{len(data) - 1}"


def test_permanent_http_errors_are_not_retried(monkeypatch, tmp_path):
    class MissingModel:
        def open(self, request, **kwargs):
            raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)

    monkeypatch.setattr(downloader.time, "sleep", lambda seconds: pytest.fail("No retry for 404"))
    with pytest.raises(urllib.error.HTTPError):
        downloader.download_blob(MissingModel(), tmp_path, entry(b"weights"))


def test_real_http_truncation_resumes_and_verifies(monkeypatch, tmp_path):
    data = b"real-HTTP-truncated-weights"
    ranges = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            ranges.append(self.headers.get("Range"))
            offset = 0 if len(ranges) == 1 else 8
            self.send_response(200 if offset == 0 else 206)
            self.send_header("Content-Length", str(len(data) - offset))
            if offset:
                self.send_header("Content-Range", f"bytes {offset}-{len(data) - 1}/{len(data)}")
            self.end_headers()
            self.wfile.write(data[:8] if offset == 0 else data[offset:])
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        monkeypatch.setattr(downloader, "REGISTRY", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setattr(downloader.time, "sleep", lambda seconds: None)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        downloader.download_blob(opener, tmp_path, entry(data))
        assert ranges == [None, f"bytes=8-{len(data) - 1}"]
        assert (tmp_path / entry(data)["digest"].replace(":", "-")).read_bytes() == data
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_progressing_interruptions_do_not_exhaust_the_retry_limit(monkeypatch, tmp_path):
    data = b"0123456789abcdef"
    descriptor = entry(data)
    monkeypatch.setattr(downloader, "MAX_INTERRUPTS", 2)
    monkeypatch.setattr(downloader.time, "sleep", lambda seconds: None)
    responses = [Response(data[:4])]
    for offset in (4, 8, 12):
        responses.append(
            Response(
                data[offset : offset + 4],
                206,
                {"Content-Range": f"bytes {offset}-{len(data) - 1}/{len(data)}"},
            )
        )
    downloader.download_blob(Opener(responses), tmp_path, descriptor)
    assert (tmp_path / descriptor["digest"].replace(":", "-")).read_bytes() == data


def test_ignored_range_never_erases_a_longer_saved_prefix(monkeypatch, tmp_path):
    data = b"0123456789abcdef"
    descriptor = entry(data)
    partial = (tmp_path / descriptor["digest"].replace(":", "-")).with_suffix(".repair-part")
    partial.write_bytes(data[:10])
    monkeypatch.setattr(downloader, "MAX_INTERRUPTS", 2)
    monkeypatch.setattr(downloader.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="Progress is saved"):
        downloader.download_blob(
            Opener([Response(data[:4]), Response(data[:6])]), tmp_path, descriptor
        )
    assert partial.read_bytes() == data[:10]


def test_later_ranges_use_official_cdn_url_without_exposing_signature(
    monkeypatch, tmp_path, capsys
):
    data = b"0123456789abcdef"
    descriptor = entry(data)
    final_url = "https://blob.r2.cloudflarestorage.com/model?signature=secret-value"

    class RedirectedResponse(Response):
        def geturl(self):
            return final_url

    monkeypatch.setattr(downloader.time, "sleep", lambda seconds: None)
    opener = Opener(
        [
            RedirectedResponse(data[:4]),
            Response(data[4:], 206, {"Content-Range": f"bytes 4-{len(data) - 1}/{len(data)}"}),
        ]
    )
    downloader.download_blob(opener, tmp_path, descriptor)
    assert opener.requests[1].full_url == final_url
    assert "secret-value" not in capsys.readouterr().out
