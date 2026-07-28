import subprocess
from pathlib import Path

import yaml

from tools.context_manifest import build_context_manifest
from tools.git_checkpoint import GitCheckpointManager
from tools.init_project import init_project
from tools.project_registry import ProjectRegistry, is_framework_root
from tools.project_search import ProjectSearchIndex
from tools.resources import resolve_craft_dir


def test_project_registry_keeps_only_existing_projects(tmp_path: Path):
    project = tmp_path / "novel"
    project.mkdir()
    init_project(project, "demo", "雾城来信")
    registry = ProjectRegistry(tmp_path / "registry.yaml")

    registry.remember(project)

    assert registry.list()[0]["path"] == str(project.resolve())
    assert registry.list()[0]["title"] == "雾城来信"
    project.rename(tmp_path / "moved")
    assert registry.list() == []


def test_framework_detection_requires_package_and_studio(tmp_path: Path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "studio.py").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "openwrite"\n', encoding="utf-8"
    )
    assert is_framework_root(tmp_path) is True


def test_craft_resources_fall_back_to_installed_framework(tmp_path: Path):
    bundled = resolve_craft_dir(tmp_path)
    assert (bundled / "scene_craft.md").is_file()
    custom = tmp_path / "craft"
    custom.mkdir()
    (custom / "scene_craft.md").write_text("custom", encoding="utf-8")
    assert resolve_craft_dir(tmp_path) == custom


def test_project_search_returns_line_provenance_and_refreshes(tmp_path: Path):
    init_project(tmp_path, "demo", "雾城来信")
    novel_root = tmp_path / "data" / "novels" / "demo"
    character = novel_root / "src" / "characters" / "lin_cen.md"
    character.write_text(
        "# 林岑\n\n## 未解目标\n\n她在雨夜寻找失踪的旧信。\n", encoding="utf-8"
    )
    index = ProjectSearchIndex(novel_root)

    payload = index.search("雨夜 旧信", scope="characters")

    assert payload["indexed"] >= 1
    result = payload["results"][0]
    assert result["path"] == "src/characters/lin_cen.md"
    assert result["line"] == 5
    assert result["heading"] == "未解目标"

    character.write_text("# 林岑\n\n她改为寻找钟楼。\n", encoding="utf-8")
    assert index.search("旧信", scope="characters")["results"] == []
    assert index.search("钟楼", scope="characters")["results"][0]["line"] == 3


def test_project_search_exposes_outline_as_its_own_scope(tmp_path: Path):
    init_project(tmp_path, "demo", "雾城来信")
    novel_root = tmp_path / "data" / "novels" / "demo"
    outline = novel_root / "src" / "outline.md"
    outline.write_text("# 大纲\n\n## 第一章\n\n雨夜收到旧信。\n", encoding="utf-8")

    payload = ProjectSearchIndex(novel_root).search("旧信", scope="outline")

    assert payload["results"][0]["path"] == "src/outline.md"
    assert payload["results"][0]["scope"] == "outline"
    assert ProjectSearchIndex(novel_root).search("旧信", scope="story")["results"] == []


def test_context_manifest_records_layers_sources_and_revision(tmp_path: Path):
    init_project(tmp_path, "demo")
    novel_root = tmp_path / "data" / "novels" / "demo"
    packet = {
        "creative_focus": "本章必须保留雨夜意象",
        "story_background": "一座只在雨夜出现的城。",
        "current_arc_sections": [{"summary": "记者收到旧信"}],
    }

    manifest = build_context_manifest(novel_root, packet)

    assert manifest["strategy"] == "hierarchical-provenance-v1"
    assert manifest["revision"]
    levels = {item["section"]: item["level"] for item in manifest["items"]}
    assert levels["creative_focus"] == 1
    assert levels["current_arc_sections"] == 2
    focus = next(item for item in manifest["items"] if item["section"] == "creative_focus")
    assert focus["sources"][0]["path"] == "src/story/current_focus.md"


def test_git_checkpoint_requires_private_standalone_content_repository(tmp_path: Path):
    project = tmp_path / "novel"
    project.mkdir()
    init_project(project, "demo")
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project, check=True)
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=project, check=True, capture_output=True)
    metadata_path = project / ".openwrite" / "project.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    metadata["git"]["auto_checkpoint"] = True
    metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    outline = project / "data" / "novels" / "demo" / "src" / "outline.md"
    outline.write_text(outline.read_text(encoding="utf-8") + "\n新增一幕。\n", encoding="utf-8")

    result = GitCheckpointManager(project).checkpoint(
        ["data/novels/demo/src/outline.md"], "outline: add a new act"
    )

    assert result.ok is True
    assert result.committed is True
    subject = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=project,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert subject == "outline: add a new act"


def test_git_checkpoint_rejects_nested_framework_repository(tmp_path: Path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    project = tmp_path / "novel"
    project.mkdir()
    init_project(project, "demo")

    status = GitCheckpointManager(project).status()

    assert status["eligible"] is False
    assert "独立 Git 根目录" in status["reason"]
