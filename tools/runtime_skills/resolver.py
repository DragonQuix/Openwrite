"""Resolve layered Runtime Skills and compile project Rules."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from models.runtime_skills import (
    CompiledRulesV1,
    ResolvedSkillV1,
    RuleEntryV1,
    RulePreviewV1,
    SkillBudgetV1,
    SkillDiagnosticV1,
    SkillManifestV1,
    SkillResolutionV1,
)


class RuntimeSkillError(RuntimeError):
    """A user-correctable Runtime Skill or Rule error."""

    def __init__(self, message: str, *, code: str = "INVALID_SKILL") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _SkillRecord:
    manifest: SkillManifestV1
    root: Path
    layer: str
    source: str


_LAYER_ORDER = ("builtin", "global", "project")


def _safe_relative(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _stable_id(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value)
    return (str(value),)


class RuntimeSkillResolver:
    """Load and resolve built-in, machine-global and project Skills."""

    def __init__(
        self,
        project_root: Path,
        *,
        builtin_root: Path | None = None,
        global_root: Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.builtin_root = Path(builtin_root or Path(__file__).parent).resolve()
        configured_global = os.environ.get("OPENWRITE_GLOBAL_SKILLS_DIR", "").strip()
        self.global_root = Path(
            global_root or configured_global or (Path.home() / ".openwrite" / "skills")
        ).resolve()

    def discover(self) -> tuple[dict[str, _SkillRecord], tuple[SkillDiagnosticV1, ...]]:
        records: dict[str, _SkillRecord] = {}
        diagnostics: list[SkillDiagnosticV1] = []
        roots = (
            ("builtin", self.builtin_root),
            ("global", self.global_root),
            ("project", self.project_root / ".openwrite" / "skills"),
        )
        for layer, root in roots:
            if not root.is_dir():
                continue
            for skill_dir in sorted(root.iterdir()):
                manifest_path = skill_dir / "manifest.yaml"
                if not skill_dir.is_dir() or not manifest_path.is_file():
                    continue
                record = self._load_record(layer, skill_dir, manifest_path, diagnostics)
                if record is None:
                    continue
                previous = records.get(record.manifest.id)
                if previous is not None and previous.layer == layer:
                    diagnostics.append(
                        SkillDiagnosticV1(
                            code="duplicate_skill",
                            skill_id=record.manifest.id,
                            message=f"同一层存在重复 Skill: {record.manifest.id}",
                            source=record.source,
                        )
                    )
                records[record.manifest.id] = record
        return records, tuple(diagnostics)

    def _load_record(
        self,
        layer: str,
        skill_dir: Path,
        manifest_path: Path,
        diagnostics: list[SkillDiagnosticV1],
    ) -> _SkillRecord | None:
        source = str(manifest_path)
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("manifest 必须是对象")
            manifest = SkillManifestV1.model_validate(raw)
        except (OSError, ValueError, ValidationError) as exc:
            diagnostics.append(
                SkillDiagnosticV1(
                    code="invalid_manifest",
                    message=f"无法读取 Skill manifest: {exc}",
                    source=source,
                )
            )
            return None
        paths = (manifest.instructions_file,) + manifest.references
        if any(not _safe_relative(path) for path in paths):
            diagnostics.append(
                SkillDiagnosticV1(
                    code="unsafe_path",
                    skill_id=manifest.id,
                    message="Skill 资源路径必须是当前 Skill 目录内的相对路径",
                    source=source,
                )
            )
            return None
        return _SkillRecord(manifest=manifest, root=skill_dir, layer=layer, source=source)

    def list_skills(self) -> dict[str, Any]:
        records, diagnostics = self.discover()
        return {
            "skills": [
                {
                    "id": record.manifest.id,
                    "version": record.manifest.version,
                    "name": record.manifest.name,
                    "description": record.manifest.description,
                    "layer": record.layer,
                    "enabled": record.manifest.enabled,
                    "agents": list(record.manifest.agents),
                    "tasks": list(record.manifest.tasks),
                    "allow_tools": list(record.manifest.allow_tools),
                    "requires": list(record.manifest.requires),
                    "conflicts_with": list(record.manifest.conflicts_with),
                    "output_contract": record.manifest.output_contract,
                    "budget": record.manifest.budget.model_dump(mode="json"),
                    "source": record.source,
                }
                for record in records.values()
            ],
            "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
        }

    def resolve(
        self,
        *,
        agent: str,
        base_tools: Iterable[str],
        task: str = "",
        intent: str = "",
        document_type: str = "",
        explicit_skills: Iterable[str] | None = None,
        budget_chars: int = 24000,
    ) -> SkillResolutionV1:
        records, initial_diagnostics = self.discover()
        diagnostics = list(initial_diagnostics)
        available = set(records)
        requested = tuple(
            str(item).strip() for item in (explicit_skills or ()) if str(item).strip()
        )
        if not requested:
            requested = self._configured_skills(agent)
        reasons: list[str] = []
        selected_ids: list[str] = []
        for skill_id, record in records.items():
            manifest = record.manifest
            if not manifest.enabled:
                continue
            matches = bool(
                (agent and (not manifest.agents or agent in manifest.agents))
                and (
                    not task
                    or not manifest.tasks
                    or task in manifest.tasks
                    or task.startswith(tuple(f"{value}." for value in manifest.tasks))
                )
                and (not intent or not manifest.intents or intent in manifest.intents)
                and (
                    not document_type
                    or not manifest.document_types
                    or document_type in manifest.document_types
                )
            )
            if not requested and matches:
                selected_ids.append(skill_id)
                reasons.append(f"{skill_id}: agent/task/intent 匹配")
        if requested:
            for skill_id in requested:
                if skill_id not in available:
                    diagnostics.append(
                        SkillDiagnosticV1(
                            code="unknown_skill",
                            skill_id=skill_id,
                            message=f"未找到 Skill: {skill_id}",
                        )
                    )
                    continue
                selected_ids.append(skill_id)
                reasons.append(f"{skill_id}: 显式指定")

        selected_ids = self._expand_dependencies(selected_ids, records, diagnostics)
        selected_ids = self._filter_conflicts(selected_ids, records, diagnostics)
        resolved: list[ResolvedSkillV1] = []
        instructions: list[str] = []
        reference_budget = 0
        instruction_budget = max(256, min(100000, int(budget_chars)))
        base = set(str(item) for item in base_tools)
        allowed: set[str] | None = None
        output_contracts: list[str] = []
        for skill_id in selected_ids:
            selected_record = records.get(skill_id)
            if selected_record is None or not selected_record.manifest.enabled:
                continue
            manifest = selected_record.manifest
            text = self._read_resource(
                selected_record,
                manifest.instructions_file,
                manifest.budget.max_instruction_chars,
            )
            if text:
                remaining = instruction_budget - sum(len(item) for item in instructions)
                if remaining <= 0:
                    diagnostics.append(
                        SkillDiagnosticV1(
                            code="budget_exceeded",
                            severity="warning",
                            skill_id=skill_id,
                            message="Skill 指令总预算已用尽",
                            source=selected_record.source,
                        )
                    )
                else:
                    instructions.append(text[:remaining])
            refs: list[str] = []
            for reference in manifest.references:
                if reference_budget >= manifest.budget.max_reference_chars:
                    break
                content = self._read_resource(
                    selected_record,
                    reference,
                    min(manifest.budget.max_reference_chars - reference_budget, 100000),
                )
                if content:
                    refs.append(f"{reference}\n{content}")
                    reference_budget += len(content)
            resolved.append(
                ResolvedSkillV1(
                    id=manifest.id,
                    version=manifest.version,
                    layer=selected_record.layer,
                    instructions=text[: manifest.budget.max_instruction_chars],
                    references=tuple(refs),
                    allow_tools=manifest.allow_tools,
                    output_contract=manifest.output_contract,
                    budget=manifest.budget,
                )
            )
            skill_tools = set(manifest.allow_tools)
            if "*" in skill_tools:
                skill_tools = set(base)
            allowed = skill_tools if allowed is None else allowed | skill_tools
            if manifest.output_contract not in output_contracts:
                output_contracts.append(manifest.output_contract)
        effective = tuple(sorted(base if allowed is None else base & allowed))
        joined = "\n\n".join(instructions)
        return SkillResolutionV1(
            resolution_id=_stable_id(
                agent,
                task,
                intent,
                document_type,
                ",".join(selected_ids),
                ",".join(effective),
            ),
            agent=agent,
            task=task,
            intent=intent,
            document_type=document_type,
            skills=tuple(resolved),
            allowed_tools=effective,
            instructions=joined[:instruction_budget],
            output_contracts=tuple(output_contracts),
            budget=next_budget(resolved, instruction_budget),
            reasons=tuple(reasons),
            diagnostics=tuple(diagnostics),
        )

    def diagnose(self) -> dict[str, Any]:
        records, initial_diagnostics = self.discover()
        diagnostics = list(initial_diagnostics)
        for skill_id, record in records.items():
            self._expand_dependencies([skill_id], records, diagnostics)
            self._filter_conflicts([skill_id], records, diagnostics)
        return {
            "skills": len(records),
            "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
        }

    def _configured_skills(self, agent: str) -> tuple[str, ...]:
        config = self.project_root / ".openwrite" / "runtime.yaml"
        if not config.exists():
            return ()
        try:
            raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return ()
        if not isinstance(raw, dict):
            return ()
        active = raw.get("skills", raw.get("active_skills", ()))
        bindings = raw.get("agent_skills", {})
        if isinstance(bindings, dict) and isinstance(bindings.get(agent), list):
            active = bindings[agent]
        return _as_str_tuple(active)

    @staticmethod
    def _expand_dependencies(
        selected: list[str],
        records: dict[str, _SkillRecord],
        diagnostics: list[SkillDiagnosticV1],
    ) -> list[str]:
        result: list[str] = []
        visiting: set[str] = set()

        def visit(skill_id: str) -> None:
            if skill_id in result:
                return
            if skill_id in visiting:
                diagnostics.append(
                    SkillDiagnosticV1(
                        code="dependency_cycle",
                        skill_id=skill_id,
                        message=f"Skill 依赖存在环: {skill_id}",
                    )
                )
                return
            record = records.get(skill_id)
            if record is None:
                diagnostics.append(
                    SkillDiagnosticV1(
                        code="missing_dependency",
                        skill_id=skill_id,
                        message=f"Skill 依赖不存在: {skill_id}",
                    )
                )
                return
            visiting.add(skill_id)
            for dependency in record.manifest.requires:
                if dependency not in records:
                    diagnostics.append(
                        SkillDiagnosticV1(
                            code="missing_dependency",
                            skill_id=skill_id,
                            message=f"缺少依赖 {dependency}",
                            source=record.source,
                        )
                    )
                    continue
                visit(dependency)
            visiting.remove(skill_id)
            result.append(skill_id)

        for item in selected:
            visit(item)
        return result

    @staticmethod
    def _filter_conflicts(
        selected: list[str],
        records: dict[str, _SkillRecord],
        diagnostics: list[SkillDiagnosticV1],
    ) -> list[str]:
        accepted: list[str] = []
        for skill_id in selected:
            record = records.get(skill_id)
            if record is None:
                continue
            conflicts = set(record.manifest.conflicts_with)
            conflicts.update(
                other
                for other in accepted
                if skill_id in records[other].manifest.conflicts_with
            )
            if conflicts.intersection(accepted):
                diagnostics.append(
                    SkillDiagnosticV1(
                        code="conflict",
                        skill_id=skill_id,
                        message=(
                            "Skill 与已选 Skill 冲突: "
                            + ", ".join(sorted(conflicts.intersection(accepted)))
                        ),
                        source=record.source,
                    )
                )
                continue
            accepted.append(skill_id)
        return accepted

    @staticmethod
    def _read_resource(record: _SkillRecord, relative: str, limit: int) -> str:
        if not _safe_relative(relative):
            return ""
        path = (record.root / relative).resolve()
        try:
            path.relative_to(record.root)
            return path.read_text(encoding="utf-8")[: max(0, limit)]
        except (OSError, ValueError, UnicodeError):
            return ""


def next_budget(skills: list[ResolvedSkillV1], total_instruction_budget: int) -> SkillBudgetV1:
    if not skills:
        return SkillBudgetV1(max_instruction_chars=total_instruction_budget)
    return SkillBudgetV1(
        max_instruction_chars=min(
            total_instruction_budget,
            sum(item.budget.max_instruction_chars for item in skills),
        ),
        max_reference_chars=sum(item.budget.max_reference_chars for item in skills),
        max_tool_calls=min(item.budget.max_tool_calls for item in skills),
    )


def resolve_runtime(
    project_root: Path,
    *,
    agent: str,
    base_tools: Iterable[str],
    task: str = "",
    intent: str = "",
    document_type: str = "",
    explicit_skills: Iterable[str] | None = None,
) -> SkillResolutionV1:
    return RuntimeSkillResolver(project_root).resolve(
        agent=agent,
        base_tools=base_tools,
        task=task,
        intent=intent,
        document_type=document_type,
        explicit_skills=explicit_skills,
    )


def render_runtime_context(
    resolution: SkillResolutionV1,
    rules: CompiledRulesV1 | None = None,
) -> str:
    lines = [
        "Runtime Skill resolution:",
        f"- id: {resolution.resolution_id}",
        "- skills: "
        + (", ".join(f"{item.id}@{item.version}" for item in resolution.skills) or "none"),
        f"- tool budget: {resolution.budget.max_tool_calls}",
    ]
    if resolution.instructions:
        lines.extend(("", "Runtime instructions:", resolution.instructions))
    if rules is not None:
        lines.extend(("", f"Confirmed project rules revision: {rules.revision}"))
        if rules.mechanical_constraints:
            lines.append("Mechanical constraints:")
            lines.extend(f"- {item.text}" for item in rules.mechanical_constraints)
        if rules.semantic_preferences:
            lines.append("Semantic preferences:")
            lines.extend(f"- {item.text}" for item in rules.semantic_preferences)
    return "\n".join(lines)


class RuleCompiler:
    """Compile project Markdown rules into a reviewable, atomic artifact."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / ".openwrite"
        self.rules_root = self.root / "rules"
        self.compiled_path = self.root / "compiled-rules.json"
        self.preview_root = self.root / "rule-previews"

    def compile(self) -> CompiledRulesV1:
        mechanical: list[RuleEntryV1] = []
        semantic: list[RuleEntryV1] = []
        source_paths: list[str] = []
        for path in sorted(self.rules_root.glob("*.md")):
            source_paths.append(str(path.relative_to(self.project_root)))
            section = "semantic"
            for line_number, raw_line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    heading = line.lstrip("#").strip().lower()
                    if any(token in heading for token in ("mechanical", "机械", "constraint")):
                        section = "mechanical"
                    elif any(token in heading for token in ("semantic", "语义", "preference")):
                        section = "semantic"
                    continue
                if line.startswith("-") or line.startswith("*"):
                    line = line[1:].strip()
                if line.lower().startswith("@mechanical:"):
                    section, line = "mechanical", line.split(":", 1)[1].strip()
                elif line.lower().startswith("@semantic:"):
                    section, line = "semantic", line.split(":", 1)[1].strip()
                if not line:
                    continue
                entry = RuleEntryV1(
                    text=line[:2000],
                    source=str(path.relative_to(self.project_root)),
                    line=line_number,
                )
                (mechanical if section == "mechanical" else semantic).append(entry)
        revision = _stable_id(
            *[
                f"{entry.source}:{entry.line}:{entry.text}"
                for entry in [*mechanical, *semantic]
            ]
        )
        return CompiledRulesV1(
            revision=revision,
            mechanical_constraints=tuple(mechanical),
            semantic_preferences=tuple(semantic),
            source_paths=tuple(source_paths),
        )

    def preview(self) -> RulePreviewV1:
        compiled = self.compile()
        current = self._load_compiled()
        before = json.dumps(
            current.model_dump(mode="json") if current else {},
            ensure_ascii=False,
            indent=2,
        ).splitlines()
        after = json.dumps(
            compiled.model_dump(mode="json"), ensure_ascii=False, indent=2
        ).splitlines()
        diff = "\n".join(
            difflib.unified_diff(
                before,
                after,
                fromfile="compiled-rules",
                tofile="candidate-rules",
                lineterm="",
            )
        )
        preview_id = _stable_id(compiled.revision, current.revision if current else "")
        preview = RulePreviewV1(
            preview_id=preview_id,
            base_revision=current.revision if current else "",
            compiled=compiled,
            unified_diff=diff,
        )
        self.preview_root.mkdir(parents=True, exist_ok=True)
        self._atomic_json(self.preview_root / f"{preview_id}.json", preview.model_dump(mode="json"))
        return preview

    def apply(self, preview_id: str, *, confirm: bool = False) -> CompiledRulesV1:
        if not confirm:
            raise RuntimeSkillError("规则应用需要显式确认", code="CONFIRMATION_REQUIRED")
        preview_path = self.preview_root / f"{preview_id}.json"
        if not preview_path.exists():
            raise RuntimeSkillError("规则预览不存在或已过期", code="PREVIEW_NOT_FOUND")
        preview = RulePreviewV1.model_validate(json.loads(preview_path.read_text(encoding="utf-8")))
        current = self._load_compiled()
        if (current.revision if current else "") != preview.base_revision:
            raise RuntimeSkillError("规则基线已变化，请重新预览", code="STALE_PREVIEW")
        latest = self.compile()
        if latest.revision != preview.compiled.revision:
            raise RuntimeSkillError("规则源文件已变化，请重新预览", code="STALE_PREVIEW")
        self._atomic_json(self.compiled_path, latest.model_dump(mode="json"))
        preview_path.unlink(missing_ok=True)
        return latest

    def active(self) -> CompiledRulesV1 | None:
        return self._load_compiled()

    def _load_compiled(self) -> CompiledRulesV1 | None:
        if not self.compiled_path.exists():
            return None
        try:
            return cast(
                CompiledRulesV1,
                CompiledRulesV1.model_validate(
                    json.loads(self.compiled_path.read_text(encoding="utf-8"))
                ),
            )
        except (OSError, ValueError, ValidationError):
            return None

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
