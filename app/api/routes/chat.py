"""Chat routes: single-shot JSON and SSE streaming."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sse_starlette import JSONServerSentEvent
from sse_starlette.sse import EventSourceResponse

from app.api.dependencies import get_container
from app.jiahui.auth import Principal, current_principal
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.container import AppContainer

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    container: AppContainer = Depends(get_container),
    principal: Principal = Depends(current_principal),
) -> ChatResponse:
    """Answer one turn and return the full JSON response."""
    if container.settings.business_profile == "jiahui":
        payload = payload.model_copy(update={"user_id": principal.user_id})
        await container.chat_service.require_session_owner(payload.session_id, principal.user_id)
    return await container.chat_service.chat(
        payload, trace_id=request.state.trace_id, scopes=tuple(principal.scopes)
    )


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    container: AppContainer = Depends(get_container),
    principal: Principal = Depends(current_principal),
) -> EventSourceResponse:
    """Answer one turn as an SSE stream of node, token and answer events."""

    if container.settings.business_profile == "jiahui":
        payload = payload.model_copy(update={"user_id": principal.user_id})
        await container.chat_service.require_session_owner(payload.session_id, principal.user_id)

    async def events():
        async for item in container.chat_service.stream(
            payload, trace_id=request.state.trace_id, scopes=tuple(principal.scopes)
        ):
            yield JSONServerSentEvent(item)

    return EventSourceResponse(events())


@router.get("/profile")
async def business_profile(
    request: Request, container: AppContainer = Depends(get_container)
) -> JSONResponse:
    from fastapi import HTTPException

    from app.jiahui.catalog import Catalog
    from app.jiahui.runtime import EXAMPLES, ScopedRetriever

    jiahui = container.settings.business_profile == "jiahui"
    principal = None
    if jiahui:
        try:
            principal = await current_principal(request)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
    count = (
        len(Catalog.load(container.settings.jiahui_data_dir).visible_records(set(principal.scopes)))
        if principal
        else 0
    )
    status = "尚未录入产品资料" if not count else f"当前可访问 {count} 条产品资料"
    if (
        jiahui
        and principal is not None
        and not count
        and isinstance(container.retriever, ScopedRetriever)
        and container.retriever.knowledge(principal.scopes)
    ):
        status = "尚未录入产品资料（结构化目录）；可查询参考原文，现行有效性待确认。"
    if jiahui and principal is None:
        status = "请登录后查看产品资料"
    caps = getattr(
        container,
        "model_capabilities",
        {"llm": "未核验", "embedding": "未核验", "reranker": "未核验"},
    )

    return JSONResponse(
        {
            "mode": "jiahui" if jiahui else "ecommerce",
            "name": "嘉惠内部业务助手" if jiahui else "Adaptive RAG · 电商客服",
            "welcome": "查询食品原料产品资料、规格和业务知识；所有回答以获授权的嘉惠资料为依据。"
            if jiahui
            else "你好，我可以帮你查看商品政策、订单、物流和售后问题。",
            "examples": EXAMPLES
            if jiahui
            else ["如何申请退货？", "查询订单ORD10001", "商品有哪些规格？"],
            "product_count": count,
            "catalog_status": status,
            "capabilities": caps,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/business/{kind}")
async def read_business(
    kind: str,
    identifier: str = "",
    principal: Principal = Depends(current_principal),
    container: AppContainer = Depends(get_container),
) -> dict:
    from fastapi import HTTPException

    from app.jiahui.business import UnconnectedERP

    if container.settings.business_profile != "jiahui":
        raise HTTPException(404)
    if kind not in {"prices", "inventory", "orders"}:
        raise HTTPException(404)
    principal.require(kind)
    from typing import cast

    from app.jiahui.business import BusinessKind

    return await UnconnectedERP().query(cast(BusinessKind, kind), identifier, principal.scopes)
