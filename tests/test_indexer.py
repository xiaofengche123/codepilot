import os
import json
from pathlib import Path

import pytest

from rag import indexer


class FakeModel:
    def encode(self, documents, show_progress_bar=False):
        return FakeEmbeddings([[float(len(document)), 1.0] for document in documents])


class FakeEmbeddings(list):
    def tolist(self):
        return list(self)


class FakeCollection:
    def __init__(self):
        self.records = {}
        self.deleted_files = []

    def add(self, ids, documents, metadatas, embeddings):
        for uid, document, metadata, embedding in zip(
            ids, documents, metadatas, embeddings
        ):
            self.records[uid] = (document, metadata, embedding)

    def upsert(self, ids, documents, metadatas, embeddings):
        self.add(ids, documents, metadatas, embeddings)

    def delete(self, where):
        file_name = where["file"]
        self.deleted_files.append(file_name)
        self.records = {
            uid: value
            for uid, value in self.records.items()
            if value[1]["file"] != file_name
        }


class FakeClient:
    def __init__(self):
        self.collection = FakeCollection()

    def get_collection(self, name):
        return self.collection

    def create_collection(self, name):
        return self.collection

    def delete_collection(self, name):
        self.collection = FakeCollection()


def test_python_ast_splits_top_level_function_and_class(tmp_path):
    content = """
def first():
    return 1

class Service:
    def run(self):
        return 2
""".strip()
    chunks = indexer._split_python(tmp_path / "sample.py", content, "sample.py")
    assert len(chunks) == 2
    assert chunks[0]["content"].startswith("def first")
    assert chunks[1]["content"].startswith("class Service")


def test_skip_path_covers_eval_and_broken_virtualenv():
    assert indexer._has_skipped_part({".rag-eval", "answers.json"})
    assert indexer._has_skipped_part({"venv.broken-3.12.2", "package.py"})
    assert indexer._has_skipped_part({"agent面试", "notes.md"})
    assert not indexer._has_skipped_part({"rag", "retriever.py"})


def test_embedding_model_load_is_local_only(monkeypatch):
    captured = {}

    def fake_sentence_transformer(model_name, **kwargs):
        captured.update(model_name=model_name, **kwargs)
        return object()

    monkeypatch.setattr(indexer, "SentenceTransformer", fake_sentence_transformer)
    monkeypatch.setattr(indexer, "_embedding_model", None)
    indexer._get_model()
    assert captured["local_files_only"] is True


def test_incremental_index_skips_unchanged_and_cleans_deleted_file(
    tmp_path, monkeypatch
):
    source = tmp_path / "sample.py"
    source.write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    eval_dir = tmp_path / ".rag-eval"
    eval_dir.mkdir()
    (eval_dir / "answers.json").write_text(
        '[{"query": "hello", "relevant": ["sample.py:1-2"]}]',
        encoding="utf-8",
    )
    fake_client = FakeClient()
    monkeypatch.setattr(indexer.chromadb, "PersistentClient", lambda path: fake_client)
    monkeypatch.setattr(indexer, "_get_model", lambda: FakeModel())

    first = indexer.index_project(str(tmp_path))
    assert "索引 1 个文件" in first
    assert fake_client.collection.records
    assert all(
        value[1]["file"] == "sample.py"
        for value in fake_client.collection.records.values()
    )
    assert all(
        value[1]["content_type"] == "code"
        for value in fake_client.collection.records.values()
    )

    second = indexer.index_project(str(tmp_path))
    assert "索引已是最新" in second

    old_mtime = source.stat().st_mtime
    source.write_text("def hello():\n    return 'changed'\n", encoding="utf-8")
    os.utime(source, (old_mtime + 2, old_mtime + 2))
    changed = indexer.index_project(str(tmp_path))
    assert "索引 1 个文件" in changed
    assert "sample.py" in fake_client.collection.deleted_files

    source.unlink()
    removed = indexer.index_project(str(tmp_path))
    assert "清理 1 个已删除文件" in removed
    assert fake_client.collection.records == {}


def test_legacy_index_state_triggers_content_type_rebuild(tmp_path, monkeypatch):
    source = tmp_path / "pkg" / "sample.py"
    source.parent.mkdir()
    source.write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    state_path = tmp_path / indexer.STATE_FILE
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps({"pkg/sample.py": source.stat().st_mtime}),
        encoding="utf-8",
    )
    fake_client = FakeClient()
    fake_client.collection.records["legacy"] = (
        "old",
        {"file": "pkg/sample.py"},
        [0.0, 0.0],
    )
    monkeypatch.setattr(indexer.chromadb, "PersistentClient", lambda path: fake_client)
    monkeypatch.setattr(indexer, "_get_model", lambda: FakeModel())

    result = indexer.index_project(str(tmp_path))

    assert "索引 1 个文件" in result
    assert "legacy" not in fake_client.collection.records
    assert all(
        metadata["file"] == "pkg/sample.py"
        and metadata["content_type"] == "code"
        for _, metadata, _ in fake_client.collection.records.values()
    )
    stored = json.loads(state_path.read_text(encoding="utf-8"))
    assert stored["version"] == indexer.INDEX_SCHEMA_VERSION
    assert "pkg/sample.py" in stored["files"]


def test_model_load_failure_does_not_delete_existing_chunks(tmp_path, monkeypatch):
    source = tmp_path / "sample.py"
    source.write_text("def changed():\n    return True\n", encoding="utf-8")
    state_path = tmp_path / indexer.STATE_FILE
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps(
            {
                "version": indexer.INDEX_SCHEMA_VERSION,
                "files": {"sample.py": source.stat().st_mtime - 10},
            }
        ),
        encoding="utf-8",
    )
    fake_client = FakeClient()
    fake_client.collection.records["sample.py:1-2"] = (
        "old",
        {"file": "sample.py", "content_type": "code"},
        [0.0, 0.0],
    )
    monkeypatch.setattr(indexer.chromadb, "PersistentClient", lambda path: fake_client)
    monkeypatch.setattr(
        indexer,
        "_get_model",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    with pytest.raises(RuntimeError, match="offline"):
        indexer.index_project(str(tmp_path))

    assert "sample.py:1-2" in fake_client.collection.records
    assert fake_client.collection.deleted_files == []
