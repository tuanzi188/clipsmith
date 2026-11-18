# ClipSmith

**转写稿优先的 AI 剪辑台。** 上传素材 → 得到逐句转写稿 → 点句子选段（或让模型选）→ 切出片段。

不是又一个"输入关键词，切一段视频"的工具：交互、选段策略、渲染层都是独立模块，方便继续迭代。

```
frontend (React + Vite)
        │  /api  (SSE 推送任务状态)
        ▼
FastAPI ──┬── jobs/      每个任务一个目录，纯文件存储
          ├── engine/    mock | funasr | funclip   （只负责"说了什么、什么时候说"）
          ├── planner.py rules | llm                 （只输出句子 id 区间）
          └── media.py   ffmpeg / ffprobe            （只负责切）
```

## 对话即剪辑

上传时说的那句话，会作为项目里的第一条指令自动发给 AI。它自己决定做什么：

1. `get_project_state` 看素材、转写、已有片段
2. `get_transcript` 读稿（支持搜索和分页）
3. `set_export_options` 设置画幅 / 适配方式 / 字幕 / 去停顿 / 画质
4. `select_clips` 用**句子 id 区间**选段
5. `render_clips` 出片
6. 汇报做了什么 + 片段清单 + 下一步建议

之后继续对话就能迭代：

- "第二条再短一点" → `update_clip` 改句子范围，自动重渲染
- "换成 1:1" → `set_export_options`
- "人别裁掉，用模糊背景" → `set_export_options {fit: blur}`
- "出终版画质" → `set_export_options {quality: final}` 后重渲染
- "导出剪映草稿" → `export_draft`
- "删掉第一条" → `delete_clips`（明确要求才执行）

### 从 0 开始剪

首页拖入素材后，**不写任何指令也可以直接点「剪辑 →」**：

1. 上传完成即自动转写；
2. 转写结束时，若首页没有写要求，前端会发一条默认初剪指令（竖屏 9:16、去停顿、加字幕、素材少先出一条）；
3. AI 按顺序查状态、读稿、选段、出片，在右侧对话里汇报；
4. 之后继续说"再短一点 / 换 1:1 / 用模糊背景 / 出终版画质"即可迭代。

默认指令就是一条普通对话，会显示在对话记录和「你的要求」条里，想改随时改。没配 LLM 时不会自动执行，仍可手动点「自动选段」。

三条硬约束：

- **模型只能通过工具改项目**，不能直接碰媒体；
- **选段只认句子 id**，不认时间码，杜绝"模型编了个时间点"；
- 每次工具调用都在对话里留下回执（receipt），做了什么一眼可见。

项目右侧的「剪辑导演」面板就是这套循环的入口；首页输入的 brief 会在转写完成后自动发送。

## 现在能做什么

- **对话即剪辑**：说一句，AI 自己查状态、读稿、选段、设画幅、渲染并回复结果
- **对话式首页**：拖入 / 粘贴素材，顺便说"剪成 3 条竖屏、去掉停顿、加字幕"
- **多项目 tab**：同时开多个素材，切换保留选区与播放位置；关 tab 不删文件
- 上传 mp4 / mov / mkv / wav / mp3，自动转写
- 左侧素材预览，点击转写稿任意句自动跳转
- 单击选句，Shift + 单击选区间；转写稿支持搜索和按说话人筛选
- **规则选段**（离线、永远可用）或 **LLM 选段**（任意 OpenAI 兼容接口）
- **去停顿**：用 ffmpeg `silencedetect` 找出死气段落，导出时裁掉并无缝拼接
- **画幅导出**：原画幅 / 9:16 竖屏 / 1:1 方形，可选中裁切或**模糊背景填充**
- 片段卡片带 hook / 评分 / 理由，ffmpeg 帧精确切割，可预览、下载
- **SRT 导出**：整篇转写稿，或单个片段（时间轴按去停顿后的成片重映射）
- **AI 文案**：每条片段生成标题、发布文案和标签，一键复制
- **自定义选段要求**：给 LLM 追加指令，例如"只挑有争议的观点，每条 20 秒内"
- **烧字幕导出**：ASS 字幕按输出分辨率排版（字号/边距按画面高度比例），在切段编码时一次烧入，不做二次压缩
- **交付级处理**：两遍 EBU R128 响度归一（视频流直通不重编）、切点 8ms 防爆音、48 kHz / 192 kbps 音频
- **画质档位**：草稿 / 标准 / 终版（CRF 23/20/17，veryfast/medium/slow），对话或 UI 都能切
- **剪映草稿导出**：生成能在剪映里继续编辑的工程（视频轨 + 字幕轨）
- 转写稿密度时间条：看见进度、选区和播放位置
- 任务状态走 SSE，断线自动退化为轮询；最近任务可直接恢复
- `mock` 引擎不需要任何模型就能把整条链路跑通（开发/演示用）
- CLI 可以脱离服务端批处理：`clipsmith run input.mp4`

