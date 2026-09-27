"""Build the reviewed SEO location catalog from official Bogotá GIS services.

This is a release-time data task, never a request-time dependency.  It keeps
the public pages useful when an ArcGIS service is slow and records exactly
which source and consultation date produced every percentage.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx
from shapely.geometry import shape
from shapely.strtree import STRtree


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "seo_location_catalog.json"
POT = (
    "https://services7.arcgis.com/lsxbLWF2l19Rmhqj/arcgis/rest/services/"
    "POT_Bogota_Decreto_555_2021/FeatureServer"
)
SECTORS = (
    "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/"
    "catastro/sectorcatastral/MapServer/0"
)

# Deliberately reviewed commercial/development locations.  The script will
# fail if Catastro no longer returns the expected official code and name.
SELECTED_SECTORS = {
    "008201": "quinta-camacho",
    "008313": "lago-gaitan",
    "008308": "el-chico",
    "008307": "chico-norte",
    "008512": "cedritos",
    "008415": "santa-barbara-central",
    "009125": "pasadena",
    "005304": "la-castellana",
    "007104": "teusaquillo",
    "007207": "galerias",
    "006216": "ciudad-salitre-nororiental",
    "006311": "modelia",
    "005507": "normandia",
    "009141": "la-candelaria",
    "002103": "restrepo",
    "007401": "polo-club",
}


def slugify(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def fetch_geojson(client: httpx.Client, url: str, fields: str, where: str = "1=1") -> list[dict]:
    features: list[dict] = []
    offset = 0
    while True:
        response = client.get(
            f"{url}/query",
            params={
                "f": "geojson",
                "where": where,
                "outFields": fields,
                "returnGeometry": "true",
                "outSR": 3857,
                "resultOffset": offset,
                "resultRecordCount": 2000,
                "orderByFields": "OBJECTID ASC",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        batch = payload.get("features", [])
        features.extend(batch)
        if len(batch) < 2000:
            return features
        offset += len(batch)


def overlay_breakdown(region, records: list[tuple], tree: STRtree) -> list[dict]:
    totals: dict[str, float] = defaultdict(float)
    for index in tree.query(region, predicate="intersects"):
        geometry, label = records[int(index)]
        overlap = region.intersection(geometry).area
        if overlap > 0:
            totals[label or "Sin clasificación"] += overlap
    total = sum(totals.values()) or region.area
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return [
        {"name": name.title(), "pct": round(area / total * 100, 1)}
        for name, area in ranked
        if area / total >= 0.005
    ]


def main() -> None:
    with httpx.Client(
        timeout=90,
        verify=False,
        follow_redirects=True,
        headers={"User-Agent": "Ainmo SEO catalog builder (ainmo.uk)"},
    ) as client:
        localities = fetch_geojson(
            client, f"{POT}/31", "OBJECTID,NOMBRE,CODIGO_LOCALIDAD"
        )
        treatments = fetch_geojson(
            client, f"{POT}/15", "OBJECTID,TRATAMIENTO"
        )
        activities = fetch_geojson(
            client, f"{POT}/14", "OBJECTID,NOMBRE_AREA_ACTIVIDAD"
        )
        sectors = fetch_geojson(
            client,
            SECTORS,
            "OBJECTID,SCACODIGO,SCANOMBRE,SCATIPO",
            "SCATIPO = 0",
        )

    treatment_records = [
        (shape(item["geometry"]).buffer(0), item["properties"].get("TRATAMIENTO"))
        for item in treatments
        if item.get("geometry")
    ]
    activity_records = [
        (
            shape(item["geometry"]).buffer(0),
            item["properties"].get("NOMBRE_AREA_ACTIVIDAD"),
        )
        for item in activities
        if item.get("geometry")
    ]
    treatment_tree = STRtree([record[0] for record in treatment_records])
    activity_tree = STRtree([record[0] for record in activity_records])

    locality_geometries: list[tuple] = []
    locality_catalog = []
    for item in localities:
        props = item["properties"]
        geometry = shape(item["geometry"]).buffer(0)
        name = str(props["NOMBRE"]).title()
        locality_geometries.append((geometry, name, slugify(name)))
        locality_catalog.append(
            {
                "name": name,
                "slug": slugify(name),
                "code": str(props["CODIGO_LOCALIDAD"]),
                "treatments": overlay_breakdown(geometry, treatment_records, treatment_tree),
                "activities": overlay_breakdown(geometry, activity_records, activity_tree),
            }
        )

    sector_catalog = []
    found_codes = set()
    for item in sectors:
        props = item["properties"]
        code = str(props["SCACODIGO"])
        if code not in SELECTED_SECTORS:
            continue
        found_codes.add(code)
        geometry = shape(item["geometry"]).buffer(0)
        center = geometry.representative_point()
        locality = next(
            (
                {"name": name, "slug": slug}
                for locality_geometry, name, slug in locality_geometries
                if locality_geometry.covers(center)
            ),
            None,
        )
        if locality is None:
            raise RuntimeError(f"No locality found for sector {code}")
        sector_catalog.append(
            {
                "name": str(props["SCANOMBRE"]).title(),
                "slug": SELECTED_SECTORS[code],
                "code": code,
                "locality": locality,
                "treatments": overlay_breakdown(geometry, treatment_records, treatment_tree),
                "activities": overlay_breakdown(geometry, activity_records, activity_tree),
            }
        )

    missing = set(SELECTED_SECTORS) - found_codes
    if missing:
        raise RuntimeError(f"Reviewed sector codes missing from Catastro: {sorted(missing)}")

    payload = {
        "consulted": date.today().isoformat(),
        "sources": {
            "localities": f"{POT}/31",
            "treatments": f"{POT}/15",
            "activities": f"{POT}/14",
            "sectors": SECTORS,
        },
        "localities": sorted(locality_catalog, key=lambda item: item["name"]),
        "sectors": sorted(sector_catalog, key=lambda item: item["name"]),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Wrote {len(payload['localities'])} localities and "
        f"{len(payload['sectors'])} reviewed sectors to {OUTPUT}"
    )


if __name__ == "__main__":
    main()
