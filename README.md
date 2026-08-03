<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.svg">
    <img src="assets/logo-light.svg" width="360" alt="OpenWrite">
  </picture>
</p>

<h1 align="center">Autonomous Novel Writing CLI AI Agent<br><sub>自动化长篇小说写作 CLI AI Agent</sub></h1>

<p align="center">
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/version-5.8.0-2563eb" alt="Version"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-%3E%3D3.10-22c55e" alt="Python >= 3.10"></a>
  <img src="https://img.shields.io/badge/entry-openwrite%20dante-0f172a" alt="Primary Entry: openwrite dante">
  <img src="https://img.shields.io/badge/structure-src%20%2B%20data-1d4ed8" alt="Structure: src + data">
</p>

<p align="center">
  长篇小说不是一次性 prompt。OpenWrite 把立项、设定、滚动大纲、章节写作、审查、真相文件和 workflow 放进同一条长期生产线里，让你和 Dante 持续把一本书写下去。
</p>

## 只做一件事：把长篇小说写完

OpenWrite 不覆盖短篇、翻译、剧本或互动游戏。它把产品能力集中在长篇小说最难的部分：作者意图不会在几十章后丢失，人物与世界状态可追踪，近期写作目标能压住模型跑偏，并且旧稿可以接入、成稿可以完整导出。

它的核心特色是：

- **Goethe / Dante 双 Agent**：Goethe 负责把想法变成可写资产，Dante 负责持续写作、审查与状态结算。
- **创作罗盘**：`author_intent.md` 保存整本书的长期承诺，`current_focus.md` 保存近期最高优先级目标；两者都会进入章节上下文。
- **单一真源**：人物、设定和大纲以 `src/` 为确认版，机器缓存与运行状态留在 `data/`，避免多份文档互相打架。
- **长篇连续性**：每章按大纲窗口、人物、世界规则、伏笔、上一章正文和真相文件组装 canonical packet。
- **有界章节记忆**：每章把摘要、客观观察和三阶段 token 用量写入 `data/memory/chapters/`，下一章只注入相关的有界摘要，不把整本正文塞回模型。
- **可靠提交**：写章使用跨进程作品锁；正文、真相文件和章节记忆作为一个提交单元，任一步失败都会恢复写前快照，避免半章或半份状态落盘。
- **质量闭环**：37 维审稿会读取作者意图、创作罗盘、大纲、角色正典、关系和风格约束；CLI 与 Studio 共用同一套 workflow / book state 生命周期。
- **完整交付闭环**：支持 TXT / Markdown 旧稿导入、写作进度与资产就绪度工作台、Markdown / TXT 整书导出。
- **小说 Studio**：`openwrite studio` 覆盖模型连接、Goethe / Dante 长会话、小说资产编辑与新建、旧稿导入、同步、上下文检查、写章、37 维审稿、连续性/伏笔、来源提取晋升和整书导出。
- **分层长会话**：Goethe / Dante 的滚动状态保存在 `data/workflows/*session.yaml`，完整对话追加保存在对应的 `*session.jsonl`；运行时只注入摘要与最近窗口，旧历史不会丢失。
- **真实活动可视化**：Studio 的 `AI WORKING` 读取后端 ReAct 事件，显示模型思考、工具调用、结果校验与回复落盘阶段；长时提示会带上最后一个真实步骤。
- **统一写入确认门**：Goethe / Dante 对大纲、人物、世界关系和其他正式资产的修改都先预览 diff；只有本轮用户明确确认后，`confirm=true` 才能真正落盘。
- **LightRAG 项目搜索**：项目搜索使用 MIT 许可的 [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG)，把真源、人物、世界设定和正文建立为知识图谱与向量索引，同时保留文件和行号定位。

### 一个小说内核，四个入口

OpenWrite 借鉴了 InkOS“多个交互入口共用同一 action surface”的思路，但只保留长篇小说需要的能力。CLI、Studio、Goethe 和 Dante 不再各自维护一套写章、审稿或来源处理逻辑：它们都把请求交给同一个小说应用服务，完成态以真实工具结果和落盘状态为准，不以模型口头声明为准。

```text
CLI ─────┐
Studio ──┼─> Novel action surface ─> NovelApplicationService
Goethe ──┤                              ├─ canonical packet
Dante ───┘                              ├─ chapter write/review pipeline
                                        ├─ source-pack lifecycle
                                        └─ workflow / truth / memory / BookState
```

