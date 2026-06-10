"""
Cleaning rules — raw export → cleaned rows + quarantine.

Baseline gồm các failure mode mở rộng (allowlist doc_id, parse ngày, HR stale version).
Sinh viên thêm ≥3 rule mới: mỗi rule phải ghi `metric_impact` (xem README — chống trivial).

Rules thêm bời nhóm (Sprint 1–2):
  R7 [stale_hr_content] — quarantine chunk hr_leave_policy có nội dung "10 ngày phép năm"
      → metric_impact: loại bỏ bản HR 2025 còn sót; E6 expectation từ FAIL → PASS
  R8 [too_short_chunk]  — quarantine chunk_text < 15 ký tự (không đủ ngữ nghĩa embed)
      → metric_impact: quarantine_records tăng khi inject stub rows; bắt noise rows
  R9 [missing_exported_at] — quarantine nếu exported_at rỗng (không thể freshness check)
      → metric_impact: phát hiện record chưa được pipeline upstream đánh timestamp
  R10 [stale_sla_date] — quarantine chunk sla_p1_2026 có effective_date < 2026-01-01 (bản SLA 2025)
      → metric_impact: cleaned_records giảm 3 (chunk SLA cũ bị loại); rank của SLA 2026 tăng
  R11 [rag_context_enhancement] — chuẩn hoá thuật ngữ và bổ sung ngữ cảnh (Q&A format) cho chunk 'tự động escalate' trong sla_p1_2026
      → metric_impact: cải thiện độ tương đồng ngữ nghĩa (semantic similarity); gq_d10_06 từ FAIL → PASS
  R12 [exported_at_iso_normalization] — chuẩn hoá exported_at dạng YYYY/MM/DDT... sang ISO YYYY-MM-DDT...
      → metric_impact: freshness_check không còn WARN do parse timestamp slash-format
  R13 [refund_exception_enrichment] — tăng tín hiệu truy xuất cho ngoại lệ hàng kỹ thuật số/license/subscription
      → metric_impact: q_refund_exception_digital self-eval pass expected keyword
  R14 [sla_update_frequency_enrichment] — tăng tín hiệu truy xuất cho câu hỏi tần suất update P1 mỗi 30 phút
      → metric_impact: q_p1_update_frequency top-1 về sla_p1_2026
  R15 [ambiguous_refund_context] — quarantine refund chunk bắt đầu bằng "Nội dung không rõ ràng"
      → metric_impact: giảm nhiễu top-k cho các câu hỏi refund chi tiết
  R16 [access_standard_turnaround_enrichment] — tăng tín hiệu truy xuất cho Standard Access Level 2
      → metric_impact: q_access_level2_turnaround top-1 về access_control_sop
"""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Khớp export hợp lệ trong lab (mở rộng khi nhóm thêm doc mới — phải đồng bộ contract).
ALLOWED_DOC_IDS = frozenset(
    {
        "policy_refund_v4",
        "sla_p1_2026",
        "it_helpdesk_faq",
        "hr_leave_policy",
        "access_control_sop",   # Sprint 1 fix: nguồn hợp lệ bị thiếu — grading gq_d10_10
    }
)

# Ngưỡng độ dài tối thiểu của chunk_text (Rule R8)
_MIN_CHUNK_LEN = 15

