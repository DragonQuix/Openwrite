"""Strict contracts for evidence-backed source analysis."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    chunk_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=320)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_range(self) -> EvidenceRef:
        if self.end <= self.start:
            raise ValueError("evidence end must be greater than start")
        if self.line_end < self.line_start:
            raise ValueError("evidence line_end must be >= line_start")
        return self


class SourceFindingV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    category: Literal[
        "promise",
        "structure",
        "character",
        "conflict",
        "hook",
        "pacing",
        "voice",
        "reader_drive",
        "method",
        "risk",
    ]
    claim: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0, le=1)
    reusable: bool
    source_bound: bool
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=8)


class SourceChunkRecordV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    char_count: int = Field(gt=0)
    estimated_tokens: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_hint: str = ""
    status: Literal["pending", "running", "completed", "failed", "stale"]
    report_ref: str = ""
    error_code: str = ""
    error_message: str = ""

    @model_validator(mode="after")
    def validate_chunk_range(self) -> SourceChunkRecordV2:
        if self.end - self.start != self.char_count:
            raise ValueError("chunk range must match char_count")
        return self


class SourceManifestV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    source_id: str
    relative_name: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_ref: str
    prompt_version: str
    focus: list[str]
    change_status: Literal["added", "modified", "unchanged", "deleted"]
    total_chars: int = Field(ge=0)
    input_budget_tokens: int = Field(gt=0)
    created_at: str
    updated_at: str
    status: Literal["pending", "running", "completed", "failed", "stale"]
    chunks: list[SourceChunkRecordV2]

    @model_validator(mode="after")
    def validate_coverage(self) -> SourceManifestV2:
        cursor = 0
        for index, chunk in enumerate(self.chunks):
            if chunk.index != index or chunk.start != cursor:
                raise ValueError("chunks must be ordered and contiguous")
            cursor = chunk.end
        if cursor != self.total_chars:
            raise ValueError("chunks must cover the complete source")
        return self


class SourceChunkReportV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    source_id: str
    chunk_id: str
    chunk_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str
    model: str = ""
    summary: str = Field(min_length=1, max_length=1200)
    findings: list[SourceFindingV2]


class SourceReportV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    source_id: str
    relative_name: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str
    models: list[str] = Field(default_factory=list)
    status: Literal["completed", "incomplete"]
    summary: str
    findings: list[SourceFindingV2]
    failed_chunks: list[str]
    source_bound_terms: list[str]
    generated_at: str


class ProfileItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    source_ids: list[str] = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(min_length=1)


class ReferenceProfileV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    profile_id: str
    source_ids: list[str] = Field(min_length=1)
    source_revisions: dict[str, str]
    common_methods: list[ProfileItemV1]
    differences: list[ProfileItemV1]
    optional_variants: list[ProfileItemV1]
    conflicts: list[str]
    excluded_items: list[str]
    generated_at: str


class PromotionPreviewV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    preview_id: str
    profile_id: str
    target: Literal["style", "rules", "inspiration", "setting_candidates"]
    target_ref: str
    baseline_revision: str
    proposed_content: str
    unified_diff: str
    included_claims: list[str]
    excluded_claims: list[str]
    created_at: str
