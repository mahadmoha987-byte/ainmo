#!/usr/bin/env python3
"""Build Ainmo's persistent Bogotá autocomplete index from official GIS data.

The import is resumable. By default it reads principal UAECD address plates
(PDOTIPO=1), classifies each point against POT Layer 15 locally, and upserts
the canonical index. ``--secondary-aliases`` loads compact PDOTIPO 2/3 entry
aliases without hiding the already-ready principal index.

Examples:
  python3 tools/import_address_index.py --dry-run --limit 2000
  python3 tools/import_address_index.py
  python3 tools/import_address_index.py --resume
  python3 tools/import_address_index.py --secondary-aliases --resume

Required for a real import:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY
"""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geocode import canonical_catastro_address, normalize_address_search  # noqa: E402

ADDRESS_LAYER = (
    "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/"
    "catastro/placadomiciliaria/MapServer/0"
)
TREATMENT_LAYER = (
    "https://services7.arcgis.com/lsxbLWF2l19Rmhqj/arcgis/rest/services/"
    "POT_Bogota_Decreto_555_2021/FeatureServer/15"
)
USER_AGENT = "AinmoAddressIndexer/1.0 (owner-operated; https://ainmo.uk)"
PAGE_SIZE = 2_000
UPSERT_SIZE = 500
SUPABASE_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
SUPABASE_ATTEMPTS = 7
GRID_SIZE = 0.005  # approximately 550 m; narrows point-in-polygon candidates
PRIMARY_WHERE = "PDOTIPO=1"
ALIAS_WHERE = "PDOTIPO IN (2,3)"

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def request_json(
    base_url: str,
    params: dict[str, Any],
    *,
    timeout: float = 45,
    attempts: int = 4,
) -> dict:
    encoded = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{base_url}?{encoded}",
        headers={"User-Agent": USER_AGENT},
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(
                request, context=SSL_CONTEXT, timeout=timeout
            ) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ValueError("ArcGIS returned non-object JSON")
            if payload.get("error"):
                raise RuntimeError(f"ArcGIS error: {payload['error']}")
            return payload
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(2**attempt, 6))
    raise RuntimeError(f"Official GIS request failed: {last_error}") from last_error


def point_in_rings(x: float, y: float, rings: list[list[list[float]]]) -> bool:
    """Even/odd point-in-polygon test, including holes and multipart rings."""
    inside = False
    for ring in rings:
        if len(ring) < 3:
            continue
        previous = len(ring) - 1
        for index, (xi, yi) in enumerate(ring):
            xj, yj = ring[previous]
            if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-20) + xi
            ):
                inside = not inside
            previous = index
    return inside


@dataclass(frozen=True)
class TreatmentPolygon:
    treatment: str
    rings: list[list[list[float]]]
    xmin: float
    ymin: float
    xmax: float
    ymax: float


class TreatmentGrid:
    def __init__(self, polygons: list[TreatmentPolygon]):
        self.polygons = polygons
        self.cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        for polygon_index, polygon in enumerate(polygons):
            min_cell = self._cell(polygon.xmin, polygon.ymin)
            max_cell = self._cell(polygon.xmax, polygon.ymax)
            for cell_x in range(min_cell[0], max_cell[0] + 1):
                for cell_y in range(min_cell[1], max_cell[1] + 1):
                    self.cells[(cell_x, cell_y)].append(polygon_index)

    @staticmethod
    def _cell(x: float, y: float) -> tuple[int, int]:
        return math.floor(x / GRID_SIZE), math.floor(y / GRID_SIZE)

    def classify(self, x: float, y: float) -> tuple[str | None, bool]:
        treatments: set[str] = set()
        for polygon_index in self.cells.get(self._cell(x, y), []):
            polygon = self.polygons[polygon_index]
            if not (
                polygon.xmin <= x <= polygon.xmax
                and polygon.ymin <= y <= polygon.ymax
            ):
                continue
            if point_in_rings(x, y, polygon.rings):
                treatments.add(polygon.treatment)
        if len(treatments) == 1:
            return next(iter(treatments)), False
        return None, len(treatments) > 1