## 快速开始

### 后端

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e .                  # 默认 mock 引擎，依赖很轻
uvicorn clipsmith.api:app --port 8000 --reload
```

### 前端

```bash
cd frontend
npm install
npm run dev                       # http://localhost:5173，/api 自动代理到 8000
```

生产模式下先 `npm run build`，后端会直接托管 `frontend/dist`，只跑 8000 一个端口。

Windows 可以直接用 `scripts\dev.ps1` 一次开两个窗口。

## 接真实 ASR

默认 `CLIPSMITH_ENGINE=mock`，转写内容是合成文本，时间轴和音频对不上，只用于跑通 UI，**不要拿它剪真实素材**。要真转写，最轻的是 faster-whisper：

```bash
pip install faster-whisper              # 或 pip install -e ".[whisper]"
export CLIPSMITH_ENGINE=whisper         # PowerShell: $env:CLIPSMITH_ENGINE="whisper"
export CLIPSMITH_WHISPER_MODEL=small    # tiny/base/small/medium，中文建议 small 起步
export CLIPSMITH_WHISPER_DEVICE=cpu     # 有 CUDA 可填 cuda
export CLIPSMITH_WHISPER_COMPUTE=int8   # GPU 可填 float16
```

想用 FunASR（中文识别和标点更稳，但要装 torch）：

```bash
pip install -e ".[asr]"                       # funasr + torch，体积较大
export CLIPSMITH_ENGINE=funasr                 # PowerShell: $env:CLIPSMITH_ENGINE="funasr"
export CLIPSMITH_ASR_MODEL=paraformer-zh       # 或 iic/SenseVoiceSmall
export CLIPSMITH_ASR_DEVICE=cpu                # 有显卡可填 cuda:0
export CLIPSMITH_ASR_SPK=1                     # 可选：说话人分离（cam++）
```

也可以用已有的 FunClip 检出：

```bash
git clone https://github.com/modelscope/FunClip vendor/FunClip
export CLIPSMITH_ENGINE=funclip
export CLIPSMITH_FUNCLIP_PATH=vendor/FunClip
```

`engine/funclip.py` 只调用 `VideoClipper.recog`。转写结果的时间戳（毫秒）在这一层统一换算成秒。

### 引擎对比

| 引擎 | 依赖 | 适用 |
|---|---|---|
| `mock` | 无 | 开发 UI、跑测试、演示（文本与音频不对应） |
| `whisper` | faster-whisper | 本地真实转写，安装最轻，CPU 可用 |
| `funasr` | funasr + torch | 本地真实转写，中文标点/断句更稳 |
| `funclip` | FunClip 全量依赖 | 已装好 FunClip、需要它的说话人分离 |

## 接 LLM 选段

任何 OpenAI 兼容接口都可以：

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.deepseek.com/v1
export CLIPSMITH_LLM_MODEL=deepseek-chat
```

本机的 magpie 网关这样配（模型名用 `/v1/models` 里列出的完整 id）：

```bash
export OPENAI_API_KEY=local
export OPENAI_BASE_URL=http://127.0.0.1:3425/v1
export CLIPSMITH_LLM_MODEL=codearts/deepseek-v4.1-flash-1017
```

没配 Key 时接口仍然可用，会自动回退到规则选段并在任务消息里说明原因——**LLM 挂了不会让任务失败**。

也可以把这些变量写进 `backend/.env`，启动时自动读取（已存在的系统环境变量优先）：

```dotenv
CLIPSMITH_ENGINE=mock
OPENAI_API_KEY=local
OPENAI_BASE_URL=http://127.0.0.1:3425/v1
CLIPSMITH_LLM_MODEL=codearts/deepseek-v4.1-flash-1017
CLIPSMITH_LLM_TIMEOUT=180
```

