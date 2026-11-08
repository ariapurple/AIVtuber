from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from ailive2d.auth import AuthRegistry, load_users_from_config, require_admin, require_roles, require_user
from ailive2d.config import ROOT, load_config, save_hotkeys, save_tts
from ailive2d.orchestrator import Orchestrator

WEB_DIR = ROOT / "web"
orchestrator: Orchestrator | None = None
auth_registry: AuthRegistry | None = None
AUTH_ENABLED = True


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    author: str = "觀眾"


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class HotkeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class HotkeyMapRequest(BaseModel):
    hotkeys: dict[str, str]


class TrackingRequest(BaseModel):
    enabled: bool


class TtsSettingsRequest(BaseModel):
    voice: str = Field(min_length=3, max_length=80)
    rate: str = Field(default="+8%", max_length=12)
    preview: bool = True


class MemoryFactRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    author: str = "system"


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    n: int = Field(default=4, ge=1, le=20)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TrainChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    author: str = ""


PUBLIC_PATHS = {
    "/login",
    "/api/login",
    "/api/health",
}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global orchestrator, auth_registry, AUTH_ENABLED
    config = load_config()
    AUTH_ENABLED = bool(config.auth.enabled)
    users = load_users_from_config({"users": config.auth.users})
    auth_registry = AuthRegistry(users, enabled=AUTH_ENABLED)
    orchestrator = Orchestrator(config)
    # VTS connect is best-effort; training chat works without it.
    asyncio.create_task(orchestrator.connect_vts())
    yield
    await orchestrator.close()


app = FastAPI(title="AILive2D", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

_boot = load_config()
_secret = os.environ.get("AILIVE2D_SECRET") or _boot.auth.secret


def _orch() -> Orchestrator:
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="not ready")
    return orchestrator


def _auth() -> AuthRegistry:
    if auth_registry is None:
        raise HTTPException(status_code=503, detail="auth not ready")
    return auth_registry


@app.middleware("http")
async def auth_gate(request: Request, call_next: Callable):
    path = request.url.path
    if path.startswith("/static"):
        return await call_next(request)
    if path in PUBLIC_PATHS:
        return await call_next(request)
    if not AUTH_ENABLED:
        return await call_next(request)
    user = request.session.get("user")
    if not user:
        if path.startswith("/api/"):
            return JSONResponse({"ok": False, "error": "未登入"}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)
    # trainer: only training + me/logout + health pages
    if user.get("role") == "trainer":
        allowed_prefix = (
            "/train",
            "/api/train",
            "/api/me",
            "/api/logout",
            "/api/health",
        )
        if path == "/" or path == "/index.html":
            return RedirectResponse(url="/train", status_code=302)
        if not any(path == p or path.startswith(p + "/") or path.startswith(p) for p in allowed_prefix if p):
            # exact allow list
            if path not in {"/train", "/api/me", "/api/logout", "/api/health"} and not path.startswith("/api/train"):
                if path.startswith("/api/"):
                    return JSONResponse({"ok": False, "error": "trainer 只能用訓練 chatroom"}, status_code=403)
                return RedirectResponse(url="/train", status_code=302)
    return await call_next(request)


# SessionMiddleware must be added LAST so it wraps auth_gate (outermost).
app.add_middleware(
    SessionMiddleware,
    secret_key=_secret,
    session_cookie=_boot.auth.cookie_name or "ailive2d_session",
    same_site="lax",
    https_only=False,
    max_age=60 * 60 * 24 * 14,
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "auth_enabled": AUTH_ENABLED}


@app.get("/login")
async def login_page(request: Request) -> FileResponse:
    if request.session.get("user"):
        role = request.session["user"].get("role")
        # browser will follow; API clients ignore
    return FileResponse(WEB_DIR / "login.html")


@app.post("/api/login")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    user = _auth().authenticate(body.username.strip(), body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    request.session["user"] = {"username": user.username, "role": user.role}
    redirect = "/train" if user.role == "trainer" else "/"
    return {"ok": True, "username": user.username, "role": user.role, "redirect": redirect}


@app.post("/api/logout")
async def logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request) -> dict[str, Any]:
    if not AUTH_ENABLED:
        return {"ok": True, "auth_enabled": False, "user": {"username": "local", "role": "admin"}}
    user = require_user(request)
    return {"ok": True, "auth_enabled": True, "user": user}


@app.get("/")
async def index(request: Request) -> FileResponse:
    require_admin(request) if AUTH_ENABLED else None
    return FileResponse(WEB_DIR / "index.html")


@app.get("/train")
async def train_page(request: Request) -> FileResponse:
    if AUTH_ENABLED:
        require_roles(request, "admin", "trainer")
    return FileResponse(WEB_DIR / "train.html")


