"""Replay-only phase-one CLI. Never imports wx4auto or opens WeChat databases."""

import argparse
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path

from shipment_bot.wechat.readonly import Inbox, JsonlReplay


def package_status():
    try:
        info = metadata("wx4auto")
        return {
            "installed": True,
            "version": info["Version"],
            "license": info.get("License"),
            "live_enabled": False,
        }
    except PackageNotFoundError:
        return {"installed": False, "live_enabled": False}


def main():
    parser = argparse.ArgumentParser(description="嘉惠微信只读采集回放（不连接真实微信）")
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--group", action="append", required=True)
    args = parser.parse_args()
    inbox = Inbox(args.database, args.account, set(args.group))
    inbox.run(JsonlReplay(args.replay))
    print(
        {
            "persisted_messages": len(inbox.messages()),
            "send_enabled": False,
            "wx4auto": package_status(),
        }
    )


if __name__ == "__main__":
    main()
