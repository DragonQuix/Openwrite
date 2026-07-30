import json
import logging
import os
from http import HTTPStatus
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from tools.agent.book_state import BookStage, BookStateStore
from tools.cli import _save_chapter
from tools.context_builder import ContextBuilder
from tools.init_project import init_project
from tools.project_registry import ProjectRegistry
from tools.studio import (
    StudioApplication,
    StudioError,
    create_server,
    render_chat_markdown,
)
from tools.studio_preferences import StudioModelSettingsStore
from tools.workflow_scheduler import WorkflowScheduler


def test_studio_model_form_uses_valid_output_step_and_interface_presets():
    assets = Path(__file__).parent.parent / "tools" / "studio_assets"
    html = (assets / "index.html").read_text(encoding="utf-8")
    javascript = (assets / "app.js").read_text(encoding="utf-8")

    assert (
        'id="model-max-tokens" type="number" min="256" max="131072" '
        'step="1" value="24000"'
    ) in html
    assert 'value="deepseek-pro">DeepSeek · V4 Pro<' in html
    assert 'value="deepseek-flash">DeepSeek · V4 Flash<' in html
    assert 'value="openai">OpenAI 格式接口<' in html
    assert 'value="anthropic">Anthropic 格式接口<' in html
    assert 'id="model-remember-key" type="checkbox" checked' in html
    assert 'model: "deepseek-v4-pro"' in javascript
    assert 'model: "deepseek-v4-flash"' in javascript
    assert "remember_api_key" in javascript
    assert (
        'return name === "deepseek-v4-flash" ? "deepseek-flash" : "deepseek-pro";'
        in javascript
    )


def test_studio_onboarding_ui_guides_new_projects_and_next_actions():
    assets = Path(__file__).parent.parent / "tools" / "studio_assets"
    html = (assets / "index.html").read_text(encoding="utf-8")
    javascript = (assets / "app.js").read_text(encoding="utf-8")
    styles = (assets / "styles.css").read_text(encoding="utf-8")

    assert "runNextAction" in javascript
    assert "agentEmptyGuidance" in javascript
    assert "next_action_items" in javascript
    assert "open_goethe" in javascript
    assert 'id="product-tour"' in html
    assert 'id="product-tour-spotlight"' in html
    assert 'id="product-tour-back"' in html
    assert "productTourSteps" in javascript
    assert "认识你的写作工作台" in javascript
    assert "左侧是作品的工作地图" in javascript
    assert "故事、人物、世界" in javascript
    assert "goToPreviousProductTourStep" in javascript
    assert "productTourStorageKey" in javascript
    assert "productTourDebugMode" in javascript
    assert 'get("debug") === "onboarding"' in javascript
    assert '"?debug=onboarding"' in javascript
    assert 'localStorage.setItem(productTourStorageKey, "seen")' in javascript
    assert "startProductTour" in javascript
    assert "advanceProductTourAfterAction" in javascript
    assert "const tourAction" in javascript
    assert "告诉 Goethe 你想写什么故事" in javascript
    assert "next-action-button" in styles
    assert ".product-tour-spotlight" in styles
    assert ".product-tour-card" in styles
    assert "project-demo-seed" not in html
    assert "自定义作品目录（可选）" in html
    assert 'id="project-path" required' not in html
    assert "suggestProjectPath" in javascript
    assert "confirmDeleteProject" in javascript
    assert "recent-project-delete" in styles
    assert "delete-project-form" not in html


def test_studio_init_accepts_demo_short_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    app = StudioApplication(
        tmp_path,
        model_settings_store=StudioModelSettingsStore(tmp_path / "prefs"),
    )
    result = app.initialize_project(
        {
            "project_path": str(tmp_path / "demo_book"),
            "novel_id": "demo_novel",
            "title": "雾城来信",
            "template": "demo_short",
        }
    )
    assert result["initialized"] is True
    assert result["snapshot"]["readiness"]["characters"] is True
    assert result["snapshot"]["readiness"]["outline"] is True
    assert result["snapshot"]["readiness"]["author_intent"] is True


def test_studio_delete_project_removes_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    app = StudioApplication(
        tmp_path,
        model_settings_store=StudioModelSettingsStore(tmp_path / "prefs"),
    )
    app.initialize_project(
        {
            "project_path": str(tmp_path / "doomed_novel"),
            "novel_id": "doomed",
            "title": "将删之书",
            "template": "default",
        }
    )
    assert (tmp_path / "doomed_novel" / "novel_config.yaml").exists()

    with pytest.raises(StudioError, match="确认"):
        app.delete_project({"project_path": str(tmp_path / "doomed_novel"), "confirm": "wrong"})

    app.delete_project(
        {"project_path": str(tmp_path / "doomed_novel"), "confirm": "doomed"}
    )
    assert not (tmp_path / "doomed_novel").exists()


