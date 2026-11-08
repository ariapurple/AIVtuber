from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import edge_tts
import pygame

try:
    import pygame._sdl2.audio as sdl_audio
except Exception:
    sdl_audio = None

from ailive2d.config import TtsConfig

VOICES: list[dict[str, str]] = [
    {"id": "zh-HK-HiuMaanNeural", "label": "粵語 · 曉曼（女）"},
    {"id": "zh-HK-HiuGaaiNeural", "label": "粵語 · 曉佳（女）"},
    {"id": "zh-HK-WanLungNeural", "label": "粵語 · 雲龍（男）"},
    {"id": "zh-TW-HsiaoChenNeural", "label": "國語 · 曉臻（女）"},
    {"id": "zh-TW-HsiaoYuNeural", "label": "國語 · 曉雨（女）"},
    {"id": "zh-TW-YunJheNeural", "label": "國語 · 雲哲（男）"},
    {"id": "zh-CN-XiaoxiaoNeural", "label": "普通話 · 曉曉（女）"},
    {"id": "zh-CN-XiaoyiNeural", "label": "普通話 · 曉伊（女）"},
    {"id": "zh-CN-YunxiNeural", "label": "普通話 · 雲希（男）"},
    {"id": "zh-CN-YunyangNeural", "label": "普通話 · 雲揚（男）"},
    {"id": "ja-JP-NanamiNeural", "label": "日本語 · 七海（女）"},
    {"id": "en-US-AriaNeural", "label": "English · Aria（女）"},
]


class TtsPlayer:
    def __init__(self, config: TtsConfig) -> None:
        self._config = config

    def apply_config(self, config) -> None:
        """Hot-swap voice/rate/device without restarting."""
        self._config = config
        self._ready = False
        self._devices: list[str] | None = None
        self._cancel_play = False

    def devices(self) -> list[str]:
        if self._devices is not None:
            return self._devices
        try:
            pygame.mixer.pre_init()
            pygame.init()
            if sdl_audio is None:
                self._devices = []
            else:
                self._devices = list(sdl_audio.get_audio_device_names(False))
        except Exception:
            self._devices = []
        return self._devices

    def voices(self) -> list[dict[str, str]]:
        return list(VOICES)

    def _ensure_mixer(self) -> None:
        if self._ready and pygame.mixer.get_init():
            return
        pygame.mixer.quit()
        if self._config.audio_device:
            pygame.mixer.init(devicename=self._config.audio_device)
        else:
            pygame.mixer.init()
        self._ready = True

    def stop(self) -> None:
        """Interrupt current playback if any."""
        self._cancel_play = True
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass

    async def speak(self, text: str) -> None:
        if not text.strip():
            return
        self._cancel_play = False
        communicate = edge_tts.Communicate(text, self._config.voice, rate=self._config.rate)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            await communicate.save(str(temp_path))
            await asyncio.to_thread(self._play_file, temp_path)
        finally:
            await asyncio.to_thread(_remove_with_retry, temp_path)

    def _play_file(self, path: Path) -> None:
        self._ensure_mixer()
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if self._cancel_play:
                pygame.mixer.music.stop()
                break
            pygame.time.wait(50)
        try:
            pygame.mixer.music.unload()
        except Exception:
            pygame.mixer.music.stop()


def _remove_with_retry(path: Path) -> None:
    for _ in range(8):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            pygame.time.wait(80)
    path.unlink(missing_ok=True)
