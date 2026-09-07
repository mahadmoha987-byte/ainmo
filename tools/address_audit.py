#!/usr/bin/env python3
"""Repeatable production audit for Ainmo's Bogotá address and calculation APIs.

The sampler uses only real UAECD Catastro address points spatially contained in
official POT treatment polygons. It deliberately varies only the textual format
of those real addresses. A small invalid set is appended to test safe failures.

Usage:
  python3 tools/address_audit.py --base-url https://ainmo.uk --real 290
  python3 tools/address_audit.py --samples artifacts/address_audit/samples.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import ssl
import statistics
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

POT_LAYER = (
    "https://services7.arcgis.com/lsxbLWF2l19Rmhqj/arcgis/rest/services/"
    "POT_Bogota_Decreto_555_2021/FeatureServer/15"
)
ADDRESS_LAYER = (
    "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/"
    "catastro/placadomiciliaria/MapServer/0"
)
TREATMENTS = [
    "CONSOLIDACION",
    "RENOVACION",
    "CONSERVACION",
    "DESARROLLO",
    "MEJORAMIENTO INTEGRAL",
]
INVALID_INPUTS = [
    "Bogotá",
    "Colombia",
    "Calle cualquiera 999999",
    "###",
    "12345",
    "Carrera",
    "Calle 60 Carrera 7",
    "Av. Cra. 68 # 40-15",  # known real-looking but unresolved plate
    "CL - # -",
    "not an address",
]
USER_AGENT = "AinmoAddressAudit/1.0 (owner-operated QA; https://ainmo.uk)"
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE
OUTPUT_FIELDS = [
    "case_id", "input_address", "source_address", "format_variant",
    "expected_treatment", "expected_lotcodigo", "source_block", "source_lat",
    "source_lng", "geocode_ok", "resolution",
    "match_type", "match_confidence", "candidate_count", "candidate_source",
    "candidate_label", "candidate_lotcodigo", "candidate_lat", "candidate_lng",
    "expected_candidate_found", "expected_candidate_rank", "coordinate_drift_m",
    "coordinate_adjusted_to_lot", "address_point_lat", "address_point_lng",
    "degraded_geocode",
    "geocode_ms", "geocode_error", "calc_ok", "calc_state", "calc_error",
    "calc_ms", "treatment", "lot_area_m2", "ic_value", "io_value",
    "max_buildable_m2", "binding_constraint", "null_count", "nan_count",
    "math_check", "math_delta_m2", "treatment_mismatch", "lot_mismatch",
    "retry_recovered", "http_or_json_error", "anomalies",
]
_request_lock = threading.Lock()
_last_request_at = 0.0


def request_json(url: str, params: dict[str, Any], timeout: float = 35, min_gap: float = 0.04) -> dict:
    """GET JSON with a small process-wide gap and a descriptive User-Agent."""
    global _last_request_at
    with _request_lock:
        wait = min_gap - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()
    encoded = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{encoded}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, context=SSL_CONTEXT, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("JSON response was not an object")
    return parsed


def audit_request_json(url: str, params: dict[str, Any]) -> tuple[dict, str]:
    """Retry one transient production failure while preserving it in the report."""
    try:
        return request_json(url, params), ""
    except Exception as first:
        time.sleep(.25)
        try:
            return request_json(url, params), f"{type(first).__name__}: {first}"
        except Exception as second:
            raise RuntimeError(
                f"first={type(first).__name__}: {first}; retry={type(second).__name__}: {second}"
            ) from second


def point_in_polygon(x: float, y: float, rings: list[list[list[float]]]) -> bool:
    inside = False
    for ring in rings:
        j = len(ring) - 1
        for i, (xi, yi) in enumerate(ring):
            xj, yj = ring[j]
            if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-20) + xi:
                inside = not inside
            j = i
    return inside


def treatment_object_ids(treatment: str) -> list[int]:
    data = request_json(POT_LAYER + "/query", {
        "where": f"TRATAMIENTO='{treatment}'",
        "returnIdsOnly": "true",
        "f": "json",
    })
    return [int(x) for x in data.get("objectIds", [])]


def treatment_polygons(object_ids: list[int]) -> list[dict]:
    polygons: list[dict] = []
    for start in range(0, len(object_ids), 20):
        data = request_json(POT_LAYER + "/query", {
            "objectIds": ",".join(map(str, object_ids[start:start + 20])),
            "outFields": "OBJECTID,TRATAMIENTO",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
        })
        polygons.extend(data.get("features", []))
    return polygons


def addresses_in_polygon(feature: dict, limit: int = 500) -> list[dict]:
    rings = (feature.get("geometry") or {}).get("rings") or []
    points = [p for ring in rings for p in ring]
    if not points:
        return []
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    envelope = {"xmin": min(xs), "ymin": min(ys), "xmax": max(xs), "ymax": max(ys)}
    data = request_json(ADDRESS_LAYER + "/query", {
        "where": "1=1",
        "geometry": json.dumps(envelope, separators=(",", ":")),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PDONVIAL,PDOTEXTO,PDOCLOTE",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": limit,
        "f": "json",
    })
    output = []
    for item in data.get("features", []):
        geometry = item.get("geometry") or {}
        x, y = geometry.get("x"), geometry.get("y")
        attrs = item.get("attributes") or {}
        if x is None or y is None or not point_in_polygon(x, y, rings):
            continue
        if not attrs.get("PDONVIAL") or not attrs.get("PDOTEXTO"):
            continue
        output.append({"attributes": attrs, "geometry": geometry})
    return output


def canonical_address(attrs: dict) -> str:
    via = str(attrs["PDONVIAL"]).strip()
    text = str(attrs["PDOTEXTO"]).strip()
    parts = text.split()
    if len(parts) >= 2:
        text = f"{parts[0]}-{parts[1]}" + ((" " + " ".join(parts[2:])) if len(parts) > 2 else "")
    return f"{via} # {text}"


def is_searchable_street_plate(attrs: dict) -> bool:
    """Exclude unit/interior records that lack a cross street and door number."""
    parts = str(attrs.get("PDOTEXTO") or "").strip().split()
    return len(parts) >= 2 and bool(re.match(r"^\d", parts[0])) and bool(re.match(r"^\d", parts[1]))


def formatted_address(attrs: dict, variant_index: int) -> tuple[str, str]:
    via = str(attrs["PDONVIAL"]).strip()
    text = str(attrs["PDOTEXTO"]).strip()
    code, _, number = via.partition(" ")
    full = {"CL": "Calle", "KR": "Carrera", "AK": "Avenida Carrera", "AC": "Avenida Calle", "DG": "Diagonal", "TV": "Transversal"}.get(code, code)
    dotted = {"CL": "Cl.", "KR": "Cra.", "AK": "Av. Cra.", "AC": "Av. Cl.", "DG": "Dg.", "TV": "Tv."}.get(code, code)
    parts = text.split()
    hyphen = f"{parts[0]}-{parts[1]}" + ((" " + " ".join(parts[2:])) if len(parts) > 2 else "") if len(parts) >= 2 else text
    variants = [
        ("canonical", f"{via} # {hyphen}"),
        ("abbreviated", f"{dotted} {number} #{hyphen}"),
        ("spelled_out", f"{full} {number} # {hyphen}"),
        ("without_hash", f"{full} {number} {text}"),
        ("no_separator_spaces", f"{dotted} {number}#{hyphen}"),
        ("no_punctuation", f"{via} {text}"),
    ]
    return variants[variant_index % len(variants)]


def generate_samples(real_count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    per_treatment = real_count // len(TREATMENTS)
    remainder = real_count % len(TREATMENTS)
    samples: list[dict] = []
    seen: set[tuple[str, str]] = set()
    seen_lots: set[tuple[str, str]] = set()
    for treatment_index, treatment in enumerate(TREATMENTS):
        quota = per_treatment + (1 if treatment_index < remainder else 0)
        ids = treatment_object_ids(treatment)
        rng.shuffle(ids)
        # Query a random subset of polygons until enough contained address points exist.
        for start in range(0, min(len(ids), 240), 20):
            polygons = treatment_polygons(ids[start:start + 20])
            rng.shuffle(polygons)
            for polygon in polygons:
                try:
                    addresses = addresses_in_polygon(polygon)
                except Exception:
                    continue
                rng.shuffle(addresses)
                accepted_from_polygon = 0
                for item in addresses:
                    attrs = item["attributes"]
                    if not is_searchable_street_plate(attrs):
                        continue
                    key = (str(attrs.get("PDONVIAL", "")).strip(), str(attrs.get("PDOTEXTO", "")).strip())
                    if key in seen:
                        continue
                    lot_key = (treatment, str(attrs.get("PDOCLOTE") or "").strip())
                    if lot_key[1] and lot_key in seen_lots:
                        continue
                    seen.add(key)
                    if lot_key[1]:
                        seen_lots.add(lot_key)
                    variant, input_address = formatted_address(attrs, len(samples))
                    samples.append({
                        "case_id": f"real-{len(samples)+1:03d}",
                        "input_address": input_address,
                        "source_address": canonical_address(attrs),
                        "format_variant": variant,
                        "expected_treatment": treatment,
                        "expected_lotcodigo": str(attrs.get("PDOCLOTE") or "").strip(),
                        "source_block": f"{key[0]}|{key[1].split()[0] if key[1].split() else ''}",
                        "source_lat": item["geometry"]["y"],
                        "source_lng": item["geometry"]["x"],
                    })
                    accepted_from_polygon += 1
                    if sum(1 for s in samples if s["expected_treatment"] == treatment) >= quota:
                        break
                    if accepted_from_polygon >= 2:
                        break
                if sum(1 for s in samples if s["expected_treatment"] == treatment) >= quota:
                    break
            if sum(1 for s in samples if s["expected_treatment"] == treatment) >= quota:
                break
        got = sum(1 for s in samples if s["expected_treatment"] == treatment)
        if got < quota:
            raise RuntimeError(f"Only sampled {got}/{quota} real addresses for {treatment}")
    for index, address in enumerate(INVALID_INPUTS, start=1):
        samples.append({
            "case_id": f"invalid-{index:02d}",
            "input_address": address,
            "source_address": "",
            "format_variant": "invalid",
            "expected_treatment": "INVALID",
            "expected_lotcodigo": "",
        })
    return samples


def nested_counts(value: Any) -> tuple[int, int]:
    nulls = nans = 0
    if value is None:
        return 1, 0
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return 0, 1
    if isinstance(value, dict):
        for child in value.values():
            n, a = nested_counts(child); nulls += n; nans += a
    elif isinstance(value, list):
        for child in value:
            n, a = nested_counts(child); nulls += n; nans += a
    return nulls, nans


def first_numeric_metric(metrics: dict, keys: list[str]) -> float | None:
    for key in keys:
        value = metrics.get(key)
        if isinstance(value, dict):
            value = value.get("valor")
        if isinstance(value, (int, float)) and math.isfinite(value):
            return float(value)
    return None


def metric_number(metric: Any, key: str = "valor") -> float | None:
    value = metric.get(key) if isinstance(metric, dict) else metric
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def extract_indices(metrics: dict) -> tuple[float | None, float | None]:
    """Extract explicitly stated IC/IO values without inventing resultante values."""
    ic = first_numeric_metric(metrics, ["indice_construccion", "ic", "ice_max"])
    io = first_numeric_metric(metrics, ["indice_ocupacion", "io"])
    if ic is None:
        for key in (
            "area_construible_max_sin_manzana_completa_m2",
            "area_construible_max_m2",
            "area_construible_maxima_m2",
        ):
            metric = metrics.get(key)
            if not isinstance(metric, dict):
                continue
            ic = metric_number(metric, "ice_max")
            if ic is None:
                match = re.search(r"\bIC(?:e)?\s*=\s*([0-9]+(?:[.,][0-9]+)?)", str(metric.get("nota", "")), re.I)
                if match:
                    ic = float(match.group(1).replace(",", "."))
            if ic is not None:
                break
    if io is None:
        metric = metrics.get("planta_maxima_m2")
        if isinstance(metric, dict):
            match = re.search(r"\bIO\s*=\s*([0-9]+(?:[.,][0-9]+)?)", str(metric.get("nota", "")), re.I)
            if match:
                io = float(match.group(1).replace(",", "."))
    return ic, io


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def audit_case(sample: dict, base_url: str) -> dict:
    row = {field: "" for field in OUTPUT_FIELDS}
    row.update({k: sample.get(k, "") for k in row if k in sample})
    anomalies: list[str] = []
    started = time.perf_counter()
    try:
        geo, recovered = audit_request_json(base_url + "/api/geocode", {"q": sample["input_address"]})
        if recovered:
            row["retry_recovered"] = f"geocode: {recovered}"
            anomalies.append("transient_request_failure")
        row["geocode_ms"] = round((time.perf_counter() - started) * 1000, 1)
    except Exception as exc:
        row["http_or_json_error"] = f"geocode: {type(exc).__name__}: {exc}"
        row["anomalies"] = "http_or_json_error"
        return row
    candidates = geo.get("candidates") or []
    row["geocode_ok"] = bool(geo.get("ok"))
    row["resolution"] = geo.get("resolution", "")
    row["candidate_count"] = len(candidates)
    row["geocode_error"] = geo.get("error", "")
    if not candidates:
        if sample["format_variant"] != "invalid" and sample["input_address"] != "Av. Cra. 68 # 40-15":
            anomalies.append("real_address_unresolved")
        row["anomalies"] = ";".join(anomalies)
        return row
    if sample["format_variant"] == "invalid":
        anomalies.append("invalid_input_resolved")
    expected_lot = str(sample.get("expected_lotcodigo") or "")
    expected_ranks = [
        index + 1 for index, item in enumerate(candidates)
        if str(item.get("lotcodigo") or "") == expected_lot
    ]
    row["expected_candidate_found"] = bool(expected_ranks) if expected_lot else ""
    row["expected_candidate_rank"] = expected_ranks[0] if expected_ranks else ""
    # The UI asks the user to choose when multiple candidates exist. Select the
    # known source lot for the calculation phase so geocoder ambiguity does not
    # contaminate the independent calculation audit.
    candidate = candidates[expected_ranks[0] - 1] if expected_ranks else candidates[0]
    row["match_type"] = candidate.get("match_type", "")
    row["match_confidence"] = candidate.get("match_confidence", "")
    row["candidate_source"] = candidate.get("source", "")
    row["candidate_label"] = candidate.get("label", "")
    row["candidate_lotcodigo"] = candidate.get("lotcodigo", "")
    row["candidate_lat"] = candidate.get("lat", "")
    row["candidate_lng"] = candidate.get("lng", "")
    row["coordinate_adjusted_to_lot"] = bool(candidate.get("coordinate_adjusted_to_lot"))
    row["address_point_lat"] = candidate.get("address_point_lat", "")
    row["address_point_lng"] = candidate.get("address_point_lng", "")
    coordinates = (sample.get("source_lat"), sample.get("source_lng"), candidate.get("lat"), candidate.get("lng"))
    if all(isinstance(value, (int, float)) for value in coordinates):
        row["coordinate_drift_m"] = round(haversine_m(*coordinates), 1)
        if row["coordinate_drift_m"] > 250 and not row["coordinate_adjusted_to_lot"]:
            anomalies.append("geocode_coordinate_drift")
    row["degraded_geocode"] = candidate.get("source") == "nominatim" and candidate.get("match_type") != "osm_address"
    row["lot_mismatch"] = bool(expected_lot and not expected_ranks)
    if len(candidates) > 1 and len({str(item.get("label") or "") for item in candidates}) == 1:
        anomalies.append("duplicate_address_across_lots")
    if row["degraded_geocode"]:
        anomalies.append("degraded_geocode")
    if row["lot_mismatch"]:
        anomalies.append("geocode_lot_mismatch")
    calc_started = time.perf_counter()
    try:
        calc, recovered = audit_request_json(base_url + "/api/calc", {
            "lat": candidate["lat"], "lng": candidate["lng"],
            "expected_lotcodigo": candidate.get("lotcodigo") or "",
            "scenario_only": "true", "address": sample["input_address"],
        })
        if recovered:
            row["retry_recovered"] = ((str(row["retry_recovered"]) + "; ") if row["retry_recovered"] else "") + f"calc: {recovered}"
            anomalies.append("transient_request_failure")
        row["calc_ms"] = round((time.perf_counter() - calc_started) * 1000, 1)
    except Exception as exc:
        row["http_or_json_error"] = f"calc: {type(exc).__name__}: {exc}"
        anomalies.append("http_or_json_error")
        row["anomalies"] = ";".join(anomalies)
        return row
    row["calc_ok"] = bool(calc.get("ok"))
    row["calc_error"] = calc.get("error", "")
    row["calc_state"] = "full_report" if calc.get("ok") else calc.get("error", "cannot_calculate")
    if not calc.get("ok"):
        if calc.get("error") == "cadastral_mismatch":
            anomalies.append("address_point_polygon_mismatch")
        if calc.get("error") == "ambiguous_regulation":
            anomalies.append("conflicting_official_regulation")
        if calc.get("error") in {"zero_features", "layer_zero_features"}:
            anomalies.append("official_regulation_missing_at_linked_lot")
        if calc.get("error") in {"internal", "layer_error", "layer_timeout", "layer_parse_error"}:
            anomalies.append("calc_failure")
        row["anomalies"] = ";".join(anomalies)
        return row
    data = calc.get("data") or {}
    metrics = data.get("metrics") or {}
    row["treatment"] = data.get("tratamiento", "")
    row["lot_area_m2"] = ((data.get("lote") or {}).get("area_m2") or {}).get("valor", "")
    row["binding_constraint"] = data.get("binding_constraint", "")
    row["ic_value"], row["io_value"] = extract_indices(metrics)
    row["max_buildable_m2"] = first_numeric_metric(metrics, [
        "area_construible_max_m2", "area_construible_max_sin_manzana_completa_m2",
        "area_construible_maxima_m2",
    ])
    nulls, nans = nested_counts(data)
    row["null_count"], row["nan_count"] = nulls, nans
    if nans:
        anomalies.append("nan_or_infinity")
    row["treatment_mismatch"] = bool(
        sample.get("expected_treatment") not in {"", "INVALID"}
        and data.get("tratamiento") != sample.get("expected_treatment")
    )
    if row["treatment_mismatch"]:
        anomalies.append(
            "source_point_treatment_differs_from_linked_lot"
            if row["coordinate_adjusted_to_lot"] else "treatment_mismatch"
        )
    # Check every explicit ICE × lot-area metric exposed by the result.
    math_deltas = []
    area = row["lot_area_m2"]
    if isinstance(area, (int, float)):
        for metric in metrics.values():
            if not isinstance(metric, dict) or not isinstance(metric.get("ice_max"), (int, float)):
                continue
            if not isinstance(metric.get("valor"), (int, float)):
                continue
            math_deltas.append(abs(metric["valor"] - area * metric["ice_max"]))
    if math_deltas:
        row["math_delta_m2"] = round(max(math_deltas), 4)
        row["math_check"] = "pass" if max(math_deltas) <= 0.11 else "fail"
        if row["math_check"] == "fail":
            anomalies.append("math_mismatch")
    else:
        row["math_check"] = "not_applicable"
    row["anomalies"] = ";".join(anomalies)
    return row


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def add_anomaly(row: dict, anomaly: str) -> None:
    current = [item for item in str(row.get("anomalies", "")).split(";") if item]
    if anomaly not in current:
        current.append(anomaly)
    row["anomalies"] = ";".join(current)


def apply_cross_case_anomalies(rows: list[dict]) -> None:
    """Flag population-level inconsistencies and statistically slow requests."""
    geocode_times = [float(r["geocode_ms"]) for r in rows if r["geocode_ms"] != ""]
    calc_times = [float(r["calc_ms"]) for r in rows if r["calc_ms"] != ""]
    geocode_threshold = max(3_000.0, percentile(geocode_times, .95) * 2)
    calc_threshold = max(10_000.0, percentile(calc_times, .95) * 2)
    for row in rows:
        if row["geocode_ms"] != "" and float(row["geocode_ms"]) > geocode_threshold:
            add_anomaly(row, "geocode_latency_outlier")
        if row["calc_ms"] != "" and float(row["calc_ms"]) > calc_threshold:
            add_anomaly(row, "calc_latency_outlier")

    by_block: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("source_block") and row.get("expected_treatment") not in {"", "INVALID"}:
            by_block[(str(row["source_block"]), str(row["expected_treatment"]))].append(row)
    for group in by_block.values():
        successful = [row for row in group if row.get("calc_ok") is True]
        if len(successful) < 2:
            continue
        treatments = {str(row.get("treatment")) for row in successful if row.get("treatment")}
        if len(treatments) > 1:
            for row in successful:
                add_anomaly(row, "same_block_treatment_inconsistent")
        indices = [float(row["ic_value"]) for row in successful if isinstance(row.get("ic_value"), (int, float))]
        if len(indices) >= 2 and max(indices) - min(indices) > .01:
            for row in successful:
                add_anomaly(row, "same_block_index_inconsistent")


def write_summary(rows: list[dict], output: Path, base_url: str) -> None:
    real = [r for r in rows if r["format_variant"] != "invalid"]
    anomaly_counts = Counter(a for r in rows for a in str(r["anomalies"]).split(";") if a)
    resolution_counts = Counter(str(r["resolution"]) for r in real)
    calc_states = Counter(str(r["calc_state"]) for r in real)
    by_format: dict[str, list[dict]] = defaultdict(list)
    for row in real:
        by_format[str(row["format_variant"])].append(row)
    geocode_times = [float(r["geocode_ms"]) for r in rows if r["geocode_ms"] != ""]
    calc_times = [float(r["calc_ms"]) for r in rows if r["calc_ms"] != ""]
    lines = [
        "# Ainmo production address audit",
        "",
        f"- Target: `{base_url}`",
        f"- Cases: **{len(rows)}** ({len(real)} real Catastro addresses; {len(rows)-len(real)} invalid/negative controls)",
        f"- Exact-address rate: **{resolution_counts['exact_address']}/{len(real)} ({resolution_counts['exact_address']/max(len(real),1):.1%})**",
        f"- Same-block fallback rate: **{resolution_counts['same_block']}/{len(real)} ({resolution_counts['same_block']/max(len(real),1):.1%})**",
        f"- Unresolved real-address rate: **{sum(1 for r in real if not r['geocode_ok'])}/{len(real)} ({sum(1 for r in real if not r['geocode_ok'])/max(len(real),1):.1%})**",
        f"- Degraded city/region geocodes: **{sum(bool(r['degraded_geocode']) for r in rows)}**",
        f"- Invalid controls incorrectly resolved: **{anomaly_counts['invalid_input_resolved']}**",
        f"- Full calculation reports: **{calc_states['full_report']}/{len(real)}**",
        f"- Address-point/parcel mismatches: **{anomaly_counts['address_point_polygon_mismatch']}**",
        f"- Official address points safely re-anchored to their linked lot: **{sum(bool(r['coordinate_adjusted_to_lot']) for r in rows)}**",
        f"- Geocodes over 250 m from their source point: **{anomaly_counts['geocode_coordinate_drift']}**",
        f"- NaN/Infinity payloads: **{sum(int(r['nan_count'] or 0) for r in rows)}**",
        f"- Explicit calculation math failures: **{sum(r['math_check'] == 'fail' for r in rows)}**",
        f"- Unrecovered HTTP/JSON errors: **{sum(bool(r['http_or_json_error']) for r in rows)}**",
        f"- Geocode latency: median **{statistics.median(geocode_times):.0f} ms**, p95 **{percentile(geocode_times,.95):.0f} ms**",
        f"- Calculation latency: median **{statistics.median(calc_times):.0f} ms**, p95 **{percentile(calc_times,.95):.0f} ms**",
        "",
        "## Issues ranked by frequency",
        "",
    ]
    if anomaly_counts:
        for issue, count in anomaly_counts.most_common(10):
            examples = [str(r["input_address"]) for r in rows if issue in str(r["anomalies"]).split(";")][:3]
            lines.append(f"1. `{issue}` — **{count}** cases. Examples: " + "; ".join(f"`{x}`" for x in examples))
    else:
        lines.append("No coded anomalies detected.")
    lines += ["", "## Resolution success by input format", "", "| Format | Cases | Exact | Fallback | Unresolved |", "|---|---:|---:|---:|---:|"]
    for variant, group in sorted(by_format.items()):
        lines.append(
            f"| {variant} | {len(group)} | {sum(r['resolution']=='exact_address' for r in group)} | "
            f"{sum(r['resolution']=='same_block' for r in group)} | {sum(not r['geocode_ok'] for r in group)} |"
        )
    lines += ["", "## Calculation states", ""]
    for state, count in calc_states.most_common():
        lines.append(f"- `{state or 'not_run'}`: {count}")
    lines += ["", "## Coverage by expected treatment", "", "| Treatment | Cases | Resolved | Full reports | Anomalies |", "|---|---:|---:|---:|---:|"]
    for treatment in TREATMENTS:
        group = [row for row in real if row["expected_treatment"] == treatment]
        lines.append(
            f"| {treatment} | {len(group)} | {sum(bool(row['geocode_ok']) for row in group)} | "
            f"{sum(row['calc_state'] == 'full_report' for row in group)} | {sum(bool(row['anomalies']) for row in group)} |"
        )
    lines += [
        "", "## Interpretation", "",
        "Null fields are recorded but are not automatically treated as bugs because Ainmo intentionally uses `SIN_DATO`/null for unavailable or resultante regulatory values. Math checks apply only where the API exposes both a numeric `ice_max` and its corresponding area result.",
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://ainmo.uk")
    parser.add_argument("--real", type=int, default=290, help="Real cases; 10 invalid controls are added")
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/address_audit"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sample_path = args.output_dir / "samples.json"
    if args.samples:
        samples = json.loads(args.samples.read_text(encoding="utf-8"))
    else:
        samples = generate_samples(args.real, args.seed)
        sample_path.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(audit_case, sample, args.base_url.rstrip("/")): sample for sample in samples}
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                rows.append(future.result())
            except Exception as exc:
                sample = futures[future]
                row = {field: "" for field in OUTPUT_FIELDS}
                row.update({k: sample.get(k, "") for k in row if k in sample})
                row["http_or_json_error"] = f"worker: {type(exc).__name__}: {exc}"
                row["anomalies"] = "http_or_json_error"
                rows.append(row)
            if done % 25 == 0 or done == len(samples):
                print(f"completed {done}/{len(samples)}", flush=True)
    rows.sort(key=lambda r: str(r["case_id"]))
    apply_cross_case_anomalies(rows)
    csv_path = args.output_dir / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader(); writer.writerows(rows)
    write_summary(rows, args.output_dir / "SUMMARY.md", args.base_url.rstrip("/"))
    print(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
