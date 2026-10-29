"""Experimental Windows adapter; third-party transport is never imported in tests."""

import getpass
import json
import logging
import os
import sys
import time
from pathlib import Path

from shipment_bot.extract import ExtractionError, Extractor, validate_image
from shipment_bot.group_audit import GroupAudit, resolve_group

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/group-audit"
CONFIG = DATA / "config.json"


def main():
    if os.name != "nt":
        raise RuntimeError("本适配器只能在安装了电脑版微信的 Windows 上运行。")
    DATA.mkdir(parents=True, exist_ok=True)
    config = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    if not config.get("group_name"):
        config["group_name"] = input("只监听的群名（必须唯一）：").strip()
        config["bot_nickname"] = input("机器人在这个群的昵称：").strip()
        if not all(config.values()):
            raise ValueError("群名和昵称不可为空。")
        CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    # Importing this library reads local databases/process memory. No model API at check stage.
    from wechatauto import MediaDownloader, WeChatDB
    from wechatauto.db import Listener

    logging.getLogger().setLevel(logging.WARNING)
    db = WeChatDB()
    account = db.get_self_info()["username"]
    group = resolve_group(db, config["group_name"])
    if not account or not db.get_messages(group, limit=1):
        raise RuntimeError("未能读取当前账号/群消息。请先在该群发一条测试文字；尚未确认兼容。")
    binding = {"account": account, "group": group}
    print("当前账号：", account)
    print("指定群：", config["group_name"], group)
    print("群昵称：", config["bot_nickname"])
    if sys.argv[1:] == ["check"]:
        config["binding"] = binding
        CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        print("群文字读取检测通过；没有发送消息、没有调用模型。图片获取及回群仍待实测。")
        return
    if config.get("binding") != binding:
        raise RuntimeError("账号或群与检测记录不符。请先运行 setup-group-audit.cmd 重新检测。")
    key_file = DATA / "api-key.txt"
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key and key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
    if not key:
        print("Key 仅保存在本机 wechat-audit/data/group-audit/api-key.txt；不要分享此文件夹。")
        key = getpass.getpass("DeepSeek API Key（输入不显示）：").strip()
        if not key:
            raise RuntimeError("API Key 不可为空。")
        key_file.write_text(key, encoding="utf-8")
    extractor = Extractor(key)
    print("正在加载本地 OCR；不下载本地语言模型。", flush=True)
    extractor.prepare_ocr()
    media = MediaDownloader(db)
    image_dir = DATA / "images"
    image_dir.mkdir(exist_ok=True)

    def image_loader(target, message):
        # No thumbnail substitution and no UI clicks to fetch originals.
        path = media.download_image(
            target,
            int(message["local_id"]),
            save_dir=str(image_dir),
            tier="original",
            shard=message.get("shard"),
        )
        if not path:
            media.detect_image_key()
            path = media.download_image(
                target,
                int(message["local_id"]),
                save_dir=str(image_dir),
                tier="original",
                shard=message.get("shard"),
            )
        if not path:
            raise ExtractionError("未取得单据原图。请在电脑微信打开图片并查看原图，再重新 @审核。")
        return validate_image(Path(path), [image_dir])

    store_name = __import__("hashlib").sha256((account + group).encode()).hexdigest()[:24]
    workflow = GroupAudit(
        DATA / (store_name + ".sqlite3"),
        group,
        account,
        config["bot_nickname"],
        extractor,
        image_loader,
    )

    def on_message(message, _listener):
        try:
            reply = workflow.handle(group, message)
            if reply is None:
                return
            # Recheck exact group binding before constructing a sending GUI.
            if resolve_group(db, config["group_name"]) != group:
                raise RuntimeError("群身份已变化，取消发送。")
            from wechatauto.guia import quick_send

            response = quick_send(reply, config["group_name"], verify=True)
            if not response or response.get("status") != "成功":
                print("回群未确认成功；不会自动重发。请检查微信，必要时重新 @审核。", flush=True)
            else:
                print("已向指定群发送本次审核结果。", flush=True)
        except Exception as error:
            print(
                f"本次处理未完成（{type(error).__name__}），没有确认成功，不会自动重试发送。",
                flush=True,
            )

    # A fresh watermark seeds at latest message: no offline backlog replay.
    listener = Listener(db, interval=1.0, watermark_file="", max_retries=0)
    listener.add_listener(group, on_message)
    print("已启用指定群审单。只有 @昵称 审核 编号 才回复；Ctrl+C 停止。", flush=True)
    listener.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        listener.stop()


if __name__ == "__main__":
    main()
