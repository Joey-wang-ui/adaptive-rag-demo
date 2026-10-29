"""Compatibility adapter for the pinned wechatauto-replica 1.2.8.1 media API."""

from shipment_bot.extract import ExtractionError

V2_MAGIC = b"\x07\x08\x56\x32\x08\x07"


class CurrentImageProbe:
    def download_image(self, *args, **kwargs):
        self.audit_stage = "定位本条图片消息"
        return super().download_image(*args, **kwargs)

    def _image_files(self, *args, **kwargs):
        self.audit_stage = "定位本张图片缓存文件"
        return super()._image_files(*args, **kwargs)

    def _wxgf_to_jpg(self, data):
        self.audit_stage = "转换微信图片格式"
        return super()._wxgf_to_jpg(data)

    def decrypt_image(self, dat_path, aes_key=None, xor_key=None):
        # Upstream validates against an arbitrary first cache file. A stale/V1
        # probe can reject the correct cfg-derived key for this V2 message.
        self.audit_stage = "读取当前图片加密头"
        with open(dat_path, "rb") as file:
            header = file.read(31)
        if header[:6] == V2_MAGIC:
            if len(header) != 31:
                raise ExtractionError("本张图片缓存不完整，请在电脑微信重新下载图片。")
            self._key_probe = header[15:31]
        self.audit_stage = "解密当前图片"
        return super().decrypt_image(dat_path, aes_key=aes_key, xor_key=xor_key)

    def _scan_aes_key(self, monitor=False, monitor_timeout=120):
        # A single scan can find a key for the currently open image. Do not enter
        # upstream's two-minute monitor loop inside a message callback.
        self.audit_stage = "查找当前图片解密信息"
        return super()._scan_aes_key(monitor=False)

    def _resolve_aes_key(self):
        self.audit_stage = "验证当前图片解密信息"
        key = super()._resolve_aes_key()
        if not key:
            raise ExtractionError(
                "当前图片的解密信息未就绪，尚未读取图片内容。请保持微信登录，"
                "在电脑微信点开本张图片、等大图完整显示，再回群重新 @审核。"
                "若仍失败，请提供微信版本号；不要提供任何密钥。"
            )
        # Keep upstream's actual ciphertext validation; never assume a key works.
        if not self._validate_key(key):
            raise ExtractionError("当前图片解密验证未通过，请重新打开本张大图后重试。")
        self._image_key = key
        return key


def build_media(media_class, db):
    class BoundMedia(CurrentImageProbe, media_class):
        pass

    return BoundMedia(db)


def check_synthetic_decryption(media_class, jpeg, directory):
    """Offline self-test using generated data and a synthetic key; no WeChat access."""
    import hashlib
    import struct
    from types import SimpleNamespace

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cfg = 0x12345678
    wxid = "synthetic-self-test-only"
    key = hashlib.md5((str(cfg) + wxid).encode()).hexdigest()[:16]
    xor = cfg & 0xFF
    prefix, raw, tail = jpeg[:32], jpeg[32:-100], jpeg[-100:]
    encryptor = Cipher(algorithms.AES(key.encode()), modes.ECB()).encryptor()
    ciphertext = encryptor.update(prefix + bytes([16]) * 16) + encryptor.finalize()
    encrypted = directory / "synthetic.dat"
    encrypted.write_bytes(
        V2_MAGIC
        + struct.pack("<II", len(prefix), len(tail))
        + b"\0"
        + ciphertext
        + raw
        + bytes(value ^ xor for value in tail)
    )
    db = SimpleNamespace(cfg_dword=cfg, wxid=wxid, account_dir=str(directory))
    media = build_media(media_class, db)
    # A stale probe previously rejected the correct key. No scan is needed now.
    media._key_probe = b"\0" * 16

    def no_scan(*args, **kwargs):
        raise AssertionError("Synthetic decryption should not scan any process")

    media._scan_aes_key = no_scan
    decoded = media.decrypt_image(str(encrypted))
    if decoded != jpeg:
        raise RuntimeError("Synthetic V2 image decryption did not preserve image bytes")
    return media, decoded
