import os

import pytest
from PIL import Image
from shipment_bot.extract import ExtractionError
from shipment_bot.image_transport import V2_MAGIC, CurrentImageProbe, check_synthetic_decryption


class FakeMedia:
    def decrypt_image(self, path, aes_key=None, xor_key=None):
        return self._key_probe

    def _scan_aes_key(self, **kwargs):
        self.scan_args = kwargs
        return "synthetic-result"

    def _resolve_aes_key(self):
        return self.resolved

    def _validate_key(self, key):
        return self.valid


class BoundMedia(CurrentImageProbe, FakeMedia):
    pass


def test_probe_is_current_ciphertext_not_stale_cache(tmp_path):
    path = tmp_path / "image.dat"
    path.write_bytes(V2_MAGIC + b"\0" * 9 + b"current-cipher!!")
    media = BoundMedia()
    media._key_probe = b"stale-cache-probe"
    assert media.decrypt_image(path) == b"current-cipher!!"


def test_single_scan_never_enters_monitor_loop():
    media = BoundMedia()
    assert media._scan_aes_key(monitor=True, monitor_timeout=120) == "synthetic-result"
    assert media.scan_args == {"monitor": False}


@pytest.mark.parametrize("resolved,valid", [(None, False), ("synthetic-key", False)])
def test_unavailable_or_invalid_key_is_never_accepted(resolved, valid):
    media = BoundMedia()
    media.resolved, media.valid = resolved, valid
    with pytest.raises(ExtractionError):
        media._resolve_aes_key()


def test_validated_key_reused_without_revealing_it():
    media = BoundMedia()
    media.resolved, media.valid = "synthetic-key", True
    assert media._resolve_aes_key() == "synthetic-key"
    assert media._image_key == "synthetic-key"


def test_truncated_v2_image_is_rejected_before_crypto(tmp_path):
    path = tmp_path / "image.dat"
    path.write_bytes(V2_MAGIC)
    with pytest.raises(ExtractionError, match="缓存不完整"):
        BoundMedia().decrypt_image(path)


@pytest.mark.skipif(os.name != "nt", reason="real transport dependency is installed on Windows")
def test_real_pinned_library_decrypts_current_image_with_stale_probe_without_scanning(tmp_path):
    from wechatauto import MediaDownloader

    jpeg = tmp_path / "source.jpg"
    Image.new("RGB", (200, 200), "white").save(jpeg)
    _, decoded = check_synthetic_decryption(MediaDownloader, jpeg.read_bytes(), tmp_path)
    assert decoded == jpeg.read_bytes()
