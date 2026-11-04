"""Desktop-only setup and authenticated local settings; never enabled by normal ASGI."""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import hmac
import io
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, SecretStr

from app.config.settings import Settings
from app.jiahui.auth import (
    COOKIE_NAME,
    SESSION_SECONDS,
    AuthStore,
    Principal,
    check_same_origin,
    current_principal,
)
from app.jiahui.catalog import FIELDS, Catalog, allowed
from app.jiahui.graph import build_jiahui_graph
from app.jiahui.import_catalog import import_csv
from app.jiahui.runtime import probe_capabilities
from app.jiahui.vault import credential_vault
from app.main import create_app
from app.providers.deepseek import DeepSeekProvider

logger = logging.getLogger(__name__)


class InitialSetup(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=12, max_length=1024)
    api_key: SecretStr = SecretStr("")
    remember_key: bool = False


class ModelUpdate(BaseModel):
    api_key: SecretStr = Field(min_length=1, max_length=1024)
    remember_key: bool = False


class Upload(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=21_000_000)
    status: Literal["draft", "current"] = "draft"
    version: str = Field(default="来源版本待确认", min_length=1, max_length=100)


def safe_filename(value: str, suffix: str) -> str:
    name = re.split(r"[/\\]", value)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)
    if not name.lower().endswith(suffix) or name in {".", ".."}:
        raise ValueError("请选择正确类型的文件")
    return name


async def update_model(container, key: str) -> None:
    settings = container.settings.model_copy(
        update={"deepseek_api_key": SecretStr(key), "llm_backend": "auto"}
    )
    provider = DeepSeekProvider(settings)
    # Retain old clients until shutdown so an in-flight answer can finish safely.
    container.retired_llms = [*getattr(container, "retired_llms", []), container.llm]
    container.llm = provider
    container.settings = settings
    service = container.chat_service
    service._settings, service._llm = settings, provider
    service._graph = build_jiahui_graph(
        llm=provider,
        settings=settings,
        retriever=container.retriever,
        reranker=service._reranker,
        registry=service.registry,
    )
    container.model_capabilities = await probe_capabilities(
        provider, container.retriever, service._reranker, settings
    )


