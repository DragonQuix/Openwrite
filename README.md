<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.svg">
    <img src="assets/logo-light.svg" width="360" alt="OpenWrite">
  </picture>
</p>

<h1 align="center">面向长篇小说的 AI Agent 创作工作台<br><sub>从灵感、设定和大纲，一直写到审稿与成书</sub></h1>

<p align="center">
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/version-5.8.0-2563eb" alt="Version 5.8.0"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/Python-%E2%89%A53.10-22c55e?logo=python&logoColor=white" alt="Python >= 3.10"></a>
  <a href="https://github.com/LiPu-jpg/Openwrite/stargazers"><img src="https://img.shields.io/github/stars/LiPu-jpg/Openwrite?style=flat&color=f59e0b" alt="GitHub Stars"></a>
  <a href="https://github.com/LiPu-jpg/Openwrite/issues"><img src="https://img.shields.io/github/issues/LiPu-jpg/Openwrite?color=ef4444" alt="GitHub Issues"></a>
</p>

<p align="center">
  <strong>长篇小说不是一次性 Prompt。</strong><br>
  OpenWrite 把作者意图、人物与世界状态、滚动大纲、章节记忆、写作、审稿和修订放进同一条可持续的创作流程。
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#推荐工作流">推荐工作流</a> ·
  <a href="#studio-工作台">Studio</a> ·
  <a href="#命令行速查">CLI</a> ·
  <a href="#工作原理">工作原理</a>
</p>

## OpenWrite 是什么

OpenWrite 专注于一件事：**让一本长篇小说在几十章、几百章之后仍然写得下去，而且不轻易丢失作者意图和故事事实。**

它不是只负责生成下一段文字的聊天壳，而是一套本地优先的小说创作系统：Goethe 负责把想法整理成可写资产，Dante 负责持续写作与审查；Studio、CLI 和两个 Agent 共用同一个小说内核，最终状态以真实工具结果和落盘文件为准。

```text
灵感与素材
    ↓
Goethe：规划、人物、设定、大纲
    ↓  确认可写资产
Dante：组装上下文 → 写章 → 审稿 → 修订 → 状态结算
    ↓
Markdown / TXT / EPUB
```

## 核心能力

| 能力 | OpenWrite 如何处理 |
|---|---|
| **Goethe / Dante 双 Agent** | Goethe 长期整理创意与正典资产，Dante 持续推进正文、审稿和运行态；两者通过明确 handoff 衔接。 |
| **创作罗盘与单一真源** | `author_intent.md` 保存全书承诺，`current_focus.md` 保存近期目标；人物、世界和大纲以 `src/` 为确认版真源。 |
| **面向长篇的上下文** | 最近正文、滚动大纲、相关人物、世界规则、伏笔、真相文件、精确人物时态和语义召回共同组成 canonical packet。 |
| **可追踪的人物与世界状态** | 章节结算会更新客观事实；轻量内联批注可记录人物位置、伤势、认知等时态变化，并检查连续性冲突。 |
| **可靠写章与版本保护** | 作品级锁覆盖写章过程；正文、真相文件和章节记忆作为一个提交单元，失败时恢复写前快照。正文支持 checkpoint、批注和 AI 修订 diff。 |
| **37 维审稿闭环** | 审稿读取作者意图、正典、关系、风格和章节目标；问题可在 Studio 中定位、筛选并生成可审阅的修订提案。 |
| **私有参考库与风格采纳** | 对小说、旧稿和 Canon 做证据化拆解；只有人工明确选中的风格、规则或设定候选才会进入当前项目。 |
| **本地与云端检索** | 默认可使用本机 FastEmbed 完成向量检索，也支持 OpenAI-compatible embedding；需要关系遍历时再启用 LightRAG 图谱模式。 |
| **完整创作工作台** | Studio 覆盖建书、模型配置、资产编辑、AI 协作、搜索、连续性、参考库、Skills、导入、导出和诊断。 |
| **标准 SKILL.md 扩展** | Goethe / Dante 可按轮次启用标准 Skill；Skill 只提供有界指令和静态参考资料，不能绕过原有权限与写入确认。 |

## 快速开始

### 方式一：双击启动

下载或克隆完整仓库后，直接双击根目录中的启动文件：

