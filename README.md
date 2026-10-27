# 妖妖克隆音色（MOSS-TTS-Nano）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A54.0-blue)](https://astrbot.app)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.10-3776AB)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/CPU-only-lightgrey)](#性能参考)

基于 [MOSS-TTS-Nano](https://github.com/OpenMOSS/MOSS-TTS-Nano)（100M 参数，ONNX Runtime CPU 推理）的 AstrBot 本地声音克隆插件。
上传一段参考音频，就能用那个音色合成任意文本，全流程本地推理，不依赖任何云端 TTS。

> **一句话安装**：插件市场搜「妖妖克隆音色」→ 安装依赖 → 上传一段 10 秒参考音频 → `/声音 试听 你好`。

## 目录

- [特性](#特性)
- [环境要求](#环境要求)
- [安装](#安装)
- [面板](#面板)
- [指令](#指令)
- [配置项](#配置项)
- [模型](#模型)
- [性能参考](#性能参考)
- [常见问题](#常见问题)
- [项目结构](#项目结构)
- [参与开发](#参与开发)
- [致谢](#致谢)
- [免责声明](#免责声明)
- [许可](#许可)

## 特性

- **声音克隆**：8~15 秒参考音频即可复刻音色，可保存多套音色随时切换
- **纯本地**：ONNX Runtime CPU 推理，无需显卡
- **后台面板**：环境检测、依赖检测、参考音频上传、参数调整、在线试听
- **指令调试**：聊天内试听、改参数、切音色
- **自动语音**：按概率把回复转成语音条，可设置字数上限
- **分句合成**：长文本自动拆句再拼接，规避自回归模型尾部跑偏
- **省内存**：闲置自动释放模型实例，`/声音 重载` 可手动释放

## 环境要求

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| AstrBot | ≥ 4.0 | 依赖插件配置、Web 面板与 `Record` 消息组件 |
| Python | ≥ 3.10 | 使用 `str \| None` 等新语法 |
| 操作系统 | Linux / macOS | Windows 未测试；需要能执行系统命令（`ffmpeg`） |
| CPU | 4 核以上 | 纯 CPU 推理，无 GPU 依赖 |
| 内存 | ≥ 4 GB（推荐 8 GB） | 加载两个 ONNX 模型约占 1.5~2 GB |
| 磁盘 | ≥ 3 GB | 模型约 730 MB，`.cache` 缓存与依赖另计 |
| 网络 | 首次需访问 HuggingFace | 可用 `HF_ENDPOINT` 镜像，也可手动放置模型离线运行 |

Python 依赖（`requirements.txt` 已覆盖大部分，`ffmpeg` 需系统包管理器安装）：

```
onnxruntime>=1.20.0
soundfile>=0.12.1
numpy>=1.24
torch
torchaudio
```

可选依赖：

- `ffmpeg`（系统包）：上传音频转 48 kHz 单声道 wav，建议安装
- `torchcodec`：MOSS 上游的加载路径会用到，**必须与 torch 构建对齐**（CPU 版 torch 配 CPU 版 torchcodec）：

  ```bash
  pip install --index-url https://download.pytorch.org/whl/cpu torchcodec
  ```

  装不上或不匹配也没关系，插件会自动回退到 `soundfile` 解码，功能不受影响。

> 面板里的「环境检测 / 依赖检测」会把上述项目逐条跑一遍，装之前可以先点一下确认缺什么。

## 安装

插件市场搜索「妖妖克隆音色」安装，或手动：

```bash
cd AstrBot/data/plugins
git clone https://github.com/wwwwwwwang/astrbot_plugin_moss_tts.git
```

然后在 AstrBot 后台 → 插件 → 找到本插件 → **安装依赖** → **重载插件**。

首次合成会自动下载模型（约 730 MB）。国内网络先设镜像再重载：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

下载失败或想离线部署，见 [模型](#模型) 一节的必需文件清单。

## 面板

管理后台 → 插件 → 「妖妖克隆音色」→ 打开面板。

| 区块 | 说明 |
| --- | --- |
| 环境检测 | Python 版本、CPU 核心、内存、磁盘、是否有 GPU |
| 依赖检测 | onnxruntime / soundfile / numpy / torch / torchaudio / ffmpeg 状态，可一键安装 |
| 模型 | 模型目录、ONNX 文件数量、占用空间、是否可推理 |
| 参考音频 | 上传（自动转 48 kHz 单声道 wav）、列表选择、重命名、删除、试听原音 |
| 合成参数 | 温度、top_p、帧上限、长句分句、闲置释放、自动语音概率与字数上限 |
| 试听 | 输入文本 → 生成 → 在线播放，显示时长、采样率、耗时 |
| 维护 | 释放模型实例、清理临时音频 |

面板参数保存在插件目录的 `panel_state.json`，保存后立即生效，无需重启。

## 指令

```
/声音                    查看当前音色与参数
/声音 试听 随便说点什么    用当前音色念一段
/声音 设置 /绝对/路径.wav  切换参考音频
/声音 设置                引用一条语音后发送，直接设为参考音
/声音 参数 0.7 0.9        临时调整温度 / top_p
/声音 自动 开｜关          开关自动语音回复
/声音 概率 0.3            设置自动语音触发概率
/声音 重载                重新读取面板配置并释放模型实例
```

指令改动只作用于当前进程，`/声音 自动`、`/声音 概率` 会写回 `panel_state.json`；`/声音 设置`、`/声音 参数` 属于临时覆盖，需在后台保存才持久化。

## 配置项

### 插件配置（`_conf_schema.json`）

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `ref_audio` | 参考音频路径 | `voices/ref_voice.wav` |
| `audio_temperature` | 采样温度，越低越稳 | 0.8 |
| `audio_top_p` | 核采样阈值 | 0.95 |
| `max_new_frames` | 单次生成帧上限 | 375 |
| `model_dir` | 模型根目录 | `/tmp/moss-tts` |

自动语音相关开关在面板参数里配置（见下），不放在插件配置中，避免两处维护。

### 面板参数（`panel_state.json`）

面板参数优先级高于插件配置，保存在插件目录下，不入库。

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `ref_audio` | 当前参考音频 | 空 |
| `audio_temperature` / `audio_top_p` | 同插件配置 | 0.7 / 0.95 |
| `max_new_frames` | 单次生成帧上限 | 375 |
| `split_long_text` | 长句分句合成 | false |
| `auto_release` | 空闲后自动释放模型 | false |
| `auto_voice` / `auto_voice_prob` / `auto_voice_max_chars` | 自动语音开关、概率、字数上限 | false / 0.3 / 60 |
| `tmp_keep_hours` / `tmp_max_files` | 临时音频保留时长与数量上限 | 24 / 60 |
| `voice_names` | 音色别名映射 | 空 |

## 模型

默认目录 `/tmp/moss-tts`，首次合成时自动从 HuggingFace 拉取（完整约 730 MB）：

```
models/MOSS-TTS-Nano-100M-ONNX/
models/MOSS-Audio-Tokenizer-Nano-ONNX/
```

国内网络可设置环境变量 `HF_ENDPOINT=https://hf-mirror.com`。也可在插件配置里指定已有的模型目录。

> 注意：`/tmp` 在部分系统重启后会清空，长期使用建议在配置里改成持久目录。

### 手动部署：目录里到底要哪些文件

只拷 `.onnx` 跑不起来——权重是外置的，必带同目录的 `.data`，还差 tokenizer。

`models/MOSS-TTS-Nano-100M-ONNX/`

| 文件 | 大小 |
| --- | --- |
| `moss_tts_prefill.onnx` | 277K |
| `moss_tts_decode_step.onnx` | 285K |
| `moss_tts_local_decoder.onnx` | 48K |
| `moss_tts_local_cached_step.onnx` | 52K |
| `moss_tts_local_fixed_sampled_frame.onnx` | 460K |
| `moss_tts_local_shared.data` | 219M |
| `moss_tts_global_shared.data` | 420M |
| `tokenizer.model` | 460K |
| `tts_browser_onnx_meta.json` | 4K |

`models/MOSS-Audio-Tokenizer-Nano-ONNX/`

| 文件 | 大小 |
| --- | --- |
| `moss_audio_tokenizer_encode.onnx` | 797K |
| `moss_audio_tokenizer_decode_step.onnx` | 343K |
| `moss_audio_tokenizer_decode_full.onnx` | 666K |
| `moss_audio_tokenizer_encode.data` | 42M |
| `moss_audio_tokenizer_decode_shared.data` | 42M |
| `codec_browser_onnx_meta.json` | 17K |

一共 8 个 onnx + 4 个 data + 1 个 tokenizer + 2 个 meta。`.cache/huggingface/` 缓存目录可以删。

## 性能参考

4 核 CPU / 8G 内存、无 GPU：

| 场景 | 结果 |
| --- | --- |
| 4 秒音频 | 约 9 秒生成 |
| 10 秒音频 | 约 22 秒生成 |
| 采样率 | 48 kHz 双声道 |
| 显存占用 | 无 |
| 首次合成耗时 | 额外 5~10 秒（模型加载，之后复用实例） |

## 常见问题

**长句尾部跑偏 / 后面变成乱码音**
自回归模型通病。开启「长句分句合成」，或者把一次合成的文本写短。

**音色不像原声**
优先换更干净、更长的参考音频（10 秒以上、单人、无 BGM），其次降低 temperature。

**内存吃满 / 服务变慢**
开启「闲置自动释放」或发 `/声音 重载`；ONNX 实例会先释放，下次合成再重新加载。

**`libtorchcodec_image.so` 加载失败**
torchcodec 与 torch 的 CPU/CUDA 构建不匹配。改装对应版本，或直接不装——插件会回退 `soundfile`。

**模型下载卡住 / 超时**
设置 `HF_ENDPOINT=https://hf-mirror.com` 后重载插件；仍然失败就用 [手动部署](#手动部署目录里到底要哪些文件) 放置文件。

**保存面板参数后设置被还原**
面板保存的是 `panel_state.json` 全量状态，请先刷新面板拿到最新值再修改保存。

## 项目结构

```
astrbot_plugin_moss_tts/
├── main.py                 # 插件入口、指令注册、自动语音
├── moss_engine.py          # 模型加载与合成封装
├── panel_api.py            # 后台面板接口
├── pages/                  # 面板前端
├── _conf_schema.json       # 插件配置项定义
├── metadata.yaml           # 插件市场元信息
├── requirements.txt
├── CHANGELOG.md
├── CONTRIBUTING.md
├── tmp/                    # 临时合成音频（不入库）
└── voices/                 # 参考音频（不入库）
```

## 参与开发

提 Issue 前先看 [常见问题](#常见问题)；提交代码请读 [CONTRIBUTING.md](CONTRIBUTING.md)。
改动记录见 [CHANGELOG.md](CHANGELOG.md)，版本遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## 致谢

- [MOSS-TTS-Nano](https://github.com/OpenMOSS/MOSS-TTS-Nano) 与 [MOSS-Audio-Tokenizer](https://github.com/OpenMOSS)：模型与推理流程来自 OpenMOSS
- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：插件框架与 Web 面板能力
- 所有上传参考音频、反馈问题的使用者

## 免责声明

声音克隆仅限**本人音色**或**已获授权**的音色。把他人声音用于冒充、诈骗、伪造发言等用途，可能违反《民法典》声音权条款及相关法律法规，后果由使用者自负。

## 许可

[MIT License](LICENSE) © 2026 wwwwwwwwang