@app.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    return await _orch().status()


@app.post("/api/vts/connect")
async def vts_connect(request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    return await _orch().connect_vts()


@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    orch = _orch()
    if orch.busy:
        return {"ok": False, "error": "忙碌中，請稍候", "stage": orch.stage}
    task = asyncio.create_task(orch.handle_chat(body.text, author=body.author))
    orch._current_task = task
    try:
        return await task
    finally:
        if orch._current_task is task:
            orch._current_task = None


@app.post("/api/train/chat")
async def train_chat(body: TrainChatRequest, request: Request) -> dict[str, Any]:
    """Text-only training chatroom: LLM + memory, no TTS / VTS."""
    user = require_roles(request, "admin", "trainer") if AUTH_ENABLED else {"username": "local", "role": "admin"}
    orch = _orch()
    author = (body.author or user.get("username") or "trainer").strip() or "trainer"
    if orch.busy:
        return {"ok": False, "error": "忙碌中，請稍候", "stage": orch.stage}
    task = asyncio.create_task(
        orch.handle_chat(body.text, author=author, speak=False, animate=False)
    )
    orch._current_task = task
    try:
        result = await task
    finally:
        if orch._current_task is task:
            orch._current_task = None
    if result.get("ok"):
        orch.append_train_history(
            {
                "author": author,
                "user_text": body.text,
                "reply": result.get("reply"),
                "emotion": result.get("emotion"),
                "role_user": user.get("username"),
            }
        )
    return result


@app.get("/api/train/history")
async def train_history(request: Request) -> dict[str, Any]:
    require_roles(request, "admin", "trainer") if AUTH_ENABLED else None
    return {"ok": True, "messages": list(_orch().train_history[-100:])}


@app.post("/api/cancel")
async def cancel(request: Request) -> dict[str, Any]:
    require_roles(request, "admin", "trainer") if AUTH_ENABLED else None
    return await _orch().cancel_pipeline()


@app.post("/api/speak")
async def speak(body: SpeakRequest, request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    try:
        orch = _orch()
        orch._set_stage("tts", "只播語音")
        await orch.speak_only(body.text)
        orch._set_stage("idle", "播完")
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/hotkey")
async def hotkey(body: HotkeyRequest, request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    return await _orch().trigger_hotkey(body.name)


@app.post("/api/hotkeys")
async def save_map(body: HotkeyMapRequest, request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    save_hotkeys(body.hotkeys)
    _orch().config.vts.hotkeys = body.hotkeys
    return {"ok": True, "hotkeys": body.hotkeys}


@app.post("/api/vts/tracking")
async def tracking(body: TrackingRequest, request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    return await _orch().set_tracking(body.enabled)


@app.post("/api/demo")
async def demo(request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    orch = _orch()
    if orch.busy:
        return {"ok": False, "error": "忙碌中，請稍候", "stage": orch.stage}
    task = asyncio.create_task(orch.demo())
    orch._current_task = task
    try:
        return await task
    finally:
        if orch._current_task is task:
            orch._current_task = None


@app.post("/api/tts")
async def tts_settings(body: TtsSettingsRequest, request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    orch = _orch()
    known = {item["id"] for item in orch.tts.voices()}
    if body.voice not in known:
        raise HTTPException(status_code=400, detail="未知聲線")
    if not body.rate.startswith(("+", "-")) or not body.rate.endswith("%"):
        raise HTTPException(status_code=400, detail="語速格式應為 +8% 或 -10%")
    save_tts(body.voice, body.rate)
    result = orch.set_tts(body.voice, body.rate)
    if body.preview:
        await orch.speak_only("哈囉，我而家用呢把聲同大家傾偈。")
    return result


@app.get("/api/memory/status")
async def memory_status(request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    return _orch().memory.status()


@app.post("/api/memory/fact")
async def memory_fact(body: MemoryFactRequest, request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    doc_id = await _orch().memory.add(body.text, kind="fact", author=body.author)
    return {"ok": True, "id": doc_id, "count": _orch().memory.count()}


@app.post("/api/memory/search")
async def memory_search(body: MemorySearchRequest, request: Request) -> dict[str, Any]:
    require_admin(request) if AUTH_ENABLED else None
    hits = await _orch().memory.recall(body.query, n=body.n)
    return {
        "ok": True,
        "hits": [
            {
                "text": hit.text,
                "kind": hit.kind,
                "author": hit.author,
                "distance": hit.distance,
            }
            for hit in hits
        ],
    }


def main() -> None:
    config = load_config()
    host = os.environ.get("AILIVE2D_HOST") or config.server.host
    port = int(os.environ.get("AILIVE2D_PORT") or config.server.port)
    uvicorn.run(
        "ailive2d.server:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
