"""LightRAG-backed semantic and structural search for a novel project."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, TypeVar

from tools.library_catalog import (
    SEARCH_SCOPES,
    SCOPE_LABELS,
    describe_document,
    normalize_search_scope,
    scope_for_path,
)

MAX_INDEXED_BYTES = 2 * 1024 * 1024
MAX_QUERY_CHARS = 200
LIGHTRAG_MODES = {"local", "global", "hybrid", "naive", "mix"}
MANIFEST_VERSION = 2

logger = logging.getLogger(__name__)
T = TypeVar("T")


class SearchConfigurationError(RuntimeError):
    """Raised when LightRAG cannot be configured from the active model profile."""


class SearchBackendError(RuntimeError):
    """Raised when the LightRAG index cannot be updated or queried."""


class SearchIndexBusyError(SearchBackendError):
    """Raised when another process is currently updating the same LightRAG index."""


@dataclass(frozen=True)
class SearchResult:
    path: str
    title: str
    line: int
    heading: str
    snippet: str
    scope: str
    category: str
    category_label: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "line": self.line,
            "heading": self.heading,
            "snippet": self.snippet,
            "scope": self.scope,
            "scope_label": SCOPE_LABELS.get(self.scope, self.scope),
            "category": self.category,
            "category_label": self.category_label,
            "score": self.score,
        }


@dataclass(frozen=True)
class IndexedDocument:
    path: str
    title: str
    body: str
    scope: str
    category: str
    category_label: str
    revision: str
    doc_id: str
    source_key: str


@dataclass(frozen=True)
class RetrievedChunk:
    source_key: str
    content: str
    rank: int


@dataclass(frozen=True)
class BackendSearchResult:
    chunks: list[RetrievedChunk]
    inserted: int = 0
    updated: int = 0
    deleted: int = 0


class SearchBackend(Protocol):
    def search(
        self,
        documents: list[IndexedDocument],
        query: str,
        *,
        limit: int,
    ) -> BackendSearchResult: ...

    def refresh(self, documents: list[IndexedDocument]) -> BackendSearchResult: ...


@dataclass(frozen=True)
class LightRAGConfiguration:
    provider: str
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    timeout_seconds: int
    embedding_model: str
    embedding_base_url: str
    embedding_api_key: str
    embedding_dimension: int
    embedding_max_tokens: int
    query_mode: str

    @classmethod
    def from_runtime(cls) -> LightRAGConfiguration:
        from tools.llm.client import LLMConfig
        from tools.model_profiles import active_model_profile

        llm = LLMConfig.from_env()
        active = active_model_profile() or {}
        if not llm.api_key.strip():
            raise SearchConfigurationError("LightRAG 需要已配置的模型 API Key")
        if llm.api_format == "responses":
            raise SearchConfigurationError(
                "LightRAG 搜索需要 Chat Completions 兼容的模型路由"
            )

        explicit_embedding_key = os.environ.get(
            "OPENWRITE_LIGHTRAG_EMBEDDING_API_KEY", ""
        ).strip()
        profile_embedding_key = str(active.get("embedding_api_key") or "").strip()
        explicit_embedding_base = os.environ.get(
            "OPENWRITE_LIGHTRAG_EMBEDDING_BASE_URL", ""
        ).strip()
        profile_embedding_base = str(active.get("embedding_base_url") or "").strip()
        embedding_base_url = (
            explicit_embedding_base or profile_embedding_base or llm.base_url
        ).rstrip("/")
        embedding_api_key = explicit_embedding_key or profile_embedding_key or llm.api_key

        if llm.provider == "anthropic" and not (
            (explicit_embedding_key or profile_embedding_key)
            and (explicit_embedding_base or profile_embedding_base)
        ):
            raise SearchConfigurationError(
                "Anthropic 不提供 embedding；请在模型档案中配置独立的 "
                "Embedding Base URL 和 API Key"
            )
        if "deepseek.com" in embedding_base_url.casefold():
            raise SearchConfigurationError(
                "DeepSeek 接口不提供 embedding；请在模型档案中配置独立的 "
                "Embedding Base URL"
            )
        if not embedding_api_key:
            raise SearchConfigurationError("LightRAG 需要可用的 Embedding API Key")

        embedding_model = (
            os.environ.get("OPENWRITE_LIGHTRAG_EMBEDDING_MODEL", "").strip()
            or str(active.get("embedding_model") or "").strip()
            or "text-embedding-3-small"
        )
        embedding_dimension = _bounded_env_int(
            "OPENWRITE_LIGHTRAG_EMBEDDING_DIM",
            active.get("embedding_dimension"),
            default=1536,
            minimum=1,
            maximum=65536,
        )
        embedding_max_tokens = _bounded_env_int(
            "OPENWRITE_LIGHTRAG_EMBEDDING_MAX_TOKENS",
            active.get("embedding_max_tokens"),
            default=8192,
            minimum=256,
            maximum=131072,
        )
        query_mode = os.environ.get("OPENWRITE_LIGHTRAG_MODE", "mix").strip().lower()
        if query_mode not in LIGHTRAG_MODES:
            raise SearchConfigurationError("OPENWRITE_LIGHTRAG_MODE 配置无效")

        return cls(
            provider=llm.provider,
            llm_model=llm.model,
            llm_base_url=llm.base_url.rstrip("/"),
            llm_api_key=llm.api_key,
            timeout_seconds=max(1, int(llm.timeout_seconds)),
            embedding_model=embedding_model,
            embedding_base_url=embedding_base_url,
            embedding_api_key=embedding_api_key,
            embedding_dimension=embedding_dimension,
            embedding_max_tokens=embedding_max_tokens,
            query_mode=query_mode,
        )

    @property
    def workspace(self) -> str:
        payload = {
            "schema": MANIFEST_VERSION,
            "provider": self.provider,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "embedding_model": self.embedding_model,
            "embedding_base_url": self.embedding_base_url,
            "embedding_dimension": self.embedding_dimension,
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        return f"search-{hashlib.sha256(encoded).hexdigest()[:16]}"


class LightRAGSearchBackend:
    """Incremental LightRAG index using the library's local JSON/NanoVectorDB stores."""

    def __init__(
        self,
        novel_root: Path,
        *,
        configuration: LightRAGConfiguration | None = None,
    ) -> None:
        self.novel_root = Path(novel_root).resolve()
        self.configuration = configuration or LightRAGConfiguration.from_runtime()
        self.working_dir = self.novel_root / ".openwrite" / "lightrag"
        self.workspace = self.configuration.workspace
        self.manifest_path = (
            self.working_dir / self.workspace / "openwrite-search-manifest.json"
        )
        self.lock_path = self.working_dir / f"{self.workspace}.lock"

    def search(
        self,
        documents: list[IndexedDocument],
        query: str,
        *,
        limit: int,
    ) -> BackendSearchResult:
        return _run_async(lambda: self._search(documents, query, limit=limit))

    def refresh(self, documents: list[IndexedDocument]) -> BackendSearchResult:
        return _run_async(lambda: self._refresh(documents))

    async def _search(
        self,
        documents: list[IndexedDocument],
        query: str,
        *,
        limit: int,
    ) -> BackendSearchResult:
        with _IndexUpdateLock(self.lock_path):
            rag = self._create_rag()
            initialized = False
            try:
                await rag.initialize_storages()
                initialized = True
                stats = await self._sync_documents(rag, documents)
                from lightrag import QueryParam

                retrieval_limit = min(200, max(40, limit * 8))
                payload = await rag.aquery_data(
                    query,
                    param=QueryParam(
                        mode=self.configuration.query_mode,
                        top_k=min(100, max(20, limit * 4)),
                        chunk_top_k=retrieval_limit,
                        enable_rerank=False,
                    ),
                )
                chunks = self._chunks_from_payload(payload)
                return BackendSearchResult(chunks=chunks, **stats)
            finally:
                if initialized:
                    await rag.finalize_storages()

    async def _refresh(self, documents: list[IndexedDocument]) -> BackendSearchResult:
        with _IndexUpdateLock(self.lock_path):
            rag = self._create_rag()
            initialized = False
            try:
                await rag.initialize_storages()
                initialized = True
                stats = await self._sync_documents(rag, documents)
                return BackendSearchResult(chunks=[], **stats)
            finally:
                if initialized:
                    await rag.finalize_storages()

    def _create_rag(self) -> Any:
        config = self.configuration
        try:
            from lightrag import LightRAG
            from lightrag.llm.openai import openai_complete_if_cache, openai_embed
            from lightrag.utils import EmbeddingFunc
        except ImportError as exc:
            raise SearchConfigurationError(
                "LightRAG 依赖未安装，请重新运行 OpenWrite 启动器安装依赖"
            ) from exc
        anthropic_complete_if_cache = None
        if config.provider == "anthropic":
            try:
                from lightrag.llm.anthropic import anthropic_complete_if_cache
            except ImportError as exc:
                raise SearchConfigurationError(
                    "LightRAG 的 Anthropic 适配器未安装，请重新运行 OpenWrite 启动器"
                ) from exc

        async def llm_model_func(
            prompt: str,
            system_prompt: str | None = None,
            history_messages: list[dict[str, Any]] | None = None,
            **kwargs: Any,
        ) -> Any:
            kwargs.pop("base_url", None)
            kwargs.pop("api_key", None)
            kwargs.setdefault("timeout", config.timeout_seconds)
            history = history_messages or []
            if config.provider == "anthropic":
                if anthropic_complete_if_cache is None:  # pragma: no cover - guarded above
                    raise SearchConfigurationError("Anthropic LightRAG 适配器不可用")
                return await anthropic_complete_if_cache(
                    config.llm_model,
                    prompt,
                    system_prompt=system_prompt,
                    history_messages=history,
                    base_url=config.llm_base_url,
                    api_key=config.llm_api_key,
                    **kwargs,
                )
            return await openai_complete_if_cache(
                config.llm_model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history,
                base_url=config.llm_base_url,
                api_key=config.llm_api_key,
                **kwargs,
            )

        async def embedding_func(texts: list[str], **kwargs: Any) -> Any:
            context = str(kwargs.pop("context", "document"))
            return await openai_embed.func(
                texts,
                model=config.embedding_model,
                base_url=config.embedding_base_url,
                api_key=config.embedding_api_key,
                max_token_size=config.embedding_max_tokens,
                context=context,
            )

        return LightRAG(
            working_dir=str(self.working_dir),
            workspace=self.workspace,
            llm_model_func=llm_model_func,
            llm_model_name=config.llm_model,
            embedding_func=EmbeddingFunc(
                embedding_dim=config.embedding_dimension,
                max_token_size=config.embedding_max_tokens,
                model_name=config.embedding_model,
                supports_asymmetric=True,
                func=embedding_func,
            ),
            tiktoken_model_name="gpt-4o-mini",
            chunk_token_size=900,
            chunk_overlap_token_size=120,
            llm_model_max_async=2,
            embedding_func_max_async=4,
            addon_params={"language": "Chinese"},
        )

    async def _sync_documents(
        self,
        rag: Any,
        documents: list[IndexedDocument],
    ) -> dict[str, int]:
        manifest = self._load_manifest()
        indexed = manifest["documents"]
        current = {document.path: document for document in documents}
        inserted = 0
        updated = 0
        deleted = 0

        for path in sorted(set(indexed) - set(current)):
            await self._delete_document(rag, str(indexed[path].get("doc_id") or ""))
            indexed.pop(path, None)
            deleted += 1
            self._write_manifest(manifest)

        for document in documents:
            previous = indexed.get(document.path)
            if previous and previous.get("revision") == document.revision:
                continue
            if previous:
                await self._delete_document(rag, str(previous.get("doc_id") or ""))
                indexed.pop(document.path, None)
                updated += 1
                self._write_manifest(manifest)
            else:
                inserted += 1

            await rag.ainsert(
                document.body,
                ids=document.doc_id,
                file_paths=document.source_key,
            )
            indexed[document.path] = {
                "revision": document.revision,
                "doc_id": document.doc_id,
                "source_key": document.source_key,
                "scope": document.scope,
                "category": document.category,
                "category_label": document.category_label,
                "title": document.title,
            }
            self._write_manifest(manifest)

        return {"inserted": inserted, "updated": updated, "deleted": deleted}

    async def _delete_document(self, rag: Any, doc_id: str) -> None:
        if not doc_id:
            return
        result = await rag.adelete_by_doc_id(doc_id)
        status = str(getattr(result, "status", "") or "")
        if status not in {"success", "not_found"}:
            raise SearchBackendError("LightRAG 无法删除过期文档索引")

    def _load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.is_file():
            try:
                payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            if (
                isinstance(payload, dict)
                and payload.get("version") == MANIFEST_VERSION
                and isinstance(payload.get("documents"), dict)
            ):
                return payload
        return {
            "version": MANIFEST_VERSION,
            "engine": "HKUDS/LightRAG",
            "workspace": self.workspace,
            "documents": {},
        }

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.manifest_path.parent,
            prefix=".openwrite-search-manifest.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = Path(handle.name)
        temp_path.replace(self.manifest_path)

    @staticmethod
    def _chunks_from_payload(payload: Any) -> list[RetrievedChunk]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        raw_chunks = data.get("chunks")
        if not isinstance(raw_chunks, list):
            return []
        chunks: list[RetrievedChunk] = []
        for rank, item in enumerate(raw_chunks, 1):
            if not isinstance(item, dict):
                continue
            source_key = str(item.get("file_path") or "").strip()
            content = str(item.get("content") or "").strip()
            if source_key and content:
                chunks.append(RetrievedChunk(source_key, content, rank))
        return chunks


