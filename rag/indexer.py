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
CHUNK_LINES = 30  # 非 Python 文件的固定切分行数
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".codepilot"}

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".html", ".css", ".vue", ".yaml", ".yml", ".json", ".md", ".txt",
    ".sh", ".sql", ".xml", ".toml", ".cfg", ".ini", ".cpp", ".c", ".h",
}

_embedding_model = None


def _get_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedding_model


# ── Python 函数/类切分（AST） ─────────────────────────────────

def _split_python(filepath: Path, content: str, rel_path: str) -> list[dict]:
    """用 AST 按函数和类边界切分 Python 文件。"""
    chunks = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return _split_fixed(filepath, content, rel_path)

    lines = content.split("\n")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno - 1
            end = node.end_lineno if hasattr(node, "end_lineno") else start + CHUNK_LINES
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
    for i in range(0, len(lines), CHUNK_LINES):
        text = "\n".join(lines[i:i + CHUNK_LINES])
        if not text.strip():
            continue
        chunks.append({
            "file": rel_path,
            "start_line": i + 1,
            "end_line": min(i + CHUNK_LINES, len(lines)),
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
    if not force and state_path.exists():
        try:
            index_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # 收集文件
    files_to_index = []
    for filepath in root.rglob("*"):
        parts = set(filepath.relative_to(root).parts)
        if parts & SKIP_DIRS:
            continue
        if not filepath.is_file():
            continue
        if filepath.suffix not in CODE_EXTENSIONS:
            continue

        rel = str(filepath.relative_to(root))
        mtime = filepath.stat().st_mtime

        if not force and index_state.get(rel) == mtime:
            continue

        files_to_index.append((filepath, rel))
        index_state[rel] = mtime

    if not files_to_index:
        return "[完成] 索引已是最新，没有需要更新的文件"

    # 初始化 ChromaDB
    client = chromadb.PersistentClient(path=chroma_path)
    try:
        client.delete_collection(COLLECTION_NAME) if force else None
    except Exception:
        pass

    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        collection = client.create_collection(COLLECTION_NAME)

    model = _get_model()

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
    state_path.write_text(json.dumps(index_state, indent=2), encoding="utf-8")

    elapsed = time.time() - start_time
    return f"[完成] 索引 {len(files_to_index)} 个文件，{total_chunks} 个片段，耗时 {elapsed:.1f}s"


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
