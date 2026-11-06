"""Admin-only desktop skill management and non-persistent draft previews."""

from __future__ import annotations

from typing import Literal

from fastapi import Depends, HTTPException, Request, Response
from pydantic import Field

from app.jiahui.graph import build_jiahui_graph
from app.jiahui.skill_templates import (
    SkillStore,
    SkillTemplates,
    StrictModel,
    TemplateConflict,
    apply_skill_instructions,
)
from app.prompts.jiahui import GENERAL_CHAT_SYSTEM, KNOWLEDGE_SYSTEM, WEB_SEARCH_SYSTEM

SkillName = Literal["product_lookup", "knowledge_qa", "general_chat", "web_search"]


class TemplateUpdate(StrictModel):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    templates: SkillTemplates


class TemplateRestore(StrictModel):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class TemplatePreview(StrictModel):
    templates: SkillTemplates
    skill: SkillName
    mode: Literal["rules", "answer"] = "rules"
    question: str = Field(default="你好", min_length=1, max_length=4000)
    web_search: bool = False


def register_skill_routes(router, admin, data_dir):
    store = SkillStore(data_dir)

    @router.get("/skills")
    async def read(response: Response, principal=Depends(admin)):
        response.headers["Cache-Control"] = "no-store"
        try:
            return {**store.inspect(), "history": store.versions()}
        except (ValueError, OSError):
            raise HTTPException(400, detail="模板读取失败，请核对本机模板文件与备份") from None

    @router.put("/skills")
    async def save(payload: TemplateUpdate, response: Response, principal=Depends(admin)):
        response.headers["Cache-Control"] = "no-store"
        try:
            return {
                **store.save(payload.templates, payload.expected_revision, principal.username),
                "history": store.versions(),
            }
        except TemplateConflict as exc:
            raise HTTPException(409, detail=str(exc)) from None
        except ValueError:
            raise HTTPException(
                400, detail="模板内容过长或文件格式异常，请缩短表达要求并核对备份"
            ) from None
        except OSError:
            raise HTTPException(400, detail="模板保存未完成，请检查文件权限或备份") from None

    @router.post("/skills/restore")
    async def restore(payload: TemplateRestore, response: Response, principal=Depends(admin)):
        response.headers["Cache-Control"] = "no-store"
        try:
            return {
                **store.restore(payload.revision, payload.expected_revision, principal.username),
                "history": store.versions(),
            }
        except TemplateConflict as exc:
            raise HTTPException(409, detail=str(exc)) from None
        except (ValueError, OSError):
            raise HTTPException(400, detail="无法恢复该版本，请检查是否存在、完整且有效") from None

    @router.post("/skills/preview")
    async def preview(
        payload: TemplatePreview, request: Request, response: Response, principal=Depends(admin)
    ):
        response.headers["Cache-Control"] = "no-store"
        bases = {
            "general_chat": GENERAL_CHAT_SYSTEM,
            "knowledge_qa": KNOWLEDGE_SYSTEM,
            "web_search": WEB_SEARCH_SYSTEM,
        }
        template = getattr(payload.templates, payload.skill)
        rules = (
            template.model_dump()
            if payload.skill == "product_lookup"
            else apply_skill_instructions(bases[payload.skill], template.instructions)
        )
        if payload.skill == "knowledge_qa":
            rules = {"prompt": rules, "layout": payload.templates.knowledge_qa.layout}
        if payload.mode == "rules":
            return {"saved": False, "mode": "rules", "rules": rules}
        if payload.skill == "web_search" and not payload.web_search:
            raise HTTPException(400, detail="联网测试需明确勾选允许本次联网；规则预览不联网")
        if payload.web_search and payload.skill != "web_search":
            raise HTTPException(400, detail="只有联网搜索模板可启用联网测试")
        container = request.app.state.container
        await container.chat_service._enforce_rate_limit(principal.user_id)
        graph = build_jiahui_graph(
            llm=container.llm,
            settings=container.settings,
            retriever=container.retriever,
            reranker=container.chat_service._reranker,
            registry=container.chat_service.registry,
            template_override=payload.templates,
        )
        result = await graph.ainvoke(
            {
                "user_query": payload.question,
                "messages": [],
                "extra": {"scopes": list(principal.scopes), "web_search": payload.web_search},
            }
        )
        return {
            "saved": False,
            "mode": "answer",
            "answer": result.get("final_answer", ""),
            "citations": [c.model_dump(mode="json") for c in result.get("citations", [])],
            "refused": bool(result.get("refused")),
            "capabilities": container.model_capabilities,
        }
