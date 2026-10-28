#!/usr/bin/env python3
"""妖妖固定音色 TTS（MOSS-TTS-Nano 克隆 + 分句合成拼接）

用法:
    python clone_tts.py --text "要念的话" --out /tmp/out.mp3
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
import soundfile as sf

BASE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(BASE, "ref_voice.wav")
MOSS = "/AstrBot/data/moss-tts"
PY = sys.executable

# 兼容 torchaudio / torchcodec 问题的启动壳
WRAPPER = """
import torchaudio, soundfile as sf, torch, runpy, sys
def _load(p, **k):
    d, sr = sf.read(str(p), always_2d=True)
    return torch.from_numpy(d.T).float(), sr
torchaudio.load = _load
sys.argv = ['infer_onnx.py'] + {argv!r}
runpy.run_path('infer_onnx.py', run_name='__main__')
"""


def split_text(text: str, max_len: int = 18):
    """按标点切句，过长的再按逗号切，保证每段不会太长（自回归模型尾部容易漂）。"""
    parts = re.split(r'(?<=[。！？；!?;])', text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= max_len:
            out.append(p)
            continue
        for seg in re.split(r'(?<=[，,、])', p):
            seg = seg.strip()
            if seg:
                out.append(seg)
    return out


def synth(text: str, out_wav: str) -> bool:
    argv = [
        "--prompt-audio-path", REF,
        "--text", text,
        "--output-audio-path", out_wav,
        "--disable-wetext-processing",
        "--max-new-frames", "900",
    ]
    env = dict(os.environ, HF_ENDPOINT="https://hf-mirror.com")
    r = subprocess.run([PY, "-c", WRAPPER.format(argv=argv)],
                       cwd=MOSS, env=env, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-800:] + r.stderr[-800:])
        return False
    return os.path.exists(out_wav)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", default="/tmp/tts_out.mp3")
    ap.add_argument("--split", action="store_true",
                    help="按标点拆短句分别合成再拼接（长句防跑偏，但语气更断）")
    a = ap.parse_args()

    segs = split_text(a.text) if a.split else [a.text]
    print("分句:" if a.split else "整句合成:", segs, flush=True)
    t_all = time.time()

    tmp = tempfile.mkdtemp(prefix="moss_seg_")
    try:
        waves = []
        ok_chars = 0
        for i, s in enumerate(segs):
            w = os.path.join(tmp, f"p{i}.wav")
            t0 = time.time()
            ok = synth(s, w)
            el = time.time() - t0
            print(f"  [{i+1}/{len(segs)}] {len(s)}字 耗时{el:.1f}s 文本: {s}", flush=True)
            if not ok:
                print("合成失败:", s, flush=True)
                continue
            d, sr = sf.read(w, always_2d=True)
            waves.append(d)
            ok_chars += len(s)
            print(f"  ok {i+1}/{len(segs)} 音频{len(d)/sr:.2f}s", flush=True)

        if not waves:
            sys.exit(1)

        gap = np.zeros((int(0.15 * 48000), waves[0].shape[1]), dtype=waves[0].dtype)
        merged = waves[0]
        for w in waves[1:]:
            merged = np.concatenate([merged, gap, w], axis=0)

        merged_wav = os.path.join(tmp, "merged.wav")
        sf.write(merged_wav, merged, 48000)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", merged_wav,
                        "-codec:a", "libmp3lame", "-b:a", "64k", "-ar", "44100",
                        "-ac", "1", "-write_xing", "1", a.out], check=True)
        total = time.time() - t_all
        print(f"输出: {a.out} 音频{len(merged)/48000:.2f}s | 共{ok_chars}字 总耗时{total:.1f}s", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