class ProjectSearchIndex:
    """Compatibility surface backed by LightRAG with line-level source provenance."""

    def __init__(
        self,
        novel_root: Path,
        *,
        backend_factory: Callable[[Path], SearchBackend] | None = None,
    ) -> None:
        self.novel_root = Path(novel_root).resolve()
        self.index_path = self.novel_root / ".openwrite" / "lightrag"
        self._backend_factory = backend_factory or LightRAGSearchBackend

    def search(
        self,
        query: str,
        *,
        scope: str = "all",
        limit: int = 20,
    ) -> dict[str, Any]:
        normalized_scope = normalize_search_scope(scope)
        clean_query = " ".join(str(query or "").strip().split())[:MAX_QUERY_CHARS]
        if not clean_query:
            return {
                "query": "",
                "scope": normalized_scope,
                "scope_label": SCOPE_LABELS[normalized_scope],
                "results": [],
                "indexed": 0,
                "engine": "none",
                "warning": "",
            }
        limit = min(50, max(1, int(limit)))
        documents = self._documents()
        literal_results = self._literal_results(
            documents,
            clean_query,
            scope=normalized_scope,
        )
        warning = ""
        warning_code = ""

        try:
            backend = self._backend_factory(self.novel_root)
            backend_result = backend.search(documents, clean_query, limit=limit)
        except SearchConfigurationError as exc:
            backend_result = BackendSearchResult(chunks=[])
            warning = f"{exc}；已使用精确文本搜索"
            warning_code = "LIGHTRAG_NOT_CONFIGURED"
        except SearchIndexBusyError:
            backend_result = BackendSearchResult(chunks=[])
            warning = (
                "LightRAG 索引正在由另一任务更新；本次已使用精确文本搜索"
            )
            warning_code = "LIGHTRAG_INDEX_BUSY"
        except Exception:
            logger.warning("LightRAG project search failed", exc_info=True)
            backend_result = BackendSearchResult(chunks=[])
            warning = "LightRAG 索引或检索失败；本次已使用精确文本搜索"
            warning_code = "LIGHTRAG_SEARCH_FAILED"

        semantic_results = self._semantic_results(
            documents,
            backend_result.chunks,
            clean_query,
            scope=normalized_scope,
        )
        results = self._merge_results(semantic_results, literal_results, limit=limit)
        engine = "lightrag" if semantic_results or not warning else "literal-fallback"
        return {
            "query": clean_query,
            "scope": normalized_scope,
            "scope_label": SCOPE_LABELS[normalized_scope],
            "results": [item.to_dict() for item in results],
            "indexed": len(documents),
            "engine": engine,
            "warning": warning,
            "warning_code": warning_code,
            "index_updates": {
                "inserted": backend_result.inserted,
                "updated": backend_result.updated,
                "deleted": backend_result.deleted,
            },
        }

    def refresh(self) -> int:
        documents = self._documents()
        try:
            self._backend_factory(self.novel_root).refresh(documents)
        except SearchConfigurationError:
            pass
        return len(documents)

    def _documents(self) -> list[IndexedDocument]:
        documents = []
        for path in self._iter_documents():
            try:
                body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relative = self._relative(path)
            descriptor = describe_document(relative, body)
            revision = hashlib.sha256(body.encode("utf-8")).hexdigest()
            documents.append(
                IndexedDocument(
                    path=relative,
                    title=self._title(path, body),
                    body=body,
                    scope=descriptor.scope,
                    category=descriptor.category,
                    category_label=descriptor.category_label,
                    revision=revision,
                    doc_id=self._doc_id(relative, revision),
                    source_key=self._source_key(relative),
                )
            )
        return documents

    def _iter_documents(self) -> Iterable[Path]:
        roots = [
            (self.novel_root / "src", {".md", ".yaml", ".yml"}),
            (self.novel_root / "data" / "manuscript", {".md"}),
            (self.novel_root / "data" / "world", {".md"}),
            (
                self.novel_root / "data" / "foreshadowing",
                {".md", ".yaml", ".yml"},
            ),
        ]
        for root, suffixes in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                try:
                    if (
                        path.is_file()
                        and not path.is_symlink()
                        and path.suffix.lower() in suffixes
                        and path.stat().st_size <= MAX_INDEXED_BYTES
                    ):
                        yield path
                except OSError:
                    continue

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.novel_root).as_posix()

    @staticmethod
    def _doc_id(relative: str, revision: str) -> str:
        digest = hashlib.sha256(f"{relative}\0{revision}".encode()).hexdigest()
        return f"doc-{digest}"

    @staticmethod
    def _source_key(relative: str) -> str:
        suffix = Path(relative).suffix.lower() or ".md"
        digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]
        return f"openwrite-{digest}{suffix}"

    @staticmethod
    def _title(path: Path, body: str) -> str:
        match = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
        return match.group(1).strip() if match else path.stem.replace("_", " ")

    @staticmethod
    def _scope(relative: str) -> str:
        return scope_for_path(relative)

    def _semantic_results(
        self,
        documents: list[IndexedDocument],
        chunks: list[RetrievedChunk],
        query: str,
        *,
        scope: str,
    ) -> list[SearchResult]:
        by_source = {document.source_key: document for document in documents}
        query_terms = self._query_terms(query)
        results: list[SearchResult] = []
        seen_paths: set[str] = set()
        for chunk in chunks:
            document = by_source.get(chunk.source_key)
            if document is None or document.path in seen_paths:
                continue
            if scope != "all" and document.scope != scope:
                continue
            line, heading, snippet, literal_hits = self._locate_chunk(
                document.body,
                chunk.content,
                query_terms,
            )
            results.append(
                SearchResult(
                    path=document.path,
                    title=document.title,
                    line=line,
                    heading=heading,
                    snippet=snippet,
                    scope=document.scope,
                    score=1000.0 - chunk.rank + literal_hits,
                )
            )
            seen_paths.add(document.path)
        return results

    def _literal_results(
        self,
        documents: list[IndexedDocument],
        query: str,
        *,
        scope: str,
    ) -> list[SearchResult]:
        terms = self._query_terms(query)
        results = []
        for document in documents:
            if scope != "all" and document.scope != scope:
                continue
            match = self._match_document(document, terms)
            if match is not None:
                results.append(match)
        results.sort(key=lambda item: (-item.score, item.path, item.line))
        return results

    @staticmethod
    def _merge_results(
        semantic: list[SearchResult],
        literal: list[SearchResult],
        *,
        limit: int,
    ) -> list[SearchResult]:
        merged = list(semantic)
        seen = {item.path for item in semantic}
        merged.extend(item for item in literal if item.path not in seen)
        return merged[:limit]

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        return [item.casefold() for item in query.split() if item]

    @classmethod
    def _locate_chunk(
        cls,
        body: str,
        chunk: str,
        terms: list[str],
    ) -> tuple[int, str, str, float]:
        chunk_text = chunk.strip()
        offset = body.find(chunk_text) if chunk_text else -1
        if offset < 0 and chunk_text:
            probe = next(
                (line.strip() for line in chunk_text.splitlines() if len(line.strip()) >= 12),
                "",
            )
            offset = body.find(probe) if probe else -1
        start_line = body[: max(0, offset)].count("\n") + 1 if offset >= 0 else 1
        chunk_lines = chunk_text.splitlines() or [chunk_text]
        best: tuple[float, int, str] | None = None
        for index, line in enumerate(chunk_lines):
            stripped = line.strip()
            if not stripped:
                continue
            folded = stripped.casefold()
            hits = sum(term in folded for term in terms)
            score = float(hits * 3 + (1 if not stripped.startswith("#") else 0))
            candidate = (score, start_line + index, stripped[:280])
            if best is None or candidate[0] > best[0]:
                best = candidate
        if best is None:
            best = (0.0, start_line, chunk_text[:280])
        literal_hits, line, snippet = best
        heading = cls._heading_for_line(body, line)
        return line, heading, snippet, literal_hits

    @staticmethod
    def _heading_for_line(body: str, target_line: int) -> str:
        heading = ""
        for number, line in enumerate(body.splitlines(), 1):
            if number > target_line:
                break
            match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if match:
                heading = match.group(1).strip()
        return heading

    @staticmethod
    def _match_document(
        document: IndexedDocument,
        terms: list[str],
    ) -> SearchResult | None:
        title_folded = document.title.casefold()
        heading = ""
        best: tuple[float, int, str, str] | None = None
        for number, line in enumerate(document.body.splitlines(), 1):
            heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if heading_match:
                heading = heading_match.group(1).strip()
            haystack = f"{document.title}\n{heading}\n{line}".casefold()
            hits = sum(term in haystack for term in terms)
            if not hits:
                continue
            all_terms = hits == len(terms)
            title_hits = sum(term in title_folded for term in terms)
            heading_hits = sum(term in heading.casefold() for term in terms)
            score = hits + title_hits * 3 + heading_hits * 2 + (4 if all_terms else 0)
            snippet = line.strip() or heading
            candidate = (float(score), number, heading, snippet[:280])
            if best is None or candidate[0] > best[0]:
                best = candidate
        if best is None:
            return None
        score, line, matched_heading, snippet = best
        return SearchResult(
            document.path,
            document.title,
            line,
            matched_heading,
            snippet,
            document.scope,
            score,
        )


