# 贡献指南

感谢愿意搭把手。这个插件体量不大，改动之前请先看下面几段，能省掉来回沟通。

## 提 Issue 之前

1. 先看 README 的[常见问题](README.md#常见问题)，模型下载、音色不像、内存占用这几类基本都在里面。
2. 打开插件面板的「环境检测 / 依赖检测」，把结果一起贴上来。
3. 报 bug 时尽量给全：AstrBot 版本、Python 版本、系统、复现步骤、完整报错日志（贴文本，别只截图）。
4. 声音不像、尾部跑偏属于模型本身特性，不算 bug；但可以带着参考音频信息（时长、是否单人、是否有 BGM）来讨论。

## 开发环境

```bash
git clone https://github.com/wwwwwwwang/astrbot_plugin_moss_tts.git
cd astrbot_plugin_moss_tts
pip install -r requirements.txt
```

把插件目录软链或拷到 `AstrBot/data/plugins/` 下，在后台重载插件即可调试。面板前端在 `pages/`，改完刷新浏览器缓存。

需要模型时：

```bash
export HF_ENDPOINT=https://hf-mirror.com   # 国内加速
```

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `main.py` | 插件入口、指令注册、自动语音、生命周期（`initialize` / `terminate`） |
| `moss_engine.py` | 模型加载、tokenize、合成、实例复用与释放 |
| `panel_api.py` | 面板接口、状态读写、环境/依赖检测、音频转码 |
| `pages/` | 面板前端静态资源 |
| `_conf_schema.json` | 插件配置项定义 |

几个约定：

- 面板运行态放 `panel_state.json`，不进 Git；新增键请同时在 `DEFAULT_STATE` 和 README 的「面板参数」表里补上。
- 模型相关的重活一律丢线程里跑，别阻塞事件循环。
- 新增指令沿用 `/声音` 子命令风格，并在 README「指令」一节同步。
- 合成结果统一落到 `tmp/`，由 `tmp_cleaner.py` 负责清理。

## 提交规范

用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：

```
feat: 支持多音色快速切换
fix: 修正面板保存后参数被覆盖的问题
docs: 补充环境要求与手动部署说明
```

一次提交只做一件事；纯格式化改动单独提交，别和功能混在一起。

## 不要提交的东西

`.gitignore` 已经挡了一部分，提交前还是确认一下别带进去：

- 模型文件（`*.onnx`、`*.data`、`models/`）
- 参考音频与合成音频（`voices/`、`tmp/`、`*.wav`）
- `panel_state.json`、`data/`、`.env` 等运行态或含私有路径的文件
- `__pycache__/`

## Pull Request

- 说清改动目的和影响范围，关联相关 Issue。
- 自己先跑通：`/声音 试听 你好` 能出语音，面板能打开、能保存。
- 涉及面板参数的改动，贴一张改动前后的截图或说明。
- 文档和代码一起改；README 与 CHANGELOG 不同步的 PR 通常会被要求补上。

## 声音使用边界

不要提交他人音色的参考音频，也不要提交用于演示真人音色的合成样本。测试请用自己录制的声音或可公开使用的素材。
