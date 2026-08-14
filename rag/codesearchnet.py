"""CodeSearchNet 人工标注池的下载、缓存与独立 NDCG 评测。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen

from rag.indexer import _get_model
from rag.reranker import rerank
from rag.retriever import SearchHit, bm25_rank, reciprocal_rank_fusion


ANNOTATIONS_URL = (
    "https://raw.githubusercontent.com/github/CodeSearchNet/"
    "master/resources/annotationStore.csv"
)
_GITHUB_URL = re.compile(
    r"https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+?)#L(\d+)-L(\d+)$"
)


def _download(url: str, *, attempts: int = 3) -> bytes:
    error = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "CodePilot-CodeSearchNet/1"})
            with urlopen(request, timeout=30) as response:
                return response.read()
        except Exception as exc:  # 网络错误类型随 Python/平台变化
            error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"下载失败 {url}: {error}") from error


def download_annotations(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(_download(ANNOTATIONS_URL))
    return target


def _parse_github_url(url: str) -> tuple[str, int, int]:
    matched = _GITHUB_URL.fullmatch(url)
    if not matched:
        raise ValueError(f"无法解析 CodeSearchNet GitHubUrl: {url}")
    owner, repo, commit, path, start, end = matched.groups()
    raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{path}"
    return raw, int(start), int(end)


def _cache_path(cache_dir: Path, raw_url: str) -> Path:
    return cache_dir / f"{hashlib.sha256(raw_url.encode()).hexdigest()}.txt"


def prepare_snippets(
    annotations_path: Path,
    output_path: Path,
    cache_dir: Path,
    *,
    workers: int = 8,
) -> dict:
    """按固定 commit 下载被人工判断过的代码行；原文件按 URL 去重缓存。"""
    rows = list(csv.DictReader(annotations_path.open(encoding="utf-8")))
    parsed = {row["GitHubUrl"]: _parse_github_url(row["GitHubUrl"]) for row in rows}
    raw_urls = sorted({value[0] for value in parsed.values()})
    cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(raw_url: str) -> tuple[str, str | None]:
        path = _cache_path(cache_dir, raw_url)
        if path.exists():
            return raw_url, None
        try:
            path.write_bytes(_download(raw_url))
            return raw_url, None
        except Exception as exc:
            return raw_url, str(exc)

    failures = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(fetch, url) for url in raw_urls]
        for index, future in enumerate(as_completed(futures), 1):
            _, error = future.result()
            if error:
                failures.append(error)
            if index % 250 == 0:
                print(f"已准备 {index}/{len(futures)} 个源码文件")

    snippets = {}
    for row in rows:
        url = row["GitHubUrl"]
        raw_url, start, end = parsed[url]
        path = _cache_path(cache_dir, raw_url)
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        code = "\n".join(lines[start - 1:end])
        if code.strip():
            snippets[url] = {
                "uid": url,
                "language": row["Language"],
                "code": code,
            }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for snippet in sorted(snippets.values(), key=lambda value: value["uid"]):
            handle.write(json.dumps(snippet, ensure_ascii=False) + "\n")
    summary = {
        "annotation_rows": len(rows),
        "queries": len({row["Query"] for row in rows}),
        "unique_urls": len(parsed),
        "prepared_snippets": len(snippets),
        "failed_files": len(failures),
        "sample_failures": failures[:10],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _ndcg(ranked_uids: list[str], qrels: dict[str, int], k: int) -> float:
    gains = [qrels.get(uid, 0) for uid in ranked_uids[:k]]
    dcg = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(gains, 1))
    ideal = sorted(qrels.values(), reverse=True)[:k]
    idcg = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, 1))
    return dcg / idcg if idcg else 0.0


def _vector_rank(
    query: str, documents: list[SearchHit], document_vectors, model, limit: int,
) -> list[SearchHit]:
    query_vector = model.encode(
        [query], show_progress_bar=False, normalize_embeddings=True,
    )[0]
    scores = document_vectors @ query_vector
    ranked = sorted(
        zip(scores, documents), key=lambda value: (-float(value[0]), value[1].uid)
    )[:limit]
    return [
        SearchHit(
            uid=hit.uid, document=hit.document, metadata=hit.metadata,
            score=float(score), vector_rank=rank,
        )
        for rank, (score, hit) in enumerate(ranked, 1)
    ]


def evaluate_judged_pool(
    annotations_path: Path,
    snippets_path: Path,
    *,
    ks: tuple[int, ...] = (10, 100),
    rerank_candidates: int = 30,
) -> dict:
    """在全部 2,874 个被判断 URL 的并集上评测；未判断 query-url 视为 0。"""
    rows = list(csv.DictReader(annotations_path.open(encoding="utf-8")))
    snippets = {
        value["uid"]: value
        for line in snippets_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and (value := json.loads(line))
    }
    documents = [
        SearchHit(
            uid=value["uid"], document=value["code"],
            metadata={"file": value["uid"], "language": value["language"]},
        )
        for value in snippets.values()
    ]
    model = _get_model()
    document_vectors = model.encode(
        [hit.document for hit in documents], show_progress_bar=True,
        batch_size=64, normalize_embeddings=True,
    )
    qrels: defaultdict[str, dict[str, int]] = defaultdict(dict)
    all_qrels: defaultdict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        all_qrels[row["Query"]][row["GitHubUrl"]] = int(row["Relevance"])
        if row["GitHubUrl"] in snippets:
            qrels[row["Query"]][row["GitHubUrl"]] = int(row["Relevance"])

    missing_rows = [row for row in rows if row["GitHubUrl"] not in snippets]
    missing_grades = Counter(int(row["Relevance"]) for row in missing_rows)

    max_k = max(ks)
    totals: dict[str, dict[int, list[float]]] = {
        mode: {k: [] for k in ks} for mode in ("bm25", "vector", "hybrid", "rerank")
    }
    per_query = []
    for index, query in enumerate(sorted(qrels), 1):
        vector = _vector_rank(
            query, documents, document_vectors, model,
            max(max_k, rerank_candidates * 3),
        )
        bm25 = bm25_rank(query, documents, max(max_k, rerank_candidates * 3))
        hybrid = reciprocal_rank_fusion(
            vector, bm25, max_k, rrf_k=10, vector_weight=0.25, bm25_weight=2.0,
        )
        candidates = reciprocal_rank_fusion(
            vector, bm25, rerank_candidates, rrf_k=10,
            vector_weight=0.25, bm25_weight=2.0,
        )
        reranked = rerank(query, candidates, max_k)
        rankings = {
            "bm25": [hit.uid for hit in bm25],
            "vector": [hit.uid for hit in vector],
            "hybrid": [hit.uid for hit in hybrid],
            "rerank": [hit.uid for hit in reranked],
        }
        row_result = {"query": query, "ndcg": {}}
        for mode, ranked in rankings.items():
            row_result["ndcg"][mode] = {}
            for k in ks:
                score = _ndcg(ranked, qrels[query], k)
                totals[mode][k].append(score)
                row_result["ndcg"][mode][str(k)] = score
        per_query.append(row_result)
        print(f"CodeSearchNet {index}/{len(qrels)}: {query}")

    return {
        "benchmark": "CodeSearchNet human-judged URL pool",
        "protocol": (
            "Rank the union of human-judged URLs; unjudged query-url pairs are treated "
            "as relevance 0. This is not the archived leaderboard full-corpus score."
        ),
        "queries": len(qrels),
        "candidate_snippets": len(documents),
        "annotation_rows_used": sum(len(values) for values in qrels.values()),
        "coverage": {
            "official_annotation_rows": len(rows),
            "official_unique_urls": len({row["GitHubUrl"] for row in rows}),
            "prepared_unique_urls": len(snippets),
            "unique_url_fraction": len(snippets) / len({row["GitHubUrl"] for row in rows}),
            "missing_annotation_rows": len(missing_rows),
            "missing_relevance_grades": {
                str(grade): missing_grades.get(grade, 0) for grade in range(4)
            },
            "queries_with_missing_judgments": sum(
                any(uid not in snippets for uid in judgments)
                for judgments in all_qrels.values()
            ),
            "queries_without_prepared_relevant_result": sum(
                not any(grade > 0 for grade in judgments.values())
                for judgments in qrels.values()
            ),
        },
        "rerank_candidates": rerank_candidates,
        "metrics": {
            mode: {f"ndcg@{k}": sum(values[k]) / len(values[k]) for k in ks}
            for mode, values in totals.items()
        },
        "per_query": per_query,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="CodeSearchNet 外部检索基准")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--data-dir", type=Path, default=Path(".rag-eval/external-data/codesearchnet"))
    prepare.add_argument("--workers", type=int, default=8)
    run = subparsers.add_parser("evaluate")
    run.add_argument("--data-dir", type=Path, default=Path(".rag-eval/external-data/codesearchnet"))
    run.add_argument("--output", type=Path, default=Path(".rag-eval/codesearchnet-result.json"))
    args = parser.parse_args()

    data_dir = args.data_dir
    annotations = download_annotations(data_dir / "annotationStore.csv")
    snippets = data_dir / "snippets.jsonl"
    if args.command == "prepare":
        prepare_snippets(
            annotations, snippets, data_dir / "raw-files", workers=args.workers,
        )
        return
    if not snippets.exists():
        raise SystemExit("缺少 snippets.jsonl；请先运行 prepare")
    report = evaluate_judged_pool(annotations, snippets)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
