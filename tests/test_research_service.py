from __future__ import annotations

import json
from pathlib import Path
from threading import Thread
from urllib.request import ProxyHandler, build_opener

import pytest

from tools.init_project import init_project
from tools.research_service import (
    MAX_PROCESS_OUTPUT_BYTES,
    ResearchService,
    ResearchServiceError,
)
from tools.studio import create_server


def test_research_service_archives_and_reads_report(tmp_path: Path):
    novel_root = tmp_path / "novel"
    reports = novel_root / "data" / "research" / "reports"
    reports.mkdir(parents=True)
    (reports / "episode_1.md").write_text("# 研究结果\n\n正文", encoding="utf-8")
    (reports / "episode_1.json").write_text(
        json.dumps(
            {
                "title": "叙事视角",
                "status": "succeeded",
                "episode_id": "episode_1",
                "created_at": "2026-08-03T00:00:00Z",
                "metrics": {"sources": 3},
            }
        ),
        encoding="utf-8",
    )
    service = ResearchService(novel_root)
    assert service.list_reports()[0]["id"] == "episode_1"
    assert service.read_report("episode_1")["content"].startswith("# 研究结果")


def test_research_service_maps_openwrite_model_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "local-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    service = ResearchService(tmp_path)
    env = service._research_environment({})
    assert env["AGENT_PROVIDER"] == "openai"
    assert env["OPENAI_API_KEY"] == "test-key"
    assert env["AGENT_MODEL"] == "local-model"
    assert env["OPENAI_BASE_URL"] == "https://example.test/v1"


def test_research_status_does_not_expose_machine_paths(tmp_path: Path):
    framework = tmp_path / "private-install" / "deepresearch"
    framework.mkdir(parents=True)
    (framework / "package.json").write_text("{}", encoding="utf-8")

    status = ResearchService(tmp_path / "novel", framework_root=framework).status()

    assert "framework_root" not in status
    assert "node" not in status
    assert "pnpm" not in status
    assert str(tmp_path) not in json.dumps(status, ensure_ascii=False)


def test_research_service_rejects_report_outside_artifact_root(tmp_path: Path):
    service = ResearchService(tmp_path / "novel", framework_root=tmp_path / "framework")
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")

    with pytest.raises(ResearchServiceError, match="产物目录之外") as exc_info:
        service._validated_report_path(outside)

    assert exc_info.value.code == "INVALID_REPORT_PATH"


def test_research_process_output_is_bounded():
    output = bytearray(b"old")
    ResearchService._append_process_output(output, b"x" * (MAX_PROCESS_OUTPUT_BYTES + 20))

    assert len(output) == MAX_PROCESS_OUTPUT_BYTES
    assert output == b"x" * MAX_PROCESS_OUTPUT_BYTES


def test_research_service_parses_pretty_summary():
    summary = ResearchService._parse_summary(
        '{\n  "status": "succeeded",\n  "episodeId": "ep_1",\n  "report": "/tmp/report.md"\n}'
    )
    assert summary["episodeId"] == "ep_1"


def test_studio_exposes_research_status_and_report_route(tmp_path: Path):
    init_project(tmp_path, "demo")
    server = create_server(tmp_path, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{server.server_port}/api/research") as response:
            payload = json.loads(response.read())
        assert payload["ok"] is True
        assert "available" in payload["data"]
        assert payload["data"]["reports"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