def download_treatment_grid() -> TreatmentGrid:
    id_payload = request_json(
        TREATMENT_LAYER + "/query",
        {"where": "1=1", "returnIdsOnly": "true", "f": "json"},
    )
    object_ids = sorted(int(value) for value in id_payload.get("objectIds", []))
    polygons: list[TreatmentPolygon] = []
    polygon_batch_size = 100
    for start in range(0, len(object_ids), polygon_batch_size):
        payload = request_json(
            TREATMENT_LAYER + "/query",
            {
                "objectIds": ",".join(
                    map(str, object_ids[start : start + polygon_batch_size])
                ),
                "outFields": "OBJECTID,TRATAMIENTO",
                "returnGeometry": "true",
                "outSR": "4326",
                "geometryPrecision": "6",
                "f": "json",
            },
        )
        for feature in payload.get("features", []):
            attributes = feature.get("attributes") or {}
            treatment = str(attributes.get("TRATAMIENTO") or "").strip().upper()
            rings = (feature.get("geometry") or {}).get("rings") or []
            points = [point for ring in rings for point in ring]
            if not treatment or not points:
                continue
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            polygons.append(
                TreatmentPolygon(
                    treatment=treatment,
                    rings=rings,
                    xmin=min(xs),
                    ymin=min(ys),
                    xmax=max(xs),
                    ymax=max(ys),
                )
            )
        if start and start % 500 == 0:
            print(
                f"treatment polygons: {min(start + polygon_batch_size, len(object_ids))}/"
                f"{len(object_ids)}",
                flush=True,
            )
    print(f"treatment polygons indexed: {len(polygons)}", flush=True)
    return TreatmentGrid(polygons)


def source_count(where: str = PRIMARY_WHERE) -> int:
    payload = request_json(
        ADDRESS_LAYER + "/query",
        {
            "where": where,
            "returnCountOnly": "true",
            "f": "json",
        },
    )
    return int(payload.get("count") or 0)


def fetch_address_page(
    after_object_id: int,
    page_size: int,
    where: str = PRIMARY_WHERE,
) -> list[dict]:
    payload = request_json(
        ADDRESS_LAYER + "/query",
        {
            "where": f"({where}) AND OBJECTID>{int(after_object_id)}",
            "outFields": (
                "OBJECTID,PDOCODIGO,PDONVIAL,PDOTEXTO,PDOCLOTE,PDOTIPO"
            ),
            "returnGeometry": "true",
            "outSR": "4326",
            "orderByFields": "OBJECTID ASC",
            "resultRecordCount": min(max(page_size, 1), PAGE_SIZE),
            "f": "json",
        },
    )
    return payload.get("features", [])


def searchable_record(
    feature: dict,
    treatment_grid: TreatmentGrid | None,
    snapshot_date: str,
) -> tuple[dict | None, str | None]:
    attributes = feature.get("attributes") or {}
    geometry = feature.get("geometry") or {}
    pdonvial = str(attributes.get("PDONVIAL") or "").strip()
    pdotexto = str(attributes.get("PDOTEXTO") or "").strip()
    lot_code = str(attributes.get("PDOCLOTE") or "").strip()
    source_address_id = str(attributes.get("PDOCODIGO") or "").strip()
    object_id = attributes.get("OBJECTID")
    try:
        lat = float(geometry["y"])
        lng = float(geometry["x"])
        object_id = int(object_id)
        address_type = int(attributes.get("PDOTIPO") or 1)
    except (KeyError, TypeError, ValueError):
        return None, "invalid_geometry"
    if address_type not in (1, 2, 3):
        return None, "invalid_address_type"
    if not source_address_id:
        return None, "missing_address_id"
    if not (lot_code.isdigit() and len(lot_code) == 12):
        return None, "invalid_lot_code"
    text_parts = pdotexto.split()
    if (
        not pdonvial
        or len(text_parts) < 2
        or not text_parts[0][:1].isdigit()
        or not text_parts[1][:1].isdigit()
    ):
        return None, "not_searchable"
    if not (3.6 <= lat <= 4.9 and -75.1 <= lng <= -73.9):
        return None, "outside_bogota_extent"

    address = canonical_catastro_address(pdonvial, pdotexto)
    normalized = normalize_address_search(address)
    if len(normalized) < 3:
        return None, "empty_normalized_address"
    treatment = None
    ambiguity = False
    if treatment_grid is not None:
        treatment, ambiguity = treatment_grid.classify(lng, lat)
    return {
        "source_address_id": source_address_id,
        "source_object_id": object_id,
        "address": address,
        "normalized_address": normalized,
        "lot_code": lot_code,
        "lat": lat,
        "lng": lng,
        "address_type": address_type,
        "treatment": treatment,
        "source_snapshot_date": snapshot_date,
    }, "ambiguous_treatment" if ambiguity else None


