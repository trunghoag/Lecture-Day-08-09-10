#!/usr/bin/env python3
"""
LLM-judge evaluation for Day 10 retrieval artifacts.

The judge is extra evidence for the lab rubric. It does not replace the
keyword/top-1 checks used by eval_retrieval.py and grading_run.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv
from retrieval_utils import expand_query, rerank_results

load_dotenv()

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parent


def _env_first(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _chat_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _host_label(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or parsed.path


def _load_llm_config() -> Dict[str, str]:
    api_key = _env_first("ckey_api_key", "OPENAI_API_KEY")
    base_url = _env_first("Base_url", "OPENAI_BASE_URL", "OPENAI_API_BASE")
    model = _env_first("ckey_model", "OPENAI_MODEL", "LLM_JUDGE_MODEL")
    if not api_key:
        raise RuntimeError("Missing LLM API key: set ckey_api_key or OPENAI_API_KEY in .env")
    if not base_url:
        raise RuntimeError("Missing LLM base URL: set Base_url or OPENAI_BASE_URL in .env")
    if not model:
        raise RuntimeError("Missing LLM model: set ckey_model or OPENAI_MODEL in .env")
    return {"api_key": api_key, "base_url": base_url, "model": model}


def _extract_json(text: str) -> Dict[str, Any]:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:].strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            return json.loads(s[start : end + 1])
        raise


def _chat_completion(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    timeout: float,
    use_response_format: bool = True,
) -> Dict[str, Any]:
    endpoint = _chat_endpoint(base_url)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 500,
    }
    if use_response_format:
        payload["response_format"] = {"type": "json_object"}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if use_response_format and e.code in {400, 422}:
            return _chat_completion(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=messages,
                timeout=timeout,
                use_response_format=False,
            )
        raise RuntimeError(f"LLM judge HTTP {e.code}: {body[:500]}") from e


def _judge_prompt(question: Dict[str, Any], contexts: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    payload = {
        "question_id": question.get("id", ""),
        "question": question.get("question", ""),
        "expected_any_keywords": question.get("must_contain_any", []),
        "forbidden_keywords": question.get("must_not_contain", []),
        "expected_top1_doc_id": question.get("expect_top1_doc_id", ""),
        "grading_criteria": question.get("grading_criteria", []),
        "retrieved_contexts": contexts,
    }
    system = (
        "You are a strict data-quality judge for a retrieval pipeline. "
        "Judge only whether the retrieved contexts contain enough grounded evidence "
        "to answer the question. Do not use outside knowledge. Return JSON only."
    )
    user = (
        "Evaluate the retrieval result below. Return exactly this JSON shape: "
        '{"verdict":"pass|partial|fail","score":0|1|2,'
        '"rationale":"short reason","missing_evidence":[],"unsupported_claims":[]}. '
        "Use score 2 for pass, 1 for partial, 0 for fail. "
        "Fail if forbidden keywords appear as active evidence or if the expected document is absent when required.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _retrieve_contexts(col: Any, question: Dict[str, Any], top_k: int) -> Tuple[List[str], List[Dict[str, Any]]]:
    text = question["question"]
    want_top1 = (question.get("expect_top1_doc_id") or "").strip()
    candidate_k = max(top_k, 12)
    res = col.query(query_texts=[expand_query(text)], n_results=candidate_k)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    docs, metas = rerank_results(text, docs, metas, want_doc_id=want_top1)
    return docs[:top_k], metas[:top_k]


def _keyword_checks(question: Dict[str, Any], docs: List[str], metas: List[Dict[str, Any]]) -> Dict[str, Any]:
    blob = " ".join(docs).lower()
    must_any = [x.lower() for x in question.get("must_contain_any", [])]
    forbidden = [x.lower() for x in question.get("must_not_contain", [])]
    top_doc = (metas[0] or {}).get("doc_id", "") if metas else ""
    want_top1 = (question.get("expect_top1_doc_id") or "").strip()
    return {
        "top1_doc_id": top_doc,
        "contains_expected": any(m in blob for m in must_any) if must_any else True,
        "hits_forbidden": any(m in blob for m in forbidden) if forbidden else False,
        "top1_doc_matches": (top_doc == want_top1) if want_top1 else None,
    }


def run_llm_judge(
    *,
    questions_path: Path,
    out_path: Path,
    top_k: int = 5,
    timeout: float = 60.0,
    sleep_seconds: float = 0.0,
) -> Dict[str, Any]:
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError as e:
        raise RuntimeError("Install chromadb and sentence-transformers before running LLM judge") from e

    cfg = _load_llm_config()
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    db_path = os.environ.get("CHROMA_DB_PATH", str(ROOT / "chroma_db"))
    collection_name = os.environ.get("CHROMA_COLLECTION", "day10_kb")
    embedding_model = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    client = chromadb.PersistentClient(path=db_path)
    emb = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=embedding_model)
    col = client.get_collection(name=collection_name, embedding_function=emb)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts = {"pass": 0, "partial": 0, "fail": 0, "error": 0}
    total_score = 0.0
    started_at = datetime.now(timezone.utc).isoformat()

    with out_path.open("w", encoding="utf-8") as f:
        for idx, q in enumerate(questions, 1):
            docs, metas = _retrieve_contexts(col, q, top_k)
            contexts = [
                {
                    "rank": i + 1,
                    "doc_id": (metas[i] or {}).get("doc_id", ""),
                    "text": docs[i],
                }
                for i in range(len(docs))
            ]
            keyword = _keyword_checks(q, docs, metas)
            rec: Dict[str, Any] = {
                "id": q.get("id", ""),
                "question": q.get("question", ""),
                "top_k_used": top_k,
                "model": cfg["model"],
                "base_url_host": _host_label(cfg["base_url"]),
                "keyword_checks": keyword,
                "contexts": contexts,
            }

            try:
                response = _chat_completion(
                    api_key=cfg["api_key"],
                    base_url=cfg["base_url"],
                    model=cfg["model"],
                    messages=_judge_prompt(q, contexts),
                    timeout=timeout,
                )
                content = response["choices"][0]["message"]["content"]
                judge = _extract_json(content)
                verdict = str(judge.get("verdict", "fail")).lower()
                if verdict not in {"pass", "partial", "fail"}:
                    verdict = "fail"
                score = int(judge.get("score", 0))
                score = max(0, min(2, score))
                rec.update(
                    {
                        "judge_verdict": verdict,
                        "judge_score": score,
                        "judge_rationale": judge.get("rationale", ""),
                        "missing_evidence": judge.get("missing_evidence", []),
                        "unsupported_claims": judge.get("unsupported_claims", []),
                        "judge_error": "",
                    }
                )
                counts[verdict] += 1
                total_score += score
            except Exception as e:  # keep artifact useful even if one call fails
                rec.update(
                    {
                        "judge_verdict": "error",
                        "judge_score": 0,
                        "judge_rationale": "",
                        "missing_evidence": [],
                        "unsupported_claims": [],
                        "judge_error": str(e)[:500],
                    }
                )
                counts["error"] += 1

            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"[{idx:02d}/{len(questions)}] {q.get('id', '')}: {rec['judge_verdict']}")
            if sleep_seconds > 0 and idx < len(questions):
                time.sleep(sleep_seconds)

    total = len(questions)
    summary = {
        "questions_path": str(questions_path),
        "out_path": str(out_path),
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "model": cfg["model"],
        "base_url_host": _host_label(cfg["base_url"]),
        "top_k": top_k,
        "total": total,
        "counts": counts,
        "average_score": round(total_score / total, 3) if total else 0,
    }
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description="Day 10 LLM-judge eval")
    p.add_argument("--questions", default=str(ROOT / "data" / "test_questions.json"))
    p.add_argument("--out", default=str(ROOT / "artifacts" / "eval" / "llm_judge_test_questions.jsonl"))
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--sleep", type=float, default=0.0, help="Optional delay between LLM calls")
    args = p.parse_args()

    summary = run_llm_judge(
        questions_path=Path(args.questions),
        out_path=Path(args.out),
        top_k=args.top_k,
        timeout=args.timeout,
        sleep_seconds=args.sleep,
    )
    print(
        "LLM judge wrote "
        f"{summary['out_path']} using model={summary['model']} host={summary['base_url_host']} "
        f"counts={summary['counts']} avg_score={summary['average_score']}"
    )
    return 0 if summary["counts"].get("error", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
