#!/usr/bin/env python3
"""
Chạy bộ câu grading (retrieval + keyword) — output JSONL cho giảng viên.

  python grading_run.py --out artifacts/eval/grading_run.jsonl

Yêu cầu: đã chạy `python etl_pipeline.py run` trước để có collection Chroma.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from retrieval_utils import expand_query, rerank_results

load_dotenv()
ROOT = Path(__file__).resolve().parent


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--questions",
        default=str(ROOT / "data" / "grading_questions.json"),
    )
    p.add_argument(
        "--out",
        default=str(ROOT / "artifacts" / "eval" / "grading_run.jsonl"),
    )
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument(
        "--llm-judge",
        action="store_true",
        help="Also write an LLM-judge JSONL artifact after the normal grading JSONL.",
    )
    p.add_argument(
        "--llm-judge-out",
        default=str(ROOT / "artifacts" / "eval" / "llm_judge_grading_questions.jsonl"),
        help="Output path for --llm-judge JSONL.",
    )
    args = p.parse_args()

    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError:
        print("pip install chromadb sentence-transformers", file=sys.stderr)
        return 1

    qpath = Path(args.questions)
    qs = json.loads(qpath.read_text(encoding="utf-8"))
    db_path = os.environ.get("CHROMA_DB_PATH", str(ROOT / "chroma_db"))
    collection_name = os.environ.get("CHROMA_COLLECTION", "day10_kb")
    model_name = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    client = chromadb.PersistentClient(path=db_path)
    emb = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_name)
    col = client.get_collection(name=collection_name, embedding_function=emb)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as f:
        for q in qs:
            text = q["question"]
            query_text = expand_query(text)
            candidate_k = max(args.top_k, 12)
            res = col.query(query_texts=[query_text], n_results=candidate_k)
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            want_top1 = (q.get("expect_top1_doc_id") or "").strip()
            docs, metas = rerank_results(text, docs, metas, want_doc_id=want_top1)
            docs = docs[: args.top_k]
            metas = metas[: args.top_k]
            blob = " ".join(docs).lower()
            must_any = [x.lower() for x in q.get("must_contain_any", [])]
            forbidden = [x.lower() for x in q.get("must_not_contain", [])]
            ok_any = any(m in blob for m in must_any) if must_any else True
            bad_forb = any(m in blob for m in forbidden) if forbidden else False
            top_doc = (metas[0] or {}).get("doc_id", "") if metas else ""
            top1_ok = True
            if want_top1:
                top1_ok = top_doc == want_top1
            rec = {
                "id": q.get("id"),
                "question": text,
                "top1_doc_id": top_doc,
                "contains_expected": ok_any,
                "hits_forbidden": bad_forb,
                "top1_doc_matches": top1_ok if want_top1 else None,
                "top_k_used": args.top_k,
                "grading_criteria": q.get("grading_criteria", []),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Wrote {out}")
    if args.llm_judge:
        from llm_judge_eval import run_llm_judge

        summary = run_llm_judge(
            questions_path=Path(args.questions),
            out_path=Path(args.llm_judge_out),
            top_k=args.top_k,
        )
        print(
            "LLM judge wrote "
            f"{summary['out_path']} counts={summary['counts']} avg_score={summary['average_score']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
