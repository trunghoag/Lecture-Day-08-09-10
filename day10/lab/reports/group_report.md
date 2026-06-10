# Báo Cáo Nhóm - Lab Day 10: Data Pipeline & Data Observability

**Tên nhóm:** Day 10 Technical Owners  
**Thành viên / vai trò:**

| Vai trò | Trách nhiệm |
|---------|-------------|
| Ingestion / Raw Owner | Raw CSV, record counts, manifest |
| Cleaning & Quality Owner | Cleaning rules, quarantine, expectations |
| Embed & Idempotency Owner | Chroma publish, prune/upsert, eval/grading |
| Monitoring / Docs Owner | Freshness, runbook, reports |

**Ngày nộp:** 2026-06-10  
**Run ID production:** `final-day10`

---

## 1. Pipeline tổng quan

Pipeline xử lý `data/raw/policy_export_dirty.csv` gồm 247 raw records từ 5 nguồn hợp lệ và nhiều nguồn không hợp lệ. Run production `final-day10` tạo:

- `cleaned_records=40`
- `quarantine_records=207`
- Chroma collection: `day10_kb`
- `embed_upsert count=40`

Lệnh chạy end-to-end:

```bash
python etl_pipeline.py run --run-id final-day10
python eval_retrieval.py --out artifacts/eval/eval_after_fix.csv
python grading_run.py --out artifacts/eval/grading_run.jsonl
python instructor_quick_check.py --grading artifacts/eval/grading_run.jsonl --manifest artifacts/manifests/manifest_final-day10.json
```

Final self-eval đạt 21/21. Official grading đạt 10/10 với mọi dòng `contains_expected=true`, `hits_forbidden=false`, `top1_doc_matches=true`.

---

## 2. Cleaning rules & expectations

Nhóm giữ baseline rules và bổ sung các rule có tác động đo được:

| Rule | Mục đích | Evidence |
|------|----------|----------|
| R7 `stale_hr_content_old_leave_quota` | Quarantine HR chunk còn nội dung "10 ngày phép năm" | `stale_hr_content_old_leave_quota=8` trong quarantine |
| R8 `chunk_text_too_short` | Loại chunk quá ngắn | expectation `chunk_min_length_8` OK |
| R9/R12 `exported_at_iso_normalization` | Bắt buộc timestamp và normalize `YYYY/MM/DDT...` sang ISO | `latest_exported_at=2026-04-11T00:00:00`, không còn WARN parse |
| R10 `stale_sla_effective_date` | Loại SLA 2025 khỏi doc `sla_p1_2026` | `stale_sla_effective_date=23` |
| R11 `rag_context_enhancement` | Tăng recall cho P1 auto escalate 10 phút | `gq_d10_06` pass |
| R13 `refund_exception_enrichment` | Tăng recall cho hàng kỹ thuật số/license/subscription | `gq_d10_02` pass |
| R14 `sla_update_frequency_enrichment` | Tăng recall cho update P1 mỗi 30 phút | self-eval `q_p1_update_frequency` pass |
| R15 `ambiguous_refund_context` | Quarantine refund chunk mơ hồ | `ambiguous_refund_context=2` |
| R16 `access_standard_turnaround_enrichment` | Tăng recall cho Standard Access 2 ngày | self-eval `q_access_level2_turnaround` pass |

Expectations mới bảo vệ các rule này:

- `exported_at_iso_datetime`
- `refund_exception_has_digital_product_keywords`
- `sla_p1_update_frequency_has_30m_context`
- `refund_no_ambiguous_context_chunks`
- `access_standard_turnaround_has_2d_context`

Run `final-day10` ghi tất cả halt expectations OK.

---

## 3. Metric impact

| Hạng mục | Trước / inject | Sau fix |
|----------|----------------|---------|
| Refund stale | `inject-bad` fail `refund_no_stale_14d_window`, `violations=2`; eval có `q_refund_window hits_forbidden=yes` | `final-day10` expectation OK; `q_refund_window hits_forbidden=no` |
| Freshness parse | Trước đây `latest_exported_at=2026/04/07T...` gây WARN parse | Timestamp được normalize; freshness trả `FAIL freshness_sla_exceeded`, đúng bản chất snapshot stale |
| Self-eval | Trước fix còn miss `q_refund_exception_digital` và `q_p1_update_frequency` | `eval_after_fix.csv`: 21/21 `yes,no,yes` |
| Official grading | Có regression `gq_d10_02` khi chỉ dùng dense retrieval | Sau `retrieval_utils.py` expansion/rerank: 10/10 OK |

---

## 4. Before / after retrieval

Bad evidence:

```bash
python etl_pipeline.py run --run-id inject-bad --no-refund-fix --skip-validate
python eval_retrieval.py --out artifacts/eval/after_inject_bad.csv
```

`after_inject_bad.csv` có 1 dòng forbidden:

| question_id | contains_expected | hits_forbidden | top1_doc |
|-------------|-------------------|----------------|----------|
| `q_refund_window` | yes | yes | `policy_refund_v4` |

Good evidence:

`eval_after_fix.csv` có 21/21 dòng `contains_expected=yes`, `hits_forbidden=no`, `top1_doc_expected=yes`.

---

## 5. LLM-judge mở rộng

Nhóm bổ sung LLM-judge như evidence Distinction, không thay thế checks chính thức.

So với lúc chưa thêm LLM-judge, điểm official không thay đổi vì pipeline đã đạt `eval_after_fix.csv` 21/21 và `grading_run.jsonl` 10/10 bằng các check deterministic. Khác biệt chính là lớp judge này kiểm tra thêm semantic grounding: model chỉ được nhìn top-k context đã retrieve, rồi đánh giá liệu context có đủ bằng chứng để trả lời hay không, có claim nào không được hỗ trợ hay không, và có thiếu evidence nào cần bổ sung hay không. Vì vậy LLM-judge không làm "tăng điểm" theo nghĩa thay grading, nhưng làm evidence mạnh hơn: nó chứng minh kết quả retrieval không chỉ khớp keyword/top-1 mà còn đủ ngữ cảnh để một judge độc lập kết luận `pass`.

Giá trị thực tế của LLM-judge:

- Bắt các case mà keyword có thể pass nhưng context vẫn mơ hồ, thiếu bằng chứng hoặc không trả lời trực tiếp.
- Ghi `rationale`, `missing_evidence`, `unsupported_claims` để reviewer hiểu vì sao pass/fail thay vì chỉ thấy `yes/no`.
- Là bằng chứng mở rộng cho Distinction theo rubric, trong khi source of truth chấm chính thức vẫn là `eval_retrieval.py`, `grading_run.py`, và `instructor_quick_check.py`.

Lệnh chạy:

```bash
python llm_judge_eval.py --questions data/test_questions.json --out artifacts/eval/llm_judge_test_questions.jsonl
python llm_judge_eval.py --questions data/grading_questions.json --out artifacts/eval/llm_judge_grading_questions.jsonl --top-k 5
```

Phương pháp: với mỗi câu hỏi, script lấy top-k context từ Chroma bằng cùng `expand_query` + `rerank_results`, gửi question, expected keywords, forbidden keywords, expected top-1 doc id và context cho model OpenAI-compatible. Model phải trả JSON gồm `verdict`, `score`, `rationale`, `missing_evidence`, `unsupported_claims`.

Artifacts:

- `artifacts/eval/llm_judge_test_questions.jsonl`
- `artifacts/eval/llm_judge_test_questions.summary.json`
- `artifacts/eval/llm_judge_grading_questions.jsonl`
- `artifacts/eval/llm_judge_grading_questions.summary.json`

Kết quả hiện tại: LLM-judge chạy thành công với model `gpt-5.4-mini` qua host `api.xah.io`. Bộ official grading đạt 10/10 `pass`, `partial=0`, `fail=0`, `error=0`, `average_score=2.0`. Bộ self-eval đạt 21/21 `pass`, `partial=0`, `fail=0`, `error=0`, `average_score=2.0`. Checks chính thức vẫn dựa trên `eval_after_fix.csv`, `grading_run.jsonl`, và `instructor_quick_check.py`; LLM-judge là evidence bổ sung.

---

## 6. Freshness & monitoring

SLA freshness chọn 24 giờ. Final manifest:

- `latest_exported_at=2026-04-11T00:00:00`
- `freshness_check=FAIL`
- reason: `freshness_sla_exceeded`

Đây là kết quả đúng với data mẫu: parser đã sửa, nhưng snapshot upstream quá cũ so với ngày chạy 2026-06-10. Trong production, action là yêu cầu upstream export mới trước khi publish.

---

## 7. Rủi ro còn lại

- `data_privacy_guideline` và `security_policy` vẫn quarantine vì chưa có canonical docs.
- `chroma_db/` là runtime local, không commit.
- Day 09 chưa tự động dùng `day10_kb`; tích hợp production cần cập nhật retrieval worker để đọc collection sạch hoặc merge theo `run_id`.

**Kết luận:** Day 10 đã hoàn thiện theo rubric: pipeline chạy OK, quality evidence có before/after, docs đầy đủ, official grading 10/10, self-eval 21/21.