入口可以提供不同的交互体验，但以下契约只有一份：章节 ID 归一化、canonical packet、项目锁、事务回滚、错误码、workflow 推进、审稿存储和 BookState 结算。这样做的目的不是把 OpenWrite 扩成通用内容平台，而是让一本长篇小说从规划到完稿始终由同一套状态机负责。

## 推荐用法

OpenWrite 推荐你把它当成一个长期协作的主 agent，而不是一组需要手工维护的文件夹。

- 对大多数用户来说，日常只需要记住两个 Agent 入口：`openwrite goethe` 和 `openwrite dante`
- 直接运行 `openwrite` 或 `openwrite desk` 可以查看作品进度、资产就绪度和下一步建议
- 先用 `openwrite goethe` 把脑洞整理成可写资产
- 日常推进时优先用 `openwrite dante`
- 只在需要精确检查或脚本化时才直接用 `write`、`review`、`context`、`assemble`
- 不要手工维护 `data/` 里的缓存和 workflow 文件
- 只有确认版内容才建议手改 `src/`

职责拆分：

- Goethe 负责长会话规划：汇总灵感、提建议、修人物、修设定、修大纲，并在资产成熟时显式交接给 Dante
- Dante 负责把可写资产持续写成正文，并在正文推进过程中必要时回修资产

一句话说清楚：

- `src/` 是人和 AI 共读的确认版真源
- `data/` 是运行态、workflow、手稿、缓存和快照
- `dante` 才是你最应该频繁使用的入口

### 只记住这两个入口

如果你不想记一堆命令，先只记住：

- `openwrite goethe`
  负责把你的脑洞整理成可写资产：灵感、人物、设定、大纲、风格来源
- `openwrite dante`
  负责把这些资产持续写成正文：写章、审查、推进运行态

其他 CLI 命令都可以理解成高级控制面：

- 当你要调试、脚本化、强制执行某一步时再用
- 平时不需要围着 `data/` 或单个工具命令工作

## 快速开始

### 0. 双击启动（推荐）

下载或克隆完整仓库后，可以直接双击仓库根目录中的启动文件：

- macOS：`启动 OpenWrite.command`
- Windows：`启动 OpenWrite.bat`

启动器会检查 Python 3.10+，在 `.openwrite-runtime/` 创建与系统隔离的运行环境，
根据 `pyproject.toml` 和 `requirements.txt` 检查依赖是否需要更新，并自动下载、安装和运行
`pip check`。依赖没有变化且环境健康时会直接启动，不会每次重复下载。

如果默认端口已经运行 OpenWrite，启动器会直接打开现有 Studio；如果端口被其他程序占用，
会自动寻找后续可用端口。`.openwrite-runtime/` 只存在于本机并已被 Git 忽略，不会进入作品或代码提交。

启动器不会静默安装 Python 本身。如果电脑没有 Python 3.10 或更高版本，会显示对应系统的安装地址；
安装完成后再次双击即可。首次安装依赖需要联网，后续通常可直接离线启动。

### 1. 安装

```bash
git clone https://github.com/LiPu-jpg/Openwrite.git
cd Openwrite
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e .
openwrite studio
```

从框架仓库启动 Studio 时，创建作品的目录可以留空；系统会在同级自动创建
`../OpenWriteNovels/<书名>`（即 `~/OpenWriteNovels/<书名>`）。也可以展开“自定义作品目录”
指定其他位置。首次打开会先用可跳过的工作台导览介绍各区域，再依次引导模型配置、作品创建、
故事规划、大纲确认和正文写作。

### 2. 快速体验（带示范资产）

如果想跳过规划直接写一章：

```bash
openwrite init demo_novel --title "雾城来信" --template demo_short
openwrite studio
```

或在 Studio 创建对话框勾选「创建带示范资产的短篇练习」。
示范资产包含已填写的作者意图、背景、人物、三章大纲和创作罗盘，可直接进入 Dante 写第一章。

### 3. 配置模型

使用网页端时，直接点击 Studio 顶栏常驻的 **模型设置**，可以即时填写或切换
DeepSeek V4 Pro / V4 Flash、OpenAI 格式接口、Anthropic 格式接口或自定义接口，
并设置 API Key、上下文预算和最大输出。接口预设只是协议格式，Base URL 与模型名
仍可自由填写。可先做最小连接测试，点击“立即应用”后下一次对话或写章生效，无需重启。
API Key 默认只保存在当前 Studio 进程；若勾选“重启后自动恢复”，会写入本机用户私有目录
（macOS: `~/Library/Application Support/OpenWrite/`，其他: `~/.config/openwrite/`）的
`0600` 凭据文件，不写入作品、Git 或浏览器存储。

