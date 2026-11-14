import hashlib
import json
import re
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path

from pypdf import PdfReader

from .models import DocumentPage

STARTER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = STARTER_DIR.parent
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.json"
RESULTS_DIR = STARTER_DIR / "results"
MIN_CONTENT_PAGES = 30
MIN_PAGE_CHARS = 50

PAGE_LABEL = re.compile(r"^\s*(\d-\d{1,2}|[ivx]{1,4})\s*$")
RUNNING_HEADERS = {
    "Principles of Home Canning",
    "Fruit and Fruit Products",
    "Tomatoes and Tomato Products",
    "Vegetables and Vegetable Products",
}
GUIDE_NUMBER = re.compile(r"^\s*\d\s*$")
EDITOR_NOTE = re.compile(r"Ken\s*[–-]\s*these are.*?stay with the tables\.", re.DOTALL)
LINE_END_HYPHEN = re.compile(r"(\w+)-[ \t]*\n[ \t]*(\w+)")
KEEP_HYPHEN_LEFT = {"heat", "dial", "boiling", "weighted", "raw", "hot", "low", "high"}


def split_header(raw: str) -> tuple[str, str | None]:
    lines = raw.split("\n")
    label = None
    while lines and len(lines) > 1:
        head = lines[0].strip()
        match = PAGE_LABEL.match(lines[0])
        if match:
            label = match.group(1)
        elif not (head in RUNNING_HEADERS or GUIDE_NUMBER.match(lines[0]) or head == ""):
            break
        lines.pop(0)
    return "\n".join(lines), label


def _join_hyphen(match: re.Match) -> str:
    left, right = match.group(1), match.group(2)
    if left.lower() in KEEP_HYPHEN_LEFT or left[-1].isdigit():
        return f"{left}-{right}"
    return f"{left}{right}"


def normalize_text(text: str) -> str:
    text = EDITOR_NOTE.sub("", text)
    text = text.replace("�", " ")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = LINE_END_HYPHEN.sub(_join_hyphen, text)
    text = re.sub(r"[ \t]+\n", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    pages = []
    for number, pdf_page in enumerate(PdfReader(path).pages, start=1):
        metadata = {"page_label": None, "raw_chars": 0, "error": None}
        try:
            raw = pdf_page.extract_text() or ""
        except Exception as exc:
            raw = ""
            metadata["error"] = f"{type(exc).__name__}: {exc}"
        body, metadata["page_label"] = split_header(raw)
        metadata["raw_chars"] = len(raw)
        pages.append(DocumentPage(document_id, source, number, normalize_text(body), metadata))
    return pages


def _expand_page_keys(excluded: dict[str, str]) -> dict[int, str]:
    reasons = {}
    for key, reason in excluded.items():
        for part in key.split(","):
            start, _, end = part.partition("-")
            for number in range(int(start), int(end or start) + 1):
                reasons[number] = reason
    return reasons


def load_corpus(manifest_path: Path = MANIFEST_PATH) -> tuple[list[DocumentPage], dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    kept, log = [], {"excluded": [], "errors": [], "per_source": {}}
    started = time.perf_counter()

    for entry in manifest["sources"]:
        path = REPO_ROOT / entry["local_path"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            log["errors"].append({"source_id": entry["source_id"], "error": "sha256 mismatch with manifest"})

        counted = set(entry["counted_pages"])
        reasons = _expand_page_keys(entry["excluded_pages"])
        pages = load_pdf(path, entry["source_id"], path.name)
        kept_here = 0

        for page in pages:
            where = {"source_id": page.document_id, "page": page.page, "page_label": page.metadata["page_label"]}
            if page.page not in counted:
                log["excluded"].append({**where, "reason": reasons.get(page.page, "not listed in manifest")})
                continue
            if page.metadata["error"] or len(page.text) < MIN_PAGE_CHARS:
                log["errors"].append({**where, "error": page.metadata["error"] or f"fewer than {MIN_PAGE_CHARS} chars after normalization"})
                continue
            page.metadata.update(
                food_category=entry["food_category"],
                title=entry["title"],
                version=entry["version"],
                chars=len(page.text),
            )
            kept.append(page)
            kept_here += 1

        log["per_source"][entry["source_id"]] = {"physical_pages": len(pages), "kept_pages": kept_here}

    log["total_kept_pages"] = len(kept)
    log["total_kept_chars"] = sum(len(page.text) for page in kept)
    log["ingest_seconds"] = round(time.perf_counter() - started, 2)
    if len(kept) < MIN_CONTENT_PAGES:
        raise ValueError(f"Only {len(kept)} content pages; at least {MIN_CONTENT_PAGES} are required")
    return kept, log


def main() -> None:
    pages, log = load_corpus()
    RESULTS_DIR.mkdir(exist_ok=True)
    with (RESULTS_DIR / "pages_normalized.jsonl").open("w", encoding="utf-8") as handle:
        for page in pages:
            handle.write(json.dumps(asdict(page), ensure_ascii=False) + "\n")
    (RESULTS_DIR / "ingestion_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Kept {log['total_kept_pages']} pages, {log['total_kept_chars']} chars in {log['ingest_seconds']}s")
    print(f"Excluded {len(log['excluded'])} pages, {len(log['errors'])} errors")
    for error in log["errors"]:
        print("  ERROR", error)


if __name__ == "__main__":
    main()