def create_desktop_app(home: Path, data_dir: Path, bootstrap_token: str = "", vault=None):
    home, data_dir = home.resolve(), data_dir.resolve()
    home.mkdir(parents=True, exist_ok=True)
    vault = vault or credential_vault(home)
    settings_options: dict[str, Any] = {
        "_env_file": home / ".env",
        "business_profile": "jiahui",
        "jiahui_data_dir": str(data_dir),
    }
    settings = Settings(**settings_options)
    if not settings.llm_configured:
        saved = vault.read()
        if saved:
            settings = settings.model_copy(
                update={"deepseek_api_key": SecretStr(saved), "llm_backend": "auto"}
            )
    router = APIRouter(prefix="/desktop", tags=["desktop"])
    setup_lock, model_lock = asyncio.Lock(), asyncio.Lock()
    store = AuthStore(data_dir)
    from app.jiahui.guides import install_usage_guide
    from app.jiahui.pdf_catalog import repair_pdf_catalogs

    install_usage_guide(data_dir)
    repair_report = repair_pdf_catalogs(data_dir)
    if repair_report.get("warnings"):
        logger.warning(
            "desktop_catalog_repair_warning",
            extra={"warning_count": len(repair_report["warnings"])},
        )

    async def admin(request: Request) -> Principal:
        check_same_origin(request)
        principal = await current_principal(request)
        if principal.role != "admin":
            raise HTTPException(403, detail="仅管理员可管理模型与资料")
        return principal

    @router.get("/status")
    async def status(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {
            "desktop": True,
            "version": "1.1.3",
            "setup_required": not store.has_users(),
            "secure_storage": vault.persistent,
        }

    @router.post("/setup")
    async def setup(payload: InitialSetup, request: Request, response: Response):
        check_same_origin(request)
        provided = request.headers.get("x-jiahui-setup", "")
        if not bootstrap_token or not hmac.compare_digest(provided, bootstrap_token):
            raise HTTPException(403, detail="请从桌面程序首次启动页面初始化")
        async with setup_lock:
            if store.has_users():
                raise HTTPException(409, detail="已经初始化，请直接登录；不会覆盖原账号")
            key = payload.api_key.get_secret_value().strip()
            if key and payload.remember_key:
                try:
                    vault.write(key)
                except (OSError, ValueError) as exc:
                    raise HTTPException(400, detail=str(exc)) from None
            try:
                created = await asyncio.to_thread(
                    store.create_user,
                    payload.username,
                    payload.password.get_secret_value(),
                    "admin",
                )
            except ValueError as exc:
                raise HTTPException(400, detail=str(exc)) from None
            if not created:
                raise HTTPException(409, detail="账号已存在，不会覆盖")
            if key:
                async with model_lock:
                    await update_model(request.app.state.container, key)
            token = await asyncio.to_thread(
                store.login, payload.username, payload.password.get_secret_value(), "desktop-setup"
            )
            response.set_cookie(
                COOKIE_NAME,
                token,
                max_age=SESSION_SECONDS,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
            )
            response.headers["Cache-Control"] = "no-store"
            return store.principal(token).as_dict()

    @router.get("/settings")
    async def current_settings(
        request: Request, response: Response, principal: Principal = Depends(admin)
    ):
        container = request.app.state.container
        scopes = set(principal.scopes)
        catalog = Catalog.load(data_dir)
        records = catalog.visible_records(scopes)
        stats = container.retriever.knowledge_stats(scopes)
        response.headers["Cache-Control"] = "no-store"
        return {
            "data_dir": str(data_dir),
            "api_key_configured": container.settings.llm_configured,
            "secure_storage": vault.persistent,
            "capabilities": container.model_capabilities,
            "current_products": sum(r.get("status") == "current" for r in records),
            "reference_products": sum(r.get("status") == "reference" for r in records),
            "product_count": len(records),
            "draft_products": sum(
                r.get("status") == "draft" and allowed(r, scopes) for r in catalog.records
            ),
            **stats,
        }

    @router.get("/documents")
    async def documents(
        request: Request, response: Response, principal: Principal = Depends(admin)
    ):
        response.headers["Cache-Control"] = "no-store"
        entries = request.app.state.container.retriever.knowledge_documents(set(principal.scopes))
        return {
            "documents": [
                {
                    key: entry.get(key, "")
                    for key in ("id", "title", "source_file", "location", "version", "status")
                }
                for entry in entries
            ]
        }

    @router.post("/repair")
    async def repair(request: Request, principal: Principal = Depends(admin)):
        report = await asyncio.to_thread(repair_pdf_catalogs, data_dir)
        rows = Catalog.load(data_dir).visible_records(set(principal.scopes))
        return {
            **report,
            "product_count": len(rows),
            "reference_products": sum(row.get("status") == "reference" for row in rows),
        }

    @router.post("/model")
    async def model(payload: ModelUpdate, request: Request, principal: Principal = Depends(admin)):
        key = payload.api_key.get_secret_value().strip()
        if not key:
            raise HTTPException(400, detail="请输入Key")
        async with model_lock:
            if payload.remember_key:
                try:
                    vault.write(key)
                except (OSError, ValueError) as exc:
                    raise HTTPException(400, detail=str(exc)) from None
            await update_model(request.app.state.container, key)
        return {
            "saved": True,
            "persisted": payload.remember_key,
            "capabilities": request.app.state.container.model_capabilities,
        }

    @router.get("/template")
    async def template(principal: Principal = Depends(admin)):
        output = io.StringIO(newline="")
        csv.writer(output).writerow(FIELDS)
        return PlainTextResponse(
            "\ufeff" + output.getvalue(),
            headers={"Content-Disposition": 'attachment; filename="jiahui-products-template.csv"'},
            media_type="text/csv; charset=utf-8",
        )

    @router.post("/import/products")
    async def products(payload: Upload, principal: Principal = Depends(admin)):
        try:
            name = safe_filename(payload.filename, ".csv")
            content = payload.content.encode("utf-8-sig")
            if len(content) > 15 * 1024 * 1024:
                raise ValueError("CSV最大15MB")
            # The UI's explicit draft/current choice overrides embedded status.
            reader = csv.DictReader(io.StringIO(payload.content.lstrip("\ufeff"), newline=""))
            if not reader.fieldnames:
                raise ValueError("CSV为空")
            fields = list(reader.fieldnames)
            if "状态" not in fields:
                fields.append("状态")
            rows = [
                row for row in reader if any(value and str(value).strip() for value in row.values())
            ]
            if not rows:
                raise ValueError("CSV只有表头或空白行，没有可导入的产品；请填写模板后再导入")
            for row in rows:
                if None in row:
                    raise ValueError("CSV存在列数不一致的记录")
                row["状态"] = payload.status
            digest = hashlib.sha256(content).hexdigest()
            source_root = data_dir / "sources" / "desktop-uploads"
            source_root.mkdir(parents=True, exist_ok=True)
            original = source_root / (digest + ".csv")
            if not original.exists():
                original.write_bytes(content)
            with tempfile.TemporaryDirectory(prefix="jiahui-import-") as temp:
                source = Path(temp) / name
                with source.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
                result = await asyncio.to_thread(
                    import_csv, source, data_dir, status=payload.status
                )
            return {**result, "status": payload.status}
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(400, detail=str(exc)) from None

    @router.post("/import/pdf")
    async def pdf(payload: Upload, principal: Principal = Depends(admin)):
        try:
            from app.jiahui.pdf_import import import_pdf

            name = safe_filename(payload.filename, ".pdf")
            content = base64.b64decode(payload.content, validate=True)
            result = await asyncio.to_thread(
                import_pdf, content, data_dir, filename=name, version=payload.version
            )
            visible = Catalog.load(data_dir).visible_records(set(principal.scopes))
            result["reference_products"] = sum(row.get("status") == "reference" for row in visible)
            result["product_count"] = len(visible)
            return result
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(400, detail=str(exc)) from None
        except ImportError:
            raise HTTPException(503, detail="PDF组件未安装，请使用完整桌面包") from None
        except Exception as exc:
            logger.warning("desktop_pdf_import_failed", extra={"error_type": type(exc).__name__})
            raise HTTPException(
                400, detail="PDF解析或导入未完成，请查看日志并核对已导入页数；可安全重试"
            ) from None

    return create_app(settings, extra_routers=[router])
