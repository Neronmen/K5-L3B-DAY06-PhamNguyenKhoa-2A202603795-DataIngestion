# Benchmark

Written on 2026-11-14, **before any retrieval run**. File: [questions.json](questions.json). Verify with `python scripts/verify_benchmark.py` from `starter/`.

## Composition

| | dev | test | total |
|---|---:|---:|---:|
| Questions | 15 | 5 | 20 |
| With metadata filter | 3 (q11–q13) | 2 (q18–q19) | 5 |

Types: direct 4, paraphrase 3, conditional/exception 6, metadata_filter 5, table 2. All questions are in English, like the corpus.

## Gold and evidence

- `gold_page` is the 1-based **physical** PDF page (see `page_counting_rule` in [data/manifest.json](../data/manifest.json)), not the printed label such as `2-7`.
- Each `evidence_quote` was copied from `starter/results/pages_normalized.jsonl` and checked automatically to appear on the gold page after normalization. The author also read each source page to check the answer, including conditions and exceptions.
- When several pages are valid, they are listed in `alternative_gold` (q05, q09, q10). A hit on any of them counts.
- q15 and q20 have evidence inside process tables, whose row/column layout is lost by text extraction. They are kept on purpose to measure that failure.

## Split

The split was fixed by topic before running anything: test questions cover topics that no dev question covers (jar sterilization, seal testing, salsa peppers, wild mushrooms, fig process time). Leakage risk: q20 (figs) shares the food with dev q02, but asks a different fact on a different page.

## Filters

`filter` uses the `food_category` metadata (`general`, `principles`, `fruits`, `tomatoes`, `vegetables`), which comes from the guide each page belongs to, not from guessing. A filter is an **assumed input given before retrieval**, for example a user browsing the fruit guide. It is never derived from the gold answer. Each filter was picked because the question wording could pull pages from another category (for example "green tomato pie filling" sits in the fruit guide, q12). Strategy C applies the filter only to questions that have one; questions with `filter: null` run unfiltered.

## Selection criterion (fixed before seeing test results)

The strategy is chosen on **dev only**, in this order:
1. Higher Evidence Hit@3.
2. If tied, higher Hit@1.
3. If still tied, fewer chunks and lower ingest time.

Test results are then reported once for the chosen setup and the others, without further tuning.
