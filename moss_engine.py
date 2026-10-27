"""MOSS-TTS-Nano 常驻推理引擎封装。

把模型加载一次放内存，后续合成只需 1-3 秒，避免每次重启进程。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

MOSS_DIR = "/AstrBot/data/moss-tts"
DEFAULT_MODEL_DIR = os.path.join(MOSS_DIR, "models", "MOSS-TTS-Nano-100M-ONNX")


_AUDIO_BACKEND: str | None = None


def _native_audio_ok() -> bool:
    """真 torchcodec + torchaudio 能不能正常解码。"""
    try:
        import torchaudio  # noqa: PLC0415
        from torchcodec.decoders import AudioDecoder  # noqa: F401,PLC0415

        return callable(torchaudio.load)
    except Exception:  # noqa: BLE001
        return False


def _patch_torchcodec() -> str:
    """torchaudio 新版走 torchcodec 解码。

    优先用官方 torchcodec（CPU 版 wheel）；只有在它真的加载不了
    （缺 ffmpeg / ABI 不匹配的镜像）时才把 torchaudio.load 换成 soundfile。
    返回实际用的后端名。
    """
    global _AUDIO_BACKEND
    if _AUDIO_BACKEND is not None:
        return _AUDIO_BACKEND

    if _native_audio_ok():
        _AUDIO_BACKEND = "torchcodec"
        return _AUDIO_BACKEND

    if "torchcodec" not in sys.modules:
        stub = types.ModuleType("torchcodec")
        stub.__path__ = []  # type: ignore[attr-defined]
        sys.modules["torchcodec"] = stub
    import soundfile as sf
    import torch
    import torchaudio

    def _load(path, **kwargs):
        data, sr = sf.read(str(path), always_2d=True)
        return torch.from_numpy(data.T).float(), sr

    torchaudio.load = _load  # type: ignore[assignment]
    _AUDIO_BACKEND = "soundfile"
    return _AUDIO_BACKEND


class MossEngine:
    """单例推理引擎。"""

    _instance: MossEngine | None = None
    _lock = threading.Lock()

    def __init__(self, model_dir: str = DEFAULT_MODEL_DIR) -> None:
        self.model_dir = model_dir
        self._rt: Any = None
        self._init_lock = threading.Lock()
        self._ref_lock = threading.Lock()
        self._ref_cache: dict[tuple, list[list[int]]] = {}
        self.load_seconds: float = 0.0

    @classmethod
    def _holder(cls):
        """把单例挂在 sys.modules 上一个假模块里。

        插件重载会重新执行本模块，模块级变量全部重置，旧实例就变成
        没人引用的孤儿，ONNX session 占的内存只能等 GC 慢慢收。
        放到 sys.modules 里可以跨重载复用同一份模型。
        """
        import sys

        name = "_moss_tts_engine_holder"
        holder = sys.modules.get(name)
        if holder is None:
            holder = types.ModuleType(name)
            holder.instance = None  # type: ignore[attr-defined]
            sys.modules[name] = holder
        return holder

    @classmethod
    def get(cls, model_dir: str = DEFAULT_MODEL_DIR) -> MossEngine:
        with cls._lock:
            holder = cls._holder()
            inst = getattr(holder, "instance", None)
            if inst is None:
                inst = MossEngine(model_dir)
                holder.instance = inst  # type: ignore[attr-defined]
            cls._instance = inst
            return inst

    @classmethod
    def reset(cls, *, release: bool = True) -> None:
        """释放单例。release=True 时顺带清空 runtime，让 ONNX 会话可被回收。"""
        import gc

        with cls._lock:
            holder = cls._holder()
            inst = getattr(holder, "instance", None)
            if inst is not None and release:
                inst.close()
            holder.instance = None  # type: ignore[attr-defined]
            cls._instance = None
        if release:
            gc.collect()
            # glibc 默认会把释放的内存留在 arena 里，RSS 降不下来。
            # 主动 trim 一次，把空闲页还给操作系统。
            try:
                import ctypes

                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        """丢弃已加载的 runtime（模型权重随 GC 释放）。"""
        self._rt = None
        with self._ref_lock:
            self._ref_cache.clear()

    def ensure_loaded(self) -> Any:
        with self._init_lock:
            if self._rt is not None:
                return self._rt
            _patch_torchcodec()
            if MOSS_DIR not in sys.path:
                sys.path.insert(0, MOSS_DIR)
            from onnx_tts_runtime import OnnxTtsRuntime  # noqa: PLC0415

            t0 = time.time()
            self._rt = OnnxTtsRuntime(model_dir=self.model_dir)
            self.load_seconds = time.time() - t0
            # synth(prompt_audio_path=...) 内部会调 encode_reference_audio，
            # 包一层让每次合成都走缓存，别重复编码参考音频。
            raw_encode = self._rt.encode_reference_audio
            self._rt.encode_reference_audio = lambda p: self._encode_cached(raw_encode, p)
            return self._rt

    def _encode_cached(self, raw_encode, wav_path: str) -> list[list[int]]:
        """参考音频编码缓存（按路径 + mtime + 大小）。编码一次约 1.5s。"""
        try:
            st = os.stat(wav_path)
            key: tuple | None = (os.path.abspath(wav_path), st.st_mtime_ns, st.st_size)
        except OSError:
            key = None
        if key is not None:
            with self._ref_lock:
                cached = self._ref_cache.get(key)
            if cached is not None:
                return cached
        codes = raw_encode(wav_path)
        if key is not None:
            with self._ref_lock:
                # 只留最后一段参考音频，够用又不占内存
                self._ref_cache.clear()
                self._ref_cache[key] = codes
        return codes

    def encode_reference(self, wav_path: str) -> list[list[int]]:
        rt = self.ensure_loaded()
        return rt.encode_reference_audio(wav_path)

    def synth(
        self,
        text: str,
        out_wav: str,
        *,
        voice: str | None = None,
        prompt_audio: str | None = None,
        audio_temperature: float | None = None,
        audio_top_p: float | None = None,
        max_new_frames: int | None = None,
        seed: int | None = None,
        enable_wetext: bool = False,
    ) -> str:
        rt = self.ensure_loaded()
        gd = rt.manifest.setdefault("generation_defaults", {})
        if audio_temperature is not None:
            gd["audio_temperature"] = float(audio_temperature)
        if audio_top_p is not None:
            gd["audio_top_p"] = float(audio_top_p)
        if max_new_frames:
            gd["max_new_frames"] = int(max_new_frames)

        kwargs: dict[str, Any] = {
            "text": text,
            "output_audio_path": out_wav,
            "enable_wetext": enable_wetext,
            "enable_normalize_tts_text": True,
        }
        if voice:
            kwargs["voice"] = voice
        if prompt_audio:
            kwargs["prompt_audio_path"] = prompt_audio
        if seed is not None:
            kwargs["seed"] = seed

        res = rt.synthesize(**kwargs)
        return str(res["audio_path"])

    def voice_profile(self, wav_path: str, out_json: str) -> dict:
        """把参考音频编码成声纹，存成 json（避免每次重新编码）。"""
        codes = self.encode_reference(wav_path)
        prof = {"source": str(Path(wav_path).name), "codes": codes, "created": int(time.time())}
        Path(out_json).write_text(
            __import__("json").dumps(prof, ensure_ascii=False), encoding="utf-8"
        )
        return prof
