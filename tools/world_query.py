"""世界观查询工具

扫描 data/novels/{novel_id}/src/world/entities/*.md，返回结构化摘要。
LLM 通过此工具快速了解全部世界观实体，按需再 Read 具体文件。

用法:
    python3 -m tools.world_query <novel_id>                    # 列出所有实体摘要
    python3 -m tools.world_query <novel_id> <entity_id>        # 查看单个实体详情
    python3 -m tools.world_query <novel_id> --type concept     # 按类型筛选
    python3 -m tools.world_query <novel_id> --relations        # 输出关系图谱
    python3 tools/world_query.py <novel_id>                    # 兼容直跑
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.frontmatter import compose_toml_document, parse_toml_front_matter


def parse_entity(filepath: Path) -> Dict[str, Any]:
    """解析单个 Markdown 实体文件为结构化数据。

    解析规则:
        - 文件名(不含.md) = id
        - H1 = name
        - 第一个 blockquote = "type | subtype | status"
        - H1 后第一段正文 = description
        - ## 规则 下的列表 = rules
        - ## 特征 下的列表 = features
        - ## 关联 下的列表 = relations (格式: "entity_id — 说明")
        - 其他 ## = extra sections
    """
    text = filepath.read_text(encoding="utf-8")
    meta, body = parse_toml_front_matter(text)
    meta_relations = meta.get("related", []) if isinstance(meta.get("related"), list) else []

    status_from_meta = "status" in meta
    entity: Dict[str, Any] = {
        "id": str(meta.get("id", filepath.stem)).strip() or filepath.stem,
        "name": str(meta.get("name", "")).strip(),
        "type": str(meta.get("type", "")).strip(),
        "subtype": str(meta.get("subtype", "")).strip(),
        "status": str(meta.get("status", "active")).strip() or "active",
        "description": str(meta.get("summary", "")).strip(),
        "rules": [],
        "features": [],
        "relations": _normalize_meta_relations(meta_relations),
        "tags": list(meta.get("tags", [])) if isinstance(meta.get("tags"), list) else [],
        "detail_refs": list(meta.get("detail_refs", []))
        if isinstance(meta.get("detail_refs"), list)
        else [],
        "extra_sections": {},
        "file": str(filepath),
    }

    lines = body.split("\n")
    i = 0

    # Skip leading blank lines / comments
    while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith("#") is False):
        if lines[i].strip().startswith("# ") and not lines[i].strip().startswith("## "):
            break
        i += 1

    # Find H1 title
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("# ") and not line.startswith("## "):
            entity["name"] = line[2:].strip()
            i += 1
            break
        i += 1

    # Find optional legacy metadata blockquote (> type | subtype | status).
    # Front matter may only partially fill these fields, so we still probe and
    # only backfill missing values instead of skipping the line entirely.
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("> "):
            meta_line = line[2:].strip()
            parts = [p.strip() for p in meta_line.split("|")]
            if len(parts) >= 1 and not entity["type"]:
                entity["type"] = parts[0]
            if len(parts) >= 2 and not entity["subtype"]:
                entity["subtype"] = parts[1]
            if len(parts) >= 3 and not status_from_meta:
                entity["status"] = parts[2] or entity["status"]
            i += 1
            break
        if line.startswith("## ") or line.startswith("# "):
            break
        break

    # Find description (first paragraph after metadata)
    desc_lines: List[str] = []
    # Skip blank lines
    while i < len(lines) and not lines[i].strip():
        i += 1
    if not entity["description"]:
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("## "):
                break
            desc_lines.append(stripped)
            i += 1
        entity["description"] = " ".join(desc_lines)

    # Parse sections
    current_section = ""
    section_items: List[str] = []
    section_text_lines: List[str] = []

    def flush_section():
        nonlocal section_items, section_text_lines
        key = _normalize_section(current_section)
        if key == "rules":
            entity["rules"] = section_items[:]
        elif key == "features":
            entity["features"] = section_items[:]
        elif key == "relations":
            entity["relations"].extend(_parse_relations(section_items))
        elif current_section:
            content = "\n".join(section_text_lines).strip()
            if section_items:
                content = "\n".join(f"- {item}" for item in section_items)
            entity["extra_sections"][current_section] = content
        section_items = []
        section_text_lines = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("## "):
            flush_section()
            current_section = stripped[3:].strip()
            i += 1
            continue

        if stripped.startswith("- "):
            section_items.append(stripped[2:].strip())
        elif stripped:
            section_text_lines.append(stripped)

        i += 1

    flush_section()
    entity["relations"] = _dedupe_relations(entity["relations"])

    return entity


def _normalize_section(name: str) -> str:
    """将段落标题归一化为字段名。"""
    mapping = {
        "规则": "rules",
        "rules": "rules",
        "特征": "features",
        "features": "features",
        "关联": "relations",
        "relations": "relations",
    }
    return mapping.get(name, mapping.get(name.lower(), ""))


def _parse_relations(items: List[str]) -> List[Dict[str, str]]:
    """解析关联列表。格式: 'entity_id — 说明' 或 'entity_id - 说明'"""
    relations = []
    for item in items:
        # Support both — (em dash) and - (hyphen) as separator
        match = re.match(r"^(\S+)\s*[—–-]\s*(.+)$", item)
        if match:
            relations.append(
                {
                    "target": match.group(1).strip(),
                    "description": match.group(2).strip(),
                }
            )
        else:
            relations.append({"target": item.strip(), "description": ""})
    return relations


def _normalize_meta_relations(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Normalize TOML front matter related entries to legacy relation shape."""
    relations: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target", "")).strip()
        if not target:
            continue
        description = (
            str(item.get("description", "")).strip()
            or str(item.get("note", "")).strip()
            or str(item.get("kind", "")).strip()
        )
        relations.append({"target": target, "description": description})
    return relations


