import asyncio
from pathlib import Path
from types import SimpleNamespace

from tools.project_search import (
    BackendSearchResult,
    IndexedDocument,
    LightRAGConfiguration,
    LightRAGSearchBackend,
    ProjectSearchIndex,
    RetrievedChunk,
    SearchConfigurationError,
)


def test_project_search_maps_lightrag_chunks_back_to_scoped_source_lines(tmp_path: Path):
    novel_root = tmp_path / "novel"
    character = novel_root / "src" / "characters" / "lin_cen.md"
    world = novel_root / "src" / "world" / "clocktower.md"
    character.parent.mkdir(parents=True)
    world.parent.mkdir(parents=True)
    character.write_text(
        "# 林岑\n\n## 未解目标\n\n她在雨夜寻找失踪的旧信。\n",
        encoding="utf-8",
    )
    world.write_text("# 旧钟楼\n\n钟楼会吞掉寄出的信。\n", encoding="utf-8")

    class FakeBackend:
        def search(self, documents, query, *, limit):
            del query, limit
            by_path = {document.path: document for document in documents}
            return BackendSearchResult(
                chunks=[
                    RetrievedChunk(
                        by_path["src/world/clocktower.md"].source_key,
                        "钟楼会吞掉寄出的信。",
                        1,
                    ),
                    RetrievedChunk(
                        by_path["src/characters/lin_cen.md"].source_key,
                        "## 未解目标\n\n她在雨夜寻找失踪的旧信。",
                        2,
                    ),
                ],
                inserted=2,
            )

        def refresh(self, documents):
            return BackendSearchResult(chunks=[], inserted=len(documents))

    payload = ProjectSearchIndex(
        novel_root,
        backend_factory=lambda root: FakeBackend(),
    ).search("她一直在追查什么", scope="characters")

    assert payload["engine"] == "lightrag"
    assert payload["index_updates"]["inserted"] == 2
    assert len(payload["results"]) == 1
    assert payload["results"][0]["path"] == "src/characters/lin_cen.md"
    assert payload["results"][0]["line"] == 5
    assert payload["results"][0]["heading"] == "未解目标"


def test_project_search_uses_explicit_literal_fallback_when_lightrag_is_unconfigured(
    tmp_path: Path,
):
    novel_root = tmp_path / "novel"
    story = novel_root / "src" / "story" / "background.md"
    story.parent.mkdir(parents=True)
    story.write_text("# 背景\n\n钟楼每天少走十三秒。\n", encoding="utf-8")

    def unavailable(root):
        del root
        raise SearchConfigurationError("缺少 embedding")

    payload = ProjectSearchIndex(novel_root, backend_factory=unavailable).search(
        "十三秒",
        scope="story",
    )

    assert payload["engine"] == "literal-fallback"
    assert payload["warning_code"] == "LIGHTRAG_NOT_CONFIGURED"
    assert payload["results"][0]["line"] == 3


def test_lightrag_manifest_tracks_incremental_insert_update_and_delete(tmp_path: Path):
    configuration = LightRAGConfiguration(
        provider="openai",
        llm_model="test-model",
        llm_base_url="https://models.example/v1",
        llm_api_key="llm-secret",
        timeout_seconds=30,
        embedding_model="test-embedding",
        embedding_base_url="https://embeddings.example/v1",
        embedding_api_key="embedding-secret",
        embedding_dimension=8,
        embedding_max_tokens=512,
        query_mode="mix",
    )
    backend = LightRAGSearchBackend(tmp_path, configuration=configuration)

    class FakeRAG:
        def __init__(self):
            self.inserted = []
            self.deleted = []

        async def ainsert(self, body, *, ids, file_paths):
            self.inserted.append((ids, file_paths, body))

        async def adelete_by_doc_id(self, doc_id):
            self.deleted.append(doc_id)
            return SimpleNamespace(status="success")

    first = IndexedDocument(
        path="src/story/background.md",
        title="背景",
        body="第一版背景",
        scope="story",
        revision="rev-1",
        doc_id="doc-1",
        source_key="source-1.md",
    )
    second = IndexedDocument(
        path="src/world/rules.md",
        title="规则",
        body="世界规则",
        scope="world",
        revision="rev-2",
        doc_id="doc-2",
        source_key="source-2.md",
    )
    rag = FakeRAG()

    initial = asyncio.run(backend._sync_documents(rag, [first, second]))
    unchanged = asyncio.run(backend._sync_documents(rag, [first, second]))
    revised = IndexedDocument(
        **{
            **first.__dict__,
            "body": "第二版背景",
            "revision": "rev-3",
            "doc_id": "doc-3",
        }
    )
    changed = asyncio.run(backend._sync_documents(rag, [revised]))

    assert initial == {"inserted": 2, "updated": 0, "deleted": 0}
    assert unchanged == {"inserted": 0, "updated": 0, "deleted": 0}
    assert changed == {"inserted": 0, "updated": 1, "deleted": 1}
    assert rag.deleted == ["doc-2", "doc-1"]
    assert [item[0] for item in rag.inserted] == ["doc-1", "doc-2", "doc-3"]
    manifest = backend._load_manifest()
    assert list(manifest["documents"]) == ["src/story/background.md"]
    assert manifest["documents"]["src/story/background.md"]["doc_id"] == "doc-3"
