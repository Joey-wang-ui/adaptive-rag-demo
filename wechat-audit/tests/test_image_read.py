import threading
import time
from unittest.mock import Mock

import pytest
from PIL import Image
from shipment_bot.extract import ExtractionError, Extractor, validate_image
from shipment_bot.image_read import ImageReader


@pytest.fixture
def original(tmp_path):
    path = tmp_path / "original.png"
    Image.new("RGB", (200, 200), "white").save(path)
    return path


def test_missing_original_returns_help_without_unnecessary_key_scan(tmp_path):
    media = Mock()
    media.download_image.return_value = None
    reader = ImageReader(media, tmp_path)
    with pytest.raises(ExtractionError, match="尚未下载到电脑"):
        reader("group", {"local_id": 1, "shard": "message_1.db"})
    media.detect_image_key.assert_not_called()
    assert [call.kwargs["tier"] for call in media.download_image.call_args_list] == [
        "original",
        "full",
    ]
    assert media.download_image.call_args.kwargs["shard"] == "message_1.db"


@pytest.mark.parametrize("failure", [PermissionError, RuntimeError, ValueError, FileNotFoundError])
def test_transport_failure_is_actionable_and_does_not_leak_raw_error(tmp_path, failure):
    media = Mock()
    media.download_image.side_effect = failure("private-path secret-api-key")
    with pytest.raises(ExtractionError, match="读取微信图片失败") as error:
        ImageReader(media, tmp_path)("group", {"local_id": 1})
    assert "private-path" not in str(error.value)
    assert "secret-api-key" not in str(error.value)


def test_timeout_does_not_spawn_repeated_scans_and_late_result_waits_for_manual_retry(
    tmp_path, original
):
    release = threading.Event()
    media = Mock()

    def slow(*args, **kwargs):
        release.wait(5)
        return str(original)

    media.download_image.side_effect = slow
    reader = ImageReader(media, tmp_path, timeout=0.02)
    message = {"local_id": 1, "shard": "message_1.db"}
    started = time.monotonic()
    try:
        with pytest.raises(ExtractionError, match="超时"):
            reader("group", message)
        with pytest.raises(ExtractionError, match="超时"):
            reader("group", message)
        with pytest.raises(ExtractionError, match="上一张图片仍在读取"):
            reader("group", {"local_id": 2})
        assert time.monotonic() - started < 1
        assert media.download_image.call_count == 1
        release.set()
        assert reader.pending[1].wait(2)
        # No callback to the group or model exists on the background thread.
        assert reader("group", message).path == original
        assert media.download_image.call_count == 1
    finally:
        release.set()


def test_missing_file_is_a_review_error_not_an_uncaught_oserror(tmp_path):
    with pytest.raises(ExtractionError, match="未找到或无法读取"):
        validate_image(tmp_path / "gone.png", [tmp_path])


def test_ocr_failure_becomes_review_error_instead_of_silent_adapter_failure(original):
    extractor = Extractor("test-only-key")
    extractor._ocr = Mock(side_effect=RuntimeError("private-path test-only-key"))
    with pytest.raises(ExtractionError, match="OCR处理失败") as error:
        extractor.read_image(original)
    assert "test-only-key" not in str(error.value)
    assert "private-path" not in str(error.value)


def test_received_version_fallback_is_explicit_and_never_requests_thumbnail(tmp_path, original):
    media = Mock()
    media.download_image.side_effect = [None, str(original)]
    asset = ImageReader(media, tmp_path)("group", {"local_id": 1})
    assert asset.path == original
    assert "非已确认原图" in asset.warning
    assert [call.kwargs["tier"] for call in media.download_image.call_args_list] == [
        "original",
        "full",
    ]
