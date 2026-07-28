import os
from pathlib import Path

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


def test_incremental_index_skips_unchanged_and_cleans_deleted_file(
    tmp_path, monkeypatch
):
    source = tmp_path / "sample.py"
    source.write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    fake_client = FakeClient()
    monkeypatch.setattr(indexer.chromadb, "PersistentClient", lambda path: fake_client)
    monkeypatch.setattr(indexer, "_get_model", lambda: FakeModel())

    first = indexer.index_project(str(tmp_path))
    assert "索引 1 个文件" in first
    assert fake_client.collection.records

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
