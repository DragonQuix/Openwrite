from pathlib import Path

from tools.frontmatter import parse_toml_front_matter
from tools.story_planning import StoryPlanningStore


def test_append_ideation_writes_runtime_draft(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")

    store.append_ideation("主角是社畜术师")
    store.append_ideation("设定偏现代都市修仙")

    assert "主角是社畜术师" in store.ideation_path.read_text(encoding="utf-8")
    assert "设定偏现代都市修仙" in store.ideation_path.read_text(encoding="utf-8")


def test_ideation_summary_tracks_current_ideation_hash(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")

    store.append_ideation("主角是社畜术师")
    store.save_ideation_summary(
        """# 当前想法汇总

## 核心方向

- 都市职场异能
"""
    )

    assert store.ideation_summary_path.exists()
    assert store.ideation_summary_is_current() is True

    store.append_ideation("公司地下还有隐秘节点")

    assert store.ideation_summary_is_current() is False


def test_confirmed_ideation_summary_seeds_placeholder_foundation(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")
    store.story_src_dir.mkdir(parents=True, exist_ok=True)
    foundation_path = store.story_src_dir / "foundation.md"
    foundation_path.write_text(
        "# 基础设定\n\n## 核心机制\n\n（待填写）\n",
        encoding="utf-8",
    )
    store.append_ideation("主角是被雪藏的穿越艺人")
    store.save_ideation_summary(
        "# 当前想法汇总\n\n## 核心方向\n\n- 娱乐圈成长爽文\n"
    )

    seeded = store.seed_placeholder_foundation_from_ideation_summary()

    assert seeded is True
    assert "娱乐圈成长爽文" in foundation_path.read_text(encoding="utf-8")
    assert foundation_path.read_text(encoding="utf-8") == (
        store.foundation_draft_path.read_text(encoding="utf-8")
    )


def test_confirmed_ideation_summary_does_not_replace_existing_foundation(
    tmp_path: Path,
):
    store = StoryPlanningStore(tmp_path, "demo")
    store.story_src_dir.mkdir(parents=True, exist_ok=True)
    foundation_path = store.story_src_dir / "foundation.md"
    foundation_path.write_text(
        "# 基础设定\n\n## 核心机制\n\n歌曲会触发记忆共鸣。\n",
        encoding="utf-8",
    )
    store.append_ideation("主角是被雪藏的穿越艺人")
    store.save_ideation_summary("# 当前想法汇总\n\n- 娱乐圈成长爽文\n")

    seeded = store.seed_placeholder_foundation_from_ideation_summary()

    assert seeded is False
    assert "歌曲会触发记忆共鸣" in foundation_path.read_text(encoding="utf-8")
    assert not store.foundation_draft_path.exists()


def test_promote_foundation_writes_src_story_files(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")

    store.save_foundation_draft(background="背景A", foundation="设定B")
    promoted = store.promote_foundation()

    assert promoted is True
    background_meta, background_body = parse_toml_front_matter(
        (store.story_src_dir / "background.md").read_text(encoding="utf-8")
    )
    foundation_meta, foundation_body = parse_toml_front_matter(
        (store.story_src_dir / "foundation.md").read_text(encoding="utf-8")
    )

    assert background_meta["id"] == "story_background"
    assert background_meta["type"] == "story_document"
    assert background_meta["summary"] == "背景A"
    assert background_body.strip() == "背景A"

    assert foundation_meta["id"] == "story_foundation"
    assert foundation_meta["type"] == "story_document"
    assert foundation_meta["summary"] == "设定B"
    assert foundation_body.strip() == "设定B"


def test_save_foundation_draft_normalizes_runtime_drafts(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")

    store.save_foundation_draft(background="背景A", foundation="设定B")

    background_meta, background_body = parse_toml_front_matter(
        store.background_draft_path.read_text(encoding="utf-8")
    )
    foundation_meta, foundation_body = parse_toml_front_matter(
        store.foundation_draft_path.read_text(encoding="utf-8")
    )

    assert background_meta["id"] == "story_background"
    assert foundation_meta["id"] == "story_foundation"
    assert background_body.strip() == "背景A"
    assert foundation_body.strip() == "设定B"
    assert not (store.story_src_dir / "background.md").exists()
    assert not (store.story_src_dir / "foundation.md").exists()


def test_load_story_document_returns_metadata_and_body(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")
    store.story_src_dir.mkdir(parents=True, exist_ok=True)
    (store.story_src_dir / "background.md").write_text(
        """+++
id = "story_background"
type = "story_document"
summary = "都市异能职场故事。"
detail_refs = ["premise", "conflict"]
+++

# 背景

都市异能职场故事。
""",
        encoding="utf-8",
    )

    document = store.load_story_document("background")

    assert document["meta"]["id"] == "story_background"
    assert document["meta"]["summary"] == "都市异能职场故事。"
    assert document["body"].lstrip().startswith("# 背景")


def test_outline_requires_confirmation_before_promotion(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")

    store.save_outline_draft("# 大纲草案")

    assert store.promote_outline(confirmed=False) is False
    assert store.outline_src_path.exists() is False
    assert store.outline_draft_path.read_text(encoding="utf-8") == "# 大纲草案"


def test_outline_promotion_requires_confirmed_draft(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")

    store.save_outline_draft("# 大纲草案")

    assert store.promote_outline(confirmed=True) is True
    assert store.outline_src_path.read_text(encoding="utf-8") == "# 大纲草案"
    assert store.outline_draft_path.read_text(encoding="utf-8") == "# 大纲草案"


def test_save_outline_draft_only_stages_until_confirmation(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")
    content = """# 大纲草案

#### 第一章：起步

> 内容焦点: 主角第一次接触异常。

<!-- OPENWRITE:LONG_RANGE_PLAN:START -->
# 长线规划

## 第一篇：未来规划
### 第一节：更远的冲突
- 概要：这里只是长线规划，不该进入当前可写窗口解析。
<!-- OPENWRITE:LONG_RANGE_PLAN:END -->
"""

    store.save_outline_draft(content)

    assert store.outline_src_path.exists() is False
    assert store.outline_draft_path.read_text(encoding="utf-8") == content
    assert store.outline_edit_state_path.exists()


def test_stage_outline_edits_preserves_unmentioned_content_until_confirmation(
    tmp_path: Path,
):
    store = StoryPlanningStore(tmp_path, "demo")
    original = """# 测试小说

#### 第一章：开端

> 内容焦点: 主角进入雾城。

#### 第二章：追踪

> 内容焦点: 主角追查钟楼。
"""
    store.outline_src_path.parent.mkdir(parents=True)
    store.outline_src_path.write_text(original, encoding="utf-8")

    result = store.stage_outline_edits(
        base_revision=store.outline_source_revision(),
        edits=[
            {
                "old_text": "#### 第二章：追踪\n\n> 内容焦点: 主角追查钟楼。",
                "new_text": "#### 第二章：潜入\n\n> 内容焦点: 主角夜探钟楼。",
            }
        ],
    )

    assert result["ok"] is True
    assert result["next_action"] == "confirm_outline_edits"
    assert "-#### 第二章：追踪" in result["diff"]
    assert "+#### 第二章：潜入" in result["diff"]
    assert store.outline_src_path.read_text(encoding="utf-8") == original
    assert "#### 第一章：开端" in store.outline_draft_path.read_text(encoding="utf-8")

    assert store.promote_outline(confirmed=True) is True
    promoted = store.outline_src_path.read_text(encoding="utf-8")
    assert "#### 第一章：开端" in promoted
    assert "#### 第二章：潜入" in promoted
    assert "#### 第二章：追踪" not in promoted
    assert store.outline_edit_state_path.exists() is False


def test_stage_outline_edits_rejects_ambiguous_old_text(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")
    store.outline_src_path.parent.mkdir(parents=True)
    store.outline_src_path.write_text("相同描述\n相同描述\n", encoding="utf-8")

    result = store.stage_outline_edits(
        base_revision=store.outline_source_revision(),
        edits=[{"old_text": "相同描述", "new_text": "新描述"}],
    )

    assert result["ok"] is False
    assert result["error"] == "ambiguous_old_text"
    assert store.outline_draft_path.exists() is False


def test_outline_promotion_rejects_source_changed_after_staging(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")
    store.outline_src_path.parent.mkdir(parents=True)
    store.outline_src_path.write_text("# 原大纲\n", encoding="utf-8")
    staged = store.stage_outline_edits(
        base_revision=store.outline_source_revision(),
        edits=[{"old_text": "原大纲", "new_text": "暂存大纲"}],
    )
    assert staged["ok"] is True

    store.outline_src_path.write_text("# 外部新大纲\n", encoding="utf-8")

    assert store.promote_outline(confirmed=True) is False
    assert store.outline_src_path.read_text(encoding="utf-8") == "# 外部新大纲\n"


def test_multiple_outline_edits_accumulate_on_pending_draft(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")
    store.outline_src_path.parent.mkdir(parents=True)
    store.outline_src_path.write_text("# 原标题\n\n第一处\n\n第二处\n", encoding="utf-8")

    first = store.stage_outline_edits(
        base_revision=store.outline_source_revision(),
        edits=[{"old_text": "第一处", "new_text": "第一处已改"}],
    )
    snapshot = store.read_outline_for_edit(query="第二处")
    second = store.stage_outline_edits(
        base_revision=snapshot["revision"],
        edits=[{"old_text": "第二处", "new_text": "第二处已改"}],
    )

    assert first["ok"] is True
    assert snapshot["source_kind"] == "pending_draft"
    assert second["ok"] is True
    assert second["edit_count"] == 2
    assert store.outline_src_path.read_text(encoding="utf-8") == "# 原标题\n\n第一处\n\n第二处\n"
    draft = store.outline_draft_path.read_text(encoding="utf-8")
    assert "第一处已改" in draft
    assert "第二处已改" in draft


def test_read_outline_for_edit_returns_query_window_and_full_revision(tmp_path: Path):
    store = StoryPlanningStore(tmp_path, "demo")
    store.outline_src_path.parent.mkdir(parents=True)
    store.outline_src_path.write_text(
        "# 测试\n\n#### 第一章\n内容A\n\n#### 第二章\n内容B\n",
        encoding="utf-8",
    )

    snapshot = store.read_outline_for_edit(query="第二章", context_lines=1)

    assert snapshot["ok"] is True
    assert snapshot["query_found"] is True
    assert "#### 第二章" in snapshot["content"]
    assert snapshot["revision"] == store.outline_source_revision()
