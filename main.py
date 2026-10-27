"""妖妖 MOSS-TTS 克隆音色插件。

后台配置 + 指令调试：
  /声音            查看当前音色与状态
  /声音 试听 文本   用当前音色念一段
  /声音 设置 <路径> 指定参考音频（也可直接引用一条语音发「/声音 设置」）
  /声音 参数 0.7 0.9  临时调整温度/top_p
  /声音 重载        重新加载模型
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import time
import uuid
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.star import Context, Star, register

from .moss_engine import DEFAULT_MODEL_DIR, MossEngine
from .panel_api import PanelAPI, load_state, safe_ref_audio
from .tmp_cleaner import cleanup_tmp

PLUGIN_DIR = Path(__file__).parent
VOICE_DIR = PLUGIN_DIR / "voices"
TMP_DIR = PLUGIN_DIR / "tmp"

_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\u2600-\u27BF"
    "\u2B00-\u2BFF"
    "\uFE0F"
    "\u200D"
    "]"
)


def clean_for_tts(text: str) -> str:
    """把聊天文本洗成适合念的样子：去链接、去 markdown、去表情、换行改逗号。"""
    t = re.sub(r"https?://\S+", "", text)
    t = re.sub(r"\[CQ:[^\]]+\]", "", t)
    t = re.sub(r"[*_`#>|\[\]{}<>]", "", t)
    t = _EMOJI_RE.sub("", t)
    t = re.sub(r"\s*\n+\s*", "，", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" ，。、")


@register(
    "astrbot_plugin_moss_tts",
    "妖妖",
    "MOSS-TTS-Nano 克隆音色，可在后台配置参考音频与合成参数",
    "1.0.0",
)
class MossTTSPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        self._state = load_state()
        self._last_cleanup = 0.0
        self.panel = PanelAPI(self)
        self.panel.register(context)
        self._maybe_cleanup_tmp(force=True)

    # ---------- 状态 ----------
    def reload_state(self) -> None:
        """面板保存后重新载入参数。"""
        self._state = load_state()

    # ---------- 配置读取 ----------
    @property
    def ref_audio(self) -> str:
        p = self._state.get("ref_audio") or self.config.get("ref_audio") or str(VOICE_DIR / "ref_voice.wav")
        if Path(p).is_file():
            return p
        default = VOICE_DIR / "ref_voice.wav"
        if default.is_file():
            if p != str(default):
                logger.warning(f"[moss_tts] 参考音频不存在，回退到默认：{p} -> {default}")
            return str(default)
        logger.warning(f"[moss_tts] 参考音频不存在，也没有默认 ref_voice.wav：{p}")
        return p

    @property
    def temperature(self) -> float:
        try:
            return float(self._state.get("audio_temperature", self.config.get("audio_temperature", 0.8)))
        except (TypeError, ValueError):
            return 0.8

    @property
    def top_p(self) -> float:
        try:
            return float(self._state.get("audio_top_p", self.config.get("audio_top_p", 0.95)))
        except (TypeError, ValueError):
            return 0.95

    @property
    def max_frames(self) -> int:
        try:
            return int(self._state.get("max_new_frames", self.config.get("max_new_frames", 375)))
        except (TypeError, ValueError):
            return 375

    # ---------- 合成 ----------
    async def _synth(self, text: str) -> str:
        out = TMP_DIR / f"moss_{uuid.uuid4().hex[:10]}.wav"
        engine = MossEngine.get(self.config.get("model_dir") or DEFAULT_MODEL_DIR)
        started = time.time()
        await asyncio.to_thread(
            engine.synth,
            text,
            str(out),
            prompt_audio=safe_ref_audio(self.ref_audio),
            audio_temperature=self.temperature,
            audio_top_p=self.top_p,
            max_new_frames=self.max_frames,
        )
        logger.info(f"[moss-tts] 合成 {len(text)} 字，耗时 {time.time() - started:.1f}s")
        self._maybe_cleanup_tmp()
        return str(out)

    def _maybe_cleanup_tmp(self, force: bool = False) -> None:
        """每 5 分钟最多清一次 tmp，避免合成音频无限堆积。"""
        if not force and time.time() - self._last_cleanup < 300:
            return
        self._last_cleanup = time.time()
        try:
            removed = cleanup_tmp(
                TMP_DIR,
                int(self._state.get("tmp_keep_hours") or 24),
                int(self._state.get("tmp_max_files") or 60),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[moss-tts] 清理 tmp 失败: {e}")
            return
        if removed:
            logger.info(f"[moss-tts] 清理 tmp 音频 {removed} 个")

    def _maybe_release(self) -> None:
        """按开关释放模型。由调用方在「一次完整合成流程」结束后调用，
        避免分句合成时每句都重新加载。"""
        if not self._state.get("auto_release", True):
            return
        try:
            MossEngine.reset()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[moss-tts] 自动释放失败: {e}")

    async def synth_preview(self, text: str) -> str:
        """面板试听：按需分句合成，长句更稳。"""
        import re

        import numpy as np
        import soundfile as sf

        try:
            if not self._state.get("split_long_text"):
                return await self._synth(text)

            parts = [p.strip() for p in re.split(r"(?<=[。！？；!?;，,])", text) if p.strip()]
            if len(parts) <= 1:
                return await self._synth(text)

            chunks = []
            for part in parts:
                path = await self._synth(part)
                data, sr = sf.read(path, always_2d=True)
                chunks.append((data, sr))
            if not chunks:
                return await self._synth(text)

            sr = chunks[0][1]
            gap = np.zeros((int(0.15 * sr), chunks[0][0].shape[1]), dtype=chunks[0][0].dtype)
            merged = chunks[0][0]
            for data, _ in chunks[1:]:
                merged = np.concatenate([merged, gap, data], axis=0)
            out = TMP_DIR / f"moss_{uuid.uuid4().hex[:10]}.wav"
            sf.write(str(out), merged, sr)
            return str(out)
        finally:
            self._maybe_release()

    # ---------- 自动语音 ----------
    @filter.on_decorating_result()
    async def auto_voice(self, event: AstrMessageEvent):
        """按概率把纯文本回复直接换成语音条（面板里开关 + 概率控制）。"""
        if not self._state.get("auto_voice"):
            return
        if event.get_extra("moss_tts_no_voice"):
            return
        try:
            result = event.get_result()
        except Exception:  # noqa: BLE001
            return
        chain = getattr(result, "chain", None)
        if not chain:
            return
        if any(isinstance(c, Record) for c in chain):
            return
        if any(not isinstance(c, Plain) for c in chain):
            return
        text = "".join(c.text for c in chain).strip()
        if not text or text.startswith("/"):
            return
        try:
            max_chars = int(self._state.get("auto_voice_max_chars") or 60)
            prob = float(self._state.get("auto_voice_prob") or 0.3)
        except (TypeError, ValueError):
            max_chars, prob = 60, 0.3
        if len(text) > max_chars:
            return
        if random.random() > prob:
            return
        spoken = clean_for_tts(text)
        if len(spoken) < 1:
            return
        try:
            path = await self._synth(spoken)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[moss-tts] 自动语音合成失败: {e}")
            return
        self._maybe_release()
        result.chain = [Record(file=path)]

    # ---------- 指令 ----------
    @filter.command("声音")
    async def voice_cmd(self, event: AstrMessageEvent):
        """音色管理指令"""
        event.set_extra("moss_tts_no_voice", True)
        raw = event.message_str.strip()
        parts = raw.split(maxsplit=2)
        sub = parts[1] if len(parts) > 1 else ""
        arg = parts[2] if len(parts) > 2 else ""

        if sub in ("", "信息", "info"):
            exists = os.path.exists(self.ref_audio)
            yield event.plain_result(
                "音色状态\n"
                f"参考音频：{self.ref_audio}\n"
                f"文件存在：{'是' if exists else '否'}\n"
                f"温度：{self.temperature}　top_p：{self.top_p}　帧上限：{self.max_frames}\n"
                f"模型目录：{self.config.get('model_dir') or '默认'}\n"
                f"自动语音：{'开' if self._state.get('auto_voice') else '关'}"
                f"（概率 {self._state.get('auto_voice_prob')}，上限 {self._state.get('auto_voice_max_chars')} 字）\n"
                "用法：/声音 试听 文本 ｜ /声音 设置 <路径> ｜ /声音 参数 0.7 0.9 ｜ /声音 自动 开 ｜ /声音 重载"
            )
            return

        if sub == "试听":
            text = arg or "这是当前的音色，你听听还满意吗？"
            path = await self._synth(text)
            self._maybe_release()
            yield event.chain_result([Record(file=path)])
            return

        if sub == "设置":
            if arg:
                target = arg.strip().strip('"')
                if not os.path.isabs(target):
                    yield event.plain_result("请给绝对路径，或者直接引用一条语音发「/声音 设置」")
                    return
                if not os.path.exists(target):
                    yield event.plain_result(f"找不到这个文件：{target}")
                    return
                self.config["ref_audio"] = target
                yield event.plain_result(f"参考音频已切换：{target}\n（后台保存后才会持久化）")
                return
            # 从引用消息里取语音
            chain = getattr(event.message_obj, "message", None) or []
            for comp in chain:
                if getattr(comp, "type", "") == "Record":
                    src = getattr(comp, "path", None) or getattr(comp, "url", None)
                    if src:
                        yield event.plain_result(f"收到语音，正在设为参考音：{src}")
                        self.config["ref_audio"] = src
                        return
            yield event.plain_result("没找到语音。引用一条语音再发这个指令，或者给个绝对路径。")
            return

        if sub == "参数":
            nums = arg.split()
            if len(nums) >= 2:
                self.config["audio_temperature"] = float(nums[0])
                self.config["audio_top_p"] = float(nums[1])
                yield event.plain_result(
                    f"已调整：温度 {nums[0]}　top_p {nums[1]}（重启或保存后台后持久化）"
                )
            else:
                yield event.plain_result("用法：/声音 参数 0.7 0.9")
            return

        if sub == "自动":
            arg = arg.strip()
            if arg in ("开", "on", "1"):
                self._state["auto_voice"] = True
            elif arg in ("关", "off", "0"):
                self._state["auto_voice"] = False
            else:
                yield event.plain_result(
                    f"自动语音：{'开' if self._state.get('auto_voice') else '关'}"
                    f"　概率 {self._state.get('auto_voice_prob')}"
                    f"　字数上限 {self._state.get('auto_voice_max_chars')}\n"
                    "用法：/声音 自动 开｜/声音 自动 关"
                )
                return
            from .panel_api import save_state

            save_state(self._state)
            yield event.plain_result(
                f"自动语音已{'开启' if self._state.get('auto_voice') else '关闭'}"
                f"（概率 {self._state.get('auto_voice_prob')}）"
            )
            return

        if sub == "概率":
            try:
                prob = max(0.0, min(1.0, float(arg)))
            except (TypeError, ValueError):
                yield event.plain_result("用法：/声音 概率 0.3")
                return
            from .panel_api import save_state

            self._state["auto_voice_prob"] = prob
            save_state(self._state)
            yield event.plain_result(f"自动语音概率已设为 {prob}")
            return

        if sub == "重载":
            MossEngine.reset()
            yield event.plain_result("模型实例已释放，下次合成会重新加载（内存会先降下来）。")
            return

        yield event.plain_result("看不懂这个子命令，发 /声音 看用法。")

    async def terminate(self):
        # 卸载/重载时主动放掉模型，避免新旧实例各占一份内存
        try:
            MossEngine.reset()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[moss-tts] 释放模型失败: {e}")
        logger.info("[moss-tts] 插件已卸载")
