"""用 Playwright 渲染面板截图，供 README 使用。

用法（需要在仓库根目录执行）：
    python scripts/screenshot_panel.py [--dark]

脚本会拦截面板的所有 /api/plug/astrbot_plugin_moss_tts/* 请求并返回样例数据，
因此不需要启动 AstrBot、也不需要真的模型。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "pages"
OUT_DIR = ROOT / "docs" / "screenshots"

STATUS = {
    "success": True,
    "env": {
        "platform": "Linux 6.8",
        "python": "3.11.9",
        "cpu_count": 4,
        "mem_gb": 7.8,
        "disk_free_gb": 42.1,
        "gpu": False,
    },
    "deps": {
        "onnxruntime": {"ok": True, "optional": False, "detail": "1.20.1"},
        "soundfile": {"ok": True, "optional": False, "detail": "0.12.1"},
        "numpy": {"ok": True, "optional": False, "detail": "1.26.4"},
        "torch": {"ok": True, "optional": False, "detail": "2.4.1+cpu"},
        "torchaudio": {"ok": True, "optional": False, "detail": "2.4.1+cpu"},
        "torchcodec": {"ok": False, "optional": True, "detail": "回退 soundfile"},
        "ffmpeg": {"ok": True, "optional": False, "detail": "6.1.1"},
    },
    "model": {
        "ready": True,
        "dir": "/tmp/moss-tts/models",
        "onnx_files": 8,
        "size_gb": 0.71,
    },
    "config": {
        "audio_temperature": 0.8,
        "audio_top_p": 0.95,
        "max_new_frames": 375,
        "split_long_text": True,
        "auto_release": True,
        "auto_voice": False,
        "auto_voice_prob": 0.3,
        "auto_voice_max_chars": 60,
        "tmp_keep_hours": 24,
        "tmp_max_files": 60,
        "ref_audio": "/opt/astrbot/data/plugins/astrbot_plugin_moss_tts/voices/ref_voice.wav",
        "ref_exists": True,
        "ref_alias": "妖妖",
        "ref_name": "ref_voice.wav",
    },
}

VOICES = {
    "success": True,
    "items": [
        {"name": "ref_voice.wav", "label": "妖妖", "size_kb": 1820, "mtime": "2026-10-27 21:04"},
        {"name": "narrator.wav", "label": "旁白", "size_kb": 1490, "mtime": "2026-10-26 13:37"},
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dark", action="store_true", help="生成深色模式截图")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / ("panel-dark.png" if args.dark else "panel.png")
    theme = "dark" if args.dark else "light"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1040, "height": 900},
            device_scale_factor=2,
            color_scheme=theme,
        )

        def handler(route):
            path = route.request.url.split("/api/plug/astrbot_plugin_moss_tts/", 1)[-1]
            if path.startswith("panel/status"):
                body = STATUS
            elif path.startswith("panel/voices"):
                body = VOICES
            else:
                body = {"success": True}
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                body=json.dumps(body, ensure_ascii=False),
            )

        page.route("**/api/plug/astrbot_plugin_moss_tts/**", handler)

        # 面板用相对路径请求接口，file:// 下会被浏览器拦掉，所以起一个本地静态服务。
        handler_cls = partial(SimpleHTTPRequestHandler, directory=str(PAGES))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]

        try:
            page.goto(f"http://127.0.0.1:{port}/panel/index.html")
            page.wait_for_selector("#voices .voice-item", timeout=15000)
            page.wait_for_timeout(300)
            page.screenshot(path=str(target), full_page=True)
        finally:
            server.shutdown()
            browser.close()

    print(f"saved: {target} ({target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