def _dedupe_relations(relations: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Preserve order while removing duplicate relation entries."""
    deduped: List[Dict[str, str]] = []
    seen = set()
    for rel in relations:
        target = str(rel.get("target", "")).strip()
        description = str(rel.get("description", "")).strip()
        key = (target, description)
        if not target or key in seen:
            continue
        seen.add(key)
        deduped.append({"target": target, "description": description})
    return deduped


def list_entities(
    novel_id: str,
    entity_type: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> List[Dict[str, str]]:
    """列出所有实体的摘要（id + name + type + status + description 前50字）。

    这是 LLM 应该首先调用的函数 — 快速了解全局，无需读取每个文件。
    """
    root = (project_root or Path(__file__).parent.parent).resolve()
    entities_dir = root / "data" / "novels" / novel_id / "src" / "world" / "entities"

    if not entities_dir.exists():
        return []

    results = []
    for f in sorted(entities_dir.rglob("*.md")):
        entity = parse_entity(f)
        if entity_type and entity["type"] != entity_type:
            continue
        desc = entity["description"]
        short_desc = desc[:60] + "..." if len(desc) > 60 else desc
        results.append(
            {
                "id": entity["id"],
                "name": entity["name"],
                "type": entity["type"],
                "subtype": entity["subtype"],
                "status": entity["status"],
                "description": short_desc,
            }
        )

    return results


def get_entity(
    novel_id: str,
    entity_id: str,
    project_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """获取单个实体的完整结构化数据。"""
    root = (project_root or Path(__file__).parent.parent).resolve()
    entities_dir = root / "data" / "novels" / novel_id / "src" / "world" / "entities"
    matches = sorted(entities_dir.rglob(f"{entity_id}.md")) if entities_dir.exists() else []
    if not matches:
        return None
    return parse_entity(matches[0])


def search_relation_targets(
    novel_id: str,
    query: str,
    project_root: Optional[Path] = None,
    *,
    entity_type: str = "",
    limit: int = 20,
) -> Dict[str, Any]:
    """Search character and world entity files for relation candidates."""
    root = (project_root or Path(__file__).parent.parent).resolve()
    query_text = str(query or "").strip()
    query_terms = [term.casefold() for term in re.split(r"\s+", query_text) if term.strip()]
    type_filter = str(entity_type or "").strip().casefold()
    candidates = _relation_document_summaries(root, novel_id)
    ranked: List[Dict[str, Any]] = []
    for candidate in candidates:
        candidate_type = str(candidate.get("type") or "").casefold()
        if type_filter and type_filter not in candidate_type:
            continue
        haystack = "\n".join(
            str(candidate.get(key) or "")
            for key in ("id", "name", "type", "subtype", "description", "content")
        ).casefold()
        if not query_terms:
            score = 1
            matched_terms: List[str] = []
        else:
            matched_terms = [term for term in query_terms if term in haystack]
            if not matched_terms:
                continue
            score = len(matched_terms)
            name = str(candidate.get("name") or "").casefold()
            identifier = str(candidate.get("id") or "").casefold()
            if any(term in name or term in identifier for term in matched_terms):
                score += 2
        snippet = _candidate_snippet(str(candidate.get("content") or ""), query_terms)
        ranked.append(
            {
                "id": candidate["id"],
                "name": candidate["name"],
                "type": candidate["type"],
                "subtype": candidate.get("subtype", ""),
                "description": candidate.get("description", ""),
                "source_path": candidate["source_path"],
                "score": score,
                "matched_terms": matched_terms,
                "snippet": snippet,
            }
        )
    ranked.sort(
        key=lambda item: (
            -int(item["score"]),
            str(item["name"]).casefold(),
            str(item["id"]).casefold(),
        )
    )
    safe_limit = max(1, min(int(limit or 20), 80))
    return {
        "query": query_text,
        "count": len(ranked),
        "candidates": ranked[:safe_limit],
    }


def get_relations_graph(
    novel_id: str,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """从所有实体的 ## 关联 段落汇总生成关系图谱。

    不再依赖手动维护的 graph.yaml —— 关系数据直接从实体文件中提取。
    """
    root = (project_root or Path(__file__).parent.parent).resolve()
    entities_dir = root / "data" / "novels" / novel_id / "src" / "world" / "entities"

    if not entities_dir.exists():
        return {"entities": [], "relations": []}

    all_entities = []
    all_relations = []
    seen_relations = set()

    for f in sorted(entities_dir.rglob("*.md")):
        entity = parse_entity(f)
        all_entities.append(entity["id"])
        for rel in entity["relations"]:
            edge = (
                entity["id"],
                rel["target"],
                rel["description"],
            )
            if edge in seen_relations:
                continue
            seen_relations.add(edge)
            all_relations.append(
                {
                    "source": entity["id"],
                    "target": rel["target"],
                    "description": rel["description"],
                }
            )

    return {"entities": all_entities, "relations": all_relations}


def get_relations_topology(
    novel_id: str,
    project_root: Optional[Path] = None,
    *,
    max_nodes: int = 120,
    max_edges: int = 240,
) -> Dict[str, Any]:
    """Build a bounded Studio topology from canonical entity files."""
    root = (project_root or Path(__file__).parent.parent).resolve()
    entities_dir = root / "data" / "novels" / novel_id / "src" / "world" / "entities"
    characters_dir = root / "data" / "novels" / novel_id / "src" / "characters"
    if not entities_dir.exists() and not characters_dir.exists():
        return _empty_topology(max_nodes=max_nodes, max_edges=max_edges)

    parsed = []
    for path in sorted(entities_dir.rglob("*.md")):
        entity = parse_entity(path)
        _, body = parse_toml_front_matter(path.read_text(encoding="utf-8"))
        entity["relations"] = _merge_relations_by_target(
            entity["relations"], _parse_markdown_relationships(body, heading_targets=False)
        )
        parsed.append(entity)
    if characters_dir.exists():
        from tools.character_sync import parse_profile_to_card

        for path in sorted(characters_dir.glob("*.md")):
            card = parse_profile_to_card(path)
            if not card:
                continue
            metadata, body = parse_toml_front_matter(path.read_text(encoding="utf-8"))
            parsed.append(
                {
                    "id": str(card.get("id") or path.stem),
                    "name": str(card.get("name") or path.stem),
                    "type": "人物",
                    "status": "active",
                    "description": str(card.get("brief") or card.get("background") or ""),
                    "role": str(metadata.get("role") or metadata.get("tier") or ""),
                    "relations": _merge_relations_by_target(
                        _normalize_character_relations(card.get("relationships")),
                        _parse_markdown_relationships(body, heading_targets=True),
                    ),
                    "file": str(path),
                }
            )
    by_id = {str(entity["id"]): entity for entity in parsed}
    by_name = {
        str(entity["name"]): entity
        for entity in parsed
        if str(entity.get("name") or "").strip()
    }
    protagonist = next(
        (
            entity
            for entity in parsed
            if entity.get("type") == "人物"
            and str(entity.get("role") or "").strip() == "主角"
        ),
        None,
    )
    if protagonist is not None:
        by_name["主角"] = protagonist
    raw_edges: List[Dict[str, str]] = []
    referenced_ids: set[str] = set()
    for entity in parsed:
        source_id = str(entity["id"])
        for relation in entity["relations"]:
            raw_target = str(relation.get("target") or "").strip()
            if not raw_target:
                continue
            clean_target = _clean_relation_target(raw_target)
            target = by_id.get(raw_target) or by_name.get(raw_target) or by_name.get(clean_target)
            target_id = str(target["id"]) if target else f"unresolved:{raw_target}"
            referenced_ids.update((source_id, target_id))
            raw_edges.append(
                {
                    "source": source_id,
                    "target": target_id,
                    "label": str(relation.get("description") or "").strip(),
                    "raw_target": raw_target,
                }
            )

    ordered = sorted(
        parsed,
        key=lambda entity: (
            str(entity["id"]) not in referenced_ids,
            str(entity.get("name") or entity["id"]).casefold(),
        ),
    )
    nodes = [_topology_node(entity, root) for entity in ordered[: max(1, max_nodes)]]
    included = {node["id"] for node in nodes}
    for edge in raw_edges:
        if len(nodes) >= max(1, max_nodes):
            break
        if edge["target"] in included or not edge["target"].startswith("unresolved:"):
            continue
        nodes.append(
            {
                "id": edge["target"],
                "label": edge["raw_target"],
                "kind": "unknown",
                "type": "未归档",
                "status": "unresolved",
                "description": "关系指向了尚未建立实体文件的目标。",
                "source_path": "",
                "unresolved": True,
            }
        )
        included.add(edge["target"])

    edges = []
    for index, edge in enumerate(raw_edges):
        if len(edges) >= max(0, max_edges):
            break
        if edge["source"] not in included or edge["target"] not in included:
            continue
        edges.append(
            {
                "id": f"relation-{index + 1}",
                "source": edge["source"],
                "target": edge["target"],
                "label": edge["label"] or "关联",
            }
        )

    unresolved_count = len(
        {edge["target"] for edge in raw_edges if edge["target"].startswith("unresolved:")}
    )
    return {
        "nodes": nodes,
        "edges": edges,
        "totals": {"nodes": len(parsed) + unresolved_count, "edges": len(raw_edges)},
        "limits": {"nodes": max_nodes, "edges": max_edges},
        "truncated": len(nodes) < len(parsed) + unresolved_count or len(edges) < len(raw_edges),
    }


def _topology_node(entity: Dict[str, Any], root: Path) -> Dict[str, Any]:
    entity_type = str(entity.get("type") or "").strip()
    source = Path(str(entity.get("file") or ""))
    try:
        source_path = source.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        source_path = source.name
    return {
        "id": str(entity["id"]),
        "label": str(entity.get("name") or entity["id"]),
        "kind": _topology_kind(entity_type),
        "type": entity_type or "未分类",
        "status": str(entity.get("status") or "active"),
        "description": str(entity.get("description") or ""),
        "source_path": source_path,
        "unresolved": False,
    }


def _topology_kind(entity_type: str) -> str:
    value = entity_type.casefold()
    groups = {
        "character": ("character", "person", "role", "人物", "角色"),
        "faction": ("faction", "organization", "group", "势力", "组织", "门派"),
        "place": (
            "place",
            "location",
            "region",
            "地点",
            "场所",
            "区域",
            "冠域",
        ),
        "concept": (
            "concept",
            "system",
            "rule",
            "概念",
            "体系",
            "规则",
            "设定",
            "能力路线",
            "反派神性生物",
        ),
    }
    for kind, markers in groups.items():
        if any(marker in value for marker in markers):
            return kind
    return "unknown"


def _normalize_character_relations(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    relations: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target") or "").strip()
        if not target:
            continue
        description = (
            str(item.get("note") or "").strip()
            or str(item.get("description") or "").strip()
            or str(item.get("kind") or "").strip()
        )
        relations.append({"target": target, "description": description})
    return _dedupe_relations(relations)


def _parse_markdown_relationships(
    body: str, *, heading_targets: bool
) -> List[Dict[str, str]]:
    """Read conservative relationship declarations from human-authored Markdown."""
    relations: List[Dict[str, str]] = []
    section_pattern = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
    matches = list(section_pattern.finditer(body))
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section = body[match.end():section_end]
        if heading in {"关系网络", "人物关系", "人际关系"}:
            for line in section.splitlines():
                item = re.match(r"^\s*[-*]\s+\*\*(.+?)\*\*[：:]\s*(.+?)\s*$", line)
                if item:
                    relations.append(
                        {
                            "target": _clean_relation_target(item.group(1)),
                            "description": item.group(2).strip()[:240],
                        }
                    )
        if not heading_targets:
            continue
        target_match = re.match(r"^与(.+?)(?:的)?(?:关系|羁绊)", heading)
        if target_match:
            relations.append(
                {
                    "target": _clean_relation_target(target_match.group(1)),
                    "description": _relationship_section_summary(section)
                    or heading[:240],
                }
            )
    return _dedupe_relations(relations)


def _relationship_section_summary(section: str) -> str:
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "|", ">", "- ", "* ")):
            continue
        clean = re.sub(r"[*_`]", "", line)
        return clean[:240]
    return ""


def _clean_relation_target(value: str) -> str:
    clean = str(value or "").strip().strip("*_")
    clean = re.sub(r"[（(][^）)]*[）)]\s*$", "", clean).strip()
    return clean


def _merge_relations_by_target(*groups: List[Dict[str, str]]) -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        for relation in group:
            target = _clean_relation_target(str(relation.get("target") or ""))
            if not target or target in seen:
                continue
            seen.add(target)
            merged.append(
                {
                    "target": target,
                    "description": str(relation.get("description") or "").strip(),
                }
            )
    return merged


def edit_world_relation(
    novel_id: str,
    source_id: str,
    target_id: str,
    description: str,
    *,
    project_root: Optional[Path] = None,
    action: str = "upsert",
    base_revision: str = "",
    confirm: bool = False,
) -> Dict[str, Any]:
    """Preview or atomically apply one canonical ``[[related]]`` edit."""
    root = (project_root or Path(__file__).parent.parent).resolve()
    source = _find_relation_document(root, novel_id, source_id)
    target = _find_relation_document(root, novel_id, target_id)
    if source is None:
        return {
            "ok": False,
            "error": _missing_relation_entity_error(
                root, novel_id, source_id, label="关系源实体不存在"
            ),
        }
    if target is None:
        return {
            "ok": False,
            "error": _missing_relation_entity_error(
                root, novel_id, target_id, label="关系目标实体不存在"
            ),
        }
    if source.resolve() == target.resolve():
        return {"ok": False, "error": "关系源和目标不能相同"}
    if action not in {"upsert", "remove"}:
        return {"ok": False, "error": "action 仅支持 upsert 或 remove"}

    original = source.read_text(encoding="utf-8")
    revision = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    metadata, body = parse_toml_front_matter(original)
    source_metadata = metadata.copy()
    target_metadata, target_body = parse_toml_front_matter(target.read_text(encoding="utf-8"))
    canonical_target = str(target_metadata.get("id") or target.stem).strip()
    related = [item for item in metadata.get("related", []) if isinstance(item, dict)]
    existing_index = next(
        (
            index
            for index, item in enumerate(related)
            if str(item.get("target") or "").strip() == canonical_target
        ),
        None,
    )
    if action == "remove" and existing_index is None:
        updated = original
    else:
        if not metadata:
            metadata = {
                "id": source.stem,
                "name": _markdown_title(body) or source.stem,
            }
        if action == "remove":
            related.pop(existing_index)
        else:
            entry = {"target": canonical_target, "kind": "related"}
            if str(description or "").strip():
                entry["note"] = str(description).strip()
            if existing_index is None:
                related.append(entry)
            else:
                related[existing_index] = {**related[existing_index], **entry}
        if related:
            metadata["related"] = related
        else:
            metadata.pop("related", None)
        updated = compose_toml_document(metadata, body)
    if original.endswith("\n") and not updated.endswith("\n"):
        updated += "\n"
    relative = source.resolve().relative_to(root).as_posix()
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )
    payload = {
        "ok": True,
        "applied": False,
        "source_id": str(source_metadata.get("id") or source.stem),
        "target_id": canonical_target,
        "target_name": str(
            target_metadata.get("name") or _markdown_title(target_body) or target.stem
        ),
        "source_path": relative,
        "base_revision": revision,
        "diff": diff,
        "changed": original != updated,
    }
    if not confirm or original == updated:
        return payload
    if not base_revision or base_revision != revision:
        return {
            **payload,
            "ok": False,
            "error": "relation_revision_conflict",
            "message": "关系源文件已变化，请重新预览后确认",
        }
    source.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=source.parent, delete=False
    ) as handle:
        handle.write(updated)
        temp_path = Path(handle.name)
    os.replace(temp_path, source)
    return {
        **payload,
        "applied": True,
        "revision": hashlib.sha256(updated.encode("utf-8")).hexdigest()[:16],
    }


def edit_world_relations(
    novel_id: str,
    relations: Optional[List[Dict[str, Any]]] = None,
    *,
    project_root: Optional[Path] = None,
    confirm: bool = False,
    base_revisions: Optional[Dict[str, str]] = None,
    preview_token: str = "",
    preview_tokens: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Preview or apply multiple canonical ``[[related]]`` edits in one request."""
    root = (project_root or Path(__file__).parent.parent).resolve()
    stored_preview_paths: List[Path] = []
    requested_tokens = [
        str(token).strip()
        for token in (preview_tokens or [])
        if str(token or "").strip()
    ]
    if str(preview_token or "").strip():
        requested_tokens.append(str(preview_token).strip())
    requested_tokens = list(dict.fromkeys(requested_tokens))
    if confirm and requested_tokens:
        stored_relations: List[Dict[str, Any]] = []
        stored_revisions: Dict[str, str] = {}
        for token in requested_tokens:
            stored_preview, stored_path, preview_error = _load_relation_preview(
                root,
                novel_id,
                token,
            )
            if preview_error:
                return {"ok": False, "error": preview_error}
            for key, revision in stored_preview["source_revisions"].items():
                existing = stored_revisions.get(str(key))
                if existing is not None and existing != str(revision):
                    return {
                        "ok": False,
                        "error": "多份关系预览的源文件基线不一致，请重新合并预览",
                    }
                stored_revisions[str(key)] = str(revision)
            stored_relations.extend(stored_preview["relations"])
            if stored_path is not None:
                stored_preview_paths.append(stored_path)
        relations = stored_relations
        base_revisions = stored_revisions

    if not isinstance(relations, list) or not relations:
        return {
            "ok": False,
            "error": "relations 不能为空；确认已有预览时可改传 preview_token",
        }
    if len(relations) > 50:
        return {"ok": False, "error": "单次最多编辑 50 条关系"}

    grouped: Dict[Path, Dict[str, Any]] = {}
    changes: List[Dict[str, Any]] = []
    canonical_relations: List[Dict[str, Any]] = []
    provided_revisions = base_revisions if isinstance(base_revisions, dict) else {}
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            return {"ok": False, "error": f"第 {index + 1} 条关系不是对象"}
        source_id = str(relation.get("source_id") or "").strip()
        target_id = str(relation.get("target_id") or "").strip()
        description = str(relation.get("description") or relation.get("note") or "").strip()
        action = str(relation.get("action") or "upsert").strip() or "upsert"
        if action not in {"upsert", "remove"}:
            return {"ok": False, "error": "action 仅支持 upsert 或 remove"}
        source = _find_relation_document(root, novel_id, source_id)
        target = _find_relation_document(root, novel_id, target_id)
        if source is None:
            return {
                "ok": False,
                "error": _missing_relation_entity_error(
                    root,
                    novel_id,
                    source_id,
                    label=f"第 {index + 1} 条关系源实体不存在",
                ),
            }
        if target is None:
            return {
                "ok": False,
                "error": _missing_relation_entity_error(
                    root,
                    novel_id,
                    target_id,
                    label=f"第 {index + 1} 条关系目标实体不存在",
                ),
            }
        if source.resolve() == target.resolve():
            return {"ok": False, "error": f"第 {index + 1} 条关系源和目标不能相同"}

        group = grouped.get(source)
        if group is None:
            original = source.read_text(encoding="utf-8")
            revision = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
            metadata, body = parse_toml_front_matter(original)
            if not metadata:
                metadata = {
                    "id": source.stem,
                    "name": _markdown_title(body) or source.stem,
                }
            related = [item for item in metadata.get("related", []) if isinstance(item, dict)]
            group = {
                "source": source,
                "original": original,
                "revision": revision,
                "metadata": metadata,
                "body": body,
                "related": related,
                "item_revisions": [],
            }
            grouped[source] = group
        item_revision = str(relation.get("base_revision") or "").strip()
        if item_revision:
            group["item_revisions"].append(item_revision)

        target_metadata, target_body = parse_toml_front_matter(
            target.read_text(encoding="utf-8")
        )
        canonical_target = str(target_metadata.get("id") or target.stem).strip()
        canonical_relations.append(
            {
                "source_id": str(group["metadata"].get("id") or source.stem),
                "target_id": canonical_target,
                "description": description,
                "action": action,
            }
        )
        existing_index = next(
            (
                rel_index
                for rel_index, item in enumerate(group["related"])
                if str(item.get("target") or "").strip() == canonical_target
            ),
            None,
        )
        changed = False
        if action == "remove":
            if existing_index is not None:
                group["related"].pop(existing_index)
                changed = True
        else:
            entry = {"target": canonical_target, "kind": "related"}
            if description:
                entry["note"] = description
            if existing_index is None:
                group["related"].append(entry)
                changed = True
            else:
                merged = {**group["related"][existing_index], **entry}
                changed = merged != group["related"][existing_index]
                group["related"][existing_index] = merged
        changes.append(
            {
                "index": index + 1,
                "source_id": str(group["metadata"].get("id") or source.stem),
                "target_id": canonical_target,
                "target_name": str(
                    target_metadata.get("name") or _markdown_title(target_body) or target.stem
                ),
                "action": action,
                "changed": changed,
            }
        )

    diffs: List[str] = []
    updated_by_source: Dict[Path, str] = {}
    source_revisions: Dict[str, str] = {}
    changed_sources = 0
    for source, group in grouped.items():
        metadata = dict(group["metadata"])
        if group["related"]:
            metadata["related"] = group["related"]
        else:
            metadata.pop("related", None)
        updated = compose_toml_document(metadata, group["body"])
        if group["original"].endswith("\n") and not updated.endswith("\n"):
            updated += "\n"
        relative = source.resolve().relative_to(root).as_posix()
        diff = "".join(
            difflib.unified_diff(
                group["original"].splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        source_id = str(metadata.get("id") or source.stem)
        source_revisions[source_id] = str(group["revision"])
        source_revisions[relative] = str(group["revision"])
        diffs.append(diff)
        updated_by_source[source] = updated
        if updated != group["original"]:
            changed_sources += 1

    payload = {
        "ok": True,
        "applied": False,
        "changed": changed_sources > 0,
        "changed_sources": changed_sources,
        "changes": changes,
        "source_revisions": source_revisions,
        "diff": "".join(diffs),
        "next_action": "用户确认后仅传 preview_token，并设置 confirm=true",
    }
    if not confirm:
        if changed_sources > 0:
            payload["preview_token"] = _save_relation_preview(
                root,
                novel_id,
                canonical_relations,
                source_revisions,
            )
        return payload
    if changed_sources == 0:
        for stored_path in stored_preview_paths:
            stored_path.unlink(missing_ok=True)
        return payload

    for source, group in grouped.items():
        relative = source.resolve().relative_to(root).as_posix()
        source_id = str(group["metadata"].get("id") or source.stem)
        expected = (
            str(provided_revisions.get(source_id) or "").strip()
            or str(provided_revisions.get(relative) or "").strip()
            or (group["item_revisions"][0] if group["item_revisions"] else "")
        )
        if expected != group["revision"]:
            return {
                **payload,
                "ok": False,
                "error": "relation_revision_conflict",
                "message": f"{relative} 已变化，请重新预览后确认",
            }

    revisions: Dict[str, str] = {}
    for source, updated in updated_by_source.items():
        if updated == grouped[source]["original"]:
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=source.parent, delete=False
        ) as handle:
            handle.write(updated)
            temp_path = Path(handle.name)
        os.replace(temp_path, source)
        relative = source.resolve().relative_to(root).as_posix()
        revisions[relative] = hashlib.sha256(updated.encode("utf-8")).hexdigest()[:16]
    for stored_path in stored_preview_paths:
        stored_path.unlink(missing_ok=True)
    return {**payload, "applied": True, "revisions": revisions}


def _relation_preview_root(root: Path, novel_id: str) -> Path:
    return root / "data" / "novels" / novel_id / "data" / "workflows" / "relation_previews"


def _relation_preview_record(
    novel_id: str,
    relations: List[Dict[str, Any]],
    source_revisions: Dict[str, str],
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "novel_id": novel_id,
        "relations": relations,
        "source_revisions": source_revisions,
    }


def _relation_preview_token(record: Dict[str, Any]) -> str:
    serialized = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def _save_relation_preview(
    root: Path,
    novel_id: str,
    relations: List[Dict[str, Any]],
    source_revisions: Dict[str, str],
) -> str:
    record = _relation_preview_record(novel_id, relations, source_revisions)
    token = _relation_preview_token(record)
    preview_root = _relation_preview_root(root, novel_id)
    preview_root.mkdir(parents=True, exist_ok=True)
    target = preview_root / f"{token}.json"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=preview_root,
        prefix=f".{token}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(record, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, target)
    return token


def _load_relation_preview(
    root: Path,
    novel_id: str,
    preview_token: str,
) -> tuple[Dict[str, Any], Optional[Path], str]:
    if not re.fullmatch(r"[a-f0-9]{24}", preview_token):
        return {}, None, "关系预览凭据格式无效，请重新预览"
    preview_root = _relation_preview_root(root, novel_id).resolve()
    path = (preview_root / f"{preview_token}.json").resolve()
    if preview_root not in path.parents or not path.is_file():
        return {}, None, "关系预览凭据不存在或已使用，请重新预览"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, None, "关系预览凭据损坏，请重新预览"
    if not isinstance(raw, dict):
        return {}, None, "关系预览凭据损坏，请重新预览"
    relations = raw.get("relations")
    source_revisions = raw.get("source_revisions")
    if (
        raw.get("schema_version") != 1
        or raw.get("novel_id") != novel_id
        or not isinstance(relations, list)
        or not isinstance(source_revisions, dict)
    ):
        return {}, None, "关系预览凭据与当前作品不匹配，请重新预览"
    record = _relation_preview_record(novel_id, relations, source_revisions)
    if _relation_preview_token(record) != preview_token:
        return {}, None, "关系预览凭据校验失败，请重新预览"
    return record, path, ""


def _relation_document_summaries(root: Path, novel_id: str) -> List[Dict[str, Any]]:
    novel_root = root / "data" / "novels" / novel_id / "src"
    summaries: List[Dict[str, Any]] = []
    world_root = novel_root / "world" / "entities"
    if world_root.exists():
        for path in sorted(world_root.rglob("*.md")):
            entity = parse_entity(path)
            content = path.read_text(encoding="utf-8")
            summaries.append(
                {
                    "id": str(entity.get("id") or path.stem),
                    "name": str(entity.get("name") or path.stem),
                    "type": str(entity.get("type") or "未分类"),
                    "subtype": str(entity.get("subtype") or ""),
                    "description": str(entity.get("description") or ""),
                    "content": content,
                    "source_path": path.resolve().relative_to(root).as_posix(),
                }
            )
    characters_root = novel_root / "characters"
    if characters_root.exists():
        from tools.character_sync import parse_profile_to_card

        for path in sorted(characters_root.glob("*.md")):
            card = parse_profile_to_card(path)
            metadata, body = parse_toml_front_matter(path.read_text(encoding="utf-8"))
            name = str(
                (card or {}).get("name")
                or metadata.get("name")
                or _markdown_title(body)
                or path.stem
            )
            summaries.append(
                {
                    "id": str((card or {}).get("id") or metadata.get("id") or path.stem),
                    "name": name,
                    "type": "人物",
                    "subtype": str(metadata.get("role") or metadata.get("tier") or ""),
                    "description": str(
                        (card or {}).get("brief")
                        or (card or {}).get("background")
                        or metadata.get("summary")
                        or ""
                    ),
                    "content": path.read_text(encoding="utf-8"),
                    "source_path": path.resolve().relative_to(root).as_posix(),
                }
            )
    return summaries


def _candidate_snippet(content: str, query_terms: List[str], *, limit: int = 160) -> str:
    clean = re.sub(r"\s+", " ", str(content or "")).strip()
    if not clean:
        return ""
    lowered = clean.casefold()
    position = 0
    for term in query_terms:
        found = lowered.find(term)
        if found >= 0:
            position = max(0, found - 40)
            break
    snippet = clean[position: position + limit]
    prefix = "..." if position > 0 else ""
    suffix = "..." if position + limit < len(clean) else ""
    return f"{prefix}{snippet}{suffix}"


def _find_relation_document(root: Path, novel_id: str, entity_id: str) -> Optional[Path]:
    novel_root = root / "data" / "novels" / novel_id / "src"
    candidates = [
        *sorted((novel_root / "characters").glob("*.md")),
        *sorted((novel_root / "world" / "entities").rglob("*.md")),
    ]
    requested = str(entity_id or "").strip()
    for path in candidates:
        metadata, body = parse_toml_front_matter(path.read_text(encoding="utf-8"))
        aliases = metadata.get("alias", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        elif not isinstance(aliases, list):
            aliases = []
        identifiers = {
            path.stem,
            str(metadata.get("id") or "").strip(),
            str(metadata.get("name") or "").strip(),
            _markdown_title(body),
            *(str(alias).strip() for alias in aliases),
        }
        if requested in identifiers:
            return path
    return None


def _missing_relation_entity_error(
    root: Path,
    novel_id: str,
    entity_id: str,
    *,
    label: str,
) -> str:
    requested = str(entity_id or "").strip()
    novel_root = root / "data" / "novels" / novel_id / "src"
    candidates = [
        *sorted((novel_root / "characters").glob("*.md")),
        *sorted((novel_root / "world" / "entities").rglob("*.md")),
    ]
    identifiers: Dict[str, tuple[str, str]] = {}
    for path in candidates:
        metadata, body = parse_toml_front_matter(path.read_text(encoding="utf-8"))
        canonical_id = str(metadata.get("id") or path.stem).strip()
        canonical_name = str(metadata.get("name") or _markdown_title(body) or path.stem).strip()
        values = [path.stem, canonical_id, canonical_name, _markdown_title(body)]
        aliases = metadata.get("alias", [])
        if isinstance(aliases, str):
            values.append(aliases)
        elif isinstance(aliases, list):
            values.extend(str(alias) for alias in aliases)
        for value in values:
            clean = str(value or "").strip()
            if clean:
                identifiers[clean] = (canonical_name, canonical_id)
    matches = difflib.get_close_matches(requested, identifiers, n=3, cutoff=0.55)
    suggestions: List[str] = []
    seen: set[tuple[str, str]] = set()
    for match in matches:
        candidate = identifiers[match]
        if candidate in seen:
            continue
        seen.add(candidate)
        suggestions.append(f"{candidate[0]}（ID: {candidate[1]}）")
    suffix = f"；可能是: {', '.join(suggestions)}" if suggestions else ""
    return f"{label}: {requested}{suffix}"


def _markdown_title(body: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _empty_topology(*, max_nodes: int, max_edges: int) -> Dict[str, Any]:
    return {
        "nodes": [],
        "edges": [],
        "totals": {"nodes": 0, "edges": 0},
        "limits": {"nodes": max_nodes, "edges": max_edges},
        "truncated": False,
    }


# ─── CLI ────────────────────────────────────────────────────────────


def _print_summary_table(entities: List[Dict[str, str]]):
    """打印实体摘要表。"""
    if not entities:
        print("（无实体）")
        return

    # Header
    print(f"{'ID':<28} {'名称':<16} {'类型':<20} {'状态':<8} 描述")
    print("─" * 100)
    for e in entities:
        type_str = f"{e['type']}/{e['subtype']}" if e["subtype"] else e["type"]
        print(f"{e['id']:<28} {e['name']:<16} {type_str:<20} {e['status']:<8} {e['description']}")


def _print_entity_detail(entity: Dict[str, Any]):
    """打印单个实体详情。"""
    type_str = f"{entity['type']}/{entity['subtype']}" if entity["subtype"] else entity["type"]
    print(f"# {entity['name']}")
    print(f"  ID: {entity['id']}  |  类型: {type_str}  |  状态: {entity['status']}")
    print(f"  文件: {entity['file']}")
    print()
    print(f"  {entity['description']}")

    if entity["rules"]:
        print(f"\n  规则 ({len(entity['rules'])}条):")
        for r in entity["rules"]:
            print(f"    - {r}")

    if entity["features"]:
        print(f"\n  特征 ({len(entity['features'])}条):")
        for f in entity["features"]:
            print(f"    - {f}")

    if entity["relations"]:
        print(f"\n  关联 ({len(entity['relations'])}条):")
        for r in entity["relations"]:
            desc = f" — {r['description']}" if r["description"] else ""
            print(f"    → {r['target']}{desc}")

    for section, content in entity["extra_sections"].items():
        print(f"\n  {section}:")
        for line in content.split("\n"):
            print(f"    {line}")


def _print_relations(graph: Dict[str, Any]):
    """打印关系图谱。"""
    print(f"实体: {', '.join(graph['entities'])}")
    print(f"关系 ({len(graph['relations'])}条):")
    for r in graph["relations"]:
        desc = f" ({r['description']})" if r["description"] else ""
        print(f"  {r['source']} → {r['target']}{desc}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    novel_id = sys.argv[1]
    root = Path(__file__).parent.parent.resolve()
    if "--project-root" in sys.argv:
        idx = sys.argv.index("--project-root")
        if idx + 1 >= len(sys.argv):
            print("--project-root 缺少路径")
            sys.exit(1)
        root = Path(sys.argv[idx + 1]).expanduser().resolve()

    # --relations flag
    if "--relations" in sys.argv:
        graph = get_relations_graph(novel_id, root)
        _print_relations(graph)
        return

    # --type filter
    entity_type = None
    if "--type" in sys.argv:
        idx = sys.argv.index("--type")
        if idx + 1 < len(sys.argv):
            entity_type = sys.argv[idx + 1]

    # Specific entity (skip flag values)
    entity_id = None
    skip_next = False
    for arg in sys.argv[2:]:
        if skip_next:
            skip_next = False
            continue
        if arg in ("--type", "--project-root", "--relations"):
            skip_next = arg in {"--type", "--project-root"}
            continue
        entity_id = arg
        break

    if entity_id:
        entity = get_entity(novel_id, entity_id, root)
        if entity:
            _print_entity_detail(entity)
        else:
            print(f"实体不存在: {entity_id}")
            sys.exit(1)
    else:
        entities = list_entities(novel_id, entity_type, root)
        _print_summary_table(entities)


if __name__ == "__main__":
    main()