def deduplicate_records(rows: list[dict]) -> tuple[list[dict], int]:
    """Keep the newest ArcGIS object for each Catastro address identifier."""
    rows_by_source_id: dict[str, dict] = {}
    duplicates = 0
    for row in rows:
        source_address_id = row["source_address_id"]
        existing = rows_by_source_id.get(source_address_id)
        if existing is not None:
            duplicates += 1
            if row["source_object_id"] <= existing["source_object_id"]:
                continue
        rows_by_source_id[source_address_id] = row
    return list(rows_by_source_id.values()), duplicates


ALIAS_FIELDS = (
    "source_address_id",
    "address",
    "normalized_address",
    "lot_code",
    "lat",
    "lng",
    "address_type",
)


def compact_alias_record(row: dict) -> dict:
    """Drop import-only fields after ArcGIS duplicate resolution."""
    return {key: row[key] for key in ALIAS_FIELDS}


class SupabaseWriter:
    def __init__(self, base_url: str, service_key: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }
        self.client = httpx.Client(
            timeout=httpx.Timeout(60, connect=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    def close(self) -> None:
        self.client.close()

    def _url(self, table: str, query: str = "") -> str:
        url = f"{self.base_url}/rest/v1/{table}"
        return f"{url}?{query}" if query else url

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Retry transient Supabase/PostgREST failures without losing a page."""
        last_error: Exception | None = None
        for attempt in range(SUPABASE_ATTEMPTS):
            try:
                response = self.client.request(method, url, **kwargs)
                if response.status_code not in SUPABASE_RETRYABLE_STATUS:
                    response.raise_for_status()
                    return response
                last_error = httpx.HTTPStatusError(
                    f"retryable Supabase status {response.status_code}",
                    request=response.request,
                    response=response,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
            if attempt + 1 < SUPABASE_ATTEMPTS:
                delay = min(2**attempt, 20)
                print(
                    f"Supabase request retry {attempt + 1}/{SUPABASE_ATTEMPTS - 1} "
                    f"in {delay}s: {last_error}",
                    flush=True,
                )
                time.sleep(delay)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Supabase request failed without a response")

    def meta(self) -> dict:
        response = self._request(
            "GET",
            self._url("address_index_meta", "singleton=eq.true&select=*"),
            headers=self.headers,
        )
        rows = response.json()
        return rows[0] if isinstance(rows, list) and rows else {}

    def update_meta(self, **values: Any) -> None:
        self._request(
            "PATCH",
            self._url("address_index_meta", "singleton=eq.true"),
            headers={**self.headers, "Prefer": "return=minimal"},
            json=values,
        )

    def upsert(self, rows: list[dict], table: str = "address_index") -> None:
        for start in range(0, len(rows), UPSERT_SIZE):
            batch = rows[start : start + UPSERT_SIZE]
            self._request(
                "POST",
                self._url(table, "on_conflict=source_address_id"),
                headers={
                    **self.headers,
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
                json=batch,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue from address_index_meta.last_object_id.",
    )
    parser.add_argument(
        "--start-object-id",
        type=int,
        default=0,
        help="Start strictly after this ArcGIS OBJECTID.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most this many source rows (pilot/debugging).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and classify official rows without writing Supabase.",
    )
    parser.add_argument(
        "--skip-treatment",
        action="store_true",
        help="Import treatment as null; intended only for diagnostics.",
    )
    parser.add_argument(
        "--secondary-aliases",
        action="store_true",
        help="Load compact PDOTIPO 2/3 entrance aliases while keeping search live.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    supabase_url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not args.dry_run and not (supabase_url and service_key):
        raise SystemExit(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY are required for a real import"
        )

    alias_mode = args.secondary_aliases
    source_where = ALIAS_WHERE if alias_mode else PRIMARY_WHERE
    target_table = "address_alias_index" if alias_mode else "address_index"
    total_source = source_count(source_where)
    snapshot_date = date.today().isoformat()
    treatment_grid = (
        None
        if args.skip_treatment or alias_mode
        else download_treatment_grid()
    )
    writer = None if args.dry_run else SupabaseWriter(supabase_url, service_key)
    start_object_id = max(args.start_object_id, 0)
    imported = 0
    scanned = 0
    skipped: Counter[str] = Counter()
    meta: dict = {}

    try:
        if writer is not None:
            meta = writer.meta()
            if args.resume:
                checkpoint_field = (
                    "alias_last_object_id" if alias_mode else "last_object_id"
                )
                count_field = (
                    "alias_imported_count" if alias_mode else "imported_count"
                )
                start_object_id = max(
                    start_object_id, int(meta.get(checkpoint_field) or 0)
                )
                imported = int(meta.get(count_field) or 0)
            if alias_mode:
                writer.update_meta(
                    # Principal suggestions remain complete and queryable while
                    # compact secondary/corner aliases are added online.
                    status="ready",
                    alias_source_count=total_source,
                    alias_last_object_id=start_object_id,
                    alias_completed_at=None,
                    error=None,
                )
            else:
                writer.update_meta(
                    status="importing",
                    source_count=total_source,
                    source_snapshot_date=snapshot_date,
                    last_object_id=start_object_id,
                    started_at=(
                        meta.get("started_at")
                        if args.resume and meta.get("started_at")
                        else datetime.now(timezone.utc).isoformat()
                    ),
                    completed_at=None,
                    error=None,
                )

        last_object_id = start_object_id
        committed_object_id = start_object_id
        completed_source = False
        while True:
            remaining = None if args.limit is None else args.limit - scanned
            if remaining is not None and remaining <= 0:
                break
            page_size = PAGE_SIZE if remaining is None else min(PAGE_SIZE, remaining)
            features = fetch_address_page(
                last_object_id, page_size, where=source_where
            )
            if not features:
                completed_source = True
                break
            scanned += len(features)
            last_object_id = max(
                int((feature.get("attributes") or {}).get("OBJECTID") or 0)
                for feature in features
            )
            # Catastro occasionally returns the same PDOCODIGO more than once.
            # PostgreSQL cannot upsert two copies of one conflict key in the
            # same statement, so collapse them deterministically per page.
            page_rows: list[dict] = []
            for feature in features:
                row, note = searchable_record(feature, treatment_grid, snapshot_date)
                if row is None:
                    skipped[note or "unknown"] += 1
                    continue
                if note:
                    skipped[note] += 1
                page_rows.append(row)
            rows, duplicate_count = deduplicate_records(page_rows)
            if alias_mode:
                rows = [compact_alias_record(row) for row in rows]
            skipped["duplicate_address_id"] += duplicate_count
            if writer is not None and rows:
                writer.upsert(rows, table=target_table)
            imported += len(rows)
            if writer is not None:
                if alias_mode:
                    writer.update_meta(
                        status="ready",
                        alias_imported_count=imported,
                        alias_last_object_id=last_object_id,
                        error=None,
                    )
                else:
                    writer.update_meta(
                        status="importing",
                        imported_count=imported,
                        last_object_id=last_object_id,
                        error=None,
                    )
            committed_object_id = last_object_id
            print(
                f"scanned={scanned:,} imported={imported:,} "
                f"last_object_id={last_object_id:,} skipped={sum(skipped.values()):,}",
                flush=True,
            )
            if len(features) < page_size:
                completed_source = True
                break

        if writer is not None:
            complete = completed_source and args.limit is None
            if alias_mode:
                writer.update_meta(
                    status="ready",
                    alias_imported_count=imported,
                    alias_last_object_id=last_object_id,
                    alias_completed_at=(
                        datetime.now(timezone.utc).isoformat()
                        if complete
                        else None
                    ),
                    error=None,
                )
            else:
                writer.update_meta(
                    status="ready" if complete else "importing",
                    imported_count=imported,
                    last_object_id=last_object_id,
                    completed_at=(
                        datetime.now(timezone.utc).isoformat()
                        if complete
                        else None
                    ),
                    error=None,
                )
        print(
            json.dumps(
                {
                    "source_count": total_source,
                    "scanned_this_run": scanned,
                    "indexed_total": imported,
                    "last_object_id": last_object_id,
                    "complete": completed_source and args.limit is None,
                    "skipped": dict(skipped),
                    "dry_run": args.dry_run,
                    "target_table": target_table,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        if writer is not None:
            try:
                checkpoint = locals().get("committed_object_id", start_object_id)
                if alias_mode:
                    writer.update_meta(
                        status="ready",
                        alias_imported_count=imported,
                        alias_last_object_id=checkpoint,
                        error=f"{type(exc).__name__}: {exc}"[:1_000],
                    )
                else:
                    writer.update_meta(
                        status="failed",
                        imported_count=imported,
                        # Only resume after the last page whose rows and checkpoint
                        # both committed. Re-reading an earlier page is safe because
                        # source_address_id is upserted idempotently.
                        last_object_id=checkpoint,
                        error=f"{type(exc).__name__}: {exc}"[:1_000],
                    )
            except Exception:
                pass
        raise
    finally:
        if writer is not None:
            writer.close()


if __name__ == "__main__":
    raise SystemExit(main())