class _IndexUpdateLock:
    def __init__(self, path: Path, *, stale_after_seconds: int = 6 * 60 * 60) -> None:
        self.path = path
        self.stale_after_seconds = stale_after_seconds
        self.acquired = False

    def __enter__(self) -> _IndexUpdateLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if self._is_stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise SearchIndexBusyError("LightRAG index is busy") from None
            payload = json.dumps({"pid": os.getpid()}).encode("ascii")
            os.write(descriptor, payload)
            os.close(descriptor)
            self.acquired = True
            return self
        raise SearchIndexBusyError("LightRAG index lock could not be recovered")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    def _is_stale(self) -> bool:
        try:
            age = max(0.0, time.time() - self.path.stat().st_mtime)
            if age > self.stale_after_seconds:
                return True
            payload = json.loads(self.path.read_text(encoding="ascii"))
            pid = int(payload.get("pid") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return True
        if pid <= 0:
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False


def _bounded_env_int(
    name: str,
    profile_value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.environ.get(name, "").strip()
    value = raw if raw else profile_value
    try:
        parsed = int(default if value in {None, ""} else value)
    except (TypeError, ValueError) as exc:
        raise SearchConfigurationError(f"{name} 必须是整数") from exc
    if not minimum <= parsed <= maximum:
        raise SearchConfigurationError(f"{name} 必须在 {minimum}-{maximum} 之间")
    return parsed


def _run_async(factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    result: list[T] = []
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(factory()))
        except BaseException as exc:  # pragma: no cover - exercised only inside async hosts
            errors.append(exc)

    thread = threading.Thread(target=runner, name="openwrite-lightrag", daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]
