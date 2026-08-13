"""
码搭 CodePilot · RAG 代码索引器

遍历项目代码文件，生成向量存入 ChromaDB。
支持增量索引（按文件 mtime 跳过未修改的）。
"""

import os
import ast
import json
import time
from pathlib import Path

from sentence_transformers import SentenceTransformer
import chromadb

# ── 配置 ───────────────────────────────────────────────────────

CHROMA_DIR = ".codepilot/chroma"
STATE_FILE = ".codepilot/index_state.json"
COLLECTION_NAME = "code_snippets"
INDEX_SCHEMA_VERSION = 2
_chunk_lines = None


def _get_chunk_lines() -> int:
    global _chunk_lines
    if _chunk_lines is None:
        from config import config
        _chunk_lines = config.get("rag.chunk_lines", 30)
    return _chunk_lines
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".idea",
    ".vscode", ".codepilot", ".rag-eval", ".pytest_cache", ".agents",
    ".codex", "agent面试",
}

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".html", ".css", ".vue", ".yaml", ".yml", ".json", ".md", ".txt",
    ".sh", ".sql", ".xml", ".toml", ".cfg", ".ini", ".cpp", ".c", ".h",
}
DOC_EXTENSIONS = {".md", ".txt"}

_embedding_model = None


def _has_skipped_part(parts: set[str]) -> bool:
    """排除依赖、缓存和评测资料；同时覆盖 venv.broken-* 等环境目录。"""
    return bool(parts & SKIP_DIRS) or any(
        part.startswith(("venv.", ".venv.")) for part in parts
    )


def _get_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        from config import config
        model_name = config.get("rag.model_name", "all-MiniLM-L6-v2")
        _embedding_model = SentenceTransformer(
            model_name,
            local_files_only=bool(config.get("rag.local_files_only", True)),
        )
    return _embedding_model


# ── Python 函数/类切分（AST） ─────────────────────────────────

def _split_python(filepath: Path, content: str, rel_path: str) -> list[dict]:
    """用 AST 按函数和类边界切分 Python 文件。

    只取顶层定义（tree.body）：ast.walk 会把类的方法和类本身各切一份，
    导致同一段代码被重复索引。
    """
    chunks = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return _split_fixed(filepath, content, rel_path)

    lines = content.split("\n")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno - 1
            end = node.end_lineno if hasattr(node, "end_lineno") else start + _get_chunk_lines()
            text = "\n".join(lines[start:end])
            if len(text.strip()) < 10:
                continue
            chunks.append({
                "file": rel_path,
                "start_line": start + 1,
                "end_line": end,
                "content": text,
            })

    # 没有函数/类定义的直接按固定行分
    if not chunks:
        return _split_fixed(filepath, content, rel_path)

    return chunks


def _split_fixed(filepath: Path, content: str, rel_path: str) -> list[dict]:
    """按固定行数切分文件。"""
    chunks = []
    lines = content.split("\n")
    for i in range(0, len(lines), _get_chunk_lines()):
        text = "\n".join(lines[i:i + _get_chunk_lines()])
        if not text.strip():
            continue
        chunks.append({
            "file": rel_path,
            "start_line": i + 1,
            "end_line": min(i + _get_chunk_lines(), len(lines)),
            "content": text,
        })
    return chunks


# ── 主索引逻辑 ─────────────────────────────────────────────────

