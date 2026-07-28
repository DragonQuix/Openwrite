"""Incremental local full-text and structural search for a novel project."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable


MAX_INDEXED_BYTES = 2 * 1024 * 1024
MAX_QUERY_CHARS = 200


@dataclass(frozen=True)
class SearchResult:
    path: str
    title: str
    line: int
    heading: str
    snippet: str
    scope: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "line": self.line,
            "heading": self.heading,
            "snippet": self.snippet,
            "scope": self.scope,
            "score": self.score,
        }


class ProjectSearchIndex:
    """SQLite-backed index with deterministic snippets and line provenance."""

    def __init__(self, novel_root: Path) -> None:
        self.novel_root = Path(novel_root).resolve()
        self.index_path = self.novel_root / ".openwrite" / "search.sqlite3"

    def search(
        self,
        query: str,
        *,
        scope: str = "all",
        limit: int = 20,
    ) -> dict[str, Any]:
        clean_query = " ".join(str(query or "").strip().split())[:MAX_QUERY_CHARS]
        if not clean_query:
            return {"query": "", "scope": scope, "results": [], "indexed": 0}
        if scope not in {"all", "outline", "story", "characters", "world", "chapters"}:
            raise ValueError("搜索范围无效")
        limit = min(50, max(1, int(limit)))
        self.refresh()
        terms = [item.casefold() for item in clean_query.split() if item]
        results: list[SearchResult] = []
        with self._connect() as database:
            query_sql = "SELECT path, title, body, scope FROM documents"
            params: list[Any] = []
            if scope != "all":
                query_sql += " WHERE scope = ?"
                params.append(scope)
            for path, title, body, item_scope in database.execute(query_sql, params):
                match = self._match_document(
                    path=str(path),
                    title=str(title),
                    body=str(body),
                    scope=str(item_scope),
                    terms=terms,
                )
                if match is not None:
                    results.append(match)
        results.sort(key=lambda item: (-item.score, item.path, item.line))
        with self._connect() as database:
            indexed = int(database.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        return {
            "query": clean_query,
            "scope": scope,
            "results": [item.to_dict() for item in results[:limit]],
            "indexed": indexed,
        }

    def refresh(self) -> int:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        candidates = {self._relative(path): path for path in self._iter_documents()}
        with self._connect() as database:
            self._create_schema(database)
            known = {
                str(row[0]): str(row[1])
                for row in database.execute("SELECT path, revision FROM documents")
            }
            for relative, path in candidates.items():
                revision = self._revision(path)
                if known.get(relative) == revision:
                    continue
                body = path.read_text(encoding="utf-8")
                database.execute(
                    """
                    INSERT INTO documents(path, title, body, scope, revision)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                      title=excluded.title,
                      body=excluded.body,
                      scope=excluded.scope,
                      revision=excluded.revision
                    """,
                    (relative, self._title(path, body), body, self._scope(relative), revision),
                )
            stale = set(known) - set(candidates)
            database.executemany("DELETE FROM documents WHERE path = ?", [(item,) for item in stale])
            database.commit()
        return len(candidates)

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.index_path, timeout=5)
        database.execute("PRAGMA journal_mode=WAL")
        return database

    @staticmethod
    def _create_schema(database: sqlite3.Connection) -> None:
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS documents(
              path TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              body TEXT NOT NULL,
              scope TEXT NOT NULL,
              revision TEXT NOT NULL
            )
            """
        )

    def _iter_documents(self) -> Iterable[Path]:
        roots = [self.novel_root / "src", self.novel_root / "data" / "manuscript"]
        for root in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.md")):
                try:
                    if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_INDEXED_BYTES:
                        yield path
                except OSError:
                    continue

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.novel_root).as_posix()

    @staticmethod
    def _revision(path: Path) -> str:
        stat = path.stat()
        seed = f"{stat.st_mtime_ns}:{stat.st_size}".encode("ascii")
        return hashlib.sha256(seed).hexdigest()[:16]

    @staticmethod
    def _title(path: Path, body: str) -> str:
        match = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
        return match.group(1).strip() if match else path.stem.replace("_", " ")

    @staticmethod
    def _scope(relative: str) -> str:
        if relative == "src/outline.md":
            return "outline"
        if relative.startswith("data/manuscript/"):
            return "chapters"
        if relative.startswith("src/characters/"):
            return "characters"
        if relative.startswith("src/world/"):
            return "world"
        return "story"

    @staticmethod
    def _match_document(
        *,
        path: str,
        title: str,
        body: str,
        scope: str,
        terms: list[str],
    ) -> SearchResult | None:
        title_folded = title.casefold()
        lines = body.splitlines()
        heading = ""
        best: tuple[float, int, str, str] | None = None
        for number, line in enumerate(lines, 1):
            heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if heading_match:
                heading = heading_match.group(1).strip()
            haystack = f"{title}\n{heading}\n{line}".casefold()
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
        return SearchResult(path, title, line, matched_heading, snippet, scope, score)
