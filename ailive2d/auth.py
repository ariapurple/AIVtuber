# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException, Request

Role = Literal["admin", "trainer"]


@dataclass
class User:
    username: str
    role: Role
    password_hash: str
    salt: str


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
    ).hex()
    return digest, salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, password_hash)


class AuthRegistry:
    def __init__(self, users: list[User], enabled: bool = True) -> None:
        self.enabled = enabled
        self._users = {u.username: u for u in users}

    def authenticate(self, username: str, password: str) -> User | None:
        user = self._users.get(username)
        if user is None:
            return None
        if not verify_password(password, user.password_hash, user.salt):
            return None
        return user

    def get(self, username: str) -> User | None:
        return self._users.get(username)


def load_users_from_config(raw: dict[str, Any]) -> list[User]:
    users: list[User] = []
    for item in raw.get("users") or []:
        username = str(item.get("username") or "").strip()
        role = str(item.get("role") or "trainer").strip()
        if role not in ("admin", "trainer"):
            role = "trainer"
        if not username:
            continue
        if item.get("password_hash") and item.get("salt"):
            users.append(
                User(
                    username=username,
                    role=role,  # type: ignore[arg-type]
                    password_hash=str(item["password_hash"]),
                    salt=str(item["salt"]),
                )
            )
        else:
            password = str(item.get("password") or "")
            digest, salt = hash_password(password)
            users.append(
                User(
                    username=username,
                    role=role,  # type: ignore[arg-type]
                    password_hash=digest,
                    salt=salt,
                )
            )
    return users


def current_user(request: Request) -> dict[str, str] | None:
    return request.session.get("user")


def require_user(request: Request) -> dict[str, str]:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登入")
    return user


def require_admin(request: Request) -> dict[str, str]:
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要 admin")
    return user


def require_roles(request: Request, *roles: str) -> dict[str, str]:
    user = require_user(request)
    if user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="權限不足")
    return user