def test_relationship_topology_includes_search_and_context_controls():
    assets = Path(__file__).parent.parent / "tools" / "studio_assets"
    html = (assets / "index.html").read_text(encoding="utf-8")
    javascript = (assets / "app.js").read_text(encoding="utf-8")
    styles = (assets / "styles.css").read_text(encoding="utf-8")

    assert 'id="relationship-search" type="search"' in html
    assert 'id="relationship-search-status"' in html
    assert "findRelationshipMatches" in javascript
    assert "相邻上下文" in javascript
    assert "search-match" in javascript
    assert ".relationship-node.search-match" in styles


def test_outline_tree_ui_supports_direct_text_editing():
    assets = Path(__file__).parent.parent / "tools" / "studio_assets"
    javascript = (assets / "app.js").read_text(encoding="utf-8")
    styles = (assets / "styles.css").read_text(encoding="utf-8")

    assert "startOutlineInlineRename" in javascript
    assert 'operation: "update_summary"' in javascript
    assert "outline-summary-editor" in javascript
    assert ".outline-tree-title-input" in styles
    assert ".outline-summary-editor" in styles


def test_agent_chat_ui_supports_sessions_and_collapsible_inspector():
    assets = Path(__file__).parent.parent / "tools" / "studio_assets"
    html = (assets / "index.html").read_text(encoding="utf-8")
    javascript = (assets / "app.js").read_text(encoding="utf-8")
    styles = (assets / "styles.css").read_text(encoding="utf-8")

    assert 'id="agent-session-new"' in html
    assert 'id="agent-session-delete"' in html
    assert 'id="agent-session-list"' in html
    assert 'id="inspector-collapse"' in html
    assert 'id="inspector-restore"' in html
    assert 'id="agent-activity"' in html
    assert 'id="agent-activity-steps"' in html
    assert "agentSessionId" in javascript
    assert '"/api/agent/session"' in javascript
    assert '"/api/agent/session/delete"' in javascript
    assert "deleteAgentSession" in javascript
    assert "默认会话不能删除" in javascript
    assert "session_id" in javascript
    assert "startAgentActivity" in javascript
    assert "pollAgentActivity" in javascript
    assert "/api/agent/activity" in javascript
    assert "finishAgentActivity" in javascript
    assert "耗时较久" in javascript
    assert "可能异常" in javascript
    assert "toggleInspectorCollapsed" in javascript
    assert ".agent-console" in styles
    assert ".agent-session-item" in styles
    assert ".danger-mini-button" in styles
    assert ".agent-activity" in styles
    assert ".agent-activity.long-running" in styles
    assert ".agent-activity.possibly-stuck" in styles
    assert "@keyframes agentPulse" in styles
    assert ".app.inspector-collapsed" in styles


def test_studio_debug_mode_writes_sanitized_backend_log(tmp_path: Path):
    init_project(tmp_path, "demo")
    app = StudioApplication(tmp_path, debug=True)

    app._debug_event(
        "unit_test",
        api_key="secret-value",
        token="token-value",
        message="后台记录" * 200,
        nested={"authorization": "bearer secret"},
    )
    for handler in logging.getLogger("tools.studio").handlers:
        handler.flush()

    log_path = (
        tmp_path
        / "data"
        / "novels"
        / "demo"
        / "data"
        / "logs"
        / "studio-debug.log"
    )
    assert app.debug_log_path == log_path
    text = log_path.read_text(encoding="utf-8")

    assert "studio.project_activated" in text
    assert "studio.unit_test" in text
    assert "secret-value" not in text
    assert "token-value" not in text
    assert "bearer secret" not in text
    assert "<redacted>" in text


def test_studio_chat_markdown_renders_commonmark_without_raw_html():
    rendered = render_chat_markdown(
        "## 计划\n\n- **第一步**\n- `第二步`\n\n<script>alert('xss')</script>\n"
        "\n[危险链接](javascript:alert('xss'))"
    )

    assert "<h2>计划</h2>" in rendered
    assert "<li><strong>第一步</strong></li>" in rendered
    assert "<code>第二步</code>" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert 'href="javascript:' not in rendered


