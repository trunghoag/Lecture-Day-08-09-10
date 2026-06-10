# Runbook - Lab Day 10

**Run production:** `final-day10`  
**Primary log:** `artifacts/logs/run_final-day10.log`  
**Manifest:** `artifacts/manifests/manifest_final-day10.json`

---

## Symptom

Các triệu chứng người dùng hoặc agent có thể thấy:
- Trả lời refund window là **14 ngày làm việc** thay vì **7 ngày làm việc**.
- Không trả lời được ngoại lệ refund cho hàng kỹ thuật số/license/subscription.
- Câu hỏi P1 update frequency bị retrieve nhầm sang IT FAQ.
- Câu hỏi Standard Access Level 2 bị retrieve nhầm sang SLA.
- Freshness báo `FAIL` vì source snapshot quá cũ.

---

## Detection

| Tín hiệu | Công cụ | Kết luận |
|----------|---------|----------|
| `expectation[refund_no_stale_14d_window] FAIL` | `etl_pipeline.py run` | Có stale refund data, phải halt nếu không phải inject demo |
| `hits_forbidden=yes` ở `q_refund_window` | `eval_retrieval.py` | Vector store đã bị nhiễm chunk 14 ngày |
| `GRADE_CHECK[...] FAIL` | `instructor_quick_check.py` | Grading official không đạt |
| `freshness_check=FAIL` | `etl_pipeline.py freshness` hoặc log run | Source snapshot quá SLA |
| `quarantine_records` tăng bất thường | manifest/log/quarantine CSV | Có vấn đề upstream schema, doc_id, duplicate, stale version |

---

## Diagnosis

| Bước | Việc làm | Kết quả mong đợi |
|------|----------|------------------|
| 1 | Mở `artifacts/manifests/manifest_final-day10.json` | Thấy `raw_records=247`, `cleaned_records=40`, `quarantine_records=207`, `latest_exported_at=2026-04-11T00:00:00` |
| 2 | Mở `artifacts/logs/run_final-day10.log` | Tất cả halt expectations OK; freshness FAIL chỉ do SLA exceeded |
| 3 | Mở `artifacts/quarantine/quarantine_final-day10.csv` | Các reason chính: `unknown_doc_id=109`, `duplicate_chunk_text=32`, `stale_sla_effective_date=23`, `stale_hr_policy_effective_date=22`, `ambiguous_refund_context=2` |
| 4 | Chạy `python eval_retrieval.py --out artifacts/eval/eval_after_fix.csv` | 21/21 dòng có `contains_expected=yes`, `hits_forbidden=no`, `top1_doc_expected=yes` |
| 5 | Chạy `python grading_run.py --out artifacts/eval/grading_run.jsonl` | 10/10 official pass |
| 6 | Nếu nghi index bẩn, tìm `embed_prune_removed` trong log | Có prune khi snapshot thay đổi; index không giữ chunk cũ |

---

## Mitigation

| Tình huống | Hành động |
|------------|-----------|
| Refund stale 14 ngày xuất hiện | Chạy pipeline chuẩn không `--skip-validate`; expectation halt sẽ chặn publish |
| Muốn chứng minh incident | Chạy `python etl_pipeline.py run --run-id inject-bad --no-refund-fix --skip-validate`, sau đó eval vào `after_inject_bad.csv` |
| Sau inject cần phục hồi | Chạy lại `python etl_pipeline.py run --run-id final-day10` để restore Chroma snapshot sạch |
| Freshness FAIL | Kiểm tra source export mới; nếu dùng data mẫu thì ghi rõ SLA fail do snapshot cũ, không phải parser |
| Unknown doc_id tăng | Không thêm allowlist nếu chưa có canonical doc; review `data_privacy_guideline`/`security_policy` trước |

---

## Prevention

- Giữ các halt expectations mới: `exported_at_iso_datetime`, `refund_exception_has_digital_product_keywords`, `sla_p1_update_frequency_has_30m_context`, `refund_no_ambiguous_context_chunks`, `access_standard_turnaround_has_2d_context`.
- Không dùng `--skip-validate` ngoài Sprint 3 demo.
- Mỗi release phải nộp cùng manifest, log, cleaned/quarantine CSV và eval/grading artifact.
- Khi nối sang Day 09, retrieval worker phải đọc collection có `run_id` mới nhất và kiểm freshness trước khi trả lời.