- macOS：`启动 OpenWrite.command`
- Windows：`启动 OpenWrite.bat`

启动器会检查 Python 3.10+，在 `.openwrite-runtime/` 中创建隔离环境，安装或更新依赖并打开 Studio。它不会静默安装 Python；首次安装依赖需要联网。

### 方式二：从源码安装

```bash
git clone https://github.com/LiPu-jpg/Openwrite.git
cd Openwrite
python3.10 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -e .
openwrite studio
```

Studio 默认只绑定 `127.0.0.1`。首次打开会引导你配置模型、创建作品、完善故事资产并开始写作。从框架仓库创建作品时，默认目录为仓库同级的 `OpenWriteNovels/<书名>`（仓库位于用户目录时即 `~/OpenWriteNovels/<书名>`），也可以在创建对话框中指定其他位置。

### 快速体验示范项目

如果想跳过规划，先体验完整写章流程：

```bash
openwrite init demo_novel --title "雾城来信" --template demo_short
openwrite studio
```

`demo_short` 只是一套三章练习资产；正式长篇建议从 Goethe 开始建立自己的作者意图、人物、设定和滚动大纲。

### 配置模型

推荐在 Studio 顶栏打开 **模型设置**。当前支持 OpenAI、Anthropic 和自定义 OpenAI-compatible 连接，可配置模型、Base URL、API Key、上下文窗口和最大输出，并在保存前测试连接。

API Key 默认只保存在当前 Studio 进程。选择“重启后自动恢复”后，密钥会写入本机用户私有目录的 `0600` 凭据文件，不会进入小说项目、Git 或浏览器存储。

CLI 和服务器环境也可以使用环境变量：

```bash
export LLM_API_KEY=your-key
export LLM_MODEL=your-model

# 自定义 OpenAI-compatible 端点时再设置
export LLM_BASE_URL=https://api.example.com/v1
```

## 推荐工作流

日常创作只需要先记住两个入口：

```bash
openwrite goethe   # 把灵感整理成可写资产
openwrite dante    # 持续写作、审稿和推进状态
```

### 1. 先和 Goethe 把书聊清楚

Goethe 适合处理题材、基调、核心卖点、人物关系、世界规则、想避免的套路和当前可写范围。它会先汇总，再通过可预览的 diff 修改正式资产。

```text
$ openwrite goethe

我想写一本都市职场异能小说。
主角白天是普通上班族，晚上能看到异常术式。
先汇总卖点和风险，再整理人物草案；暂时不要直接写正文。
```

### 2. 资产可写后交给 Dante

当人物、设定和近期大纲已经成型，让 Goethe 明确 handoff，再进入 Dante：

```text
$ openwrite dante

写第六章，目标 3500 字。
保留师徒关系的信任裂缝，不要靠新能力强行解围。
写完后审查设定与人物动机。
```

Dante 会自行完成写前检查、上下文组装、写章、事实提取和状态结算；需要修改正典资产时，仍然先展示变更并等待本轮明确确认。

### 3. 用创作罗盘固定近期目标

```bash
openwrite focus set "完成第一卷中段反转，让主角主动承担代价" \
  --keep "克制的叙述视角" \
  --keep "师徒关系的信任裂缝" \
  --avoid "靠新能力强行解围"
```

创作罗盘会进入章节上下文，并作为近期最高优先级约束。整本书更长期的承诺则保存在 `author_intent.md`。

### 4. 已有正文可以直接接入

```bash
openwrite import existing-novel.txt
openwrite desk
openwrite dante
```

TXT 和 Markdown 旧稿可以按章节导入；写完后可导出 Markdown、TXT 或 EPUB。

## Studio 工作台

```bash
openwrite studio
```