def test_studio_agent_sessions_can_be_created_selected_and_listed(tmp_path: Path):
    from tools.agent.goethe_session_state import GoetheSessionStateStore
    from tools.agent.session_state import SessionStateStore

    init_project(tmp_path, "demo")
    app = StudioApplication(tmp_path)

    goethe_created = app.create_agent_session({"agent": "goethe"})
    goethe_session_id = goethe_created["active_session_id"]
    assert goethe_session_id.startswith("goethe-")
    assert goethe_created["history"]["session_id"] == goethe_session_id
    assert goethe_created["history"]["path"].startswith(
        "data/workflows/sessions/goethe/"
    )

    goethe_store = GoetheSessionStateStore(
        tmp_path,
        "demo",
        session_id=goethe_session_id,
    )
    goethe_store.append_turn("user", "扩写第一篇的大纲")
    goethe_store.append_turn("assistant", "已经拆成三幕推进。")
    goethe_surface = app.agent_surface("goethe", limit=10, session_id=goethe_session_id)
    assert [message["content"] for message in goethe_surface["history"]["messages"]] == [
        "扩写第一篇的大纲",
        "已经拆成三幕推进。",
    ]
    assert any(
        session["id"] == goethe_session_id
        and session["title"] == "扩写第一篇的大纲"
        and session["messages"] == 2
        for session in goethe_surface["sessions"]
    )

    default_surface = app.agent_surface("goethe", limit=10, session_id="default")
    assert default_surface["history"]["path"] == "data/workflows/goethe_session.jsonl"
    assert default_surface["history"]["messages"] == []

    dante_created = app.create_agent_session({"agent": "dante"})
    dante_session_id = dante_created["active_session_id"]
    assert dante_session_id.startswith("dante-")
    dante_store = SessionStateStore(tmp_path, "demo", session_id=dante_session_id)
    dante_store.append_turn("user", "根据大纲推进第一章")
    dante_surface = app.agent_surface("dante", limit=10, session_id=dante_session_id)
    assert dante_surface["history"]["messages"][0]["content"] == "根据大纲推进第一章"
    assert dante_surface["history"]["path"].startswith(
        "data/workflows/sessions/dante/"
    )

    deleted = app.delete_agent_session(
        {"agent": "goethe", "session_id": goethe_session_id}
    )
    assert deleted["deleted"] is True
    assert deleted["deleted_session_id"] == goethe_session_id
    assert deleted["active_session_id"] == "default"
    assert not goethe_store.path.exists()
    assert not goethe_store.transcript_path.exists()
    assert not any(session["id"] == goethe_session_id for session in deleted["sessions"])
    with pytest.raises(StudioError, match="默认会话不能删除"):
        app.delete_agent_session({"agent": "goethe", "session_id": "default"})


def _read_json(url: str) -> dict:
    with build_opener(ProxyHandler({})).open(url) as response:
        return json.loads(response.read())


def test_studio_workspace_exposes_novel_only_documents(tmp_path: Path):
    init_project(tmp_path, "demo", "雾城来信")
    _save_chapter(tmp_path, "demo", "ch_001", "第一章 雨夜", "门外有人。")

    payload = StudioApplication(tmp_path).workspace()

    assert payload["snapshot"]["title"] == "雾城来信"
    assert payload["documents"]["outline"][0]["path"] == "src/outline.md"
    assert payload["documents"]["chapters"][0]["path"].endswith("ch_001.md")
    assert set(payload["documents"]) == {
        "outline",
        "story",
        "characters",
        "world",
        "chapters",
    }


def test_studio_bootstraps_first_project_without_cli(tmp_path: Path):
    app = StudioApplication(tmp_path)

    before = app.workspace()
    assert before["initialized"] is False
    assert before["snapshot"]["title"] == "新小说"

    after = app.initialize_project({"novel_id": "mist_city", "title": "雾城来信"})

    assert after["initialized"] is True
    assert after["snapshot"]["novel_id"] == "mist_city"
    assert after["snapshot"]["title"] == "雾城来信"
    assert (tmp_path / "novel_config.yaml").exists()
    assert (tmp_path / ".openwrite" / "project.yaml").exists()


def test_studio_uses_a_title_based_default_directory_from_framework_root(
    tmp_path: Path,
):
    framework = tmp_path / "framework"
    (framework / "tools").mkdir(parents=True)
    (framework / "tools" / "studio.py").write_text("", encoding="utf-8")
    (framework / "pyproject.toml").write_text('[project]\nname = "openwrite"\n', encoding="utf-8")
    app = StudioApplication(framework)
    result = app.initialize_project({"novel_id": "mist_city", "title": "雾城来信"})

    target = tmp_path / "OpenWriteNovels" / "雾城来信"
    assert result["project"]["root"] == str(target.resolve())
    assert (target / "novel_config.yaml").is_file()


def test_studio_opens_external_project_and_lists_recent_projects(tmp_path: Path):
    launcher = tmp_path / "framework"
    launcher.mkdir()
    project = tmp_path / "private_novel"
    project.mkdir()
    init_project(project, "demo", "私密作品")
    registry = ProjectRegistry(tmp_path / "recent.yaml")
    app = StudioApplication(launcher, project_registry=registry)

    workspace = app.open_project({"project_path": str(project)})

    assert workspace["initialized"] is True
    assert workspace["snapshot"]["title"] == "私密作品"
    assert workspace["project"]["root"] == str(project.resolve())
    assert workspace["project"]["recent"][0]["path"] == str(project.resolve())


