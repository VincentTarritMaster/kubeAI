#!/usr/bin/env python3
"""Run the controlled FR/EN A/B/C benchmark against a live agent."""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "docs/quality-benchmark.json"


def post(url, key, question, timeout):
    body = json.dumps({"question": question}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/diagnose", data=body, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json; charset=utf-8"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        elapsed = time.perf_counter() - started
    return payload, elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("KUBEAI_AGENT_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--out", default=str(ROOT / "docs/quality-results.json"))
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    key = os.getenv("KUBEAI_API_KEY", "").strip()
    if not key:
        raise SystemExit("KUBEAI_API_KEY est requis dans l'environnement")
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    results, facts_total, facts_ok = [], 0, 0
    for case in cases:
        try:
            payload, elapsed = post(args.url, key, case["question"], args.timeout)
            answer = str(payload.get("answer", ""))
            error = None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            answer, elapsed, error = "", 0.0, str(exc)
        found = [needle for needle in case["required"] if needle.lower() in answer.lower()]
        missing = [needle for needle in case["required"] if needle not in found]
        facts_total += len(case["required"])
        facts_ok += len(found)
        result = {
            "id": case["id"], "language": case["language"],
            "elapsed_seconds": round(elapsed, 2), "under_60s": elapsed < 60,
            "facts_found": found, "facts_missing": missing, "error": error,
            "answer_chars": len(answer),
        }
        results.append(result)
        print(f"{case['id']}: {len(found)}/{len(case['required'])} facts, "
              f"{elapsed:.1f}s, under60={elapsed < 60}")
        if error:
            print("  error: " + error)
    accuracy = facts_ok / facts_total if facts_total else 0.0
    latencies = [item["elapsed_seconds"] for item in results if not item["error"]]
    summary = {
        "facts_total": facts_total, "facts_ok": facts_ok,
        "accuracy": round(accuracy, 4),
        "accuracy_target_90_percent": accuracy >= 0.90,
        "latency_average_seconds": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "latency_max_seconds": max(latencies) if latencies else None,
        "all_under_60s": bool(latencies) and max(latencies) < 60,
        "cases": results,
    }
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in (
        "facts_total", "facts_ok", "accuracy", "accuracy_target_90_percent",
        "latency_average_seconds", "latency_max_seconds", "all_under_60s")}, ensure_ascii=False, indent=2))
    return 0 if summary["accuracy_target_90_percent"] and summary["all_under_60s"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