| 工作区 | 可以完成的流程 |
|---|---|
| **总览** | 查看写作进度、资产就绪度、审稿均分、token 使用和下一步建议。 |
| **大纲 / 故事 / 人物 / 世界** | 管理卷、节、章纲，编辑作者意图、人物档案、关系与世界规则。 |
| **正文 / 审稿** | 编辑章节、自动保存、检查版本冲突，管理 checkpoint、批注、审稿问题和 AI 修订提案。 |
| **AI 协作** | 使用持久化 Goethe / Dante 会话，并查看真实模型调用、工具执行、校验和落盘阶段。 |
| **搜索 / 连续性** | 跨正典、正文和参考资料检索，检查人物状态、关系、时间线和伏笔。 |
| **参考库** | 导入参考作品或旧稿，确认卷章覆盖，生成证据报告、对照画像并显式采纳。 |
| **Skills** | 查看已发现的标准 Skill 与 Runtime Skill，检查来源和权限，并交给 Goethe 或 Dante 使用。 |
| **工具箱** | 检查 canonical packet，执行项目迁移、同步、诊断、整书导入与 Markdown / TXT / EPUB 导出。 |

浏览器中的文档保存带有 revision 冲突检查，避免覆盖外部编辑器刚写入的内容。使用 `openwrite studio --debug` 可把脱敏后的后台日志写入当前作品的 `data/logs/studio-debug.log`。

## 工作原理

### 一个小说内核，四个入口

CLI、Studio、Goethe 和 Dante 不各自维护一套写章逻辑。它们共用同一个 action surface 和小说应用服务：

```text
CLI ─────┐
Studio ──┼─> Novel action surface ─> NovelApplicationService
Goethe ──┤                              ├─ canonical packet
Dante ───┘                              ├─ write / review / revision
                                        ├─ source and reference lifecycle
                                        └─ workflow / truth / memory / BookState
```

章节 ID、项目锁、事务回滚、错误码、workflow 推进、审稿存储和 BookState 结算因此只有一份契约。完成态来自工具结果与文件状态，不依赖模型口头宣称“已经完成”。

### 单一真源与运行态分离

```text
data/novels/{novel_id}/
├── src/                         # 人和 AI 共读的确认版真源
│   ├── outline.md
│   ├── story/
│   │   ├── author_intent.md
│   │   ├── current_focus.md
│   │   ├── background.md
│   │   └── foundation.md
│   ├── characters/*.md
│   └── world/*.md
└── data/                        # 运行态、正文、缓存与快照
    ├── manuscript/arc_*/ch_*.md
    ├── memory/chapters/
    ├── reviews/
    ├── style/
    ├── world/
    ├── workflows/
    └── planning/
```

- `src/outline.md` 是唯一大纲真源。
- `src/story/author_intent.md` 保存全书长期承诺。
- `src/story/current_focus.md` 保存当前阶段最高优先级目标。
- `data/` 保存正文、会话、章节记忆、审稿、状态、缓存和 workflow；通常不需要手工维护。
- 手改 `src/` 后运行 `openwrite sync`，让派生数据与真源重新对齐。

### 长篇上下文与人物时态

写章时，最近两章正文固定进入上下文。系统再按本章大纲和出场人物召回最多 4 段更早正文，以及 2 段拆书或参考资料。语义召回只补充远距离记忆，不会覆盖正典或精确人物状态。

人物时态可以用轻量批注记录：

```text
//**沈烬：仍在试探 -> 确认白续是敌人**
//**沈烬[位置]：贫民区工坊 -> 归墟港**
//**沈烬[伤势]：左臂轻伤 -> 已恢复**
```

系统会从大纲和正文重建索引，区分计划状态与实际状态，并报告状态断裂及来源行号。有效批注不会计入正文字数，也不会进入 Markdown、TXT 或 EPUB 成书。完整语法见 [内联人物状态批注](docs/inline-character-state.md)。

### 有界记忆与可靠提交

每章完成后，OpenWrite 会保存摘要、客观观察、状态变化和各阶段 token 用量。下一章只注入与当前任务相关的有界信息，而不是把整本正文重新塞给模型。

作品级写锁覆盖上下文读取、模型调用和最终提交。正文、真相文件与章节记忆是同一个提交单元；其中任一步失败，系统会恢复写前快照，避免只写入半章或半份状态。

<details>
<summary><strong>上下文预算如何工作</strong></summary>

`OPENWRITE_CONTEXT_TOKENS` 表示模型的完整上下文窗口。系统会先扣除 `LLM_MAX_TOKENS` 输出预留和安全余量，再为章节 packet、Agent 会话、工具结果和最近消息分配输入预算。

