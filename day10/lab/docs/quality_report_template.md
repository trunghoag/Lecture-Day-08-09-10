# Quality report - Lab Day 10

**run_id:** `final-day10`  
**Ngày:** 2026-06-10  
**Bad/inject run:** `inject-bad`

---

## 1. Tóm tắt số liệu

| Chỉ số | Inject bad | Sau fix (`final-day10`) | Ghi chú |
|--------|------------|--------------------------|---------|
| raw_records | 247 | 247 | Cùng raw CSV |
| cleaned_records | 40 | 40 | Ambiguous refund rows bị quarantine ở cả hai run |
| quarantine_records | 207 | 207 | `unknown_doc_id=109`, `duplicate_chunk_text=32`, `ambiguous_refund_context=2` |
| Expectation halt? | Có, `refund_no_stale_14d_window` fail `violations=2`; chỉ embed vì dùng `--skip-validate` | Không | Production run không dùng `--skip-validate` |
| embed_upsert | 40 | 40 | Collection `day10_kb` |

---

## 2. Before / after retrieval

Evidence files:
- Bad: `artifacts/eval/after_inject_bad.csv`
- Good: `artifacts/eval/eval_after_fix.csv`
- LLM judge self-eval: `artifacts/eval/llm_judge_test_questions.jsonl`
- LLM judge grading: `artifacts/eval/llm_judge_grading_questions.jsonl`

Key row:

| Question | Inject bad | Final clean |
|----------|------------|-------------|
| `q_refund_window` | `contains_expected=yes`, `hits_forbidden=yes`, top preview chứa `14 ngày làm việc` | `contains_expected=yes`, `hits_forbidden=no`, top preview chứa `7 ngày làm việc` |

Final self-eval summary: 21/21 rows pass with `contains_expected=yes`, `hits_forbidden=no`, `top1_doc_expected=yes`.

Official grading summary: `artifacts/eval/grading_run.jsonl` has 10 rows; all have `contains_expected=true`, `hits_forbidden=false`, and `top1_doc_matches=true`.

LLM-judge method: each question is judged from the retrieved top-k context only. The prompt includes expected keywords, forbidden keywords, expected top-1 doc id, and retrieved chunks. The model returns JSON with `verdict`, `score`, `rationale`, `missing_evidence`, and `unsupported_claims`. This is extra evidence for the rubric, not a replacement for keyword checks.

Difference versus the non-LLM baseline: the deterministic eval answers whether expected keywords/top-1/forbidden terms match. LLM-judge adds a semantic grounding check: given only the retrieved top-k chunks, it decides whether the evidence is sufficient to answer the question and records why. In the final run it does not change the official score because the baseline already passed; it increases review confidence and provides richer failure diagnostics for future regressions.

Current LLM-judge run status: artifacts were generated successfully with model `gpt-5.4-mini` through host `api.xah.io`. Official grading judge result is 10/10 pass with `partial=0`, `fail=0`, `error=0`, `average_score=2.0`. Self-eval judge result is 21/21 pass with `partial=0`, `fail=0`, `error=0`, `average_score=2.0`.

---

## 3. Freshness & monitor

Final run:
- `latest_exported_at=2026-04-11T00:00:00`
- `freshness_check=FAIL`
- reason: `freshness_sla_exceeded`
- SLA: 24 hours

Interpretation: this is no longer a parser bug. The slash timestamp format has been normalized to ISO, so freshness now correctly detects that the sample snapshot is stale relative to the run date. In production, mitigation is to request a new upstream export or widen SLA only with owner approval.

---

## 4. Corruption inject

Inject command:

```bash
python etl_pipeline.py run --run-id inject-bad --no-refund-fix --skip-validate
python eval_retrieval.py --out artifacts/eval/after_inject_bad.csv
```

Detection:
- Expectation `refund_no_stale_14d_window` fails with `violations=2`.
- `--skip-validate` intentionally allows publish for Sprint 3 demonstration.
- Retrieval evidence shows exactly one forbidden hit: `q_refund_window`.

Recovery:

```bash
python etl_pipeline.py run --run-id final-day10
```

This restores the Chroma snapshot to 40 clean chunks and removes stale vectors by prune + upsert.

---

## 5. Hạn chế & việc chưa làm

- `data_privacy_guideline` and `security_policy` are still quarantined because they do not have canonical documents in `data/docs/`.
- `chroma_db/` is local runtime state and is not committed.
- `retrieval_utils.py` is a deterministic evaluation helper for Day 10; Day 09 production integration should decide its own retrieval strategy.
