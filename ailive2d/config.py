from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


@dataclass
class LlmConfig:
    base_url: str
    model: str
    timeout_sec: int = 120


@dataclass
class TtsConfig:
    voice: str
    rate: str = "+0%"
    audio_device: str = ""


@dataclass
class VtsConfig:
    host: str = "127.0.0.1"
    port: int = 8001
    plugin_name: str = "AILive2D"
    developer: str = "Yukari"
    token_path: str = ".vts_token.txt"
    takeover_tracking: bool = True
    hotkeys: dict[str, str] = field(default_factory=dict)
    expressions: dict[str, str] = field(default_factory=dict)



@dataclass
class MemoryConfig:
    enabled: bool = True
    persist_dir: str = "data/chroma"
    collection: str = "ailive2d_memories"
    embed_model: str = "nomic-embed-text"
    top_k: int = 4



@dataclass
class SafetyConfig:
    enabled: bool = True
    refuse_reply: str = ""
    refuse_emotion: str = "shy"
    rules: list[str] = field(default_factory=list)
    block_keywords: list[str] = field(default_factory=list)


@dataclass
class CharacterConfig:
    name: str
    language: str
    persona: str


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8787


@dataclass
class AuthConfig:
    enabled: bool = True
    secret: str = "change-me"
    cookie_name: str = "ailive2d_session"
    users: list[dict] = field(default_factory=list)


@dataclass
class AppConfig:
    llm: LlmConfig
    tts: TtsConfig
    vts: VtsConfig
    memory: MemoryConfig
    safety: SafetyConfig
    character: CharacterConfig
    server: ServerConfig
    auth: AuthConfig


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or CONFIG_PATH
    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    llm = raw["llm"]
    tts = raw["tts"]
    vts = raw["vts"]
    character = raw["character"]
    memory = raw.get("memory") or {}
    safety = raw.get("safety") or {}
    server = raw.get("server") or {}
    auth = raw.get("auth") or {}
    llm_base = os.environ.get("AILIVE2D_LLM_BASE_URL") or str(llm["base_url"])
    auth_enabled_env = os.environ.get("AILIVE2D_AUTH")
    return AppConfig(
        llm=LlmConfig(
            base_url=str(llm_base).rstrip("/"),
            model=str(llm["model"]),
            timeout_sec=int(llm.get("timeout_sec") or 120),
        ),
        tts=TtsConfig(
            voice=str(tts["voice"]),
            rate=str(tts.get("rate") or "+0%"),
            audio_device=str(tts.get("audio_device") or ""),
        ),
        vts=VtsConfig(
            host=str(vts.get("host") or "127.0.0.1"),
            port=int(vts.get("port") or 8001),
            plugin_name=str(vts.get("plugin_name") or "AILive2D"),
            developer=str(vts.get("developer") or "Yukari"),
            token_path=str(vts.get("token_path") or ".vts_token.txt"),
            takeover_tracking=bool(vts.get("takeover_tracking", True)),
            hotkeys={str(k): str(v or "") for k, v in dict(vts.get("hotkeys") or {}).items()},
            expressions={
                str(k): str(v or "") for k, v in dict(vts.get("expressions") or {}).items()
            },
        ),
        memory=MemoryConfig(
            enabled=bool(memory.get("enabled", True)),
            persist_dir=str(memory.get("persist_dir") or "data/chroma"),
            collection=str(memory.get("collection") or "ailive2d_memories"),
            embed_model=str(memory.get("embed_model") or "nomic-embed-text"),
            top_k=int(memory.get("top_k") or 4),
        ),
        safety=SafetyConfig(
            enabled=bool(safety.get("enabled", True)),
            refuse_reply=str(
                safety.get("refuse_reply")
                or "呢啲唔好講住啦，我哋傾返開心啲嘅嘢～"
            ),
            refuse_emotion=str(safety.get("refuse_emotion") or "shy"),
            rules=[str(x) for x in (safety.get("rules") or []) if str(x).strip()],
            block_keywords=[
                str(x).lower()
                for x in (safety.get("block_keywords") or [])
                if str(x).strip()
            ],
        ),
        character=CharacterConfig(
            name=str(character.get("name") or "角色"),
            language=str(character.get("language") or "zh-HK"),
            persona=str(character.get("persona") or ""),
        ),
        server=ServerConfig(
            host=str(server.get("host") or "127.0.0.1"),
            port=int(server.get("port") or 8787),
        ),
        auth=AuthConfig(
            enabled=(False if auth_enabled_env in {"0", "false", "False"} else bool(auth.get("enabled", True))),
            secret=str(auth.get("secret") or "change-me"),
            cookie_name=str(auth.get("cookie_name") or "ailive2d_session"),
            users=list(auth.get("users") or []),
        ),
    )


def save_hotkeys(hotkeys: dict[str, str], path: Path | None = None) -> None:
    _patch_config({"vts": {"hotkeys": hotkeys}}, path)


def save_tts(voice: str, rate: str, path: Path | None = None) -> None:
    _patch_config({"tts": {"voice": voice, "rate": rate}}, path)


def _patch_config(patch: dict[str, Any], path: Path | None = None) -> None:
    config_path = path or CONFIG_PATH
    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(raw.get(key), dict):
            raw[key].update(value)
        else:
            raw[key] = value
    config_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
