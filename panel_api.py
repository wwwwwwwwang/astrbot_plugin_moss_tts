"""MOSS-TTS 插件管理面板后端。

提供：环境检测、依赖检测、参考音频上传/切换、参数读写、试听生成。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from astrbot.api import logger

try:
    from quart import jsonify, request, send_file
except Exception:  # pragma: no cover
    jsonify = None
    request = None
    send_file = None

PLUGIN_DIR = Path(__file__).parent
VOICE_DIR = PLUGIN_DIR / "voices"
TMP_DIR = PLUGIN_DIR / "tmp"
STATE_PATH = PLUGIN_DIR / "panel_state.json"
PLUGIN_NAME = "astrbot_plugin_moss_tts"

MOSS_DIR = "/AstrBot/data/moss-tts"

DEFAULT_STATE = {
    "ref_audio": "",
    "audio_temperature": 0.8,
    "audio_top_p": 0.95,
    "max_new_frames": 375,
    "split_long_text": False,
    "auto_release": True,
    # 自动语音：把 LLM 的纯文本回复直接换成语音条
    "auto_voice": False,
    "auto_voice_prob": 0.3,
    "auto_voice_max_chars": 60,
    # tmp 清理：超过小时数的删掉，剩下的只留最新 max_files 个
    "tmp_keep_hours": 24,
    "tmp_max_files": 60,
}


def load_state() -> dict:
    state = dict(DEFAULT_STATE)
    if STATE_PATH.exists():
        try:
            state.update(json.loads(STATE_PATH.read_text("utf-8")))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[moss-tts] 读取面板配置失败: {e}")
    return state


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")


def _mem_total_gb() -> float | None:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except Exception:  # noqa: BLE001
        return None
    return None


MAX_REF_SECONDS = 15


def safe_ref_audio(path: str, max_seconds: int = MAX_REF_SECONDS) -> str:
    """参考音频过长会让 encoder 显存/内存爆炸，这里自动截断到安全长度。"""
    if not path or not os.path.exists(path):
        return path
    try:
        import wave

        with wave.open(path) as w:
            dur = w.getnframes() / w.getframerate()
        if dur <= max_seconds + 1:
            return path
    except Exception:  # noqa: BLE001
        return path
    if not shutil.which("ffmpeg"):
        return path
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TMP_DIR / f"ref_trim_{int(time.time())}.wav"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", path,
         "-t", str(max_seconds), "-ar", "48000", "-ac", "1", str(out)],
        capture_output=True, timeout=60,
    )
    return str(out) if proc.returncode == 0 and out.exists() else path


def _rss_mb() -> float | None:
    """当前进程常驻内存（MB）。"""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:  # noqa: BLE001
        return None
    return None


def _data_url(path: str, max_seconds: int = 120) -> str:
    """把音频压成 32kbps 单声道 mp3 再转 data URL（体积小，bridge 更容易传）。"""
    import base64 as _b64
    import subprocess as _sp

    raw = None
    if shutil.which("ffmpeg"):
        try:
            proc = _sp.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                 "-t", str(max_seconds), "-ar", "24000", "-ac", "1",
                 "-b:a", "32k", "-f", "mp3", "pipe:1"],
                capture_output=True, timeout=60,
            )
            if proc.returncode == 0 and proc.stdout:
                raw = proc.stdout
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None:
        raw = Path(path).read_bytes()
        return "data:audio/wav;base64," + _b64.b64encode(raw).decode()
    return "data:audio/mpeg;base64," + _b64.b64encode(raw).decode()


def _check_module(name: str) -> dict:
    try:
        spec = importlib.util.find_spec(name)
    except Exception as e:  # noqa: BLE001
        # 例如某些包导入即崩、__spec__ 为 None
        return {"ok": False, "detail": f"检测异常: {e}"}
    if spec is None:
        return {"ok": False, "detail": "未安装"}
    version = ""
    try:
        mod = __import__(name)
        version = getattr(mod, "__version__", "")
        if not version:
            try:  # torch 之类放在内部
                version = getattr(mod.version, "version", "")  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                version = ""
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"导入失败: {e}"}
    return {"ok": True, "detail": str(version) or "已安装"}


class PanelAPI:
    def __init__(self, plugin) -> None:
        self.plugin = plugin
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- 注册 ----------
    def register(self, context) -> None:
        routes = [
            ("/panel/status", "handle_status", ["GET"]),
            ("/panel/voices", "handle_voices", ["GET"]),
            ("/panel/upload", "handle_upload", ["POST"]),
            ("/panel/voices/delete", "handle_delete_voice", ["POST"]),
            ("/panel/config", "handle_config", ["POST"]),
            ("/panel/preview", "handle_preview", ["POST"]),
            ("/panel/audio", "handle_audio", ["GET"]),
            ("/panel/audio-data", "handle_audio_data", ["GET"]),
            ("/panel/deps/install", "handle_install_deps", ["POST"]),
            ("/panel/release", "handle_release", ["POST"]),
            ("/panel/clean-tmp", "handle_clean_tmp", ["POST"]),
        ]
        for route, handler_name, methods in routes:
            context.register_web_api(
                f"/{PLUGIN_NAME}{route}",
                getattr(self, handler_name),
                methods,
                f"Plugin Panel: {handler_name}",
            )

    # ---------- 辅助 ----------
    def _model_dir(self) -> Path:
        raw = self.plugin.config.get("model_dir") or ""
        if raw:
            p = Path(raw)
            # 允许填到具体模型子目录，自动退回到 models 的父级
            return p.parent.parent if p.name.endswith("-ONNX") else p
        return Path(MOSS_DIR)

    def _model_status(self) -> dict:
        base = self._model_dir()
        tts = base / "models" / "MOSS-TTS-Nano-100M-ONNX"
        tok = base / "models" / "MOSS-Audio-Tokenizer-Nano-ONNX"
        files = list(tts.rglob("*.onnx")) + list(tok.rglob("*.onnx"))
        total = 0
        for d in (tts, tok):
            if d.exists():
                total += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        return {
            "dir": str(base),
            "exists": base.exists(),
            "onnx_files": len(files),
            "size_gb": round(total / 1024**3, 2),
            "ready": len(files) > 0,
        }

    # ---------- 环境 / 依赖 ----------
    async def handle_status(self):
        state = load_state()
        ref = state.get("ref_audio") or self.plugin.ref_audio
        deps = {
            "onnxruntime": _check_module("onnxruntime"),
            "soundfile": _check_module("soundfile"),
            "numpy": _check_module("numpy"),
            "torch": _check_module("torch"),
            "torchaudio": _check_module("torchaudio"),
            "torchcodec": _check_module("torchcodec"),
        }
        ffmpeg = shutil.which("ffmpeg")
        deps["ffmpeg"] = {"ok": bool(ffmpeg), "detail": ffmpeg or "未安装"}
        # 解码后端：优先官方 torchcodec，实在加载不了才回退 soundfile
        if not deps["torchcodec"]["ok"]:
            deps["torchcodec"]["optional"] = True
            deps["torchcodec"]["detail"] = "不可用（已回退 soundfile 解码）"
        else:
            deps["torchcodec"].setdefault("optional", False)

        disk = shutil.disk_usage(str(PLUGIN_DIR))
        env = {
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()}",
            "cpu_count": os.cpu_count(),
            "mem_gb": _mem_total_gb(),
            "disk_free_gb": round(disk.free / 1024**3, 1),
            "gpu": os.path.exists("/dev/nvidia0"),
        }
        return jsonify({
            "success": True,
            "env": env,
            "deps": deps,
            "model": self._model_status(),
            "config": {
                "ref_audio": ref,
                "ref_exists": os.path.exists(ref) if ref else False,
                "audio_temperature": state.get("audio_temperature", 0.8),
                "audio_top_p": state.get("audio_top_p", 0.95),
                "max_new_frames": state.get("max_new_frames", 375),
                "split_long_text": bool(state.get("split_long_text", False)),
                "auto_release": bool(state.get("auto_release", True)),
            },
            "voices_dir": str(VOICE_DIR),
        })

    async def handle_voices(self):
        items = []
        for p in sorted(VOICE_DIR.glob("*")):
            if p.suffix.lower() in (".wav", ".mp3", ".flac", ".m4a", ".ogg"):
                st = p.stat()
                items.append({
                    "name": p.name,
                    "path": str(p),
                    "size_kb": round(st.st_size / 1024, 1),
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                })
        return jsonify({"success": True, "items": items})

    async def handle_upload(self):
        """支持两种上传：multipart 表单，或 JSON + base64（插件页面 bridge 用）。"""
        payload = await request.get_json(force=True, silent=True) or {}
        if payload.get("base64"):
            import base64 as _b64

            raw_name = os.path.basename(str(payload.get("name") or "ref.wav"))
            suffix = Path(raw_name).suffix.lower() or ".wav"
            if suffix not in (".wav", ".mp3", ".flac", ".m4a", ".ogg"):
                return jsonify({"success": False, "error": f"不支持的格式: {suffix}"}), 400
            data = str(payload["base64"])
            if "," in data:
                data = data.split(",", 1)[1]
            target = VOICE_DIR / f"ref_{int(time.time())}{suffix}"
            target.write_bytes(_b64.b64decode(data))
        else:
            files = await request.files
            f = files.get("file")
            if f is None:
                return jsonify({"success": False, "error": "没有收到文件"}), 400
            raw_name = os.path.basename(f.filename or "ref.wav")
            suffix = Path(raw_name).suffix.lower() or ".wav"
            if suffix not in (".wav", ".mp3", ".flac", ".m4a", ".ogg"):
                return jsonify({"success": False, "error": f"不支持的格式: {suffix}"}), 400
            target = VOICE_DIR / f"ref_{int(time.time())}{suffix}"
            await f.save(str(target))
        # 统一转成 48k 单声道 wav，避免采样率不一致
        wav_target = target.with_suffix(".wav")
        if shutil.which("ffmpeg"):
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(target),
                "-t", "15", "-ar", "48000", "-ac", "1", str(wav_target),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"[moss-tts] ffmpeg 转码失败: {err[-300:]}")
        final = wav_target if wav_target.exists() else target
        if target != final and target.exists():
            target.unlink(missing_ok=True)

        state = load_state()
        state["ref_audio"] = str(final)
        save_state(state)
        return jsonify({"success": True, "path": str(final), "name": final.name})

    async def handle_delete_voice(self):
        """删除 voices 目录下的某个参考音频；若是当前正在用的，顺手清空引用。"""
        payload = await request.get_json(force=True, silent=True) or {}
        raw = str(payload.get("name") or payload.get("path") or "").strip()
        if not raw:
            return jsonify({"success": False, "error": "没有指定要删除的文件"}), 400
        suffix = Path(raw).suffix.lower()
        if suffix not in (".wav", ".mp3", ".flac", ".m4a", ".ogg"):
            return jsonify({"success": False, "error": f"不支持的格式: {suffix}"}), 400
        target = (VOICE_DIR / os.path.basename(raw)).resolve()
        try:
            target.relative_to(VOICE_DIR.resolve())
        except ValueError:
            return jsonify({"success": False, "error": "路径不合法"}), 400
        if not target.exists():
            return jsonify({"success": False, "error": "文件不存在"}), 404
        target.unlink()
        logger.info(f"[moss-tts] 已删除参考音频: {target}")

        # 被删的正好是当前参考音 -> 清空，避免状态指向幽灵文件
        state = load_state()
        cleared = False
        cur = str(state.get("ref_audio") or "")
        if cur and os.path.realpath(cur) == str(target):
            state["ref_audio"] = ""
            save_state(state)
            cleared = True
        return jsonify({"success": True, "name": target.name, "cleared_ref": cleared})

    async def handle_config(self):
        payload = await request.get_json(force=True, silent=True) or {}
        state = load_state()
        if "ref_audio" in payload:
            p = str(payload["ref_audio"]).strip()
            if p and not os.path.exists(p):
                return jsonify({"success": False, "error": "参考音频不存在"}), 400
            state["ref_audio"] = p
        for key, cast, lo, hi in (
            ("audio_temperature", float, 0.1, 1.5),
            ("audio_top_p", float, 0.1, 1.0),
        ):
            if key in payload:
                try:
                    val = cast(payload[key])
                except (TypeError, ValueError):
                    return jsonify({"success": False, "error": f"{key} 不是合法数字"}), 400
                state[key] = max(lo, min(hi, val))
        if "max_new_frames" in payload:
            try:
                state["max_new_frames"] = max(50, min(2000, int(payload["max_new_frames"])))
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "帧上限不是合法整数"}), 400
        if "split_long_text" in payload:
            state["split_long_text"] = bool(payload["split_long_text"])
        if "auto_release" in payload:
            state["auto_release"] = bool(payload["auto_release"])
        if "auto_voice" in payload:
            state["auto_voice"] = bool(payload["auto_voice"])
        for key, lo, hi in (
            ("auto_voice_prob", 0.0, 1.0),
            ("auto_voice_max_chars", 1, 200),
            ("tmp_keep_hours", 0, 24 * 30),
            ("tmp_max_files", 0, 5000),
        ):
            if key in payload:
                try:
                    val = float(payload[key])
                except (TypeError, ValueError):
                    return jsonify({"success": False, "error": f"{key} 不是合法数字"}), 400
                val = max(lo, min(hi, val))
                state[key] = round(val, 2) if key == "auto_voice_prob" else int(val)
        save_state(state)
        self.plugin.reload_state()
        return jsonify({"success": True, "config": state})

    async def handle_preview(self):
        payload = await request.get_json(force=True, silent=True) or {}
        text = str(payload.get("text") or "").strip()
        if not text:
            return jsonify({"success": False, "error": "文本不能为空"}), 400

        started = time.time()
        try:
            out = await self.plugin.synth_preview(text)
        except Exception as e:  # noqa: BLE001
            logger.exception("[moss-tts] 试听合成失败")
            return jsonify({"success": False, "error": str(e)}), 500
        elapsed = round(time.time() - started, 2)
        try:
            import wave

            with wave.open(out, "rb") as w:
                dur = round(w.getnframes() / w.getframerate(), 2)
                sr = w.getframerate()
        except Exception:  # noqa: BLE001
            dur, sr = 0, 0
        payload = {
            "success": True,
            "file": os.path.basename(out),
            "duration": dur,
            "sample_rate": sr,
            "elapsed": elapsed,
            "url": f"/api/plug/{PLUGIN_NAME}/panel/audio?f={os.path.basename(out)}",
            "audio": _data_url(out),
        }

        # 自动释放：读完音频后再丢模型，内存回到 2G 以内
        if load_state().get("auto_release", True):
            try:
                from .moss_engine import MossEngine

                MossEngine.reset()
                payload["released"] = True
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[moss-tts] 自动释放失败: {e}")
        return jsonify(payload)

    async def handle_audio(self):
        name = (request.args.get("f") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "缺少文件参数"}), 400
        for base in (TMP_DIR, VOICE_DIR):
            candidate = (base / os.path.basename(name)).resolve()
            try:
                candidate.relative_to(base.resolve())
            except ValueError:
                continue
            if candidate.exists():
                return await send_file(str(candidate), mimetype="audio/wav")
        return jsonify({"success": False, "error": "文件不存在"}), 404

    async def handle_audio_data(self):
        """返回 base64 data URL，供插件页面 iframe 内直接播放。"""
        import base64 as _b64

        name = (request.args.get("f") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "缺少文件参数"}), 400
        for base in (TMP_DIR, VOICE_DIR):
            candidate = (base / os.path.basename(name)).resolve()
            try:
                candidate.relative_to(base.resolve())
            except ValueError:
                continue
            if candidate.exists():
                return jsonify({
                    "success": True,
                    "audio": _data_url(str(candidate)),
                    "size_kb": round(candidate.stat().st_size / 1024, 1),
                })
        return jsonify({"success": False, "error": "文件不存在"}), 404

    async def handle_release(self):
        """释放模型实例并强制 GC，回收被多份实例吃掉的显存/内存。"""
        import gc

        from .moss_engine import MossEngine

        before = _rss_mb()
        try:
            inst = MossEngine._instance  # noqa: SLF001
            if inst is not None:
                if getattr(inst, "_rt", None) is not None:
                    inst._rt = None  # noqa: SLF001
                MossEngine._instance = None  # noqa: SLF001
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[moss-tts] 释放实例失败: {e}")
        for _ in range(3):
            gc.collect()
        await asyncio.sleep(0.2)
        after = _rss_mb()
        return jsonify({
            "success": True,
            "rss_before_mb": before,
            "rss_after_mb": after,
            "freed_mb": round(before - after, 1) if before and after else None,
        })

    async def handle_clean_tmp(self):
        """手动清一次 tmp 缓存音频。"""
        from .tmp_cleaner import cleanup_tmp

        state = load_state()
        removed = cleanup_tmp(
            TMP_DIR,
            int(state.get("tmp_keep_hours") or 24),
            int(state.get("tmp_max_files") or 60),
        )
        return jsonify({"success": True, "removed": removed})

    async def handle_install_deps(self):
        """可选：尝试 pip 安装缺失的依赖（受限环境可能失败）。"""
        payload = await request.get_json(force=True, silent=True) or {}
        pkgs = payload.get("packages") or []
        allowed = {"onnxruntime", "soundfile", "sentencepiece", "torch", "torchaudio"}
        pkgs = [p for p in pkgs if p in allowed]
        if not pkgs:
            return jsonify({"success": False, "error": "没有可安装的包"}), 400
        proc = await asyncio.create_subprocess_exec(
            "pip", "install", "-q", *pkgs,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return jsonify({
            "success": proc.returncode == 0,
            "log": (out or b"").decode("utf-8", "ignore")[-1500:],
        })
