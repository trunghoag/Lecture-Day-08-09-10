# Báo Cáo Cá Nhân - Lab Day 10: Data Pipeline & Observability

**Họ và tên:** Day 10 Technical Owner  
**Vai trò:** Ingestion / Cleaning / Embed / Monitoring tổng hợp  
**Ngày nộp:** 2026-06-10  
**Run ID:** `final-day10`

---

## 1. Tôi phụ trách phần nào?

Tôi phụ trách hoàn thiện lớp data pipeline của Day 10 từ raw CSV đến cleaned snapshot, expectation suite, Chroma publish và evidence grading. Các file chính tôi xử lý là `transform/cleaning_rules.py`, `quality/expectations.py`, `monitoring/freshness_check.py`, `eval_retrieval.py`, `grading_run.py` và các tài liệu trong `docs/`. Run production cuối là `final-day10`, tạo `cleaned_records=40` và `quarantine_records=207` từ 247 raw records.

## 2. Một quyết định kỹ thuật

Quyết định quan trọng nhất là giữ cơ chế **halt trước khi publish** cho dữ liệu sai chính sách. Với refund window, nếu chunk stale "14 ngày làm việc" còn trong cleaned data thì expectation `refund_no_stale_14d_window` phải fail và pipeline không được embed trong chế độ production. Chỉ riêng Sprint 3 mới dùng `--skip-validate` để chứng minh incident. Cách này giúp pipeline chặn dữ liệu bẩn trước khi agent Day 09 hoặc retrieval layer đọc vào Chroma.

## 3. Một lỗi hoặc anomaly đã xử lý

Freshness ban đầu báo WARN vì `latest_exported_at` dùng format `YYYY/MM/DDT...`, ví dụ `2026/04/07T00:00:00`, khiến parser không đọc được. Tôi sửa cleaning rule để normalize `exported_at` sang ISO và thêm fallback parser trong `monitoring/freshness_check.py`. Sau fix, final manifest ghi `latest_exported_at=2026-04-11T00:00:00`. Freshness không còn WARN parse; nó trả `FAIL freshness_sla_exceeded`, đúng vì snapshot mẫu thật sự đã quá SLA 24 giờ.

## 4. Bằng chứng trước / sau

Bad run `inject-bad` được chạy bằng:

```bash
python etl_pipeline.py run --run-id inject-bad --no-refund-fix --skip-validate
```

Log ghi `expectation[refund_no_stale_14d_window] FAIL (halt) :: violations=2`, nhưng vẫn embed vì đây là demo có chủ đích. `artifacts/eval/after_inject_bad.csv` có `q_refund_window` với `hits_forbidden=yes`. Sau khi restore bằng `final-day10`, `artifacts/eval/eval_after_fix.csv` đạt 21/21 và `artifacts/eval/grading_run.jsonl` đạt 10/10 official grading.

Tôi cũng bổ sung LLM-judge như evidence mở rộng. Phần này không thay đổi điểm official vì các deterministic checks đã pass, nhưng nó thêm một lớp kiểm tra semantic grounding: với cùng top-k context retrieve được, model judge đánh giá context có đủ bằng chứng để trả lời hay không và ghi `rationale`, `missing_evidence`, `unsupported_claims`. Kết quả cuối: `llm_judge_grading_questions.jsonl` đạt 10/10 pass và `llm_judge_test_questions.jsonl` đạt 21/21 pass với model `gpt-5.4-mini`.

## 5. Cải tiến tiếp theo

Nếu có thêm thời gian, tôi sẽ tích hợp Day 10 collection `day10_kb` vào retrieval worker của Day 09 bằng một config chọn collection theo `run_id` và freshness status. Khi đó agent orchestration không cần copy thủ công Chroma, và có thể từ chối trả lời nếu data snapshot bị stale quá SLA.