项目搜索还需要 embedding 模型。模型档案中的 `Embedding Base URL / 模型 / 维度 / API Key`
默认沿用主模型连接；OpenAI 或同时提供聊天与 embedding 的兼容网关通常无需额外配置。
DeepSeek、Anthropic 本身不提供 embedding，需要在同一档案中填写独立的 OpenAI 兼容
embedding 端点和 Key。LightRAG 索引保存在作品私有目录的 `.openwrite/lightrag/`，不会进入 Git。
首次搜索或文档发生变化时会增量提取实体与关系；后续未变化的文档不会重复入库。
服务器或 CLI 也可以用环境变量配置 embedding：

```bash
export OPENWRITE_LIGHTRAG_EMBEDDING_BASE_URL=https://api.openai.com/v1
export OPENWRITE_LIGHTRAG_EMBEDDING_API_KEY=your-embedding-key
export OPENWRITE_LIGHTRAG_EMBEDDING_MODEL=text-embedding-3-small
export OPENWRITE_LIGHTRAG_EMBEDDING_DIM=1536
```

LightRAG 的实体提取和查询使用 Chat Completions 兼容的模型端点；只提供 Responses 专用接口的网关不能作为搜索路由。

CLI 或服务器部署仍可使用环境变量：

```bash
export LLM_API_KEY=your-key
export LLM_MODEL=glm-5

# 如果你走兼容 OpenAI 的端点，也可以设置：
# export LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/chat/completions
```

### 3. 新书先开 Goethe

```bash
openwrite goethe
```

进入 Goethe 之后，先把这几类信息聊清楚：

- 题材、基调和核心卖点
- 主角、主要矛盾、想避免的套路
- 基础设定、人物草案、当前可写范围大纲
- 你自己的样文、设定素材或同人来源文本

### 4. 资产够写了，再切 Dante

```bash
openwrite dante
```

如果项目已经存在，而且你已经有可写资产，通常不需要重新开 Goethe，直接：

```bash
source .venv/bin/activate
openwrite dante
```

### 5. 用创作罗盘约束近期写作

```bash
openwrite focus set "完成第一卷中段反转，让主角主动承担代价" \
  --keep "克制的叙述视角" \
  --keep "师徒关系的信任裂缝" \
  --avoid "靠新能力强行解围"
```

这不是一条只在终端显示的备注。Dante 和章节写作管线组装上下文时，会把创作罗盘作为当前最高优先级控制面。

如果你已有小说正文，可以先导入再续写：

```bash
openwrite import existing-novel.txt
openwrite desk
openwrite dante
```

### 6. 打开小说 Studio

```bash
openwrite studio
```

如果还没有把 CLI 安装到当前 shell，可以先用仓库内 Python 直接启动：

```bash
cd /path/to/Openwrite
.venv/bin/python -m tools.cli studio --project ~/my_novel --debug
```

所有顶层命令都支持 `--project`，因此无需先切换到作品目录：

```bash
cd /path/to/Openwrite
.venv/bin/python -m tools.cli status --project ~/my_novel
.venv/bin/python -m tools.cli sync --project ~/my_novel --check
.venv/bin/python -m tools.cli doctor --project ~/my_novel
```

想让 `openwrite` 在任意目录可用，可先执行：

```bash
cd /path/to/Openwrite
source .venv/bin/activate
python -m pip install -e .
openwrite studio --project ~/my_novel --debug
```

如果当前目录还没有项目，Studio 会直接显示“创建第一本小说”页面；填写书名和小说 ID 后即可生成完整目录，无需先运行 `openwrite init`。作品目录是可选项：从框架仓库启动时会默认在 `~/OpenWriteNovels/<书名>` 创建独立作品目录。

Studio 默认只绑定 `127.0.0.1`，不会把作品文件暴露到局域网。它和 CLI 使用同一个项目目录、同一个 `src/` 真源和同一套章节写作管线；浏览器中保存的文档会做版本冲突检查，避免覆盖其他编辑器刚写入的内容。章节审稿结果保存在 `data/reviews/ch_*.json`，分数和问题明细可在 Studio 中回看；工作台同时汇总累计 token 与已审章节均分。