当输入压力升高时，系统按稳定优先级渐进压缩：先处理可从正文重建的旧章节记忆，再处理大纲摘要，然后收缩真相状态、人物数量和精确上文，最后才启用提供商级硬适配。作者意图、创作罗盘和当前章在前几级不会被删除；原文、JSONL 会话历史和 `src/` 真源也不会因发送前压缩而被改写。

可用以下命令检查 Writer 或 Reviewer 实际收到的首轮消息、来源 revision、token 估算和截断报告：

```bash
openwrite context ch_007 --agent writer --show
openwrite context ch_006 --agent reviewer --show
```

</details>

## 项目搜索与参考库

### 本地向量或云端 embedding

默认的“向量 + 精确”策略只生成向量，不调用聊天模型；使用本机 FastEmbed 时，即使没有聊天 API Key 也可以搜索。需要实体关系遍历时，可切换为“图谱 + 向量”，由 LightRAG 额外调用 Chat Completions-compatible 模型提取实体与关系。

索引保存在作品私有目录 `.openwrite/lightrag/`，不会进入 Git。正典、人物、世界设定、正文、风格资料和已采用的参考资料会增量入库，未变化的文档不会重复生成向量。

```bash
# 本地 embedding
export OPENWRITE_LIGHTRAG_EMBEDDING_PROVIDER=local
export OPENWRITE_LIGHTRAG_MODE=naive
export OPENWRITE_FASTEMBED_CACHE_DIR=~/.cache/openwrite/fastembed

# OpenAI-compatible 云端 embedding
export OPENWRITE_LIGHTRAG_EMBEDDING_PROVIDER=openai
export OPENWRITE_LIGHTRAG_EMBEDDING_BASE_URL=https://api.openai.com/v1
export OPENWRITE_LIGHTRAG_EMBEDDING_API_KEY=your-embedding-key
export OPENWRITE_LIGHTRAG_EMBEDDING_MODEL=text-embedding-3-small
export OPENWRITE_LIGHTRAG_EMBEDDING_DIM=1536
```

### 参考作品不会自动污染项目

Studio 的 **项目迁移** 用于导入或导出完整 OpenWrite 项目；**参考库** 用于导入参考小说、自己的旧稿或同人 Canon，并进行证据化拆解。两者互不替代。

参考库默认位于 `~/.local/share/openwrite/reference-library/`，不直接进入小说工作区。导入意图决定哪些内容有资格成为候选：普通参考作品的专名、人物和具体情节不能晋升；自己的旧稿、同人 Canon 或反向重建项目可以提出设定候选，但不能把来源事实伪装成风格规则。

采用流程始终是：

```text
导入并确认范围 → 证据化拆解 → 单部报告 / 多作品对照
→ Goethe 讨论取舍 → 预览采纳 diff → 用户明确确认 → 编译进项目
```

每条候选都需要明确目标、主辅角色、适用范围和是否采用。Dante 只消费已经确认的项目资产，不替用户浏览私有参考库或暗中选择作品。

## 标准 Skills

OpenWrite 可以发现标准 `SKILL.md`，并继续兼容 `.openwrite/skills/*/manifest.yaml` Runtime Skill。标准 Skill 可以放在：

- 当前作品的 `.agents/skills/<skill-id>/SKILL.md` 或 `skills/<skill-id>/SKILL.md`
- 用户目录的 `~/.agents/skills/` 或 `~/.openclaw/skills/`
- `OPENWRITE_SKILL_DIRS` 指定的一个或多个目录

Skill 默认不会进入所有写作上下文。在 Goethe 或 Dante 的本轮消息中用 `@skill-id` 明确启用：

```text
@scene-causality 检查 ch_007 的场景因果链，只给诊断和优先建议。
```

下一轮未再次指定时会恢复默认上下文。Skill 中的脚本不会自动执行，工具权限只能被收窄，不能借 Skill 扩权或绕过写入确认。

```bash
openwrite skill list
openwrite skill resolve --agent dante --task chapter.write --skill scene-causality
openwrite skill diagnose
```

## 命令行速查

### 日常入口

