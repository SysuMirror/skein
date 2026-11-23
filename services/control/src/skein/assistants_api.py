"""管理接口：人（账号与角色）与助手凭证。给命令行（``python -m skein user …``）和控制台用。

都挂在 /internal 下，要主令牌或相应角色的会话。写操作全部记审计：谁、对谁、改了什么。
目录层用 ValueError / LookupError 表达「输入不对 / 找不到」，这里翻成 400 / 404。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from skein import access
from skein.accounts import ROLES, Directory
from skein.assistants import AssistantStore


class UserIn(BaseModel):
    email: str = ""
    name: str = ""
    role: str = "member"
    # 给了就能本地登录
    password: str = ""
    # 预先给一个外部身份留位（provider + subject 成对出现）
    provider: str = ""
    subject: str = ""


class UserPatch(BaseModel):
    role: str | None = None
    disabled: bool | None = None


class PasswordIn(BaseModel):
    password: str


class IdentityIn(BaseModel):
    provider: str = Field(min_length=1)
    subject: str = Field(min_length=1)


class AssistantIn(BaseModel):
    name: str = Field(min_length=1)
    purpose: str = ""
    callback_url: str = ""


class AssistantPatch(BaseModel):
    purpose: str | None = None
    # 空串 = 清掉回调
    callback_url: str | None = None
    # 配了回调地址时是否顺手换一把签名密钥
    new_callback_secret: bool = False


def _guard(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return call(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {', '.join(ROLES)}")


def _user_view(directory: Directory, sub: str) -> dict:
    user = directory.get(sub)
    assert user is not None
    return {**user.as_dict(), "identities": directory.identities(sub)}


def create_manage_router(
    directory: Directory,
    assistants: AssistantStore,
    require: Callable[..., Any],
) -> APIRouter:
    """``require`` 是 create_app 里的权限依赖工厂。"""
    router = APIRouter(prefix="/internal")
    read_users = require(access.USER_READ)
    manage_users = require(access.USER_MANAGE)
    manage_assistants = require(access.ASSISTANT_MANAGE)

    # ---------------- 人 ----------------

    @router.get("/users")
    def list_users(_: Any = read_users) -> dict:
        return {
            "users": [_user_view(directory, user.sub) for user in directory.list()]
        }

    @router.post("/users", status_code=201)
    def create_user(body: UserIn, principal: Any = manage_users) -> dict:
        _check_role(body.role)
        user = _guard(
            directory.create,
            email=body.email,
            name=body.name,
            role=body.role,
            password=body.password,
            provider=body.provider,
            subject=body.subject,
            actor=principal.label,
        )
        return _user_view(directory, user.sub)

    @router.patch("/users/{sub}")
    def patch_user(sub: str, body: UserPatch, principal: Any = manage_users) -> dict:
        if body.role is not None:
            _check_role(body.role)
            _guard(directory.set_role, sub, body.role, actor=principal.label)
        if body.disabled is not None:
            _guard(directory.set_disabled, sub, body.disabled, actor=principal.label)
        return _user_view(directory, sub)

    @router.post("/users/{sub}/password")
    def set_password(sub: str, body: PasswordIn, principal: Any = manage_users) -> dict:
        _guard(directory.set_password, sub, body.password, actor=principal.label)
        return _user_view(directory, sub)

    @router.post("/users/{sub}/identities")
    def link_identity(sub: str, body: IdentityIn, principal: Any = manage_users) -> dict:
        _guard(directory.link, sub, body.provider, body.subject, actor=principal.label)
        return _user_view(directory, sub)

    # ---------------- 助手凭证 ----------------

    @router.get("/assistants")
    def list_assistants(_: Any = manage_assistants) -> dict:
        return {"assistants": [assistant.as_dict() for assistant in assistants.list()]}

    @router.post("/assistants", status_code=201)
    def issue_assistant(body: AssistantIn, principal: Any = manage_assistants) -> dict:
        issued = _guard(
            assistants.issue,
            body.name,
            body.purpose,
            body.callback_url,
            actor=principal.label,
        )
        return _issued_view(issued)

    @router.post("/assistants/{ref}/rotate")
    def rotate_assistant(ref: str, principal: Any = manage_assistants) -> dict:
        # 路径里可能是名字（带连字符），用 path 参数原样接
        return _issued_view(_guard(assistants.rotate, ref, actor=principal.label))

    @router.delete("/assistants/{ref}")
    def revoke_assistant(ref: str, principal: Any = manage_assistants) -> dict:
        assistant = _guard(assistants.revoke, ref, actor=principal.label)
        return {"assistant": assistant.as_dict()}

    @router.patch("/assistants/{ref}")
    def patch_assistant(
        ref: str, body: AssistantPatch, principal: Any = manage_assistants
    ) -> dict:
        secret = ""
        if body.purpose is not None:
            _guard(assistants.set_purpose, ref, body.purpose, actor=principal.label)
        if body.callback_url is not None:
            issued = _guard(
                assistants.set_callback,
                ref,
                body.callback_url,
                new_secret=body.new_callback_secret,
                actor=principal.label,
            )
            secret = issued.callback_secret
        assistant = _guard(assistants.get, ref)
        if assistant is None:
            raise HTTPException(status_code=404, detail=f"no such assistant: {ref}")
        data = {"assistant": assistant.as_dict()}
        if secret:
            data["callback_secret"] = secret
        return data

    return router


def _issued_view(issued: Any) -> dict:
    """签发类接口的响应：凭证本身 + 只出现这一次的密钥（有才带）。"""
    data = {"assistant": issued.assistant.as_dict()}
    if issued.key:
        data["key"] = issued.key
    if issued.callback_secret:
        data["callback_secret"] = issued.callback_secret
    return data
