"""Loopback-only bridge. Authentication prevents other local web pages submitting orders."""

import asyncio
import hmac
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException

from shipment_bot.extract import Extractor
from shipment_bot.models import Incoming
from shipment_bot.service import Bot
from shipment_bot.store import Store, scope


def create_app(bot: Bot, token: str) -> FastAPI:
    if len(token) < 32:
        raise ValueError("A private bridge token is required")
    app = FastAPI(title="Shipment review bridge", docs_url=None, redoc_url=None, openapi_url=None)
    locks: dict[str, asyncio.Lock] = {}

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "jiahui-shipment", "version": "1.0.0"}

    @app.post("/message")
    async def message(incoming: Incoming, authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Unauthorized")
        peer = scope(incoming.account_id, incoming.sender_id)
        lock = locks.setdefault(peer, asyncio.Lock())
        async with lock:
            try:
                reply = await asyncio.to_thread(bot.handle, incoming)
            except Exception:
                # Never log raw documents, model responses, paths, or credentials.
                return {"reply": "处理未完成，没有给出发货许可。请查看本机程序状态后重试。"}
        return {"reply": reply}

    return app


def main() -> None:
    import uvicorn

    data = Path(os.environ["SHIPMENT_DATA_DIR"])
    roots = [Path(p) for p in os.environ["SHIPMENT_MEDIA_ROOTS"].split(os.pathsep) if p]
    extractor = Extractor(os.environ.get("DEEPSEEK_API_KEY", ""))
    extractor.prepare_ocr()
    app = create_app(
        Bot(Store(data / "audit.sqlite3"), extractor, roots), os.environ["SHIPMENT_BRIDGE_TOKEN"]
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["SHIPMENT_BRIDGE_PORT"]),
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
