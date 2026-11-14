import re
from collections import defaultdict
from typing import Any


def flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def gold_candidates(question: dict[str, Any]) -> list[dict[str, Any]]:
    golds = [{
        "source_id": question["gold_source_id"],
        "page": question["gold_page"],
        "evidence_quote": question["evidence_quote"],
    }]
    return golds + list(question.get("alternative_gold", []))


def is_evidence_hit(hit: dict[str, Any], golds: list[dict[str, Any]]) -> bool:
    text = flatten(hit["text"])
    return any(
        hit["document_id"] == gold["source_id"]
        and hit["page"] == gold["page"]
        and flatten(gold["evidence_quote"]) in text
        for gold in golds
    )


def is_page_hit(hit: dict[str, Any], golds: list[dict[str, Any]]) -> bool:
    return any(hit["document_id"] == gold["source_id"] and hit["page"] == gold["page"] for gold in golds)


def filter_respected(hits: list[dict[str, Any]], question_filter: dict[str, Any] | None) -> bool | None:
    if not question_filter:
        return None
    return all(all(hit.get(key) == value for key, value in question_filter.items()) for hit in hits)


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    golds = gold_candidates(question)
    evidence_flags = [is_evidence_hit(hit, golds) for hit in hits]
    page_flags = [is_page_hit(hit, golds) for hit in hits]
    first_rank = next((rank for rank, flag in enumerate(evidence_flags, start=1) if flag), None)
    return {
        "question_id": question["id"],
        "split": question["split"],
        "type": question.get("type"),
        "question": question["question"],
        "filter": question.get("filter"),
        "gold": golds,
        "hit_at_1": bool(evidence_flags[:1] and evidence_flags[0]),
        "hit_at_3": any(evidence_flags[:3]),
        "first_hit_rank": first_rank,
        "page_hit_at_3": any(page_flags[:3]),
        "filter_respected": filter_respected(hits[:3], question.get("filter")),
        "results": [
            {
                "rank": rank,
                "chunk_id": hit["chunk_id"],
                "document_id": hit["document_id"],
                "source": hit["source"],
                "page": hit["page"],
                "page_label": hit.get("page_label"),
                "food_category": hit.get("food_category"),
                "section": hit.get("section"),
                "score": round(float(hit["score"]), 4),
                "evidence_hit": evidence_flags[rank - 1],
                "page_hit": page_flags[rank - 1],
                "text": hit["text"],
            }
            for rank, hit in enumerate(hits, start=1)
        ],
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        buckets[(trace["strategy"], trace["split"])].append(trace)
        buckets[(trace["strategy"], "all")].append(trace)

    summary: dict[str, Any] = {}
    for (strategy, split), items in sorted(buckets.items()):
        total = len(items)
        filtered = [item for item in items if item["filter_respected"] is not None]

        def ratio(count: int, size: int) -> dict[str, Any]:
            return {"n": count, "N": size, "rate": round(count / size, 3) if size else None}

        summary.setdefault(strategy, {})[split] = {
            "hit_at_1": ratio(sum(item["hit_at_1"] for item in items), total),
            "hit_at_3": ratio(sum(item["hit_at_3"] for item in items), total),
            "page_hit_at_3": ratio(sum(item["page_hit_at_3"] for item in items), total),
            "filter_respected": ratio(sum(item["filter_respected"] for item in filtered), len(filtered)) if filtered else "N/A",
            "misses_at_3": [item["question_id"] for item in items if not item["hit_at_3"]],
        }
    return summary
