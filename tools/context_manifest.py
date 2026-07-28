"""Traceable manifest for assembled writing context."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


SECTION_SOURCES: dict[str, tuple[int, list[str]]] = {
    "author_intent": (3, ["src/story/author_intent.md"]),
    "creative_focus": (1, ["src/story/current_focus.md"]),
    "story_background": (3, ["src/story/background.md", "src/story/foundation.md"]),
    "historical_arc_summaries": (4, ["src/outline.md"]),
    "current_arc_sections": (2, ["src/outline.md"]),
    "previous_chapter_content": (0, ["data/manuscript/"]),
    "protagonist_state": (1, ["data/world/current_state.md"]),
    "current_state": (3, ["data/world/current_state.md"]),
    "ledger": (3, ["data/world/ledger.md"]),
    "relationships": (3, ["data/world/relationships.md"]),
    "character_documents": (2, ["src/characters/"]),
    "concept_documents": (2, ["src/world/"]),
    "style_documents": (3, ["data/style/", "craft/"]),
}


def build_context_manifest(novel_root: Path, packet: dict[str, Any]) -> dict[str, Any]:
    """Describe context layers, provenance, size and stable revisions."""
    root = Path(novel_root).resolve()
    items: list[dict[str, Any]] = []
    for section, (level, sources) in SECTION_SOURCES.items():
        value = packet.get(section)
        rendered = _render(value)
        if not rendered.strip():
            continue
        resolved = _resolve_sources(root, sources)
        items.append(
            {
                "section": section,
                "level": level,
                "characters": len(rendered),
                "estimated_tokens": max(1, int(len(rendered) / 1.5)),
                "sources": resolved,
                "revision": hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16],
            }
        )
    revision_seed = "\n".join(
        f"{item['section']}:{item['revision']}" for item in items
    )
    return {
        "schema_version": 1,
        "strategy": "hierarchical-provenance-v1",
        "revision": hashlib.sha256(revision_seed.encode("utf-8")).hexdigest()[:16],
        "estimated_tokens": sum(int(item["estimated_tokens"]) for item in items),
        "items": items,
    }


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(f"{key}: {_render(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "\n".join(_render(item) for item in value)
    return str(value or "")


def _resolve_sources(root: Path, sources: list[str]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for relative in sources:
        path = root / relative
        if relative.endswith("/"):
            exists = path.is_dir()
            revision = "directory" if exists else "missing"
        else:
            exists = path.is_file()
            revision = _file_revision(path) if exists else "missing"
        resolved.append({"path": relative, "exists": exists, "revision": revision})
    return resolved


def _file_revision(path: Path) -> str:
    try:
        content = path.read_bytes()
    except OSError:
        return "unreadable"
    return hashlib.sha256(content).hexdigest()[:16]