def test_studio_searches_project_assets(tmp_path: Path):
    init_project(tmp_path, "demo")
    path = tmp_path / "data" / "novels" / "demo" / "src" / "story" / "background.md"
    path.write_text("# 故事背景\n\n钟楼每天少走十三秒。\n", encoding="utf-8")
    app = StudioApplication(tmp_path)

    result = app.search_project("十三秒", "story")

    assert result["results"][0]["path"] == "src/story/background.md"


def test_studio_context_preview_exposes_traceable_manifest(tmp_path: Path):
    init_project(tmp_path, "demo")
    app = StudioApplication(tmp_path)

    preview = app.context_preview("ch_001")

    assert preview["manifest"]["strategy"] == "hierarchical-provenance-v1"
    assert preview["manifest"]["revision"]


def test_studio_outline_structure_and_smart_chapter_creation(tmp_path: Path, monkeypatch):
    init_project(tmp_path, "demo")
    novel = tmp_path / "data" / "novels" / "demo"
    (novel / "src" / "outline.md").write_text(
        """# 第一卷
## 第一幕
### 第一节
#### 第一章：开门
发现门外的脚印。
#### 第二章：追踪
沿脚印进入雨巷。
""",
        encoding="utf-8",
    )
    manuscript = novel / "data" / "manuscript" / "arc_001"
    manuscript.mkdir(parents=True, exist_ok=True)
    (manuscript / "ch_001.md").write_text("已有正文", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "configured-for-test")
    calls = []

    def fake_writer(root: Path, args: dict) -> dict:
        calls.append(args)
        return {"ok": True, "chapter_id": args["chapter_id"], "word_count": 1200}

    app = StudioApplication(tmp_path, writer_executor=fake_writer)
    outline = app.outline_structure()

    assert outline["counts"]["chapter"] == 2
    assert outline["recommendation"]["chapter_id"] == "ch_002"
    result = app.write_next_chapter(
        {
            "chapter_id": "ch_002",
            "outline_revision": outline["revision"],
            "guidance": outline["recommendation"]["guidance"],
            "target_words": 3200,
        }
    )
    assert result["result"]["chapter_id"] == "ch_002"
    assert calls[0]["chapter_id"] == "ch_002"

    with pytest.raises(StudioError) as drafted:
        app.write_next_chapter({"chapter_id": "ch_001", "target_words": 3000})
    assert drafted.value.status == HTTPStatus.CONFLICT

    (novel / "src" / "outline.md").write_text("# 已变更", encoding="utf-8")
    with pytest.raises(StudioError) as stale:
        app.write_next_chapter(
            {
                "chapter_id": "ch_002",
                "outline_revision": outline["revision"],
                "target_words": 3000,
            }
        )
    assert stale.value.status == HTTPStatus.CONFLICT


def test_studio_incrementally_edits_outline_tree_with_revision_guard(tmp_path: Path):
    init_project(tmp_path, "demo")
    novel = tmp_path / "data" / "novels" / "demo"
    outline_path = novel / "src" / "outline.md"
    outline_path.write_text(
        "# 第一卷\n## 第一幕\n### 第一节\n#### 第一章：开门\n脚印。\n",
        encoding="utf-8",
    )
    app = StudioApplication(tmp_path)
    before = app.outline_structure()

    result = app.edit_outline_structure(
        {
            "operation": "add_child",
            "node_id": "section_001",
            "kind": "chapter",
            "title": "第二章：追踪",
            "revision": before["revision"],
        }
    )

    assert result["selected_node_id"] == "ch_002"
    assert result["outline"]["counts"]["chapter"] == 2
    assert "#### 第二章：追踪" in outline_path.read_text(encoding="utf-8")
    assert "checkpoint" in result

    refreshed = result["outline"]
    summary = app.edit_outline_structure(
        {
            "operation": "update_summary",
            "node_id": "section_001",
            "summary": "树上直接修改这一节内容。\n继续保留子章。",
            "revision": refreshed["revision"],
        }
    )
    source = outline_path.read_text(encoding="utf-8")
    assert "树上直接修改这一节内容。" in source
    assert "#### 第一章：开门" in source
    assert "#### 第二章：追踪" in source
    assert summary["selected_node_id"] == "section_001"

    with pytest.raises(StudioError) as stale:
        app.edit_outline_structure(
            {
                "operation": "rename",
                "node_id": "section_001",
                "title": "第一节：旧页面覆盖",
                "revision": before["revision"],
            }
        )
    assert stale.value.status == HTTPStatus.CONFLICT


