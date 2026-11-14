import re

from .models import Chunk, DocumentPage

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
HEADING_MAX_CHARS = 60
FOLLOWING_PARAGRAPH_MIN_CHARS = 150
MIN_CHUNK_CHARS = 100


def _make_chunk(page: DocumentPage, index: int, text: str, extra: dict) -> Chunk:
    return Chunk(
        chunk_id=f"{page.document_id}-p{page.page:03d}-c{index:03d}",
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        text=text,
        metadata={**page.metadata, **extra},
    )


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    if not 0 <= overlap < size:
        raise ValueError("overlap must be in [0, size)")
    text = page.text.replace("\n", " ")
    step = size - overlap
    chunks = []
    for index, start in enumerate(range(0, max(len(text) - overlap, 1), step)):
        piece = text[start:start + size].strip()
        if piece:
            chunks.append(_make_chunk(page, index, piece, {"char_start": start}))
    return chunks


def _is_heading(line: str, next_line: str | None) -> bool:
    letters = [c for c in line if c.isalpha()]
    if len(letters) >= 3 and all(c.isupper() for c in letters) and len(line) <= 80:
        return True
    return (
        len(line) <= HEADING_MAX_CHARS
        and line[0].isupper()
        and line[-1] not in ".:;,"
        and next_line is not None
        and len(next_line) >= FOLLOWING_PARAGRAPH_MIN_CHARS
    )


def _split_long_block(block: str, max_size: int) -> list[str]:
    pieces, current = [], ""
    for sentence in SENTENCE_END.split(block):
        while len(sentence) > max_size:
            pieces.append(sentence[:max_size])
            sentence = sentence[max_size:]
        if current and len(current) + 1 + len(sentence) > max_size:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    lines = page.text.split("\n")
    groups, current, section = [], [], None
    current_section = None

    def flush():
        nonlocal current
        if current:
            groups.append(("\n".join(current), current_section))
            current = []

    for position, line in enumerate(lines):
        next_line = lines[position + 1] if position + 1 < len(lines) else None
        if _is_heading(line, next_line):
            flush()
            section = line
        if not current:
            current_section = section
        for block in _split_long_block(line, max_size) if len(line) > max_size else [line]:
            if current and len("\n".join(current)) + 1 + len(block) > max_size:
                flush()
                current_section = section
            current.append(block)
    flush()

    groups = _merge_small(groups, max_size)
    return [_make_chunk(page, index, text, {"section": sec}) for index, (text, sec) in enumerate(groups)]


def _merge_small(groups: list[tuple[str, str | None]], max_size: int) -> list[tuple[str, str | None]]:
    merged = list(groups)
    position = 0
    while position < len(merged):
        text, section = merged[position]
        if len(text) >= MIN_CHUNK_CHARS or len(merged) == 1:
            position += 1
            continue
        if position + 1 < len(merged) and len(text) + 1 + len(merged[position + 1][0]) <= max_size:
            merged[position + 1] = (f"{text}\n{merged[position + 1][0]}", section or merged[position + 1][1])
            del merged[position]
        elif position > 0 and len(merged[position - 1][0]) + 1 + len(text) <= max_size:
            merged[position - 1] = (f"{merged[position - 1][0]}\n{text}", merged[position - 1][1])
            del merged[position]
        else:
            position += 1
    return merged
