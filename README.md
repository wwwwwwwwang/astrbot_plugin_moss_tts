# 妖妖克隆音色（MOSS-TTS-Nano）

基于 [MOSS-TTS-Nano](https://github.com/OpenMOSS/MOSS-TTS-Nano)（100M 参数，ONNX CPU 推理）的本地声音克隆插件。
上传一段参考音频，即可用那个音色合成任意文本；全流程本地推理，不依赖云端 TTS。

## 特性

- **声音克隆**：8~15 秒参考音频即可复刻音色，支持随时切换
- **纯本地**：ONNX Runtime CPU 推理，无需显卡
- **后台面板**：环境检测、依赖检测、参考音频上传、参数调整、在线试听
- **指令调试**：聊天里也能试听和切音色
- **分句合成**：长文本自动拆句再拼接，规避自回归模型尾部跑偏

## 面板

管理后台 → 插件 → 「妖妖克隆音色」→ 打开面板。

| 区块 | 说明 |
| --- | --- |
| 环境检测 | Python 版本、CPU 核心、内存、磁盘、是否有 GPU |
| 依赖检测 | onnxruntime / soundfile / numpy / torch / torchaudio / ffmpeg 状态 |
| 模型 | 模型目录、ONNX 文件数量、占用空间、是否可推理 |
| 参考音频 | 上传（自动转 48kHz 单声道 wav）、列表选择、单独试听原音 |
| 合成参数 | 温度、top_p、帧上限、长句分句开关 |
| 试听 | 输入文本 → 生成 → 在线播放，显示时长、采样率、耗时 |

面板参数保存在插件目录的 `panel_state.json`，保存后立即生效，无需重启。

## 指令

```
/声音                    查看当前音色与参数
/声音 试听 随便说点什么    用当前音色念一段
/声音 设置 /绝对/路径.wav  切换参考音频
/声音 设置                引用一条语音后发送，直接设为参考音
/声音 参数 0.7 0.9        临时调整温度 / top_p
/声音 重载                清空已加载的模型实例
```

## 依赖

```
onnxruntime>=1.20.0
soundfile
numpy
torch        # MOSS 的 onnx runtime 仍会 import torch
torchaudio
torchcodec    # 必须装 CPU 版：pip install --index-url https://download.pytorch.org/whl/cpu torchcodec
ffmpeg       # 系统包，用于转码与格式统一
```

> `torchcodec` 要跟 `torch` 的构建对齐：CPU 版 torch 必须配 CPU 版 torchcodec，
> 否则 `libtorchcodec_image.so` 会去找 `libtorch_cuda.so / libnvrtc.so` 而加载失败。
> 装不上时插件会自动回退到 soundfile 解码，功能不受影响，只是走备用路径。

## 模型

默认目录 `/tmp/moss-tts`，首次合成时自动从 HuggingFace 拉取（完整约 730MB）：

```
models/MOSS-TTS-Nano-100M-ONNX/
models/MOSS-Audio-Tokenizer-Nano-ONNX/
```

国内网络可设置环境变量 `HF_ENDPOINT=https://hf-mirror.com`。也可在插件配置里指定已有的模型目录。

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

## 配置项

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `ref_audio` | 参考音频路径 | `voices/ref_voice.wav` |
| `audio_temperature` | 采样温度，越低越稳 | 0.8 |
| `audio_top_p` | 核采样阈值 | 0.95 |
| `max_new_frames` | 单次生成帧上限 | 375 |
| `model_dir` | 模型根目录 | `/tmp/moss-tts` |

## 性能参考

4 核 CPU / 8G 内存、无 GPU：

| 场景 | 结果 |
| --- | --- |
| 4 秒音频 | 约 9 秒生成 |
| 10 秒音频 | 约 22 秒生成 |
| 采样率 | 48kHz 双声道 |
| 显存占用 | 无 |

## 已知问题

- **长句尾部容易跑偏**：自回归模型通病。可开启「长句分句合成」，或把句子写短。
- **音色不像原声**：优先换更干净、更长的参考音频（10 秒以上、无 BGM、无他人说话），其次降低 temperature。
- **首次合成慢**：包含模型加载，约多等 5~10 秒，之后会复用实例。

## 声明

声音克隆仅限**本人音色**或**已获授权**的音色。把他人声音用于冒充、诈骗、伪造发言等用途，可能违反《民法典》声音权条款及相关法律法规，后果自负。
