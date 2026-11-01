"""Local workstation API; both entry points use AuditService and review.compare."""

import hmac
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from shipment_bot.models import Document
from shipment_bot.workbench import AuditService


class OrderInput(BaseModel):
    order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    document: Document


class AuditInput(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    delivery: Document


class PhotoInput(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    # Local workstation passes an uploaded file from an explicitly allowed directory.
    image_path: str = Field(min_length=1, max_length=4096)


def create_app(
    service: AuditService, token: str, *, extractor=None, media_roots: list[Path] | None = None
):
    if len(token) < 32:
        raise ValueError("private local API token (32+ characters) required")

    def authorize(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
            raise HTTPException(401, "Unauthorized")

    app = FastAPI(title="嘉惠共用审核引擎", dependencies=[Depends(authorize)])

    @app.post("/orders", status_code=201)
    def confirm_order(body: OrderInput):
        try:
            service.confirm_order(body.order_id, body.document)
        except (ValueError, sqlite3.IntegrityError) as error:
            raise HTTPException(409, "订单号已存在或订单身份不完整") from error
        return {"order_id": body.order_id, "confirmed": True}

    @app.get("/orders")
    def orders():
        return service.orders()

    @app.post("/audits")
    def audit(body: AuditInput):
        try:
            return service.audit(body.task_id, body.order_id, body.delivery, "workbench")
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/ingress/wechat/audits")
    def wechat_audit(body: AuditInput):
        try:
            return service.audit(body.task_id, body.order_id, body.delivery, "wechat")
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/audits/photo")
    def photo(body: PhotoInput):
        if extractor is None or not media_roots:
            raise HTTPException(503, "尚未配置本地 OCR/提取器和图片根目录")
        from shipment_bot.extract import ExtractionError, validate_image

        try:
            path = validate_image(Path(body.image_path), media_roots)
            delivery = extractor.image(path)
            return service.audit(body.task_id, body.order_id, delivery, "workbench")
        except (ValueError, OSError, ExtractionError) as error:
            raise HTTPException(409, "图片或订单字段需要核实，未完成审核") from error

    @app.get("/audits")
    def results():
        return service.results()

    return app


def main():
    import argparse
    import os

    import uvicorn

    parser = argparse.ArgumentParser(description="嘉惠共用审核 API（仅本机）")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--media-root", type=Path, action="append", default=[])
    parser.add_argument(
        "--enable-extraction",
        action="store_true",
        help="启用已有 OCR/模型提取；识别文字会发送给配置的模型服务",
    )
    args = parser.parse_args()
    extractor = None
    if args.enable_extraction:
        from shipment_bot.extract import Extractor

        extractor = Extractor(os.environ.get("DEEPSEEK_API_KEY", ""))
        extractor.prepare_ocr()
    app = create_app(
        AuditService(args.database),
        os.environ.get("SHIPMENT_BRIDGE_TOKEN", ""),
        extractor=extractor,
        media_roots=args.media_root,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