def test_studio_delete_returns_renumbering_impact(tmp_path: Path):
    init_project(tmp_path, "demo")
    novel = tmp_path / "data" / "novels" / "demo"
    outline_path = novel / "src" / "outline.md"
    outline_path.write_text(
        "# 第一卷\n## 第一幕\n### 第一节\n"
        "#### 第14章：删除\n#### 第15章：补位\n#### 第16章：继续\n",
        encoding="utf-8",
    )
    app = StudioApplication(tmp_path)
    before = app.outline_structure()

    result = app.edit_outline_structure(
        {
            "operation": "delete",
            "node_id": "ch_014",
            "revision": before["revision"],
        }
    )

    assert result["outline"]["counts"]["chapter"] == 2
    assert [(item["old_id"], item["new_id"]) for item in result["renumbered"]] == [
        ("ch_015", "ch_014"),
        ("ch_016", "ch_015"),
    ]
    assert result["skipped_renumbering"] == []
    assert "第14章：补位" in outline_path.read_text(encoding="utf-8")
    assert "连续重编号 2 个" in result["message"]


def test_studio_model_configuration_persists_local_settings_and_never_echoes_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    init_project(tmp_path, "demo")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    settings_store = StudioModelSettingsStore(tmp_path / "studio-settings")
    app = StudioApplication(tmp_path, model_settings_store=settings_store)

    payload = app.configure_model(
        {
            "provider": "openai",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-pro",
            "api_key": "session-test-secret",
            "api_format": "chat",
            "context_tokens": 160000,
            "max_tokens": 24000,
        }
    )

    assert payload["model"] == {
        "configured": True,
        "provider": "openai",
        "base_url": "https://api.deepseek.com",
        "name": "deepseek-v4-pro",
        "api_format": "chat",
        "context_tokens": 160000,
        "max_tokens": 24000,
        "persistence": {
            "settings_saved": True,
            "credential_saved": True,
            "remember_api_key": True,
        },
    }
    payload_json = json.dumps(payload)
    assert "session-test-secret" not in payload_json
    assert str(settings_store.directory) not in payload_json
    assert "session-test-secret" not in (tmp_path / "novel_config.yaml").read_text(encoding="utf-8")
    assert settings_store.load_credential() == "session-test-secret"
    assert ContextBuilder(tmp_path, "demo").MAX_TOKENS == 160000


def test_studio_model_configuration_does_not_persist_inherited_process_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    init_project(tmp_path, "demo")
    monkeypatch.setenv("LLM_API_KEY", "process-only-secret")
    settings_store = StudioModelSettingsStore(tmp_path / "studio-settings")
    app = StudioApplication(tmp_path, model_settings_store=settings_store)

    payload = app.configure_model(
        {
            "provider": "openai",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "",
            "api_format": "chat",
            "context_tokens": 160000,
            "max_tokens": 6000,
            "remember_api_key": True,
        }
    )

    assert payload["model"]["configured"] is True
    assert payload["model"]["persistence"] == {
        "settings_saved": True,
        "credential_saved": False,
        "remember_api_key": True,
    }
    assert os.environ["LLM_API_KEY"] == "process-only-secret"
    assert settings_store.load_credential() == ""


def test_studio_model_connection_test_does_not_replace_active_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    init_project(tmp_path, "demo")
    monkeypatch.setenv("LLM_API_KEY", "active-secret")
    monkeypatch.setenv("LLM_MODEL", "active-model")
    captured = {}

    def fake_test(settings):
        captured.update(settings)
        return {"reply": "OK"}

    app = StudioApplication(tmp_path, model_test_executor=fake_test)
    result = app.test_model_connection(
        {
            "provider": "openai",
            "base_url": "https://api.deepseek.com",
            "model": "candidate-model",
            "api_key": "candidate-secret",
            "api_format": "chat",
            "context_tokens": 160000,
            "max_tokens": 24000,
        }
    )

    assert result["ok"] is True
    assert result["model"] == "candidate-model"
    assert result["reply"] == "OK"
    assert captured["api_key"] == "candidate-secret"
    assert os.environ["LLM_MODEL"] == "active-model"
    assert os.environ["LLM_API_KEY"] == "active-secret"
    assert "candidate-secret" not in json.dumps(result)


def test_studio_rejects_invalid_live_model_budgets(tmp_path: Path, monkeypatch):
    init_project(tmp_path, "demo")
    monkeypatch.setenv("LLM_API_KEY", "active-secret")
    app = StudioApplication(tmp_path)

    with pytest.raises(StudioError, match="上下文预算"):
        app.configure_model(
            {
                "model": "deepseek-v4-pro",
                "base_url": "https://api.deepseek.com",
                "context_tokens": 1000,
            }
        )


def test_studio_model_connection_errors_never_echo_provider_key_fragments(tmp_path: Path):
    init_project(tmp_path, "demo")

    def fail_with_provider_body(settings):
        raise RuntimeError(
            "401 authentication failed; Your api key: ****-7f70 is invalid"
        )

    app = StudioApplication(tmp_path, model_test_executor=fail_with_provider_body)
    with pytest.raises(StudioError) as captured:
        app.test_model_connection(
            {
                "provider": "openai",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-pro",
                "api_key": "secret-ending-7f70",
            }
        )

    message = str(captured.value)
    assert message == "连接测试失败：认证失败，请检查 API Key。"
    assert "7f70" not in message