排查 Agent 后台行为时可用 `openwrite studio --debug`，或设置 `OPENWRITE_DEBUG=1`。Debug 日志会写入当前作品的 `data/logs/studio-debug.log`，包含 Studio 请求、会话状态、Dante/Goethe 工具调用摘要和错误原因；API Key、Token、密钥类字段会脱敏。

写章不是一次模型调用后直接保存。默认链路会依次生成正文、提取客观事实、结算运行态，并把三阶段 token 合计写入章节记忆。作品级锁覆盖上下文读取、模型调用和最终提交；如果真相文件或记忆保存失败，系统会恢复旧正文与写前状态。

Studio 的主要前端工作区：

| 工作区 | 可完成的真实流程 |
|---|---|
| 总览 | 进度、资产就绪度和下一步建议 |
| 大纲 / 故事 / 人物 / 世界 | 管理卷幕章节、故事资料、角色档案和世界规则；这些确认版资料会成为 AI 的创作依据 |
| 正文 / 编辑器 | 打开、编辑和审阅已写章节，并提供版本冲突保护 |
| AI 协作 | Goethe 规划会话、Dante 正文会话，复用持久化 session 与工具调用；后台实时显示模型、工具、校验和落盘阶段；AI 回复支持安全 CommonMark 渲染 |
| 搜索 / 连续性 / 工具箱 | LightRAG 图谱/向量搜索、写前写后状态与关系/伏笔校验，以及同步、导入、导出和来源处理 |
| 顶栏 | 切换作品、配置模型，以及在大纲准备好后启动“写下一章” |
| 写作闭环 | 写下一章、章节记忆与 truth 结算、37 维审稿、Markdown/TXT 导出 |

模型连接可以直接在 Studio 顶部点击模型状态进行配置。API Key 默认只在当前 Studio 服务进程中使用；勾选记住后写入本机私有凭据文件，不写入项目文件，也不会返回给浏览器。

## 最推荐的工作流

### 1. 先用 Goethe 把灵感整理成可写资产

Goethe 不是一次性建书器，而是长期 planning 会话。先在 Goethe 里把 idea 讲清楚：

- 题材和基调
- 主角与核心冲突
- 想避免的套路
- 你自己的样文、同人来源或设定素材
- 当前最想推进到哪

推荐会话像这样：

```text
$ openwrite goethe

我想写一本都市职场异能小说。
主角是普通上班族，晚上能看到异常术式。
先帮我汇总一下当前想法。
这个汇总可以，再整理成基础设定和人物草案。
把大纲推进到能写第六章的范围。
```

### 2. 再让 Dante 推进正文

当人物、设定和当前可写范围大纲已经成型后，再进入 Dante：

```text
$ openwrite dante

写第六章，3500 字，冲突更直接。
写完后先自审，再告诉我有没有设定冲突。
```

### 3. 让 Goethe 先做汇总，再做大纲；让 Goethe 显式 handoff 给 Dante

最稳的顺序是：

1. 聊 idea
2. 让 Goethe 汇总 idea
3. 确认基础设定
4. 生成或修改可写范围大纲
5. Goethe 在资产满足条件后显式 handoff 给 Dante
6. 再交给 Dante 写章和审查

不推荐直接说“给我写一章”，尤其是在设定和大纲还没稳定的时候。

### 4. 只有少数场景才建议直达命令

| 你要做什么 | 推荐命令 | 说明 |
|---|---|---|
| 从零开始建项目或持续规划资产 | `openwrite goethe` | 长期 planning 入口 |
| 长期讨论、推进整本书 | `openwrite dante` | 主入口 |
| 强制写指定章节 | `openwrite write ch_006` | 直达写作 |
| 用子流程写章 | `openwrite multi-write ch_006` | director/writer/reviewer 子流程 |
| 单独审查已写章节 | `openwrite review ch_006` | 不进聊天 |
| 查看 canonical packet | `openwrite context ch_006 --show` | 查看章节上下文包 |
| 查看 Writer 实际首轮输入 | `openwrite context ch_006 --agent writer --show` | 审阅 system/user 消息及缺失项 |
| 查看 Reviewer 实际首轮输入 | `openwrite context ch_006 --agent reviewer --show` | 审阅正文、目标、正典与审稿 Prompt |
| 导出 canonical packet | `openwrite assemble ch_006 --output-dir out` | 调试写作输入 |
| 查看当前运行态 | `openwrite status` | 看进度和阶段 |

