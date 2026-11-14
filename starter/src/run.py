import csv
import json
import os
import platform
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, flatten, summarize
from .ingestion import REPO_ROOT, RESULTS_DIR, STARTER_DIR, load_corpus
from .models import Chunk, DocumentPage
from .store import PgVectorStore

QUESTIONS_PATH = REPO_ROOT / "benchmark" / "questions.json"
AUDIT_DIR = RESULTS_DIR / "audit"
TOP_K = 3
COHERENCE_SAMPLE_SIZE = 10

STRATEGIES = {
    "A": {"chunking": "fixed_size", "size": 500, "overlap": 50, "index": "A", "use_filter": False},
    "B": {"chunking": "by_paragraph", "max_size": 800, "index": "B", "use_filter": False},
    "C": {"chunking": "by_paragraph", "max_size": 800, "index": "B", "use_filter": True},
}


def build_chunks(pages: list[DocumentPage], index: str) -> list[Chunk]:
    config = next(cfg for cfg in STRATEGIES.values() if cfg["index"] == index)
    if config["chunking"] == "fixed_size":
        return [chunk for page in pages for chunk in fixed_size(page, config["size"], config["overlap"])]
    return [chunk for page in pages for chunk in by_paragraph(page, config["max_size"])]


def chunk_stats(chunks: list[Chunk]) -> dict:
    lengths = [len(chunk.text) for chunk in chunks]
    return {
        "chunks": len(chunks),
        "chars_total": sum(lengths),
        "chars_min": min(lengths),
        "chars_median": statistics.median(lengths),
        "chars_mean": round(statistics.mean(lengths), 1),
        "chars_max": max(lengths),
        "chunks_under_100_chars": sum(length < 100 for length in lengths),
        "approx_tokens_total": round(sum(lengths) / 4),
    }