关键设计：模型只返回 **句子 id 区间**，不返回时间码。时间码由后端从转写稿里取，模型没有机会编造时间。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/jobs` | 上传文件（multipart，字段名 `file`） |
| GET | `/api/jobs/{id}` | 任务快照 |
| GET | `/api/jobs/{id}/events` | SSE，推送完整任务快照 |
| POST | `/api/jobs/{id}/transcribe` | 转写 |
| POST | `/api/jobs/{id}/plan` | 选段：`{mode: rules\|llm\|manual, sentence_ids?, limit?, trim_silence?}` |
| POST | `/api/jobs/{id}/chat` | 对话指令：`{message, aspect?, fit?, subtitles?, trim_silence?, quality?}` |
| GET | `/api/jobs/{id}/chat` | 对话历史 + 是否在执行 |
| PATCH | `/api/jobs/{id}/options` | 更新画幅 / 适配方式 / 字幕 / 去停顿 / 画质 |
| POST | `/api/jobs/{id}/render` | 导出：`{clip_ids?, aspect?, fit?: crop\|blur, quality?: draft\|standard\|final, subtitles?}`，不传 clip_ids 则导出全部待导出片段 |
| GET | `/api/jobs/{id}/media` | 原始素材（支持 Range，可拖动进度条） |
| GET | `/api/jobs/{id}/clips/{clip_id}/file` | 渲染结果 |
| GET | `/api/jobs/{id}/transcript.srt` | 整篇转写稿 SRT |
| GET | `/api/jobs/{id}/clips/{clip_id}/srt` | 片段 SRT（已按去停顿重映射时间轴） |
| GET | `/api/jobs/{id}/export/draft?aspect=9:16` | 剪映草稿 zip（视频轨 + 字幕轨） |
| GET | `/api/health` | 当前引擎 / 是否启用 LLM |

数据目录（默认 `backend/data/`）：

```
data/jobs/<job_id>/
├── job.json          # 任务状态、转写稿、片段列表
├── source.mp4
├── audio.wav         # 转写用，16k mono
└── clips/<id>.mp4
```

## 导出剪映草稿

```bash
pip install -e ".[draft]"        # pyJianYingDraft
```

在片段区的「剪映草稿」按钮会下载一个 zip。解压到剪映的草稿目录（`全局设置 → 草稿位置`，一般形如 `.../JianyingPro Drafts`），重启/刷新剪映即可看到。

- 草稿包含：视频轨（按保留段切好，去停顿已生效）+ 字幕轨（时间轴与成片一致）
- 画布跟随当前选择的画幅：原画幅 / 1080×1920 / 1080×1080
- 素材用绝对路径引用 `backend/data/jobs/<id>/source.mp4`，**不要删任务目录**，否则草稿会丢素材
- 非原画幅时剪映里是等比缩放后的画面，需要自行裁切；下一步会加主体跟踪

## 工作台

首页是一个对话框，不是一个上传表单：

- 导入素材，同时用一句话说要求。明确的部分会被直接解析成设置（"竖屏" → 9:16，"加字幕" → 烧字幕，"不去停顿" → 关闭去停顿），其余原话作为 LLM 的选段指令。
- 导入后自动开一个项目 tab，进入三段式工作台：预览 / 转写稿 / 片段。
- **tab 之间切换不丢状态**：选区、播放位置、搜索词、说话人筛选、画幅设置都保留。
- **关闭 tab 只是收起**：任务和文件还在，可从"最近"重开；后台转写/导出也不会中断。
- 刷新页面会恢复打开的 tab；`?job=<id>` 可以直接分享/收藏某个任务。
- 在工作台里，"你的要求"会以提示条出现，点一下才跑 AI 选段——不会因为一句闲聊就自动消耗模型。

## 键盘快捷键

| 按键 | 动作 |
|---|---|
| `Space` | 播放 / 暂停 |
| `←` / `→` | 后退 / 前进 1 秒（按住 Shift 为 5 秒） |
| `X` | 选中播放位置所在的句子 |
| `Esc` | 清空选择 |
| `/` | 聚焦转写稿搜索框 |

## 目录

```
backend/
  clipsmith/
    api.py          FastAPI 路由 + SSE
    models.py       Job / Transcript / Clip
    jobs.py         文件存储
    agent.py        对话剪辑 agent（工具循环 + 回执）
    render.py       共享渲染流程（API / agent 共用）
    planner.py      选段（规则 + LLM + 手动 + 句子区间）
    media.py        ffprobe / ffmpeg
    cli.py          无服务端批处理
    engine/
      mock.py
      funasr.py
      funclip.py
  tests/
