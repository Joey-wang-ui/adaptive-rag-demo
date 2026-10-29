"""Bound read-only media work; never let a key scan stall the group listener."""

import threading
from dataclasses import dataclass
from pathlib import Path

from shipment_bot.extract import ExtractionError, validate_image


@dataclass(frozen=True)
class ImageAsset:
    path: Path
    warning: str = ""


class ImageReader:
    def __init__(self, media, directory, timeout=15):
        self.media = media
        self.directory = directory
        self.timeout = timeout
        self.pending = None
        self.lock = threading.Lock()

    def __call__(self, group, message):
        identity = (group, message.get("shard"), message.get("local_id"))
        with self.lock:
            if self.pending and self.pending[0] != identity:
                if not self.pending[1].is_set():
                    raise ExtractionError(
                        "上一张图片仍在读取，未开始读取本张。请稍后重新 @审核；也可按 Esc 停止后手动启动。"
                    )
                self.pending = None
            if self.pending is None:
                done, result = threading.Event(), {}
                self.pending = identity, done, result
                threading.Thread(
                    target=self._read, args=(group, dict(message), done, result), daemon=True
                ).start()
            _, done, result = self.pending
        if not done.wait(self.timeout):
            stage = getattr(self.media, "audit_stage", "")
            if not isinstance(stage, str):
                stage = ""
            phases = {
                "定位本条图片消息",
                "定位本张图片缓存文件",
                "转换微信图片格式",
                "读取当前图片加密头",
                "解密当前图片",
                "查找当前图片解密信息",
                "验证当前图片解密信息",
            }
            detail = f"（阶段：{stage}）" if stage in phases else ""
            raise ExtractionError(
                f"读取微信图片超时{detail}，本次未完成识别。"
                "请在电脑微信打开本张图片、等大图完整显示后重新 @审核。若仍超时，请提供软件运行日志和微信版本号；不要提供密钥。"
            )
        with self.lock:
            if self.pending and self.pending[2] is result:
                self.pending = None
        if "error" in result:
            raise result["error"]
        return result["asset"]

    def _read(self, group, message, done, result):
        try:
            print("正在获取本批单据原图；最长等待15秒，Esc可急停。", flush=True)
            # download_image already resolves decryption keys when a file exists.
            # A None result means no original: scanning keys again cannot create it.
            path = self.media.download_image(
                group,
                int(message["local_id"]),
                save_dir=str(self.directory),
                tier="original",
                shard=message.get("shard"),
            )
            warning = ""
            if not path:
                # Full is the local non-thumbnail version, NOT a guaranteed original.
                # Always disclose this fallback; it must never count as an unqualified pass.
                path = self.media.download_image(
                    group,
                    int(message["local_id"]),
                    save_dir=str(self.directory),
                    tier="full",
                    shard=message.get("shard"),
                )
                warning = (
                    "本张使用微信本机接收版（非已确认原图），可能经过压缩或为预览版；"
                    "本次识别仅供核对，请对照原件，模糊时重新发送原图。"
                )
            if not path:
                raise ExtractionError(
                    "本张图片尚未下载到电脑，或本机只有缩略图。请在电脑微信打开本张图片、"
                    "等待加载完成（可点击“查看原图”），再重新 @审核。不会用缩略图代替。"
                )
            result["asset"] = ImageAsset(validate_image(Path(path), [self.directory]), warning)
            print("本张图片读取成功，准备OCR识别。", flush=True)
        except ExtractionError as error:
            result["error"] = error
        except Exception as error:
            # Never forward third-party exception text (may contain paths/credentials).
            result["error"] = ExtractionError(
                f"读取微信图片失败（{type(error).__name__}）。请在电脑微信打开本张图片、"
                "查看原图后重新 @审核；仍失败时检查软件日志。"
            )
        finally:
            done.set()