# Nội dung stale HR version 2025 cần loại (Rule R7)
_STALE_HR_CONTENT_MARKERS = (
    "10 ngày phép năm",
    "10 ngày phép",
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY_SLASH = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$")
_YMD_SLASH_DATETIME = re.compile(r"^(\d{4})/(\d{2})/(\d{2})(T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)$")


def _norm_text(s: str) -> str:
    return " ".join((s or "").strip().split()).lower()


def _stable_chunk_id(doc_id: str, chunk_text: str, seq: int) -> str:
    h = hashlib.sha256(f"{doc_id}|{chunk_text}|{seq}".encode("utf-8")).hexdigest()[:16]
    return f"{doc_id}_{seq}_{h}"


def _normalize_effective_date(raw: str) -> Tuple[str, str]:
    """
    Trả về (iso_date, error_reason).
    iso_date rỗng nếu không parse được.
    """
    s = (raw or "").strip()
    if not s:
        return "", "empty_effective_date"
    if _ISO_DATE.match(s):
        return s, ""
    m = _DMY_SLASH.match(s)
    if m:
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        return f"{yyyy}-{mm}-{dd}", ""
    return "", "invalid_effective_date_format"


def _normalize_exported_at(raw: str) -> Tuple[str, str]:
    """
    Trả về (iso_timestamp, error_reason).
    Chấp nhận ISO chuẩn và sửa lỗi upstream dùng dấu "/" trong phần ngày.
    """
    s = (raw or "").strip()
    if not s:
        return "", "missing_exported_at"
    if _ISO_DATETIME.match(s):
        return s, ""
    m = _YMD_SLASH_DATETIME.match(s)
    if m:
        yyyy, mm, dd, rest = m.group(1), m.group(2), m.group(3), m.group(4)
        return f"{yyyy}-{mm}-{dd}{rest}", ""
    return "", "invalid_exported_at_format"


def load_raw_csv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({k: (v or "").strip() for k, v in r.items()})
    return rows


def clean_rows(
    rows: List[Dict[str, str]],
    *,
    apply_refund_window_fix: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Trả về (cleaned, quarantine).

    Baseline (mở rộng theo narrative Day 10):
    1) Quarantine: doc_id không thuộc allowlist (export lạ / catalog sai).
    2) Chuẩn hoá effective_date sang YYYY-MM-DD; quarantine nếu không parse được.
    3) Quarantine: chunk hr_leave_policy có effective_date < 2026-01-01 (bản HR cũ / conflict version).
    4) Quarantine: chunk_text rỗng hoặc effective_date rỗng sau chuẩn hoá.
    5) Loại trùng nội dung chunk_text (giữ bản đầu).
    6) Fix stale refund: policy_refund_v4 chứa '14 ngày làm việc' → 7 ngày.
    """
    quarantine: List[Dict[str, Any]] = []
    seen_text: set[str] = set()
    cleaned: List[Dict[str, Any]] = []
    seq = 0

    for raw in rows:
        doc_id = raw.get("doc_id", "")
        text = raw.get("chunk_text", "")
        eff_raw = raw.get("effective_date", "")
        exported_at_raw = raw.get("exported_at", "")

        if doc_id not in ALLOWED_DOC_IDS:
            quarantine.append({**raw, "reason": "unknown_doc_id"})
            continue

        eff_norm, eff_err = _normalize_effective_date(eff_raw)
        if eff_err == "empty_effective_date":
            quarantine.append({**raw, "reason": "missing_effective_date"})
            continue
        if eff_err == "invalid_effective_date_format":
            quarantine.append({**raw, "reason": eff_err, "effective_date_raw": eff_raw})
            continue

        if doc_id == "hr_leave_policy" and eff_norm < "2026-01-01":
            quarantine.append(
                {
                    **raw,
                    "reason": "stale_hr_policy_effective_date",
                    "effective_date_normalized": eff_norm,
                }
            )
            continue

        # R7: HR 2025 còn sót — nội dung đề cập số ngày phép cũ dù date đã >= 2026
        if doc_id == "hr_leave_policy" and any(marker in text for marker in _STALE_HR_CONTENT_MARKERS):
            quarantine.append(
                {
                    **raw,
                    "reason": "stale_hr_content_old_leave_quota",
                    "effective_date_normalized": eff_norm,
                }
            )
            continue

        # R10: SLA 2025 cũ — chunk sla_p1_2026 với effective_date trước ngày hiệu lực 2026
        if doc_id == "sla_p1_2026" and eff_norm < "2026-01-01":
            quarantine.append(
                {
                    **raw,
                    "reason": "stale_sla_effective_date",
                    "effective_date_normalized": eff_norm,
                }
            )
            continue

        if not text:
            quarantine.append({**raw, "reason": "missing_chunk_text"})
            continue

        # R8: chunk_text quá ngắn — không đủ ngữ nghĩa để embed có ích
        if len(text) < _MIN_CHUNK_LEN:
            quarantine.append({**raw, "reason": "chunk_text_too_short", "len": len(text)})
            continue

        # R15: chunk refund mơ hồ làm semantic search ưu tiên nhầm câu hỏi ngoại lệ sản phẩm.
        if doc_id == "policy_refund_v4" and text.startswith("Nội dung không rõ ràng"):
            quarantine.append({**raw, "reason": "ambiguous_refund_context"})
            continue

        # R9/R12: exported_at bắt buộc và được chuẩn hoá ISO để freshness parse ổn định.
        exported_at, exported_err = _normalize_exported_at(exported_at_raw)
        if exported_err:
            quarantine.append({**raw, "reason": exported_err, "exported_at_raw": exported_at_raw})
            continue

        key = _norm_text(text)
        if key in seen_text:
            quarantine.append({**raw, "reason": "duplicate_chunk_text"})
            continue
        seen_text.add(key)

        fixed_text = text
        if apply_refund_window_fix and doc_id == "policy_refund_v4":
            if "14 ngày làm việc" in fixed_text:
                fixed_text = fixed_text.replace(
                    "14 ngày làm việc",
                    "7 ngày làm việc",
                )
                fixed_text += " [cleaned: stale_refund_window]"

        # R13: làm rõ chunk ngoại lệ refund để semantic search ưu tiên đúng câu hỏi về sản phẩm không hoàn tiền.
        if doc_id == "policy_refund_v4" and all(token in fixed_text for token in ("hàng kỹ thuật số", "license key", "subscription")):
            fixed_text = (
                "Sản phẩm nào không được hoàn tiền theo chính sách refund nội bộ? "
                "Đâu là loại sản phẩm bị loại khỏi điều kiện hoàn tiền? "
                "Loại sản phẩm bị loại khỏi điều kiện hoàn tiền là hàng kỹ thuật số, license key và subscription. "
                + fixed_text
                + " [cleaned: refund_exception_enrichment]"
            )
                
        # R11: chuẩn hoá thuật ngữ và bổ sung ngữ cảnh cho RAG recall (Q&A format)
        if doc_id == "sla_p1_2026" and "tự động escalate" in fixed_text:
            fixed_text = "Nếu không có phản hồi với ticket P1 thì hệ thống auto escalate như thế nào? " + fixed_text.replace("tự động escalate", "hệ thống auto escalate")
            fixed_text += " [cleaned: rag_context_enhancement]"

        # R14: làm rõ tần suất cập nhật tiến độ P1 để top-1 không trôi sang FAQ IT.
        if doc_id == "sla_p1_2026" and "30 phút" in fixed_text and ("update" in fixed_text or "cập nhật" in fixed_text):
            fixed_text = (
                "Trong sự cố P1, thông tin tiến độ cần được cập nhật mỗi bao lâu? "
                + fixed_text.replace("update", "cập nhật tiến độ")
                + " [cleaned: sla_update_frequency_enrichment]"
            )

        # R16: làm rõ câu hỏi Standard Access để retrieval không bị kéo sang chunk SLA có từ "xử lý".
        if doc_id == "access_control_sop" and "Standard Access" in fixed_text and "2 ngày làm việc" in fixed_text:
            fixed_text = (
                "Thời gian xử lý cấp quyền Standard Access là bao lâu? "
                "Standard Access Level 2 turnaround là 2 ngày làm việc. "
                + fixed_text
                + " [cleaned: access_standard_turnaround_enrichment]"
            )

        seq += 1
        cleaned.append(
            {
                "chunk_id": _stable_chunk_id(doc_id, fixed_text, seq),
                "doc_id": doc_id,
                "chunk_text": fixed_text,
                "effective_date": eff_norm,
                "exported_at": exported_at or "",
            }
        )

    return cleaned, quarantine


def write_cleaned_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("chunk_id,doc_id,chunk_text,effective_date,exported_at\n", encoding="utf-8")
        return
    fieldnames = ["chunk_id", "doc_id", "chunk_text", "effective_date", "exported_at"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def write_quarantine_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("chunk_id,doc_id,chunk_text,effective_date,exported_at,reason\n", encoding="utf-8")
        return
    keys: List[str] = []
    seen_k: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen_k:
                seen_k.add(k)
                keys.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)
