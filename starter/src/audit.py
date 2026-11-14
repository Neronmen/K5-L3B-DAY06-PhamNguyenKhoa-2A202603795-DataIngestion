import csv
import json
from collections import Counter, defaultdict

from .evaluation import flatten
from .ingestion import MANIFEST_PATH, RESULTS_DIR

AUDIT_DIR = RESULTS_DIR / "audit"


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def check_metadata(record: dict, manifest: dict, pages: dict) -> list[str]:
    errors = []
    entry = manifest.get(record["document_id"])
    page = pages.get((record["document_id"], record["page"]))
    if entry is None:
        return ["unknown document_id"]
    if record["source"] != entry["local_path"].split("/")[-1]:
        errors.append("source does not match manifest file")
    if page is None:
        return errors + ["page is not a counted content page"]
    if flatten(record["text"]) not in flatten(page["text"]):
        errors.append("chunk text not found on the stated page")
    if record["food_category"] != entry["food_category"]:
        errors.append("food_category differs from manifest")
    if record["page_label"] != page["metadata"]["page_label"]:
        errors.append("page_label differs from the page header")
    if record.get("section") and record["section"] not in page["text"].split("\n"):
        errors.append("section heading not found on the page")
    return errors


def main() -> None:
    manifest = {entry["source_id"]: entry for entry in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["sources"]}
    pages = {(p["document_id"], p["page"]): p for p in load_jsonl(RESULTS_DIR / "pages_normalized.jsonl")}
    traces = [t for t in load_jsonl(RESULTS_DIR / "traces.jsonl") if t["split"] == "test"]
    labels = json.loads((AUDIT_DIR / "manual_labels.json").read_text(encoding="utf-8"))

    metadata_rows, metadata_ok = [], Counter()
    metadata_total = Counter()
    for trace in traces:
        for result in trace["results"]:
            errors = check_metadata(result, manifest, pages)
            metadata_total[trace["strategy"]] += 1
            metadata_ok[trace["strategy"]] += not errors
            metadata_rows.append({
                "strategy": trace["strategy"],
                "question_id": trace["question_id"],
                "rank": result["rank"],
                "chunk_id": result["chunk_id"],
                "source": result["source"],
                "page": result["page"],
                "page_label": result["page_label"],
                "food_category": result["food_category"],
                "section": result["section"],
                "complete_and_correct": not errors,
                "errors": "; ".join(errors),
            })
    with (AUDIT_DIR / "metadata_checks.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metadata_rows[0].keys()))
        writer.writeheader()
        writer.writerows(metadata_rows)

    filter_rows = defaultdict(lambda: [0, 0])
    for trace in traces:
        if trace["filter_respected"] is not None:
            filter_rows[trace["strategy"]][0] += trace["filter_respected"]
            filter_rows[trace["strategy"]][1] += 1

    coherence = defaultdict(list)
    for item in labels["coherence"]:
        for strategy in item["strategy"].split("/"):
            coherence[strategy].append(item["label"])
    grounding = defaultdict(list)
    for item in labels["grounding"]:
        grounding[item["strategy"]].append(item["label"])

    summary = {}
    for strategy in sorted(metadata_total):
        summary[strategy] = {
            "coherence_mean_of_2": round(sum(coherence[strategy]) / len(coherence[strategy]), 2),
            "coherence_n": len(coherence[strategy]),
            "coherence_counts_0_1_2": [coherence[strategy].count(v) for v in (0, 1, 2)],
            "metadata_complete_correct": {"n": metadata_ok[strategy], "N": metadata_total[strategy]},
            "filter_respected_test": {"n": filter_rows[strategy][0], "N": filter_rows[strategy][1]} if filter_rows[strategy][1] else "N/A",
            "grounding_counts_0_1_2": [grounding[strategy].count(v) for v in (0, 1, 2)],
            "grounding_mean_of_2": round(sum(grounding[strategy]) / len(grounding[strategy]), 2),
        }
    summary_doc = {"labeler": labels["labeler"], "per_strategy": summary}
    (AUDIT_DIR / "summary.json").write_text(json.dumps(summary_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Strategy | Coherence /2 (n) | Metadata ok | Filter (test) | Grounding 0/1/2 | Grounding /2")
    for strategy, row in summary.items():
        meta = row["metadata_complete_correct"]
        filt = row["filter_respected_test"]
        filt_text = filt if filt == "N/A" else f"{filt['n']}/{filt['N']}"
        print(f"{strategy} | {row['coherence_mean_of_2']} ({row['coherence_n']}) | {meta['n']}/{meta['N']} | {filt_text} | "
              f"{'/'.join(map(str, row['grounding_counts_0_1_2']))} | {row['grounding_mean_of_2']}")
    failures = [row for row in metadata_rows if not row["complete_and_correct"]]
    for row in failures:
        print("  metadata issue:", row["strategy"], row["question_id"], row["chunk_id"], row["errors"])


if __name__ == "__main__":
    main()
