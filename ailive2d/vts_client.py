from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path
from typing import Any

import pyvts

from ailive2d.config import AppConfig, ROOT

IDLE_PARAMS: dict[str, float] = {
    "FaceAngleX": 0.0,
    "FaceAngleY": 0.0,
    "FaceAngleZ": 0.0,
    "FacePositionX": 0.0,
    "FacePositionY": 0.0,
    "FacePositionZ": 0.0,
    "EyeOpenLeft": 0.75,
    "EyeOpenRight": 0.75,
    "EyeLeftX": 0.0,
    "EyeLeftY": 0.0,
    "EyeRightX": 0.0,
    "EyeRightY": 0.0,
    "MouthOpen": 0.0,
    "MouthSmile": 0.28,
    "Brows": 0.5,
    "BrowLeftY": 0.5,
    "BrowRightY": 0.5,
}


class VtsController:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        token_path = Path(config.vts.token_path)
        if not token_path.is_absolute():
            token_path = ROOT / token_path
        self._vts = pyvts.vts(
            plugin_info={
                "plugin_name": config.vts.plugin_name,
                "developer": config.vts.developer,
                "authentication_token_path": str(token_path),
            },
            vts_api_info={
                "version": "1.0",
                "name": "VTubeStudioPublicAPI",
                "host": config.vts.host,
                "port": config.vts.port,
            },
        )
        self.connected = False
        self.last_error = ""
        self.tracking_takeover = bool(config.vts.takeover_tracking)
        self._lock = asyncio.Lock()
        self._loop_task: asyncio.Task[None] | None = None
        self._pose: dict[str, float] = dict(IDLE_PARAMS)
        self._sway = True

    async def connect(self) -> None:
        await self._vts.connect()
        await self._vts.request_authenticate_token()
        auth = await self._vts.request(
            self._vts.vts_request.authentication(self._vts.authentic_token)
        )
        data = auth.get("data") or {}
        if not data.get("authenticated"):
            message = str(data.get("reason") or data.get("message") or auth)
            raise RuntimeError(f"VTS 尚未授權插件，請在 VTube Studio 彈窗按允許。{message}")
        self.connected = True
        self.last_error = ""
        if self.tracking_takeover:
            await self.start_takeover()

    async def close(self) -> None:
        await self.stop_takeover()
        self.connected = False
        try:
            await self._vts.close()
        except Exception:
            pass

    async def start_takeover(self) -> None:
        self.tracking_takeover = True
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._tracking_loop())

    async def stop_takeover(self) -> None:
        self.tracking_takeover = False
        task = self._loop_task
        self._loop_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def set_pose(self, **values: float) -> None:
        for key, value in values.items():
            self._pose[key] = float(value)

    def reset_pose(self) -> None:
        self._pose = dict(IDLE_PARAMS)
        self._sway = True

    async def hotkeys(self) -> list[dict[str, str]]:
        response = await self._request(self._vts.vts_request.requestHotKeyList())
        items = response.get("data", {}).get("availableHotkeys") or []
        result: list[dict[str, str]] = []
        for item in items:
            result.append(
                {
                    "name": str(item.get("name") or ""),
                    "hotkeyID": str(item.get("hotkeyID") or ""),
                    "type": str(item.get("type") or ""),
                    "file": str(item.get("file") or ""),
                }
            )
        return result

    async def trigger(self, name: str) -> dict[str, Any]:
        if not name.strip():
            return {"ok": False, "error": "empty hotkey"}
        await self._request(self._vts.vts_request.requestTriggerHotKey(name.strip()))
        return {"ok": True, "hotkey": name}

    async def set_expression(self, file_name: str, active: bool, fade: float = 0.35) -> dict[str, Any]:
        if not file_name.strip():
            return {"ok": False, "error": "empty expression"}
        response = await self._request(
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "ailive2d-exp",
                "messageType": "ExpressionActivationRequest",
                "data": {
                    "expressionFile": file_name.strip(),
                    "fadeTime": fade,
                    "active": active,
                },
            }
        )
        if response.get("messageType") == "APIError":
            return {"ok": False, "error": str((response.get("data") or {}).get("message") or response)}
        return {"ok": True, "file": file_name, "active": active}

    async def move_model(
        self,
        *,
        x: float = 0.0,
        y: float = 0.0,
        rot: float = 0.0,
        time_sec: float = 0.55,
        relative: bool = True,
    ) -> dict[str, Any]:
        response = await self._request(
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "ailive2d-move",
                "messageType": "MoveModelRequest",
                "data": {
                    "timeInSeconds": time_sec,
                    "valuesAreRelativeToModel": relative,
                    "positionX": x,
                    "positionY": y,
                    "rotation": rot,
                },
            }
        )
        if response.get("messageType") == "APIError":
            return {"ok": False, "error": str((response.get("data") or {}).get("message") or response)}
        return {"ok": True}

    async def current_model(self) -> dict[str, Any]:
        response = await self._request(
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "ailive2d-model",
                "messageType": "CurrentModelRequest",
                "data": {},
            }
        )
        data = response.get("data") or {}
        return {
            "loaded": bool(data.get("modelLoaded")),
            "name": str(data.get("modelName") or ""),
            "id": str(data.get("modelID") or ""),
        }

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            result = await self._vts.request(payload)
        return result if isinstance(result, dict) else {}

    async def _tracking_loop(self) -> None:
        blink_until = 0.0
        next_blink = time.monotonic() + 3.2
        while self.tracking_takeover and self.connected:
            now = time.monotonic()
            values = dict(self._pose)
            if self._sway:
                values["FaceAngleX"] = values.get("FaceAngleX", 0.0) + 5.5 * math.sin(now * 0.45)
                values["FaceAngleY"] = values.get("FaceAngleY", 0.0) + 2.4 * math.sin(now * 0.62)
                values["FaceAngleZ"] = values.get("FaceAngleZ", 0.0) + 2.0 * math.sin(now * 0.31)
            if now >= next_blink:
                blink_until = now + 0.12
                next_blink = now + 3.6
            if now < blink_until:
                values["EyeOpenLeft"] = 0.0
                values["EyeOpenRight"] = 0.0
            names = list(values.keys())
            nums = [float(values[name]) for name in names]
            try:
                await self._request(
                    self._vts.vts_request.requestSetMultiParameterValue(
                        names,
                        nums,
                        weight=1,
                        face_found=True,
                        mode="set",
                    )
                )
            except Exception as exc:
                self.last_error = str(exc)
            await asyncio.sleep(0.12)