def test_studio_default_model_connection_test_allows_reasoning_budget(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, int] = {}

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def chat(self, messages, temperature, max_tokens, stream):
            captured["max_tokens"] = max_tokens
            return SimpleNamespace(content="OK")

    monkeypatch.setattr("tools.llm.LLMClient", FakeClient)

    result = StudioApplication._default_model_connection_test(
        {
            "provider": "openai",
            "api_key": "secret",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "max_tokens": 24000,
            "api_format": "chat",
        }
    )

    assert result == {"reply": "OK"}
    assert captured["max_tokens"] == 32


def test_studio_empty_model_connection_reply_gets_actionable_error():
    message = StudioApplication._safe_model_connection_error(
        RuntimeError("empty model reply")
    )

    assert message == "连接测试失败：模型返回空内容，请调大最大输出后重试。"


def test_studio_document_write_is_atomic_and_version_checked(tmp_path: Path):
    init_project(tmp_path, "demo")
    app = StudioApplication(tmp_path)
    document = app.read_document("src/story/background.md")

    saved = app.write_document(
        document["path"],
        "# 故事背景\n\n一座只在雨夜出现的城。\n",
        document["version"],
    )

    assert "雨夜" in saved["content"]
    with pytest.raises(StudioError) as conflict:
        app.write_document(document["path"], "旧内容", document["version"])
    assert conflict.value.status == HTTPStatus.CONFLICT


def test_studio_rejects_paths_outside_novel_documents(tmp_path: Path):
    init_project(tmp_path, "demo")
    app = StudioApplication(tmp_path)

    with pytest.raises(StudioError) as traversal:
        app.read_document("../../README.md")
    assert traversal.value.status == HTTPStatus.FORBIDDEN

    with pytest.raises(StudioError):
        app.write_document("data/workflows/book_state.yaml", "x", None)


def test_studio_focus_and_writer_reuse_openwrite_pipeline(tmp_path: Path, monkeypatch):
    init_project(tmp_path, "demo")
    monkeypatch.setenv("LLM_API_KEY", "configured-for-test")
    calls: list[dict] = []

    def fake_writer(root: Path, args: dict) -> dict:
        calls.append(args)
        path = _save_chapter(root, "demo", args["chapter_id"], "第一章", "测试正文")
        return {
            "ok": True,
            "chapter_id": args["chapter_id"],
            "title": "第一章",
            "word_count": 4,
            "draft_path": str(path),
            "truth_updates": {},
        }

    def fake_reviewer(root: Path, args: dict) -> dict:
        return {
            "ok": True,
            "chapter_id": args["chapter_id"],
            "passed": False,
            "score": 90,
            "issues": 1,
            "summary": "警告 1 项",
            "issue_details": [
                {
                    "severity": "warning",
                    "category": "节奏检查",
                    "description": "开场略慢",
                    "suggestion": "提前冲突",
                    "dimension": 7,
                }
            ],
        }

    app = StudioApplication(
        tmp_path,
        writer_executor=fake_writer,
        review_executor=fake_reviewer,
    )
    updated = app.update_focus(
        {
            "goal": "完成开篇承诺",
            "must_keep": ["雨夜意象"],
            "must_avoid": ["解释真相"],
        }
    )
    assert updated["snapshot"]["creative_focus"]["goal"] == "完成开篇承诺"

    result = app.write_next_chapter({"target_words": 800, "guidance": "从敲门声开始"})
    assert calls[0]["target_words"] == 800
    assert result["result"]["chapter_id"] == "ch_001"
    assert len(result["workspace"]["documents"]["chapters"]) == 1
    written_state = BookStateStore(tmp_path, "demo").load_or_create()
    assert written_state.stage == BookStage.REVIEW_AND_REVISE
    workflow = WorkflowScheduler(tmp_path, "demo").load_workflow("ch_001")
    assert workflow is not None
    assert next(stage for stage in workflow.stages if stage.name == "writing").status == "completed"

    chapter_path = result["workspace"]["documents"]["chapters"][0]["path"]
    review = app.review_chapter({"path": chapter_path})
    assert review["result"]["score"] == 90
    review_path = tmp_path / "data" / "novels" / "demo" / "data" / "reviews" / "ch_001.json"
    assert review_path.exists()
    refreshed = app.workspace()
    assert refreshed["documents"]["chapters"][0]["review"]["score"] == 90
    assert "90 分" in refreshed["documents"]["chapters"][0]["subtitle"]
    reviewed_state = BookStateStore(tmp_path, "demo").load_or_create()
    assert reviewed_state.stage == BookStage.REVIEW_AND_REVISE
    assert reviewed_state.blocking_reason == "review_revision_requested"