你完全可以把这张表当“高级操作列表”：

- 日常创作：优先 `goethe` / `dante`
- 精确控制、调试或自动化脚本：再用这些直达命令

## Dante 会帮你做什么

`openwrite dante` 是长期会话 ReAct 主 agent。它会在会话里自己决定什么时候：

- 基于现有人物、设定和大纲推进正文
- 做章节 preflight
- 调 `write` 或 `multi-write`
- 调 `review`
- 必要时提出并回修人物、设定或大纲
- 推进 `book_state.yaml` 和 `wf_ch_*.yaml`

它不是一次一问一答的 prompt 包装，而是一个以正文推进为中心的持续工作主编排入口。

### Dante 的上下文来源

写章前，系统会组 canonical packet，而不是只拼一段 prompt。典型会包含：

- 当前可写范围大纲
- 故事背景和基础设定
- 相关角色文档
- 相关概念与世界规则
- 上一章正文
- `current_state.md`、`ledger.md`、`relationships.md`
- 风格合成结果和 craft 规则

Dante 也会接收 Goethe 交接过来的 handoff 摘要和当前可写窗口，不需要你从头重新解释前情。

当组装结果超过 `OPENWRITE_CONTEXT_TOKENS` 时，发送前使用四级渐进压缩：

1. 先压缩可从正文重建的旧章节记忆；
2. 再压缩大纲摘要，并始终保留以当前章为中心的窗口；
3. 然后压缩真相状态、人物数量和精确上文；
4. 最后才启用提供商硬适配，并重新估算以保证不超过预算。

作者意图、创作罗盘和当前章在前三层不会被删除。每次组装都会产生
`tiered-hierarchical-v2` 压缩报告，记录原始/最终估算 token、触发级别和动作。
完整原文、JSONL 会话历史和 `src/` 真源不会因发送前压缩而被改写。

所以更好的提问方式是给目标和约束，而不是直接指挥它去改某个缓存文件。

## 什么时候才手工改文件

绝大多数情况下，你不需要手工维护运行态文件。

推荐：

- 和 Dante 聊，确认后让它推进
- 需要人工修正时，只改 `src/` 下的确认版真源
- 改完 `src/` 后，用 `openwrite sync` 刷新派生缓存

不推荐：

- 手改 `data/hierarchy.yaml`
- 手改 `data/characters/cards/*.yaml`
- 手改 `data/workflows/wf_ch_*.yaml`
- 把 `background_draft.md`、`foundation_draft.md`、`outline_draft.md` 当另一套真相长期维护

如果你在问“该不该改这个 `data/` 文件”，大多数情况下答案都是“不该”。

## 目录心智

```text
data/novels/{novel_id}/
├── src/
│   ├── outline.md
│   ├── story/
│   │   ├── author_intent.md
│   │   ├── current_focus.md
│   │   ├── background.md
│   │   └── foundation.md
│   ├── characters/*.md
│   └── world/
│       ├── rules.md
│       ├── terminology.md
│       ├── timeline.md
│       └── entities/*.md
└── data/
    ├── planning/
    │   ├── ideation.md
    │   ├── ideation_summary.md
    │   ├── background_draft.md
    │   ├── foundation_draft.md
    │   └── outline_draft.md
    ├── manuscript/arc_*/ch_*.md
    ├── world/
    │   ├── current_state.md
    │   ├── ledger.md
    │   └── relationships.md
    ├── foreshadowing/dag.yaml
    ├── style/
    │   ├── composed.md
    │   ├── fingerprint.yaml
    │   └── manifest.toml
    ├── sources/{source_id}/
    │   ├── source.md
    │   ├── setting_profile.md
    │   ├── style/*.md
    │   └── extraction/
    ├── workflows/
    │   ├── book_state.yaml
    │   ├── goethe_session.yaml
    │   ├── goethe_session.jsonl
    │   ├── agent_session.yaml
    │   ├── agent_session.jsonl
    │   └── wf_ch_*.yaml
    ├── hierarchy.yaml
    ├── characters/cards/*.yaml
    └── test_outputs/
```

其中：