def environment(database_url: str, embedder: MyEmbedder) -> dict:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('server_version'), extversion FROM pg_extension WHERE extname = 'vector'")
        postgres, pgvector = cursor.fetchone()
    packages = ["pypdf", "psycopg", "sentence-transformers", "torch", "transformers", "python-dotenv"]
    return {
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {name: version(name) for name in packages},
        "postgresql": postgres,
        "pgvector": pgvector,
        "search": "exact cosine distance (<=>), no ANN index",
        "embedding": embedder.describe(),
        "cost_usd": 0,
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def select_on_dev(summary: dict, ingest: dict) -> dict:
    def key(strategy: str):
        dev = summary[strategy]["dev"]
        index = STRATEGIES[strategy]["index"]
        return (dev["hit_at_3"]["n"], dev["hit_at_1"]["n"], -ingest[index]["chunks"], -ingest[index]["embed_seconds"])

    ranking = sorted(STRATEGIES, key=key, reverse=True)
    best = key(ranking[0])
    tied = [strategy for strategy in ranking if key(strategy) == best]
    return {
        "criterion": "dev Hit@3, then dev Hit@1, then fewer chunks, then lower embedding time (fixed before the run)",
        "ranking": ranking,
        "tied_at_top": tied,
        "selected": ranking[0] if len(tied) == 1 else None,
    }


def coherence_samples(pages: list[DocumentPage], chunks_by_index: dict[str, list[Chunk]]) -> list[dict]:
    rows = []
    sample_pages = sorted(pages, key=lambda page: (page.document_id, page.page))[:COHERENCE_SAMPLE_SIZE]
    for number, page in enumerate(sample_pages, start=1):
        text = page.text.replace("\n", " ")
        middle = len(text) // 2
        probe = flatten(text[max(0, middle - 15):middle + 15])
        for strategy, config in STRATEGIES.items():
            if strategy == "C":
                continue
            candidates = [c for c in chunks_by_index[config["index"]] if c.document_id == page.document_id and c.page == page.page]
            if config["chunking"] == "fixed_size":
                chunk = next(c for c in candidates if c.metadata["char_start"] <= middle < c.metadata["char_start"] + 500)
            else:
                chunk = next((c for c in candidates if probe in flatten(c.text)), None) or max(
                    candidates, key=lambda c: len(set(flatten(c.text).split()) & set(probe.split()))
                )
            rows.append({
                "position_id": f"pos{number:02d}",
                "position": f"{page.document_id} p{page.page} char {middle} (page midpoint)",
                "strategy": "B/C" if strategy == "B" else strategy,
                "chunk_id": chunk.chunk_id,
                "chunk_chars": len(chunk.text),
                "chunk_text": chunk.text,
                "coherence_0_2": "",
                "reason": "",
            })
    return rows


def main() -> None:
    load_dotenv(STARTER_DIR / ".env")
    database_url = os.environ.get("DATABASE_URL", "")
    RESULTS_DIR.mkdir(exist_ok=True)
    AUDIT_DIR.mkdir(exist_ok=True)

    pages, ingestion_log = load_corpus()
    write_jsonl(RESULTS_DIR / "pages_normalized.jsonl", [asdict(page) for page in pages])
    (RESULTS_DIR / "ingestion_log.json").write_text(json.dumps(ingestion_log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Corpus: {len(pages)} content pages")

    embedder = MyEmbedder()
    store = PgVectorStore(database_url, embedder.dimensions)
    store.create_schema()

    chunks_by_index, ingest = {}, {}
    for index in sorted({config["index"] for config in STRATEGIES.values()}):
        chunks = build_chunks(pages, index)
        started = time.perf_counter()
        vectors = embedder.embed_documents([chunk.text for chunk in chunks])
        embed_seconds = time.perf_counter() - started
        started = time.perf_counter()
        first = store.upsert(index, chunks, vectors)
        upsert_seconds = time.perf_counter() - started
        count_after_first = store.count(index)
        second = store.upsert(index, chunks, vectors)
        count_after_reload = store.count(index)
        if count_after_first != count_after_reload or count_after_reload != len(chunks):
            raise RuntimeError(f"Reload check failed for index {index}: {count_after_first} -> {count_after_reload}")
        chunks_by_index[index] = chunks
        ingest[index] = {
            **chunk_stats(chunks),
            "embed_seconds": round(embed_seconds, 2),
            "upsert_seconds": round(upsert_seconds, 2),
            "first_upsert": first,
            "reload_upsert": second,
            "rows_after_first_upsert": count_after_first,
            "rows_after_reload": count_after_reload,
            "reload_duplicates": count_after_reload - count_after_first,
        }
        print(f"Index {index}: {len(chunks)} chunks, embed {embed_seconds:.1f}s, reload rows {count_after_first}->{count_after_reload}")

    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    query_vectors = {q["id"]: embedder.embed_query(q["question"]) for q in questions}
    traces, latencies = [], []
    for strategy, config in STRATEGIES.items():
        for question in questions:
            applied = question.get("filter") if config["use_filter"] else None
            started = time.perf_counter()
            hits = store.search(config["index"], query_vectors[question["id"]], TOP_K, applied)
            latencies.append(time.perf_counter() - started)
            trace = evaluate_query(question, hits)
            trace.update(strategy=strategy, index=config["index"], filter_applied=applied)
            traces.append(trace)
    write_jsonl(RESULTS_DIR / "traces.jsonl", traces)

    summary = summarize(traces)
    selection = select_on_dev(summary, ingest)
    metrics = {
        "summary": summary,
        "selection_on_dev": selection,
        "search_latency_ms_median": round(statistics.median(latencies) * 1000, 1),
    }
    (RESULTS_DIR / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    config = {"strategies": STRATEGIES, "top_k": TOP_K, "ingest": ingest, "environment": environment(database_url, embedder)}
    (RESULTS_DIR / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    write_csv(AUDIT_DIR / "coherence_samples.csv", coherence_samples(pages, chunks_by_index))
    grounding_rows = []
    for trace in traces:
        if trace["split"] != "test":
            continue
        for result in trace["results"]:
            grounding_rows.append({
                "strategy": trace["strategy"],
                "question_id": trace["question_id"],
                "rank": result["rank"],
                "chunk_id": result["chunk_id"],
                "source": result["source"],
                "page": result["page"],
                "page_label": result["page_label"],
                "food_category": result["food_category"],
                "evidence_hit_auto": result["evidence_hit"],
                "text": result["text"],
            })
    write_csv(AUDIT_DIR / "test_top3_records.csv", grounding_rows)

    print("\nStrategy | split | Hit@1 | Hit@3 | filter respected")
    for strategy, splits in summary.items():
        for split in ("dev", "test"):
            row = splits[split]
            filt = row["filter_respected"] if row["filter_respected"] == "N/A" else f"{row['filter_respected']['n']}/{row['filter_respected']['N']}"
            print(f"{strategy} | {split} | {row['hit_at_1']['n']}/{row['hit_at_1']['N']} | {row['hit_at_3']['n']}/{row['hit_at_3']['N']} | {filt}")
    print("Selection on dev:", selection)


if __name__ == "__main__":
    main()
