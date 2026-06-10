"""
Expectation suite đơn giản (không bắt buộc Great Expectations).

Sinh viên có thể thay bằng GE / pydantic / custom — miễn là có halt có kiểm soát.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass
class ExpectationResult:
    name: str
    passed: bool
    severity: str  # "warn" | "halt"
    detail: str


def run_expectations(cleaned_rows: List[Dict[str, Any]]) -> Tuple[List[ExpectationResult], bool]:
    """
    Trả về (results, should_halt).

    should_halt = True nếu có bất kỳ expectation severity halt nào fail.
    """
    results: List[ExpectationResult] = []

    # E1: có ít nhất 1 dòng sau clean
    ok = len(cleaned_rows) >= 1
    results.append(
        ExpectationResult(
            "min_one_row",
            ok,
            "halt",
            f"cleaned_rows={len(cleaned_rows)}",
        )
    )

    # E2: không doc_id rỗng
    bad_doc = [r for r in cleaned_rows if not (r.get("doc_id") or "").strip()]
    ok2 = len(bad_doc) == 0
    results.append(
        ExpectationResult(
            "no_empty_doc_id",
            ok2,
            "halt",
            f"empty_doc_id_count={len(bad_doc)}",
        )
    )

    # E3: policy refund không được chứa cửa sổ sai 14 ngày (sau khi đã fix)
    bad_refund = [
        r
        for r in cleaned_rows
        if r.get("doc_id") == "policy_refund_v4"
        and "14 ngày làm việc" in (r.get("chunk_text") or "")
    ]
    ok3 = len(bad_refund) == 0
    results.append(
        ExpectationResult(
            "refund_no_stale_14d_window",
            ok3,
            "halt",
            f"violations={len(bad_refund)}",
        )
    )

    # E4: chunk_text đủ dài
    short = [r for r in cleaned_rows if len((r.get("chunk_text") or "")) < 8]
    ok4 = len(short) == 0
    results.append(
        ExpectationResult(
            "chunk_min_length_8",
            ok4,
            "warn",
            f"short_chunks={len(short)}",
        )
    )

    # E5: effective_date đúng định dạng ISO sau clean (phát hiện parser lỏng)
    iso_bad = [
        r
        for r in cleaned_rows
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", (r.get("effective_date") or "").strip())
    ]
    ok5 = len(iso_bad) == 0
    results.append(
        ExpectationResult(
            "effective_date_iso_yyyy_mm_dd",
            ok5,
            "halt",
            f"non_iso_rows={len(iso_bad)}",
        )
    )

    # E6: không còn marker phép năm cũ 10 ngày trên doc HR (conflict version sau clean)
    bad_hr_annual = [
        r
        for r in cleaned_rows
        if r.get("doc_id") == "hr_leave_policy"
        and "10 ngày phép năm" in (r.get("chunk_text") or "")
    ]
    ok6 = len(bad_hr_annual) == 0
    results.append(
        ExpectationResult(
            "hr_leave_no_stale_10d_annual",
            ok6,
            "halt",
            f"violations={len(bad_hr_annual)}",
        )
    )

    # E7: access_control_sop phải có ít nhất 1 chunk (nguồn bắt buộc cho grading gq_d10_10)
    acl_rows = [r for r in cleaned_rows if r.get("doc_id") == "access_control_sop"]
    ok7 = len(acl_rows) >= 1
    results.append(
        ExpectationResult(
            "access_control_sop_has_data",
            ok7,
            "halt",
            f"access_control_sop_chunks={len(acl_rows)}",
        )
    )

    # E8: không có chunk nào thiếu exported_at (bắt buộc cho freshness monitoring)
    no_ts = [r for r in cleaned_rows if not (r.get("exported_at") or "").strip()]
    ok8 = len(no_ts) == 0
    results.append(
        ExpectationResult(
            "all_rows_have_exported_at",
            ok8,
            "halt",
            f"missing_exported_at={len(no_ts)}",
        )
    )

    # E9: exported_at phải parse được theo ISO datetime sau clean (R12)
    ts_bad = [
        r
        for r in cleaned_rows
        if not re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$",
            (r.get("exported_at") or "").strip(),
        )
    ]
    ok9 = len(ts_bad) == 0
    results.append(
        ExpectationResult(
            "exported_at_iso_datetime",
            ok9,
            "halt",
            f"non_iso_exported_at={len(ts_bad)}",
        )
    )

    # E10: refund exception phải còn chunk rõ để trả lời câu hỏi sản phẩm không được hoàn tiền
    refund_exception = [
        r
        for r in cleaned_rows
        if r.get("doc_id") == "policy_refund_v4"
        and all(token in (r.get("chunk_text") or "") for token in ("hàng kỹ thuật số", "license key", "subscription"))
    ]
    ok10 = len(refund_exception) >= 1
    results.append(
        ExpectationResult(
            "refund_exception_has_digital_product_keywords",
            ok10,
            "halt",
            f"refund_exception_chunks={len(refund_exception)}",
        )
    )

    # E11: SLA P1 phải có chunk rõ về cập nhật tiến độ mỗi 30 phút
    p1_update = [
        r
        for r in cleaned_rows
        if r.get("doc_id") == "sla_p1_2026"
        and "30 phút" in (r.get("chunk_text") or "")
        and "cập nhật" in (r.get("chunk_text") or "")
    ]
    ok11 = len(p1_update) >= 1
    results.append(
        ExpectationResult(
            "sla_p1_update_frequency_has_30m_context",
            ok11,
            "halt",
            f"sla_p1_update_chunks={len(p1_update)}",
        )
    )

    # E12: không để refund chunk mơ hồ lọt vào cleaned vì gây nhiễu semantic search
    ambiguous_refund = [
        r
        for r in cleaned_rows
        if r.get("doc_id") == "policy_refund_v4"
        and (r.get("chunk_text") or "").startswith("Nội dung không rõ ràng")
    ]
    ok12 = len(ambiguous_refund) == 0
    results.append(
        ExpectationResult(
            "refund_no_ambiguous_context_chunks",
            ok12,
            "halt",
            f"ambiguous_refund_chunks={len(ambiguous_refund)}",
        )
    )

    # E13: access_control_sop phải có chunk rõ về Standard Access Level 2 turnaround
    standard_access = [
        r
        for r in cleaned_rows
        if r.get("doc_id") == "access_control_sop"
        and "Standard Access" in (r.get("chunk_text") or "")
        and "2 ngày làm việc" in (r.get("chunk_text") or "")
    ]
    ok13 = len(standard_access) >= 1
    results.append(
        ExpectationResult(
            "access_standard_turnaround_has_2d_context",
            ok13,
            "halt",
            f"access_standard_turnaround_chunks={len(standard_access)}",
        )
    )

    halt = any(not r.passed and r.severity == "halt" for r in results)
    return results, halt