frontend/
  src/
    App.tsx         壳层：tab 状态、持久化、上传
    ChatHome.tsx    对话式首页
    components/ChatPanel.tsx  项目内「剪辑导演」
    Workspace.tsx   单个项目的三段式工作台
    brief.ts        从一句话里解析画幅/字幕/去停顿
    components/     VideoStage / TranscriptPane / TimelineStrip / ClipTray
    api.ts
    styles.css
scripts/
```

## 几个刻意的取舍

1. **不用 moviepy 渲染。** FunClip 用 `video.subclip()` 并在内存里持有 `VideoFileClip`，转写和导出放在两个请求里就要把整段视频常驻内存。这里每次渲染都是独立的 ffmpeg 进程，任务可恢复、并发也可控。
2. **切点对齐句子边界。** 时间码从转写稿来，前后留白（默认 0.12s / 0.18s）可调，且相邻片段会互相裁剪，不会因为留白而重叠。
3. **引擎接口只回答转写。** 选段、渲染、导出都不关心 ASR 是本地模型还是云 API，换引擎不动上层。
4. **状态推送用 SSE + 轮询兜底。** CDN/反代缓冲 event-stream 是常见坑，前端在状态为运行中时仍会每 2s 拉一次快照。
5. **去停顿在"保留段"上做，而不是重新选段。** 先按句子决定讲什么，再只裁掉句内死气（每处留 0.12s 换气）；渲染时把保留段无损拼接。这样切点既符合语义，又不会把半句话切掉。
6. **SRT 是时间线的导出视图。** 片段的 SRT 会把源时间戳映射到去停顿后的成片时间轴，导入剪辑软件后字幕和画面对得上。
7. **字幕跟着切段一次编码。** 每个保留段按源时间裁切句子、换算成段内时间，写成 ASS 后与该段一起编码；不再"先出片再烧字"，避免文字压在二次压缩的画面上。ASS 的 PlayRes 直接等于输出分辨率，字号和底边距按画面高度取比例，不依赖 libass 的 288 单位 SRT 画布。
8. **响度归一放在最后，且不重编视频。** 两遍 `loudnorm` 只重编音频，`-c:v copy` 保留已编码画面；峰值受限时 loudnorm 会主动让步（典型口播素材落在 -16 LUFS 附近），各条成片之间保持一致。

## 设计与功能参考

功能形态上参考了这些开源项目，代码为独立实现：

- **[baocut](https://github.com/JimLiu/baocut)** — "视频是可编辑文档，SRT/MP4 只是导出视图"；口令式命令 + 可撤销
- **[auto-editor](https://github.com/WyattBlue/auto-editor)** — 用 `silencedetect` 做确定性粗剪；本项目的"去停顿"沿用同一原语
- **[CutScript](https://github.com/DataAnts-AI/CutScript)** — 文本编辑器的键盘优先交互
- **[ai-video-editor](https://github.com/MartinDelophy/ai-video-editor)** — 项目管理、时间线密度条、最近任务恢复
- **[AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator)** / **[video-recap-skills](https://github.com/zenstory-ai/video-recap-skills)** — 9:16 竖屏交付与片段评分
- **[open-edit](https://github.com/veedstudio/open-edit)** — agent 驱动的剪辑管线分层

## 与 FunClip 的关系

转写这一层的思路和 `VideoClipper.recog` 来自 [FunClip](https://github.com/modelscope/FunClip)（MIT）。本项目的 UI、任务模型、选段策略、渲染与导出全部重写；`engine/funclip.py` 是一个可选的薄适配器。上游版权与许可保留在 [NOTICE](NOTICE)。

## 下一步（按优先级）

1. 竖屏主体跟踪：现在是居中裁切，下一步用 YOLO/人脸检测把主体放中间
2. 词级时间戳：支持删口头禅、单词级重排（FunASR 的 word timestamp）
3. 批量任务与队列（当前渲染是串行）
4. 评测集：10 条素材，记录选段准确率、耗时、单条成本
5. Docker 镜像 + 前端一体化部署
6. 草稿模板：保存自己的字幕样式/转场，作为"剪辑 Skill"复用

## 已知限制

- `mock` 引擎的转写是合成文本，不能当真实转写用
- 没有鉴权，默认只监听 `127.0.0.1`；不要直接暴露公网
- 渲染串行，长视频批量导出会慢
- 转写稿没有做虚拟列表，超长视频（>2 小时）前端会卡

## License

MIT。见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。