- `src/outline.md` 是唯一大纲真源
- `src/story/author_intent.md` 是整本书长期不变的创作承诺
- `src/story/current_focus.md` 是近期写作最高优先级约束，可随篇章推进更新
- `data/hierarchy.yaml` 是派生缓存
- `data/planning/ideation.md` 和 `data/planning/ideation_summary.md` 是会话与规划运行态
- `data/world/*.md` 和 `data/workflows/*` 是运行时状态；`*session.jsonl` 是完整对话归档

### 风格文件说人话

如果你把一篇参考文章交给系统，风格这条链会产出 3 层东西：

- `data/sources/{source_id}/`
  这是“拆书笔记”。AI 会把你提供的文章拆成来源说明、设定提要、叙述声音、语言习惯、节奏、对话等文档。
- `data/style/manifest.toml`
  这是“整理后的风格清单”。系统会把拆书笔记归一成：哪些能学、哪些不能照搬、哪些是作品约束、哪些是对话/叙述/节奏规则。
- `data/style/composed.md`
  这是“最终给这本书用的风格说明书”。Writer 真正主要参考的是它，而不是直接照着来源文章写。

一句话记忆：

- `sources/{source_id}` = 参考文章的拆解笔记
- `manifest.toml` = 拆解笔记整理后的可用风格清单
- `composed.md` = 给你这本书使用的最终风格说明书

## 常用命令

### 主入口

- `openwrite` / `openwrite desk`
- `openwrite dante`
- `openwrite goethe`

### 创作控制与交付

- `openwrite studio`
- `openwrite focus show`
- `openwrite focus set "本阶段目标" --keep "必须保留" --avoid "必须避免"`
- `openwrite import existing-novel.txt`
- `openwrite export --format md`
- `openwrite export --format txt -o exports/my-book.txt`
- `openwrite status --json`

### 写作与审查

- `openwrite write next`
- `openwrite write ch_006`
- `openwrite multi-write ch_006`
- `openwrite review`
- `openwrite review ch_006`

### 诊断与上下文

- `openwrite status`
- `openwrite doctor`
- `openwrite context ch_006 --show`
- `openwrite assemble ch_006 --output-dir out`
- `openwrite sync --check`
- `openwrite sync`

`context --agent` 是只读检查，不调用模型，也不修改会话或小说文件。它输出该 Agent
真正使用的首轮消息、字段完整性、来源文件 revision、截断提示、模型标识和检查结果哈希：

```bash
# Writer：检查创作阶段真正发送的 system/user 消息
openwrite context ch_007 --agent writer --show

# 带本次临时参数检查 Writer
openwrite context ch_007 --agent writer --target-words 1200 \
  --guidance "不要提前揭示旧案真相" --show

# Reviewer：章节必须已经存在；会沿用最近成功 run 的 effective target
openwrite context ch_006 --agent reviewer --show

# Dante/Goethe：提供准备发送的用户指令，检查首轮 ReAct 消息与工具表
openwrite context ch_007 --agent dante --instruction "检查下一章是否可写" --show
openwrite context ch_007 --agent goethe --instruction "检查当前规划缺口" --show

# JSON 适合 diff、Agent 调试或保存审计证据
openwrite context ch_007 --agent writer --format json -o context-review.json
```

Dante 和 Goethe 的首轮消息只包含 system prompt、会话/书状态和用户指令，不会预先塞入整份
章节 packet；它们通过 `get_context`、`get_outline_structure` 等工具按需读取。检查结果会明确提示
这一点，避免把“工具可读取”误认为“模型首轮已经看到”。

### Agent 运行时审计工具

Dante 和 Goethe 还共享一组只读审计工具。它们适合在 Agent 会话中回答“这次到底看到了什么、之前实际跑了什么、现在落盘了什么”，不会修改小说、会话、任务或迁移旧数据：

| 工具 | 能审阅什么 |
|---|---|
| `inspect_agent_context` | 指定章节和 Agent 的首轮消息、字段完整性、来源 revision、压缩报告和工具表；需要时可展开完整消息或 payload |
| `list_chapter_runs` | 按章节/状态查看写作 run manifest，包括目标字数、模型、上下文/正文/审稿 revision、阶段、usage 和错误码 |
| `get_runtime_state` | 查看当前结构化运行态及旧文档的字符数/hash 清单，不返回整份敏感旧文档 |
| `get_chapter_review` | 查看最近审稿结果、问题明细、当前正文 revision，以及结果是否 stale |
| `get_task_activity` | 查看任务摘要，或指定任务的快照和追加事件；凭据类字段会脱敏 |
| `get_goethe_handoff` | 查看 Goethe 交给 Dante 的 YAML manifest、Markdown 摘要、路径和 revision |

