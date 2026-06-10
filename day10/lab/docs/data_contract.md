# Data contract - Lab Day 10

> Đồng bộ với `contracts/data_contract.yaml` và run production `final-day10`.

---

## 1. Source map

| Nguồn | Phương thức ingest | Failure mode chính | Metric / alert |
|-------|--------------------|--------------------|----------------|
| `policy_refund_v4` | CSV export từ hệ thống chính sách CS | Stale refund 14 ngày, duplicate, chunk mơ hồ, exception khó retrieve | `refund_no_stale_14d_window`, `refund_no_ambiguous_context_chunks`, `refund_exception_has_digital_product_keywords` |
| `sla_p1_2026` | CSV export từ hệ thống SLA/ticketing | SLA 2025 lẫn vào 2026, P1 update frequency khó retrieve | `stale_sla_effective_date`, `sla_p1_update_frequency_has_30m_context` |
| `it_helpdesk_faq` | CSV export từ IT Helpdesk | Duplicate, empty chunk | `duplicate_chunk_text`, `missing_chunk_text` |
| `hr_leave_policy` | CSV export từ HR | Conflict version 2025 vs 2026, "10 ngày phép năm" stale | `stale_hr_policy_effective_date`, `stale_hr_content_old_leave_quota`, `hr_leave_no_stale_10d_annual` |
| `access_control_sop` | CSV export từ IAM/Access Control | Thiếu allowlist baseline, Standard Access query dễ trôi | `access_control_sop_has_data`, `access_standard_turnaround_has_2d_context` |
| `data_privacy_guideline` | CSV export chưa đăng ký canonical | Không có source document trong `data/docs` | `unknown_doc_id=29`, quarantine đúng |
| `security_policy` | CSV export chưa đăng ký canonical | Không có source document trong `data/docs` | `unknown_doc_id=18`, quarantine đúng |
| `legacy_catalog_xyz_zzz` / `invalid_doc_*` | Export lỗi hoặc legacy | Catalog sai, không có source of truth | `unknown_doc_id`, quarantine đúng |

---

## 2. Schema cleaned

| Cột | Kiểu | Bắt buộc | Ghi chú |
|-----|------|----------|---------|
| `chunk_id` | string | Có | Stable id: `doc_id + seq + sha256(doc_id|chunk_text|seq)` |
| `doc_id` | string | Có | Phải thuộc allowlist trong `cleaning_rules.py` |
| `chunk_text` | string | Có | Tối thiểu 15 ký tự, có thể được enrichment phục vụ retrieval |
| `effective_date` | date | Có | ISO `YYYY-MM-DD`; parser chấp nhận `DD/MM/YYYY` rồi normalize |
| `exported_at` | datetime | Có | ISO datetime; slash date `YYYY/MM/DDT...` được normalize sang `YYYY-MM-DDT...` |

Final run `final-day10`:
- `raw_records=247`
- `cleaned_records=40`
- `quarantine_records=207`
- `latest_exported_at=2026-04-11T00:00:00`

---

## 3. Quy tắc quarantine/drop

| Reason | Hành động | Owner review |
|--------|-----------|--------------|
| `unknown_doc_id` | Quarantine cho tới khi có canonical source | Ingestion Owner |
| `missing_effective_date` | Quarantine, yêu cầu upstream bổ sung | Ingestion Owner |
| `invalid_effective_date_format` | Quarantine, yêu cầu upstream chuẩn hóa | Ingestion Owner |
| `missing_exported_at` | Quarantine vì không đo freshness được | Ingestion Owner |
| `invalid_exported_at_format` | Quarantine vì không normalize được timestamp | Ingestion Owner |
| `stale_hr_policy_effective_date` | Quarantine HR trước 2026 | Cleaning Owner |
| `stale_hr_content_old_leave_quota` | Quarantine HR còn marker 10 ngày phép năm | Cleaning Owner |
| `stale_sla_effective_date` | Quarantine SLA trước 2026 | Cleaning Owner |
| `ambiguous_refund_context` | Quarantine refund chunk mơ hồ | Cleaning Owner |
| `duplicate_chunk_text` | Drop duplicate, giữ bản đầu theo ingestion order | Cleaning Owner |
| `missing_chunk_text` | Drop vì không có nội dung embed | Ingestion Owner |

Final quarantine counts:

| Reason | Count |
|--------|-------|
| `unknown_doc_id` | 109 |
| `duplicate_chunk_text` | 32 |
| `stale_sla_effective_date` | 23 |
| `stale_hr_policy_effective_date` | 22 |
| `stale_hr_content_old_leave_quota` | 8 |
| `missing_effective_date` | 6 |
| `missing_chunk_text` | 5 |
| `ambiguous_refund_context` | 2 |

---

## 4. Canonical versions

| Tài liệu | Source of truth | Version hiện tại |
|----------|-----------------|------------------|
| Chính sách hoàn tiền | `data/docs/policy_refund_v4.txt` | v4, refund window 7 ngày làm việc |
| SLA P1 | `data/docs/sla_p1_2026.txt` | 2026, first response 15 phút, resolution 4 giờ |
| IT Helpdesk FAQ | `data/docs/it_helpdesk_faq.txt` | Internal FAQ |
| HR Leave Policy | `data/docs/hr_leave_policy.txt` | 2026, annual leave 12/15/18 ngày |
| Access Control SOP | `data/docs/access_control_sop.txt` | Current, Level 4 requires IT Manager + CISO |

Canonical rule: khi có conflict cùng `doc_id`, ưu tiên version hiện hành theo `effective_date` và expectation content marker. Không thêm source mới vào allowlist nếu chưa có canonical doc.
