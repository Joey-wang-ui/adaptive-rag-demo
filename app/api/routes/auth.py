"""Same-origin login and server-owned identity for the Jiahui profile."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr, field_validator

from app.jiahui.auth import (
    COOKIE_NAME,
    SESSION_SECONDS,
    Principal,
    auth_store,
    check_same_origin,
    current_principal,
    session_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip() if isinstance(value, str) else value


def require_jiahui(request: Request) -> None:
    if getattr(request.app.state.container.settings, "business_profile", "ecommerce") != "jiahui":
        raise HTTPException(status_code=404, detail="仅嘉惠模式提供账号登录")


@router.post("/login", dependencies=[Depends(require_jiahui)])
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    check_same_origin(request)
    store = auth_store(request)
    token = await asyncio.to_thread(
        store.login,
        payload.username,
        payload.password.get_secret_value(),
        request.client.host if request.client else "local",
    )
    principal = store.principal(token)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return principal.as_dict()


@router.get("/me", dependencies=[Depends(require_jiahui)])
async def me(response: Response, principal: Principal = Depends(current_principal)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return principal.as_dict()


@router.post("/logout", dependencies=[Depends(require_jiahui)])
async def logout(request: Request, response: Response) -> dict:
    check_same_origin(request)
    await asyncio.to_thread(auth_store(request).logout, session_token(request))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}