| 目标 | 命令 |
|---|---|
| 查看进度与下一步建议 | `openwrite` 或 `openwrite desk` |
| 持续规划小说资产 | `openwrite goethe` |
| 持续写作与审查 | `openwrite dante` |
| 打开本地 Web 工作台 | `openwrite studio` |
| 写下一章 / 指定章节 | `openwrite write next` / `openwrite write ch_006` |
| 使用 Director、Writer、Reviewer 子流程 | `openwrite multi-write ch_006` |
| 单独审查章节 | `openwrite review ch_006` |
| 查看进度和运行态 | `openwrite status` |

### 诊断与交付

| 目标 | 命令 |
|---|---|
| 查看 canonical packet | `openwrite context ch_006 --show` |
| 检查实际 Agent 输入 | `openwrite context ch_006 --agent writer --show` |
| 导出上下文包 | `openwrite assemble ch_006 --output-dir out` |
| 检查环境与项目 | `openwrite doctor` / `openwrite diagnose` |
| 检查或刷新派生数据 | `openwrite sync --check` / `openwrite sync` |
| 导入已有正文 | `openwrite import existing-novel.txt` |
| 导出整书 | `openwrite export --format md` / `txt` / `epub` |
| 管理正文版本 | `openwrite version list ch_006` |
| 管理正文批注 | `openwrite annotation list ch_006` |

所有顶层命令都支持 `--project`，不需要先切换到作品目录：

```bash
openwrite status --project ~/my_novel
openwrite studio --project ~/my_novel --debug
```

`openwrite agent` 已退役；长期主编排入口是 `openwrite dante`。原子命令适合精确控制、调试和自动化，不是日常使用的前置负担。

## 常用环境变量

| 变量 | 用途 | 默认值 |
|---|---|---|
| `LLM_API_KEY` | 模型 API Key | 未设置 |
| `LLM_MODEL` | 默认模型 | `gpt-4o-mini` |
| `LLM_BASE_URL` | 自定义 OpenAI-compatible 网关 | 服务商默认值 |
| `LLM_TEMPERATURE` | 默认温度 | `0.7` |
| `LLM_MAX_TOKENS` | 最大输出 token | `24000` |
| `OPENWRITE_CONTEXT_TOKENS` | 模型完整上下文窗口 | `64000` |
| `OPENWRITE_SKILL_DIRS` | 额外标准 Skill 目录 | 未设置 |
| `OPENWRITE_REFERENCE_LIBRARY_ROOT` | 本机私有参考库目录 | `~/.local/share/openwrite/reference-library` |
| `OPENWRITE_DEBUG` | Studio 后台 debug 日志 | 未开启 |

## 常见问题

### 应该先用 Goethe 还是 Dante？

只有灵感、人物或设定还没有形成可写大纲时，先用 Goethe。已有可写资产或正在连载时，直接用 Dante。

### 修改 `src/` 后为什么没有立即生效？

`src/` 是确认版真源，`data/` 中存在为运行效率生成的派生数据。手工修改后运行 `openwrite sync`；可先用 `openwrite sync --check` 只检查差异。

### `outline_draft.md` 是另一份大纲吗？

不是。它只是规划过程中的草稿或候选，`src/outline.md` 才是唯一确认版大纲。

### 为什么 Agent 有时先要求确认？

因为对大纲、人物、世界、关系、参考采纳等正式资产的写入必须先展示 diff，并且只接受本轮明确确认。这个确认不能由旧消息、模型猜测或口头完成声明替代。

### 小说数据会上传到哪里？

项目文件、会话和索引默认保存在本机。只有真正发送给所选模型或 embedding 服务的请求内容会离开本机；选择本地 FastEmbed 可以让向量生成也留在本机。

## 开发与测试

```bash
python -m pip install -e ".[dev]"
pytest
```

提交问题或功能建议请前往 [GitHub Issues](https://github.com/LiPu-jpg/Openwrite/issues)。项目仍在持续迭代，涉及作品数据的改动建议先在自己的项目仓库中保留版本记录。

## 致谢

项目搜索使用 MIT 许可的 [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG)。也感谢所有推动 AI 写作、长上下文、知识检索和创作工作流发展的开源项目与贡献者。

特别感谢真诚、友善、团结、专业的 [Linux DO 社区（L 站）](https://linux.do/)，这里汇集了大量关于 AI、开发与开源实践的高质量讨论。

<p align="center">
  <strong>Linux DO · 新的理想型社区</strong><br>
  <a href="https://linux.do/">https://linux.do/</a>
</p>
