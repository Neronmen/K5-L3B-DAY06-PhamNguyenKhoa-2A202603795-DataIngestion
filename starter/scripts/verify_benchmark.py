import json
import re
import sys
from collections import Counter
from pathlib import Path

STARTER_DIR = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = STARTER_DIR.parent / "benchmark" / "questions.json"
PAGES_PATH = STARTER_DIR / "results" / "pages_normalized.jsonl"
REQUIRED = ["id", "split", "question", "expected_answer", "gold_source_id", "gold_page", "evidence_quote"]


def flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    pages = {}
    for line in PAGES_PATH.read_text(encoding="utf-8").splitlines():
        page = json.loads(line)
        pages[(page["document_id"], page["page"])] = page
    metadata_values = {(key, value) for page in pages.values() for key, value in page["metadata"].items()}

    problems = []
    ids = Counter(q["id"] for q in questions)
    problems += [f"duplicate id {qid}" for qid, count in ids.items() if count > 1]

    for q in questions:
        missing = [field for field in REQUIRED if field not in q]
        if missing:
            problems.append(f"{q.get('id')}: missing {missing}")
            continue
        golds = [{"source_id": q["gold_source_id"], "page": q["gold_page"], "evidence_quote": q["evidence_quote"]}]
        golds += q.get("alternative_gold", [])
        for gold in golds:
            page = pages.get((gold["source_id"], gold["page"]))
            if page is None:
                problems.append(f"{q['id']}: page {gold['source_id']} p{gold['page']} is not in the counted corpus")
            elif flatten(gold["evidence_quote"]) not in flatten(page["text"]):
                problems.append(f"{q['id']}: evidence not found on {gold['source_id']} p{gold['page']}")
        for key, value in (q.get("filter") or {}).items():
            if (key, value) not in metadata_values:
                problems.append(f"{q['id']}: filter {key}={value!r} does not exist in metadata")
            gold_page = pages.get((q["gold_source_id"], q["gold_page"]))
            if gold_page and gold_page["metadata"].get(key) != value:
                problems.append(f"{q['id']}: gold page does not satisfy filter {key}={value!r}")

    splits = Counter(q["split"] for q in questions)
    filtered = Counter(q["split"] for q in questions if q.get("filter"))
    if len(questions) < 20:
        problems.append(f"only {len(questions)} questions")
    if splits["dev"] < 15 or splits["test"] < 5:
        problems.append(f"split too small: {dict(splits)}")
    if sum(filtered.values()) < 3 or filtered["test"] < 1:
        problems.append(f"need >=3 filter questions with >=1 in test: {dict(filtered)}")

    print(f"questions={len(questions)} splits={dict(splits)} filter_questions={dict(filtered)}")
    print(f"types={dict(Counter(q.get('type') for q in questions))}")
    for problem in problems:
        print("FAIL", problem)
    print("Benchmark OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
