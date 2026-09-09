#!/usr/bin/env python3
"""Audit Ainmo's indexed autocomplete against the existing real-address corpus.

For each known Catastro address this probes realistic truncation points, records
latency and rank, and verifies that the expected lot reaches the top eight.
"""

from __future__ import annotations

import argparse
import csv
import json
import ssl
import statistics
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import certifi

USER_AGENT = "AinmoSuggestAudit/1.0 (owner-operated QA; https://ainmo.uk)"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
FIELDS = [
    "case_id",
    "input_address",
    "expected_lotcodigo",
    "probe_chars",
    "probe_text",
    "http_ok",
    "index_ready",
    "suggestion_count",
    "expected_found",
    "expected_rank",
    "latency_ms",
    "server_elapsed_ms",
    "normalized_query",
    "returned_lot_codes",
    "returned_addresses",
    "error",
]


def request_json(url: str, params: dict[str, Any], timeout: float = 10) -> dict:
    request = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(
        request, context=SSL_CONTEXT, timeout=timeout
    ) as response:
        raw = response.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("response was not a JSON object")
    return payload


def truncations(address: str) -> list[tuple[int, str]]:
    lengths = {3, 6, 10, len(address)}
    return [
        (length, address[:length])
        for length in sorted(lengths)
        if 0 < length <= len(address)
    ]


def probe(
    base_url: str,
    sample: dict,
    chars: int,
    text: str,
) -> dict:
    row = {field: "" for field in FIELDS}
    row.update(
        {
            "case_id": sample.get("case_id", ""),
            "input_address": sample["input_address"],
            "expected_lotcodigo": sample["expected_lotcodigo"],
            "probe_chars": chars,
            "probe_text": text,
        }
    )
    started = time.perf_counter()
    try:
        payload = request_json(
            base_url.rstrip("/") + "/api/address-suggest",
            {
                "q": text,
                "lat": sample.get("source_lat") or "",
                "lng": sample.get("source_lng") or "",
            },
        )
        row["latency_ms"] = round((time.perf_counter() - started) * 1_000, 1)
        row["http_ok"] = bool(payload.get("ok"))
        row["index_ready"] = payload.get("index_ready")
        row["server_elapsed_ms"] = payload.get("elapsed_ms", "")
        row["normalized_query"] = payload.get("normalized_query", "")
        suggestions = payload.get("suggestions") or []
        row["suggestion_count"] = len(suggestions)
        row["returned_lot_codes"] = "|".join(
            str(suggestion.get("lot_code") or "") for suggestion in suggestions
        )
        row["returned_addresses"] = "|".join(
            str(suggestion.get("address") or "") for suggestion in suggestions
        )
        expected = str(sample["expected_lotcodigo"])
        ranks = [
            index + 1
            for index, suggestion in enumerate(suggestions)
            if str(suggestion.get("lot_code")) == expected
        ]
        row["expected_found"] = bool(ranks)
        row["expected_rank"] = ranks[0] if ranks else ""
    except Exception as exc:
        row["latency_ms"] = round((time.perf_counter() - started) * 1_000, 1)
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[min(max(index, 0), len(ordered) - 1)]


def write_summary(rows: list[dict], samples: list[dict], output: Path, base_url: str) -> None:
    times = [float(row["latency_ms"]) for row in rows if row["latency_ms"] != ""]
    server_times = [
        float(row["server_elapsed_ms"])
        for row in rows
        if row["server_elapsed_ms"] != ""
    ]
    by_case: dict[str, list[dict]] = {}
    for row in rows:
        by_case.setdefault(str(row["case_id"]), []).append(row)
    never_found = []
    first_found_chars = []
    full_failures = []
    for sample in samples:
        group = sorted(
            by_case.get(str(sample["case_id"]), []),
            key=lambda item: int(item["probe_chars"]),
        )
        found = [row for row in group if row["expected_found"] is True]
        if found:
            first_found_chars.append(int(found[0]["probe_chars"]))
        else:
            never_found.append(sample)
        full = [
            row
            for row in group
            if int(row["probe_chars"]) == len(sample["input_address"])
        ]
        if not full or full[0]["expected_found"] is not True:
            full_failures.append(sample)
    error_counts = Counter(row["error"] for row in rows if row["error"])
    index_not_ready = sum(row["index_ready"] is False for row in rows)
    lines = [
        "# Ainmo autocomplete audit",
        "",
        f"- Target: {base_url}",
        f"- Real Catastro addresses: **{len(samples)}**",
        f"- Typeahead probes: **{len(rows)}**",
        f"- Endpoint latency p50: **{statistics.median(times):.0f} ms**",
        f"- Endpoint latency p95: **{percentile(times, .95):.0f} ms**",
        (
            f"- Server work p50: **{statistics.median(server_times):.0f} ms**"
            if server_times
            else "- Server work p50: **n/a**"
        ),
        (
            f"- Server work p95: **{percentile(server_times, .95):.0f} ms**"
            if server_times
            else "- Server work p95: **n/a**"
        ),
        f"- Correct lot found at any probe: **{len(samples) - len(never_found)}/{len(samples)}**",
        f"- Correct lot found for full input: **{len(samples) - len(full_failures)}/{len(samples)}**",
        (
            f"- Median characters before first correct suggestion: "
            f"**{statistics.median(first_found_chars):.0f}**"
            if first_found_chars
            else "- Median characters before first correct suggestion: **n/a**"
        ),
        f"- Responses while index was not ready: **{index_not_ready}**",
        f"- Request/JSON errors: **{sum(error_counts.values())}**",
        "",
        "## Addresses that never reached the top 8",
        "",
    ]
    if never_found:
        for sample in never_found[:25]:
            lines.append(
                f"- {sample['input_address']} — lote {sample['expected_lotcodigo']}"
            )
        if len(never_found) > 25:
            lines.append(f"- …and {len(never_found) - 25} more in the CSV.")
    else:
        lines.append("None.")
    lines += ["", "## Full-input failures", ""]
    if full_failures:
        for sample in full_failures[:25]:
            lines.append(
                f"- {sample['input_address']} — lote {sample['expected_lotcodigo']}"
            )
    else:
        lines.append("None.")
    if error_counts:
        lines += ["", "## Request errors", ""]
        for error, count in error_counts.most_common(10):
            lines.append(f"- **{count}×** {error}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://ainmo.uk")
    parser.add_argument(
        "--samples",
        type=Path,
        default=Path("artifacts/address_audit/samples.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/address_suggest_audit"),
    )
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    if not args.samples.exists():
        raise SystemExit(
            f"Missing {args.samples}. Run tools/address_audit.py once or pass --samples."
        )
    raw_samples = json.loads(args.samples.read_text(encoding="utf-8"))
    samples = [
        sample
        for sample in raw_samples
        if sample.get("expected_treatment") != "INVALID"
        and str(sample.get("expected_lotcodigo") or "").isdigit()
    ]
    jobs = [
        (sample, chars, text)
        for sample in samples
        for chars, text in truncations(sample["input_address"])
    ]
    rows = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(probe, args.base_url, sample, chars, text): (sample, chars)
            for sample, chars, text in jobs
        }
        for finished, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            if finished % 100 == 0 or finished == len(futures):
                print(f"completed {finished}/{len(futures)}", flush=True)
    rows.sort(key=lambda row: (str(row["case_id"]), int(row["probe_chars"])))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "results.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    write_summary(
        rows,
        samples,
        args.output_dir / "SUMMARY.md",
        args.base_url.rstrip("/"),
    )
    print(args.output_dir / "SUMMARY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