def test_studio_http_serves_ui_api_and_blocks_unsigned_writes(tmp_path: Path):
    init_project(tmp_path, "demo")
    server = create_server(tmp_path, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = build_opener(ProxyHandler({}))
    try:
        health = _read_json(f"{base}/api/health")
        assert health == {"ok": True}

        with opener.open(f"{base}/") as response:
            html = response.read().decode("utf-8")
            assert "OpenWrite Studio" in html
            assert 'id="outline-add-volume"' in html
            assert 'id="outline-node-add-child"' in html
            assert 'id="outline-edit-dialog"' in html
            assert 'id="outline-edit-impact"' in html
            assert 'id="agent-session-new"' in html
            assert 'id="agent-session-delete"' in html
            assert 'id="inspector-collapse"' in html
            assert "default-src 'self'" in response.headers["Content-Security-Policy"]

        agent_surface = _read_json(
            f"{base}/api/agents?agent=goethe&session_id=default&limit=5"
        )
        assert agent_surface["active_session_id"] == "default"
        assert agent_surface["sessions"][0]["id"] == "default"

        new_session_request = Request(
            f"{base}/api/agent/session",
            method="POST",
            data=json.dumps({"agent": "goethe"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenWrite-Studio": "1"},
        )
        with opener.open(new_session_request) as response:
            new_session = json.loads(response.read())
        assert new_session["created"] is True
        assert new_session["active_session_id"].startswith("goethe-")
        assert any(
            session["id"] == new_session["active_session_id"]
            for session in new_session["sessions"]
        )
        delete_session_request = Request(
            f"{base}/api/agent/session/delete",
            method="POST",
            data=json.dumps(
                {
                    "agent": "goethe",
                    "session_id": new_session["active_session_id"],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenWrite-Studio": "1"},
        )
        with opener.open(delete_session_request) as response:
            deleted_session = json.loads(response.read())
        assert deleted_session["deleted"] is True
        assert deleted_session["active_session_id"] == "default"
        assert not any(
            session["id"] == new_session["active_session_id"]
            for session in deleted_session["sessions"]
        )

        request = Request(
            f"{base}/api/focus",
            method="POST",
            data=json.dumps({"goal": "测试"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as denied:
            opener.open(request)
        assert denied.value.code == HTTPStatus.FORBIDDEN

        outline = _read_json(f"{base}/api/outline")
        outline_edit = Request(
            f"{base}/api/outline/edit",
            method="POST",
            data=json.dumps(
                {
                    "operation": "rename",
                    "node_id": "volume_001",
                    "title": "第一卷：网页增量编辑",
                    "revision": outline["revision"],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenWrite-Studio": "1"},
        )
        with opener.open(outline_edit) as response:
            edited_outline = json.loads(response.read())
        assert edited_outline["outline"]["roots"][0]["title"] == "第一卷：网页增量编辑"

        document = _read_json(
            f"{base}/api/document?path=src%2Fstory%2Fbackground.md"
        )
        assert isinstance(document["version"], str)
        save_request = Request(
            f"{base}/api/document",
            method="PUT",
            data=json.dumps(
                {
                    "path": document["path"],
                    "content": "# 故事背景\n\nHTTP 保存测试。\n",
                    "version": document["version"],
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-OpenWrite-Studio": "1",
            },
        )
        with opener.open(save_request) as response:
            saved = json.loads(response.read())
        assert "HTTP 保存测试" in saved["content"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_studio_sync_create_import_and_context_preview(tmp_path: Path):
    init_project(tmp_path, "demo", "雾城来信")
    app = StudioApplication(tmp_path)
    assert app.workspace()["version"] == "5.8.0"

    character = app.create_document(
        {"kind": "character", "name": "林岑", "description": "在雨夜追查旧信的记者。"}
    )
    assert character["document"]["path"].startswith("src/characters/")
    world = app.create_document(
        {"kind": "world", "name": "雾城钟楼", "description": "每天少走十三秒。"}
    )
    assert world["document"]["path"].startswith("src/world/entities/")

    synced = app.sync_project()
    assert synced["after"]["needs_sync"] is False
    assert synced["after"]["cards"] == 1

    imported = app.import_text(
        {
            "filename": "旧稿.txt",
            "content": "第一章 雨夜\n门外有人。\n\n第二章 回声\n门后没有人。",
            "arc_id": "arc_001",
        }
    )
    assert [item["chapter_id"] for item in imported["imported"]] == ["ch_001", "ch_002"]

    preview = app.context_preview("ch_001")
    assert preview["chapter_id"] == "ch_001"
    assert "作者意图" in preview["markdown"]

    with pytest.raises(StudioError) as conflict:
        app.create_document({"kind": "character", "name": "林岑"})
    assert conflict.value.status == HTTPStatus.CONFLICT
    operations = app.workspace()["operations"]
    assert operations["sync"]["needs_sync"] is False
    assert any(item["name"] == "项目配置" and item["ok"] for item in operations["diagnostics"])


def test_studio_continuity_and_foreshadowing_management(tmp_path: Path):
    init_project(tmp_path, "demo")
    app = StudioApplication(tmp_path)

    created = app.manage_foreshadowing(
        {
            "action": "create",
            "node_id": "hook_clock_001",
            "content": "钟楼每天少走十三秒",
            "weight": 8,
            "created_at": "ch_001",
            "target_chapter": "ch_010",
        }
    )

    nodes = created["continuity"]["foreshadowing"]["nodes"]
    assert nodes[0]["id"] == "hook_clock_001"
    assert created["workspace"]["snapshot"]["pending_foreshadowing"] == 1
    assert created["continuity"]["foreshadowing_validation"]["valid"] is True


def test_studio_chat_and_source_extraction_use_injected_real_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    init_project(tmp_path, "demo")
    monkeypatch.setenv("LLM_API_KEY", "configured-for-test")
    chat_calls: list[tuple[str, str]] = []
    source_calls: list[dict] = []

    def fake_chat(root: Path, novel_id: str, agent: str, message: str) -> dict:
        chat_calls.append((agent, message))
        return {"content": f"{agent} 已收到：{message}"}

    def fake_source(root: Path, payload: dict) -> dict:
        source_calls.append(payload)
        source_root = root / "data" / "novels" / "demo" / "data" / "sources" / payload["source_id"]
        (source_root / "style").mkdir(parents=True)
        (source_root / "source.md").write_text("# 来源", encoding="utf-8")
        return {"ok": True, "source_id": payload["source_id"]}

    app = StudioApplication(
        tmp_path,
        chat_executor=fake_chat,
        source_executor=fake_source,
    )
    chat = app.chat_turn(
        {
            "agent": "goethe",
            "session_id": "goethe-20260729-120000-test01",
            "run_id": "run-studio-activity-01",
            "message": "整理第一篇",
        }
    )
    assert chat["content"] == "goethe 已收到：整理第一篇"
    assert chat["content_html"] == "<p>goethe 已收到：整理第一篇</p>\n"
    assert chat["session_id"] == "goethe-20260729-120000-test01"
    assert chat["run_id"] == "run-studio-activity-01"
    assert chat_calls == [("goethe", "整理第一篇")]
    activity = app.agent_activity("run-studio-activity-01")
    assert activity["status"] == "complete"
    assert activity["step_index"] == 4
    assert activity["elapsed_seconds"] >= 0

    extracted = app.source_action(
        {
            "action": "extract",
            "source_id": "reference_01",
            "focus": "style",
            "content": "雨落在旧钟楼上。",
        }
    )
    assert extracted["result"]["ok"] is True
    assert source_calls[0]["focus"] == "style"
    packs = extracted["workspace"]["operations"]["source_packs"]
    assert packs[0]["source_id"] == "reference_01"


def test_studio_http_exposes_context_and_import_routes(tmp_path: Path):
    init_project(tmp_path, "demo")
    server = create_server(tmp_path, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = build_opener(ProxyHandler({}))
    try:
        context = _read_json(f"{base}/api/context?chapter=ch_001")
        assert context["chapter_id"] == "ch_001"

        request = Request(
            f"{base}/api/import",
            method="POST",
            data=json.dumps(
                {
                    "filename": "draft.md",
                    "content": "# 第一章 雨夜\n\n门外有人。",
                    "arc_id": "arc_001",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenWrite-Studio": "1"},
        )
        with opener.open(request) as response:
            payload = json.loads(response.read())
        assert payload["imported"][0]["chapter_id"] == "ch_001"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_studio_http_exposes_real_agent_activity(tmp_path: Path):
    init_project(tmp_path, "demo")
    server = create_server(tmp_path, port=0)
    server.app._start_agent_activity(
        "run-http-activity-01",
        agent="dante",
        session_id="default",
    )
    server.app._record_agent_activity(
        "run-http-activity-01",
        {"event": "tool_started", "turn": 2, "tool": "get_context"},
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        activity = _read_json(
            f"{base}/api/agent/activity?run_id=run-http-activity-01"
        )
        assert activity["status"] == "running"
        assert activity["phase"] == "tool_running"
        assert activity["step_index"] == 2
        assert activity["title"] == "Dante 正在调用工具"
        assert activity["note"] == "第 2 轮：组装章节上下文"
        assert activity["tool"] == "get_context"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_studio_http_can_initialize_an_empty_workspace(tmp_path: Path):
    server = create_server(tmp_path, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = build_opener(ProxyHandler({}))
    try:
        assert _read_json(f"{base}/api/workspace")["initialized"] is False
        request = Request(
            f"{base}/api/project/init",
            method="POST",
            data=json.dumps({"novel_id": "web_novel", "title": "前端新书"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenWrite-Studio": "1"},
        )
        with opener.open(request) as response:
            payload = json.loads(response.read())
        assert payload["initialized"] is True
        assert payload["snapshot"]["title"] == "前端新书"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
