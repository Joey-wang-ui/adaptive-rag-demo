from decimal import Decimal

import pytest
import trial_desktop
from shipment_bot.wechat import wxauto4_download


def test_editor_retains_decimal_units_and_unknown_values():
    row = trial_desktop.DocumentEditor.line(["蛋液", "10kg", "3", "箱", "0.10", "箱", "0.30", ""])
    assert row.quantity * row.unit_price == Decimal("0.30")
    unknown = trial_desktop.DocumentEditor.line(["蛋液", "", "", "", "", "", "", ""])
    assert unknown.quantity is None and unknown.amount is None
    with pytest.raises(ValueError):
        trial_desktop.DocumentEditor.line(["蛋液", "", "-1", "箱", "", "", "", ""])


def test_download_refuses_unverified_archive_without_creating_bundle(tmp_path):
    destination = tmp_path / "bundle"
    with pytest.raises(ValueError, match="checksum"):
        wxauto4_download.prepare_archive(b"not the official archive", destination)
    assert not destination.exists()


def test_stream_download_preserves_tls_defaults_and_verifies_content(monkeypatch, tmp_path):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"first"
            yield b"second"

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 60}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def stream(self, method, url):
            assert method == "GET" and url.startswith("https://codeload.github.com/")
            return Response()

    monkeypatch.setattr(wxauto4_download.httpx, "Client", Client)

    def prepare(content, destination):
        seen.append(content)
        return destination

    monkeypatch.setattr(wxauto4_download, "prepare_archive", prepare)
    assert wxauto4_download.download_bundle(tmp_path / "bundle") == tmp_path / "bundle"
    assert seen == [b"firstsecond"]