可以直接这样要求 Agent 做审计：

```text
请用 inspect_agent_context 检查 ch_007，列出首轮消息实际包含的来源和缺失项。
再用 list_chapter_runs 查看这个章节最近一次运行的 effective target、模型和审稿 revision。
如果审稿结果存在，用 get_chapter_review 告诉我它是否已经过期。
```

审计工具和 `openwrite context ...` 的区别是：`context` 检查器展示准备发送的首轮输入，不创建会话也不调用模型；审计工具可以在 Dante/Goethe 会话中继续检查运行后的 run、任务、审稿和 handoff。Agent 的 live self-inspection 默认排除已经持久化的当前用户轮次，避免审阅结果重复计算本轮指令。

### 风格与题材

- `openwrite style extract office_excerpt --source text.txt`
- `openwrite setting extract office_excerpt --source text.txt`
- `openwrite source review office_excerpt`
- `openwrite source promote office_excerpt --target all`
- `openwrite style synthesize`
- `openwrite radar`

其中 `source promote --target all` 会同时：
- 切换 `style_id`
- 更新 `foundation.md`
- 把规则、时间线和阵营拆进 `src/world/*.md`

旧的 `openwrite agent` 已退役，请改用 `openwrite dante`。

## Agent 分工

- `Dante`
  正文创作主 ReAct agent。默认负责基于人物、设定和大纲推进章节正文、预检、审查和状态结算；必要时为正文推进回修资产。
- `Goethe`
  长会话规划 agent。更适合从零开始汇总灵感、提建议、写背景、写人物、写设定和写大纲，并在资产成熟后显式 handoff 给 Dante。
- `write`
  direct CLI 写作入口。适合明确知道要写哪一章时使用。
- `multi-write`
  Dante 可调度的写作子流程。内部会编排 director、writer、reviewer。
- `review`
  独立审查入口。适合对现有章稿做单独质量检查。

## 常见问题

### 我应该先用 Goethe 还是 Dante

新书第一次启动，先 `openwrite goethe`。  
一旦书建立好了，日常推进基本都用 `openwrite dante`。

### 我改了 `src/`，为什么写作结果没变

先执行：

```bash
openwrite sync
```

### `outline_draft.md` 是不是另一份大纲真相

不是。`src/outline.md` 才是唯一真源。  
`outline_draft.md` 是给运行态和 workflow 可见性的待确认草稿。Goethe 修改已有大纲时会先读取
目标片段，按精确文本生成增量 diff；只有你明确确认后，草稿才会写入 `src/outline.md`。

### 为什么 Dante 有时候会先让我确认，而不是直接写

因为大纲、基础设定和 idea summary 都是门禁。  
这不是磨叽，是为了避免长篇写作在后面 20 章、50 章后开始漂。

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `LLM_API_KEY` | 模型 API Key | 无 |
| `LLM_PROVIDER` | 提供商 | `openai` |
| `LLM_MODEL` | 模型名 | `gpt-4o-mini` |
| `LLM_BASE_URL` | 自定义兼容网关 | `https://api.openai.com/v1` |
| `LLM_TEMPERATURE` | 默认温度 | `0.7` |
| `LLM_MAX_TOKENS` | 最大输出 token | `24000` |
| `OPENWRITE_CONTEXT_TOKENS` | OpenWrite 内部写作上下文预算；不是服务商硬上限 | `64000` |
| `OPENWRITE_SESSION_RECENT_TURNS` | 恢复时保留的最近会话轮数 | `24` |
| `OPENWRITE_SESSION_STATE_BYTES` | 滚动会话状态上限（字节） | `131072` |
| `OPENWRITE_SESSION_SUMMARY_BYTES` | 压缩摘要上限（字节） | `16384` |
| `OPENWRITE_SESSION_TURN_BYTES` | 单轮进入滚动状态的上限（字节） | `2048` |
| `OPENWRITE_DEBUG` | Studio 后台 debug 日志开关 | 未开启 |
| `LLM_TIMEOUT_SECONDS` | 请求超时秒数 | SDK 默认 |
| `LLM_MAX_RETRIES` | 重试次数 | SDK 默认 |

## 版本

当前版本：`5.8.0`
