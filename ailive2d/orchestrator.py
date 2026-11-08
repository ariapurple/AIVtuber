from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, assert_never

from ailive2d.config import AppConfig, load_config, CONFIG_PATH
from ailive2d.llm import Emotion, LlmClient, LlmReply
from ailive2d.memory import MemoryStore
from ailive2d.tts import TtsPlayer
from ailive2d.vts_client import VtsController

_POSE_BY_EMOTION: dict[Emotion, dict[str, float]] = {
    "happy": {"MouthSmile": 0.85, "MouthOpen": 0.12, "Brows": 0.62},
    "sad": {"FaceAngleY": -8.0, "MouthSmile": 0.0, "Brows": 0.22},
    "angry": {"MouthSmile": 0.05, "Brows": 0.15, "FaceAngleZ": -4.0},
    "shy": {"FaceAngleX": 14.0, "FaceAngleY": -4.0, "MouthSmile": 0.45},
    "surprised": {
        "FaceAngleY": 8.0,
        "MouthOpen": 0.7,
        "Brows": 0.9,
        "EyeOpenLeft": 0.95,
        "EyeOpenRight": 0.95,
    },
    "wave": {"FaceAngleX": 12.0, "MouthSmile": 0.7},
    "idle": {},
}


class Orchestrator:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.llm = LlmClient(config)
        self.tts = TtsPlayer(config.tts)
        self.vts = VtsController(config)
        self.memory = MemoryStore(config)
        self.busy = False
        self.last: dict[str, Any] = {}
        self.stage = "idle"
        self.stage_detail = ""
        self.stage_started_at: str | None = None
        self._current_task: asyncio.Task[Any] | None = None
        self._cancel_requested = False
        self.train_history: list[dict[str, Any]] = []

    async def close(self) -> None:
        await self.llm.close()
        await self.vts.close()
        await self.memory.close()

    def _set_stage(self, stage: str, detail: str = "") -> None:
        self.stage = stage
        self.stage_detail = detail
        self.stage_started_at = datetime.now(timezone.utc).isoformat()

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            raise asyncio.CancelledError("cancelled by operator")

    async def cancel_pipeline(self) -> dict[str, Any]:
        """Soft-reset: stop TTS, cancel in-flight chat/demo task, clear busy."""
        self._cancel_requested = True
        stage_before = self.stage
        self.tts.stop()
        task = self._current_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.5)
            except Exception:
                pass
        self.busy = False
        self._set_stage("idle", f"已取消（先前階段：{stage_before or 'unknown'}）")
        payload = {
            "ok": True,
            "cancelled": True,
            "previous_stage": stage_before,
            "busy": self.busy,
            "stage": self.stage,
            "stage_detail": self.stage_detail,
        }
        self.last = {**self.last, "cancel": payload}
        return payload



    async def reload_config(self) -> dict[str, Any]:
        """Reload config.yaml into the live process (no downtime).

        Safe for live: persona / safety / character / llm model / tts voice.
        Auth cookie secret + chroma persist_dir + VTS host/port still need restart
        if those change.
        """
        new_cfg = load_config()
        old = self.config
        self.config = new_cfg
        await self.llm.apply_config(new_cfg)
        self.tts.apply_config(new_cfg.tts)
        self.memory.apply_config(new_cfg)
        self.vts.apply_config(new_cfg)
        changed = {
            "persona": old.character.persona != new_cfg.character.persona,
            "safety": old.safety != new_cfg.safety,
            "llm_model": old.llm.model != new_cfg.llm.model,
            "llm_base_url": old.llm.base_url != new_cfg.llm.base_url,
            "tts": (old.tts.voice, old.tts.rate) != (new_cfg.tts.voice, new_cfg.tts.rate),
            "hotkeys": old.vts.hotkeys != new_cfg.vts.hotkeys,
        }
        needs_restart = []
        if old.auth.secret != new_cfg.auth.secret:
            needs_restart.append("auth.secret")
        if old.memory.persist_dir != new_cfg.memory.persist_dir:
            needs_restart.append("memory.persist_dir")
        if (old.vts.host, old.vts.port) != (new_cfg.vts.host, new_cfg.vts.port):
            needs_restart.append("vts.host/port")
        return {
            "ok": True,
            "reloaded": True,
            "path": str(CONFIG_PATH),
            "changed": changed,
            "needs_restart_for": needs_restart,
            "character": new_cfg.character.name,
        }

    def append_train_history(self, entry: dict[str, Any], limit: int = 200) -> None:
        self.train_history.append(entry)
        if len(self.train_history) > limit:
            self.train_history = self.train_history[-limit:]

    async def connect_vts(self) -> dict[str, Any]:
        try:
            if self.vts.connected:
                await self.vts.close()
            await self.vts.connect()
            model = await self.vts.current_model()
            hotkeys = await self.vts.hotkeys()
            return {"ok": True, "model": model, "hotkeys": hotkeys}
        except Exception as exc:
            self.vts.connected = False
            self.vts.last_error = str(exc)
            return {"ok": False, "error": str(exc)}

    async def status(self) -> dict[str, Any]:
        llm: dict[str, Any]
        try:
            llm = await self.llm.ping()
        except Exception as exc:
            llm = {"ok": False, "error": str(exc)}
        vts_info: dict[str, Any] = {
            "ok": self.vts.connected,
            "error": self.vts.last_error,
        }
        if self.vts.connected:
            try:
                vts_info["model"] = await self.vts.current_model()
                vts_info["hotkeys"] = await self.vts.hotkeys()
                vts_info["tracking_takeover"] = self.vts.tracking_takeover
            except Exception as exc:
                vts_info = {"ok": False, "error": str(exc)}
                self.vts.connected = False
        return {
            "llm": llm,
            "vts": vts_info,
            "tts": {
                "voice": self.config.tts.voice,
                "rate": self.config.tts.rate,
                "audio_device": self.config.tts.audio_device or "系統預設",
                "devices": await asyncio.to_thread(self.tts.devices),
                "voices": self.tts.voices(),
            },
            "hotkey_map": self.config.vts.hotkeys,
            "memory": self.memory.status(),
            "busy": self.busy,
            "pipeline": {
                "busy": self.busy,
                "stage": self.stage,
                "detail": self.stage_detail,
                "started_at": self.stage_started_at,
                "cancel_requested": self._cancel_requested,
                "has_task": self._current_task is not None and not self._current_task.done(),
            },
            "character": {
                "name": self.config.character.name,
                "language": self.config.character.language,
            },
            "last": self.last,
        }

    async def speak_only(self, text: str) -> None:
        await self.tts.speak(text)

    def set_tts(self, voice: str, rate: str) -> dict[str, Any]:
        self.config.tts.voice = voice
        self.config.tts.rate = rate
        return {"ok": True, "voice": voice, "rate": rate}

    async def trigger_hotkey(self, name: str) -> dict[str, Any]:
        if not self.vts.connected:
            return {"ok": False, "error": "VTS 未連線"}
        return await self.vts.trigger(name)

    async def set_tracking(self, enabled: bool) -> dict[str, Any]:
        if not self.vts.connected:
            return {"ok": False, "error": "VTS 未連線"}
        if enabled:
            await self.vts.start_takeover()
        else:
            await self.vts.stop_takeover()
        return {"ok": True, "tracking_takeover": self.vts.tracking_takeover}

    async def demo(self) -> dict[str, Any]:
        if not self.vts.connected:
            return {"ok": False, "error": "VTS 未連線"}
        if self.busy:
            return {"ok": False, "error": "忙碌中，請稍候"}
        self.busy = True
        self._cancel_requested = False
        self.train_history: list[dict[str, Any]] = []
        self._set_stage("demo", "表情動作演示")
        steps: list[str] = []
        try:
            await self.vts.start_takeover()
            self.vts.reset_pose()
            await self.vts.trigger("Reset")
            steps.append("接管鏡頭追蹤")
            speak_task = asyncio.create_task(self.tts.speak("我而家自己郁，換吓表情俾你睇。"))

            self.vts.set_pose(FaceAngleX=-18.0, MouthSmile=0.4)
            await asyncio.sleep(0.9)
            self.vts.set_pose(FaceAngleX=18.0)
            await asyncio.sleep(0.9)
            self.vts.set_pose(FaceAngleX=0.0, FaceAngleY=-10.0)
            await asyncio.sleep(0.55)
            self.vts.set_pose(FaceAngleY=8.0)
            await asyncio.sleep(0.45)
            self.vts.reset_pose()
            steps.append("轉頭同點頭")

            await self.vts.set_expression("心心眼.exp3.json", True)
            self.vts.set_pose(MouthSmile=0.9, Brows=0.65)
            steps.append("心心眼")
            await asyncio.sleep(2.2)

            await self.vts.trigger("Reset")
            await self.vts.set_expression("黑面.exp3.json", True)
            self.vts.set_pose(MouthSmile=0.0, Brows=0.12, FaceAngleZ=-5.0)
            steps.append("黑面")
            await asyncio.sleep(2.0)

            await self.vts.trigger("Reset")
            self.vts.reset_pose()
            await self.vts.move_model(rot=12.0, time_sec=0.45)
            await asyncio.sleep(0.5)
            await self.vts.move_model(rot=-24.0, time_sec=0.55)
            await asyncio.sleep(0.6)
            await self.vts.move_model(rot=12.0, time_sec=0.45)
            steps.append("身體搖擺")
            await speak_task
            payload = {"ok": True, "steps": steps}
            self.last = payload
            self._set_stage("idle", "演示完成")
            return payload
        except asyncio.CancelledError:
            self.tts.stop()
            self._set_stage("idle", "演示已取消")
            return {"ok": False, "cancelled": True, "error": "已取消"}
        finally:
            self.busy = False

    async def handle_chat(self, text: str, author: str = "觀眾", *, speak: bool = True, animate: bool = True) -> dict[str, Any]:
        if self.busy:
            return {"ok": False, "error": "忙碌中，請稍候", "stage": self.stage}
        self.busy = True
        self._cancel_requested = False
        self.train_history: list[dict[str, Any]] = []
        self._set_stage("recall", "召回長期記憶")
        try:
            await self.memory.ensure_seed_facts(
                [
                    f"角色名字叫 {self.config.character.name}，用 {self.config.character.language} 同觀眾傾偈，吃貨、懶洋洋。",
                    self.config.character.persona.strip()[:500],
                ]
            )
            self._raise_if_cancelled()
            hits = await self.memory.recall(text, n=self.config.memory.top_k)
            memory_block = self.memory.format_for_prompt(hits)
            self._raise_if_cancelled()
            self._set_stage("llm", "等 Ollama 出字")
            reply = await self.llm.reply(text, author=author, memories=memory_block)
            self._raise_if_cancelled()
            if animate:
                self._set_stage("vts", f"表情 {reply.emotion}/{reply.motion}")
                await self._apply_actions(reply)
                self._raise_if_cancelled()
            if speak:
                self._set_stage("tts", "播語音")
                await self.tts.speak(reply.text)
                self._raise_if_cancelled()
            self._set_stage("store", "寫入記憶")
            await self.memory.add(
                f"{author}: {text}\n{self.config.character.name}: {reply.text}",
                kind="chat",
                author=author,
                metadata={"emotion": reply.emotion, "motion": reply.motion},
            )
            payload = {
                "ok": True,
                "author": author,
                "user_text": text,
                "reply": reply.text,
                "emotion": reply.emotion,
                "motion": reply.motion,
                "memories_used": [
                    {
                        "text": hit.text,
                        "kind": hit.kind,
                        "author": hit.author,
                        "distance": hit.distance,
                    }
                    for hit in hits
                ],
            }
            self.last = payload
            self._set_stage("idle", "完成")
            return payload
        except asyncio.CancelledError:
            self.tts.stop()
            payload = {
                "ok": False,
                "cancelled": True,
                "error": "已取消",
                "stage": self.stage,
            }
            self.last = payload
            self._set_stage("idle", "已取消")
            return payload
        finally:
            self.busy = False
            if self.stage != "idle":
                self._set_stage("idle", self.stage_detail or "")

    async def _apply_actions(self, reply: LlmReply) -> None:
        if not self.vts.connected:
            return
        try:
            await self.vts.trigger("Reset")
        except Exception:
            pass
        self.vts.reset_pose()
        pose = dict(_POSE_BY_EMOTION[reply.emotion])
        pose.update(_POSE_BY_EMOTION[reply.motion])
        if pose:
            self.vts.set_pose(**pose)
        expression = _mapped_value(self.config.vts.expressions, reply.emotion)
        skip: set[str] = {"Reset"}
        if expression:
            skip.add(expression.replace(".exp3.json", ""))
            try:
                await self.vts.set_expression(expression, True)
            except Exception:
                pass
        names = [
            _mapped_hotkey(self.config.vts.hotkeys, reply.motion),
            _mapped_hotkey(self.config.vts.hotkeys, reply.emotion),
        ]
        seen = set(skip)
        for name in names:
            if not name or name in seen:
                continue
            seen.add(name)
            try:
                await self.vts.trigger(name)
                await asyncio.sleep(0.05)
            except Exception:
                continue


def _mapped_hotkey(mapping: dict[str, str], emotion: Emotion) -> str:
    return _mapped_value(mapping, emotion)


def _mapped_value(mapping: dict[str, str], emotion: Emotion) -> str:
    match emotion:
        case "happy":
            return mapping.get("happy") or ""
        case "sad":
            return mapping.get("sad") or ""
        case "angry":
            return mapping.get("angry") or ""
        case "shy":
            return mapping.get("shy") or ""
        case "surprised":
            return mapping.get("surprised") or ""
        case "wave":
            return mapping.get("wave") or ""
        case "idle":
            return mapping.get("idle") or ""
        case _:
            assert_never(emotion)