def index_project(project_dir: str, force: bool = False) -> str:
    """索引整个项目的代码文件到 ChromaDB。

    参数:
        project_dir: 项目根目录
        force: True 强制重建索引，False 增量（跳过未修改文件）

    返回: 索引结果摘要字符串。
    """
    root = Path(project_dir).resolve()
    if not root.exists():
        return f"[错误] 目录不存在: {project_dir}"

    chroma_path = str(root / CHROMA_DIR)
    state_path = root / STATE_FILE

    # 加载索引状态
    index_state = {}
    schema_is_current = False
    if not force and state_path.exists():
        try:
            stored_state = json.loads(state_path.read_text(encoding="utf-8"))
            if (
                isinstance(stored_state, dict)
                and stored_state.get("version") == INDEX_SCHEMA_VERSION
                and isinstance(stored_state.get("files"), dict)
            ):
                index_state = stored_state["files"]
                schema_is_current = True
        except (json.JSONDecodeError, OSError):
            pass

    # v2 为每个 chunk 增加 content_type。旧状态无法证明已有 Chroma 记录包含
    # 该字段，因此升级后自动完整重建一次，避免默认 code 过滤返回空结果。
    rebuild_for_schema = not force and state_path.exists() and not schema_is_current
    effective_force = force or rebuild_for_schema
    if effective_force:
        index_state = {}

    # 收集文件（seen_files 记录全量代码文件，用于识别已删除的文件）
    files_to_index = []
    seen_files = set()
    for filepath in root.rglob("*"):
        parts = set(filepath.relative_to(root).parts)
        if _has_skipped_part(parts):
            continue
        if not filepath.is_file():
            continue
        if filepath.suffix not in CODE_EXTENSIONS:
            continue

        # Chunk ID 和 metadata 使用跨平台稳定的 POSIX 相对路径，避免 Windows
        # 反斜杠导致评测标签、BM25 文件名检索和跨机器索引结果不一致。
        rel = filepath.relative_to(root).as_posix()
        seen_files.add(rel)
        mtime = filepath.stat().st_mtime

        if not effective_force and index_state.get(rel) == mtime:
            continue

        files_to_index.append((filepath, rel))
        index_state[rel] = mtime

    # 已从磁盘删除的文件：清理其在向量库中的残留片段，避免搜出幽灵代码
    removed_files = [rel for rel in index_state if rel not in seen_files]

    if not files_to_index and not removed_files:
        return "[完成] 索引已是最新，没有需要更新的文件"

    # 先确保模型可用，再删除或重建任何持久数据。否则离线加载失败会留下
    # 缺少已修改文件 chunk 的半更新索引。
    model = _get_model() if files_to_index else None

    # 初始化 ChromaDB
    client = chromadb.PersistentClient(path=chroma_path)
    try:
        client.delete_collection(COLLECTION_NAME) if effective_force else None
    except Exception:
        pass

    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        collection = client.create_collection(COLLECTION_NAME)

    for rel in removed_files:
        try:
            collection.delete(where={"file": rel})
        except Exception:
            pass
        del index_state[rel]

    # 修改后的文件可能产生不同的 chunk ID。写入新片段前先清理该文件的旧片段，
    # 避免函数删除、移动或行号变化后留下无法被 upsert 覆盖的幽灵结果。
    if not effective_force:
        for _, rel in files_to_index:
            try:
                collection.delete(where={"file": rel})
            except Exception:
                pass

    # 按文件切分、向量化、批量写入
    batch_size = 50
    ids_batch, docs_batch, metas_batch = [], [], []
    total_chunks = 0
    start_time = time.time()

    for i, (filepath, rel) in enumerate(files_to_index):
        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if filepath.suffix == ".py":
            chunks = _split_python(filepath, content, rel)
        else:
            chunks = _split_fixed(filepath, content, rel)

        for chunk in chunks:
            uid = f"{rel}:{chunk['start_line']}-{chunk['end_line']}"
            ids_batch.append(uid)
            docs_batch.append(chunk["content"])
            metas_batch.append({
                "file": chunk["file"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "content_type": (
                    "document" if filepath.suffix in DOC_EXTENSIONS else "code"
                ),
            })
            total_chunks += 1

        # 批量写入
        if len(ids_batch) >= batch_size:
            embeddings = model.encode(docs_batch, show_progress_bar=False).tolist()
            _upsert_batch(collection, ids_batch, docs_batch, metas_batch, embeddings)
            ids_batch, docs_batch, metas_batch = [], [], []

    # 写入剩余
    if ids_batch:
        embeddings = model.encode(docs_batch, show_progress_bar=False).tolist()
        _upsert_batch(collection, ids_batch, docs_batch, metas_batch, embeddings)

    # 保存索引状态
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {"version": INDEX_SCHEMA_VERSION, "files": index_state},
            indent=2,
        ),
        encoding="utf-8",
    )

    elapsed = time.time() - start_time
    summary = f"[完成] 索引 {len(files_to_index)} 个文件，{total_chunks} 个片段，耗时 {elapsed:.1f}s"
    if removed_files:
        summary += f"，清理 {len(removed_files)} 个已删除文件的残留片段"
    return summary


def _upsert_batch(collection, ids, docs, metas, embeddings):
    """安全 upsert，跳过已存在的 id。"""
    try:
        collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
    except Exception:
        # 逐条覆盖
        for uid, doc, meta, emb in zip(ids, docs, metas, embeddings):
            try:
                collection.upsert(ids=[uid], documents=[doc], metadatas=[meta], embeddings=[emb])
            except Exception:
                pass


def _get_collection(project_dir: str):
    """获取 ChromaDB collection（只读）。"""
    root = Path(project_dir).resolve()
    chroma_path = str(root / CHROMA_DIR)
    if not os.path.exists(chroma_path):
        return None
    client = chromadb.PersistentClient(path=chroma_path)
    try:
        return client.get_collection(COLLECTION_NAME)
    except Exception:
        return None
