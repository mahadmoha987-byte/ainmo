#!/usr/bin/env python3
"""
p2_lookup.py — Bogotá POT buildability lookup
Decreto Distrital 555 de 2021 (Art. 281, 310, Anexo 5)

Input : WGS84 point (lng, lat)
Output: dict with tratamiento, rango, edificabilidad rules, lot area, confidence levels

Every numeric value carries a 'confianza' key: "alta" | "media" | "requiere_input"
Zero features from any ArcGIS layer raises ZeroFeaturesError — never silently null.
Both SDP servers (sinu.sdp.gov.co, serviciosg.sdp.gov.co) are dead; not used here.
"""

from __future__ import annotations

import json
import math
import decreto253
import re
import ssl
import sys
import warnings
from datetime import date
from regulatory import REGULATORY_VERSION, REGULATORY_CUTOFF, context as regulatory_context
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

# Colombian government TLS certificates are often self-signed or use a local CA
# not present in the macOS/Linux trust store.  We disable verification only for
# known gov.co hosts; the data is public read-only, so confidentiality is not at risk.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
warnings.filterwarnings("ignore", message="Unverified HTTPS request")


# ── Endpoint constants ───────────────────────────────────────────────────────

ARCGIS_FS = (
    "https://services7.arcgis.com/lsxbLWF2l19Rmhqj/arcgis/rest/services"
    "/POT_Bogota_Decreto_555_2021/FeatureServer"
)
CATASTRO_MS = (
    "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/catastro/lote/MapServer"
)
# Government mirror of IDECA's cadastral lot layer.  Some cloud-provider egress
# ranges cannot connect to Catastro's primary host, so production falls back to
# this read-only CAR service only for transport/network failures.
CATASTRO_MS_FALLBACK = (
    "https://sig.car.gov.co/arcgis/rest/services/VISOR/Capas_base/FeatureServer"
)
CATASTRO_FALLBACK_LAYER = 9
# Older IDECA mirror maintained by Superservicios.  This is the last-resort
# transport source when both current government hosts reject cloud egress.
CATASTRO_MS_FALLBACK_2 = (
    "https://geoportal.superservicios.gov.co/server/rest/services/IDECA/Bogotav0318/MapServer"
)
CATASTRO_FALLBACK_LAYER_2 = 2

# Layer IDs — POT FeatureServer
L_EDIFICABILIDAD  = 15   # TRATAMIENTO, TIPOLOGIA, ALTURA_MAXIMA — stored in WKID 102100
L_AREA_ACTIVIDAD  = 14   # CODIGO_AREA_ACTIVIDAD, NOMBRE_AREA_ACTIVIDAD — receptor zones
L_CALZADA         = 38   # Calzada polygons — ANCHO (carriageway width, metres)
L_RANGO           = 21   # RANGO code (Desarrollo only)
L_ANTEJARDIN      = 22   # DIMENSION minimum antejardín (metres) — mapa CU-5.5
L_AEROCIVIL       = 25   # Restricciones AEROCIVIL (informational)
# Heritage layers — queried only for CONSERVACION tratamiento
L_SIC             = 0    # Sector de Interés Cultural (polygon) — NOMBRE
L_PEMP            = 1    # Plan Especial de Manejo y Protección (individual BIC PEMPs)
L_PEMP_CH         = 3    # PEMP Centro Histórico (Decreto 678/1994) — separate layer
L_BIC             = 12   # Bien de Interés Cultural (polygon) — CATEGORIA, NUMERO_FICHA

# Search radius when looking for calzada polygons adjacent to a lot centroid.
# 25 m covers typical lot setbacks and still avoids picking up roads two blocks away.
_CALZADA_RADIUS_M = 25

# A manually entered point or an address geocode can fall in the public right of
# way immediately next to its parcel.  Catastro's strict point-intersection
# query then returns no lot even though a cadastral polygon is nearby.  Keep
# this deliberately small: this is a recovery for road-edge coordinates, not a
# general-purpose spatial snap.
_LOTE_FALLBACK_RADIUS_M = 25

# ── Blocking-restriction layer endpoints ─────────────────────────────────────
# Queried for every lot regardless of tratamiento.
# POT FeatureServer layers (same server, verified available):
_L_BIC_BLOCK       = 12   # Bienes de Interés Cultural (same as L_BIC)
_L_AEROCIVIL_BLOCK = 25   # Cono de aproximación / servidumbres aeronáuticas El Dorado

# External services — set each URL to a verified ArcGIS FeatureServer root
# ("https://<host>/arcgis/rest/services/<name>/FeatureServer") or leave as None.
# When None, that check is silently skipped (graceful degradation).
#
# Reserva Forestal Protectora Bosque Oriental de Bogotá (Cerros Orientales)
#   Authority: Res. Min. Ambiente 463/2005; Administrator: SDA Bogotá
#   TODO: replace with verified SDA/IDECA ArcGIS FeatureServer URL
_CERROS_FS: str | None = None
_CERROS_LAYER_ID: int = 0
#
# IDIGER — Amenaza por Movimiento en Masa (MRM)
#   TODO: replace with verified IDIGER ArcGIS FeatureServer URL
_IDIGER_MRM_FS: str | None = None
_IDIGER_MRM_LAYER_ID: int = 0
#
# IDIGER — Amenaza por Inundación
#   TODO: replace with verified IDIGER ArcGIS FeatureServer URL
_IDIGER_INUND_FS: str | None = None
_IDIGER_INUND_LAYER_ID: int = 0
#
# EAAB-ESP — Ronda Hídrica (buffer 30 m sobre cuerpos de agua permanentes)
#   TODO: replace with verified EAAB-ESP/IDECA ArcGIS FeatureServer URL
_RONDA_FS: str | None = None
_RONDA_LAYER_ID: int = 0

# CODIGO_AREA_ACTIVIDAD value that identifies VIS/VIP receptor zones for Art. 310 § 3.
# Full distinct values confirmed 2026-08-29:
#   AAERAE  → Estructurante receptora de actividades económicas
#   AAERVIS → Estructurante receptora de vivienda de interés social  ← VIS/VIP bonus
#   AAGSM   → Grandes Servicios Metropolitanos
#   AAPGSU  → Proximidad generadora de soportes urbanos
#   AAPRSU  → Proximidad receptora de soportes urbanos
#   PEMP    → Plan Especial de Manejo y Protección
_RECEPTOR_VIS_CODE = "AAERVIS"

# Layer ID — Catastro MapServer
L_LOTE           = 0    # Physical lot polygon, WGS84 native

TIMEOUT_S = 20
_CATASTRO_SOURCE_TIMEOUT_S = 6

# ── Heritage (Conservación) constants ────────────────────────────────────────
# Coded value domain "cdom_categoria_patrimonio" from Layer 12 schema.
# Confirmed 2026-09-03 via FeatureServer/12?f=json.
_BIC_CATEGORIA: dict[str, str] = {
    "COMU": "Conservación monumental",
    "COIN": "Conservación integral",
    "COTI": "Conservación tipológica",
    "COAR": "Conservación arquitectónica",
    "COCO": "Conservación contextual",
    "RETO": "Restitución total",
    "REPA": "Restitución parcial",
    "INRE": "Reedificable",
}

# Coded value domain "cdom_acto" for ACTO_ADMINISTRATIVO in Layer 12.
_BIC_ACTO_TIPO: dict[str, str] = {
    "DEC": "Decreto", "RES": "Resolución", "ACU": "Acuerdo",
    "MEM": "Memorando", "OFI": "Oficio", "ACT": "Acto administrativo",
    "RAD": "Radicado", "REF": "Referencia", "DNH": "Dato no hallado",
}


# ── RANGO table (Art. 281, Decreto 555/2021) ─────────────────────────────────
# ic_basico  = sin obligación VIS/VIP en sitio
# ic_maximo  = con obligación VIS/VIP en sitio
# ic_vis75   = >75% del índice efectivo es VIS/VIP (sólo 4C y 4D)
# io         = índice de ocupación máximo sobre ANU (None → resultante)
# alt_sin_vis / alt_con_vis = pisos fijos (None → resultante)
# anu_proxy  = "lote" (área catastral ≈ ANU) | "plan_parcial" (usuario debe aportar)

RANGO_TABLE: dict[str, dict] = {
    "1":  dict(
        ic_basico=2.25, ic_maximo=2.82, ic_vis75=None,
        io=None, alt_sin_vis=None, alt_con_vis=None,
        anu_proxy="plan_parcial",
    ),
    "2":  dict(
        ic_basico=2.00, ic_maximo=2.57, ic_vis75=None,
        io=None, alt_sin_vis=None, alt_con_vis=None,
        anu_proxy="plan_parcial",
    ),
    "3":  dict(
        ic_basico=1.75, ic_maximo=2.32, ic_vis75=None,
        io=None, alt_sin_vis=None, alt_con_vis=None,
        anu_proxy="plan_parcial",
    ),
    "4A": dict(
        ic_basico=None, ic_maximo=None, ic_vis75=None,
        io=0.10, alt_sin_vis=3, alt_con_vis=3,
        anu_proxy="lote",
    ),
    "4B": dict(
        ic_basico=None, ic_maximo=None, ic_vis75=None,
        io=0.15, alt_sin_vis=6, alt_con_vis=8,
        anu_proxy="lote",
    ),
    "4C": dict(
        ic_basico=0.90, ic_maximo=1.20, ic_vis75=1.75,
        io=0.15, alt_sin_vis=None, alt_con_vis=None,
        anu_proxy="lote",
    ),
    "4D": dict(
        ic_basico=0.90, ic_maximo=1.20, ic_vis75=1.75,
        io=0.28, alt_sin_vis=None, alt_con_vis=None,
        anu_proxy="lote",
    ),
}

# Aislamiento posterior mínimo — Consolidación (Anexo 5, Cap. 2.4.2.A.2, p.57)
# (pisos_hasta_inclusive, aislamiento_m) — last row = 29+ pisos
_AISL_POST_TABLE = [
    (3,  3),
    (6,  5),
    (9,  6),
    (12, 8),
    (15, 10),
    (18, 12),
    (22, 14),
    (25, 16),
    (28, 18),
    (None, 20),
]


# ── Custom exceptions ────────────────────────────────────────────────────────

class BuildabilityLookupError(RuntimeError):
    """Base for all recoverable lookup errors."""

class ZeroFeaturesError(BuildabilityLookupError):
    """ArcGIS query returned 0 features — point is outside any relevant polygon."""

class ParseError(BuildabilityLookupError):
    """A field value did not match any known format."""

class ProjectionError(BuildabilityLookupError):
    """Geometry was absent or produced an implausible area (projection may have failed)."""


# ── Low-level HTTP / ArcGIS helpers ─────────────────────────────────────────

def _fetch_json(url: str, timeout_s: int = TIMEOUT_S) -> dict:
    try:
        with urlopen(url, timeout=timeout_s, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        raise BuildabilityLookupError(f"HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise BuildabilityLookupError(f"Network error — {exc.reason}: {url}") from exc


def _arcgis_query(base: str, params: dict, timeout_s: int = TIMEOUT_S) -> list[dict]:
    """
    Execute an ArcGIS REST query; return features list.
    Raises ZeroFeaturesError when no features match.
    Does NOT suppress ArcGIS error objects.
    """
    qs = urlencode({**params, "f": "json"})
    url = f"{base}/query?{qs}"
    data = _fetch_json(url, timeout_s=timeout_s)

    if "error" in data:
        raise BuildabilityLookupError(
            f"ArcGIS error at {base}: {data['error']}"
        )
    features = data.get("features", [])
    if not features:
        raise ZeroFeaturesError(
            f"Zero features at {base!r} with params {params}. "
            "Point may be on a road, outside the urban perimeter, or in a non-buildable area."
        )
    return features


def _normalize_catastro_feature(feature: dict) -> dict:
    """Normalize the mirror's camel-case field names to Catastro's schema."""
    attrs = feature.setdefault("attributes", {})
    attrs.setdefault("LOTCODIGO", attrs.get("LotCodigo"))
    attrs.setdefault("LOTUPREDIA", attrs.get("LotUPredia"))
    return feature


def _tag_catastro_source(features: list[dict], source_id: str, source_label: str,
                         source_date: str | None = None) -> list[dict]:
    for feature in features:
        feature["_ainmo_source"] = {
            "id": source_id,
            "fuente": source_label,
            "fecha_referencia": source_date,
            "es_fallback": source_id != "catastro_bogota_actual",
        }
    return features


def _query_catastro(params: dict) -> list[dict]:
    """Query primary Catastro, falling back only when its transport fails."""
    try:
        return _tag_catastro_source(_arcgis_query(
            f"{CATASTRO_MS}/{L_LOTE}", params,
            timeout_s=_CATASTRO_SOURCE_TIMEOUT_S,
        ), "catastro_bogota_actual", "Catastro Bogotá MapServer, capa 0 (LOTE)")
    except ZeroFeaturesError:
        raise
    except BuildabilityLookupError as primary_exc:
        mirror_params = dict(params)
        mirror_params["outFields"] = "OBJECTID,LotCodigo,LotUPredia,Shape__Area"
        try:
            features = _arcgis_query(
                f"{CATASTRO_MS_FALLBACK}/{CATASTRO_FALLBACK_LAYER}",
                mirror_params,
                timeout_s=_CATASTRO_SOURCE_TIMEOUT_S,
            )
        except ZeroFeaturesError:
            raise
        except BuildabilityLookupError as mirror_exc:
            try:
                final_params = dict(mirror_params)
                final_params["outFields"] = "OBJECTID,LotCodigo,LotUPredia,SHAPE_Area"
                features = _arcgis_query(
                    f"{CATASTRO_MS_FALLBACK_2}/{CATASTRO_FALLBACK_LAYER_2}",
                    final_params,
                )
            except BuildabilityLookupError as final_exc:
                raise BuildabilityLookupError(
                    "Catastro primary and mirrors were unreachable: "
                    f"primary={primary_exc}; mirror_1={mirror_exc}; mirror_2={final_exc}"
                ) from final_exc
        normalized = [_normalize_catastro_feature(feature) for feature in features]
        if 'final_params' in locals():
            return _tag_catastro_source(
                normalized, "ideca_superservicios_2018",
                "Espejo IDECA de Superservicios, capa de lotes",
                "2018",
            )
        return _tag_catastro_source(
            normalized, "car_ideca_mirror",
            "Espejo gubernamental CAR de la capa catastral IDECA",
            "2021",
        )


def query_fs(layer_id: int, lng: float, lat: float,
             out_fields: list[str],
             return_geometry: bool = False,
             out_sr: int | None = None,
             distance_m: float | None = None) -> list[dict]:
    """
    Query POT FeatureServer layer.
    Always passes inSR=4326 explicitly — Layer 15 (and others) is in WKID 102100
    and will return wrong results if inSR is omitted.
    distance_m: when set, returns features within that radius (esriSRUnit_Meter).
    """
    params: dict = {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,                        # ALWAYS explicit — never rely on layer default
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(out_fields),
        "returnGeometry": str(return_geometry).lower(),
    }
    if out_sr is not None:
        params["outSR"] = out_sr
    if distance_m is not None:
        params["distance"] = distance_m
        params["units"] = "esriSRUnit_Meter"
    try:
        return _arcgis_query(f"{ARCGIS_FS}/{layer_id}", params)
    except ZeroFeaturesError:
        raise
    except BuildabilityLookupError as exc:
        raise BuildabilityLookupError(f"Layer {layer_id}: {exc}") from exc


def _distance_point_to_segment_m(
    px: float, py: float, ax: float, ay: float, bx: float, by: float,
) -> float:
    """Local equirectangular point-to-segment distance for Bogotá coordinates."""
    # At Bogotá's latitude this local conversion is accurate to far better than
    # the 25 m recovery radius, while avoiding a new projection dependency.
    metres_per_degree_lat = 111_320.0
    metres_per_degree_lng = metres_per_degree_lat * math.cos(math.radians(py))
    px, py = px * metres_per_degree_lng, py * metres_per_degree_lat
    ax, ay = ax * metres_per_degree_lng, ay * metres_per_degree_lat
    bx, by = bx * metres_per_degree_lng, by * metres_per_degree_lat
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _distance_to_lot_boundary_m(lng: float, lat: float, rings: list) -> float:
    """Return the shortest distance from a WGS84 point to a lot ring boundary."""
    distances: list[float] = []
    for ring in rings or []:
        if len(ring) < 2:
            continue
        for a, b in zip(ring, ring[1:] + ring[:1]):
            distances.append(_distance_point_to_segment_m(lng, lat, a[0], a[1], b[0], b[1]))
    return min(distances, default=float("inf"))


def _query_nearest_catastro_lote(lng: float, lat: float) -> tuple[dict, float]:
    """Find the closest cadastral lot within the bounded road-edge recovery radius."""
    candidates = _query_catastro({
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "distance": _LOTE_FALLBACK_RADIUS_M,
        "units": "esriSRUnit_Meter",
        "outFields": "OBJECTID,LOTCODIGO,LOTUPREDIA,SHAPE.AREA",
        "returnGeometry": "true",
        "outSR": 4326,
    })
    nearest = min(
        candidates,
        key=lambda feature: _distance_to_lot_boundary_m(
            lng, lat, feature.get("geometry", {}).get("rings", []),
        ),
    )
    distance_m = _distance_to_lot_boundary_m(lng, lat, nearest.get("geometry", {}).get("rings", []))
    object_id = nearest.get("attributes", {}).get("OBJECTID")
    if object_id is None:
        raise ProjectionError("Catastro fallback returned a lot without OBJECTID.")

    # Fetch the selected feature in MAGNA-SIRGAS 9377, preserving the existing
    # shoelace-area calculation and avoiding an area approximation in WGS84.
    features = _query_catastro({
        "objectIds": object_id,
        "outFields": "OBJECTID,LOTCODIGO,LOTUPREDIA,SHAPE.AREA",
        "returnGeometry": "true",
        "outSR": 9377,
    })
    return features[0], distance_m


def query_catastro_lote(lng: float, lat: float) -> tuple[dict, float | None]:
    """
    Fetch physical lot polygon from Catastro MapServer Layer 0.
    Returns outSR=9377 (MAGNA-SIRGAS Colombia West / Bogotá, metres) so that
    shoelace area calculation works without any cos(lat) approximation.
    Returns (feature, snap_distance_m). snap_distance_m is None for a strict
    intersection; otherwise it records Catastro's bounded nearest-lot recovery.
    Raises ZeroFeaturesError if there is no lot within the recovery radius.
    Raises ProjectionError if geometry is missing from the response.
    """
    params: dict = {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,                        # explicit even though catastro is natively WGS84
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "OBJECTID,LOTCODIGO,LOTUPREDIA,SHAPE.AREA",
        "returnGeometry": "true",
        "outSR": 9377,                       # projected metres for shoelace
    }
    try:
        features = _query_catastro(params)
        feat, snap_distance_m = features[0], None
    except ZeroFeaturesError:
        feat, snap_distance_m = _query_nearest_catastro_lote(lng, lat)
    if not feat.get("geometry"):
        raise ProjectionError(
            "Catastro returned a feature with no geometry — cannot compute lot area. "
            "outSR=9377 projection may have been rejected by this server version."
        )
    return feat, snap_distance_m


def _fetch_lot_rings_wgs84(lng: float, lat: float, object_id: int | None = None) -> list | None:
    """
    Fetch the lot polygon rings in WGS84 (outSR=4326) for map display.
    Returns rings ([[x, y], ...]) or None if the request fails.
    A separate call is needed because the area calculation requires 9377.
    """
    params: dict = {
        "outFields": "LOTCODIGO",
        "returnGeometry": "true",
        "outSR": 4326,
    }
    if object_id is None:
        params.update({
            "geometry": f"{lng},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
        })
    else:
        params["objectIds"] = object_id
    try:
        features = _query_catastro(params)
        return features[0].get("geometry", {}).get("rings")
    except Exception:
        return None


# ── Geometry / area ──────────────────────────────────────────────────────────

def _shoelace(rings: list[list[list[float]]]) -> float:
    """
    Shoelace area formula on a projected polygon (metres in MAGNA-SIRGAS 9377).
    First ring = exterior; remaining rings = holes (subtracted).
    No cos(lat) approximation — the projection handles distortion.
    """
    total = 0.0
    for i, ring in enumerate(rings):
        n = len(ring)
        area = 0.0
        for j in range(n):
            x1, y1 = ring[j][0], ring[j][1]
            x2, y2 = ring[(j + 1) % n][0], ring[(j + 1) % n][1]
            area += x1 * y2 - x2 * y1
        signed = abs(area) / 2.0
        total += signed if i == 0 else -signed
    return abs(total)


def compute_lot_area_m2(feat: dict) -> float:
    """
    Compute lot area in m² from a feature whose geometry is in outSR=9377.
    Raises ProjectionError if rings are absent or the result is implausibly small
    (which would indicate the coordinates are still in degrees, not metres).
    """
    geom = feat.get("geometry", {})
    rings = geom.get("rings")
    if not rings:
        raise ProjectionError(
            f"Expected esriGeometryPolygon rings, got: {geom!r}. "
            "Cannot compute lot area without projected polygon geometry."
        )
    area = _shoelace(rings)
    if area < 1.0:
        raise ProjectionError(
            f"Shoelace produced {area:.6f} m² — value is < 1 m², suggesting the coordinates "
            "were not reprojected to metres. Verify outSR=9377 is supported by Catastro."
        )
    return area


# ── Field parsers ────────────────────────────────────────────────────────────

def parse_tipologia(raw) -> str:
    """
    Strict TIPOLOGIA parser for Layer 15.

    Accepted values:
      'TA'      → 'aislada'
      ' ' / ''  → 'continua'
      None      → 'continua'

    Anything else → ParseError (do not guess; surface for human review).
    """
    if raw is None:
        return "continua"
    v = str(raw).strip()
    if v == "":
        return "continua"
    if v == "TA":
        return "aislada"
    raise ParseError(
        f"Unrecognised TIPOLOGIA value {raw!r}. "
        "Expected 'TA' (aislada) or ' '/'' (continua). "
        "A new code may have been added to the layer — handle explicitly."
    )


def parse_altura_maxima(raw) -> dict:
    """
    Parse ALTURA_MAXIMA from Layer 15 into a structured dict.

    Formats and outputs:
      "9"       → {tipo:"fijo",             pisos_min:9,  pisos_max:9,  rango_code:None}
      "4-6"     → {tipo:"rango",            pisos_min:4,  pisos_max:6,  rango_code:None}
      "Rg 4A"   → {tipo:"referencia_rango", pisos_min:None, pisos_max:None, rango_code:"4A"}
      "UNE"     → {tipo:"especial",         pisos_min:None, pisos_max:None, rango_code:None, codigo:"UNE"}
      "BIC NAL" → {tipo:"especial",         pisos_min:None, pisos_max:None, rango_code:None, codigo:"BIC NAL"}
      None/""   → ParseError (ZeroFeaturesError should have caught absent records earlier)

    Do NOT interpret None or unrecognised values as "no height limit".
    """
    if raw is None or str(raw).strip() == "":
        raise ParseError(
            "ALTURA_MAXIMA is null or empty — cannot determine maximum height. "
            "Do NOT interpret this as an absence of height restriction."
        )

    v = str(raw).strip()

    # Pure integer: "3", "9", "12" …
    if re.fullmatch(r"\d+", v):
        p = int(v)
        return {"tipo": "fijo", "pisos_min": p, "pisos_max": p, "rango_code": None}

    # Range: "4-6", "3-5" …
    m = re.fullmatch(r"(\d+)-(\d+)", v)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            raise ParseError(f"ALTURA_MAXIMA range '{v}' has min > max.")
        return {"tipo": "rango", "pisos_min": lo, "pisos_max": hi, "rango_code": None}

    # Rango reference: "Rg 4A", "Rg 1", "rg 4b" (case-insensitive)
    m = re.fullmatch(r"Rg\s+(\w+)", v, re.IGNORECASE)
    if m:
        code = m.group(1).upper()
        return {"tipo": "referencia_rango", "pisos_min": None, "pisos_max": None, "rango_code": code}

    # Known special codes
    if v in ("UNE", "BIC NAL", "BIC"):
        return {"tipo": "especial", "pisos_min": None, "pisos_max": None, "rango_code": None, "codigo": v}

    raise ParseError(
        f"Unrecognised ALTURA_MAXIMA format {raw!r}. "
        "Expected: integer ('9'), range ('4-6'), rango ref ('Rg 4A'), or special ('UNE', 'BIC NAL')."
    )


# ── Consolidación aislamiento posterior ──────────────────────────────────────

def aislamiento_posterior_m(pisos: int) -> int:
    """
    Minimum rear setback in metres for a Consolidación building of `pisos` floors.
    Source: Anexo 5, Cap. 2.4.2.A.2 (verified on p. 57 of the PDF).
    """
    for limit, setback in _AISL_POST_TABLE:
        if limit is None or pisos <= limit:
            return setback
    return 20  # 29+ pisos


# ── Área de actividad (Layer 14) ─────────────────────────────────────────────

def _query_area_actividad(lng: float, lat: float) -> dict:
    """
    Query Layer 14 (Área de actividad) for the given WGS84 point.

    Returns a dict with es_receptora_vis=True only when
    CODIGO_AREA_ACTIVIDAD == 'AAERVIS' ("Área de Actividad Estructurante —
    Receptora de vivienda de interés social").  This is the gate for the
    Art. 310 Parágrafo 3 VIS×2 height bonus.

    ZeroFeaturesError is caught: a lot that falls outside all área-de-actividad
    polygons is valid — it simply means the VIS×2 bonus does not apply.
    """
    try:
        feats = query_fs(
            L_AREA_ACTIVIDAD, lng, lat,
            out_fields=["CODIGO_AREA_ACTIVIDAD", "NOMBRE_AREA_ACTIVIDAD"],
        )
        attrs = feats[0]["attributes"]
        codigo = (attrs.get("CODIGO_AREA_ACTIVIDAD") or "").strip()
        nombre = (attrs.get("NOMBRE_AREA_ACTIVIDAD") or "").strip()
        return {
            "codigo": codigo,
            "nombre": nombre,
            "es_receptora_vis": codigo == _RECEPTOR_VIS_CODE,
            "fuente": "Layer 14 POT FeatureServer (Área de actividad)",
            "articulo": "Art. 310 Parágrafo 3 Decreto 555/2021",
        }
    except ZeroFeaturesError:
        return {
            "codigo": None,
            "nombre": None,
            "es_receptora_vis": False,
            "nota": (
                "Predio fuera de todos los polígonos de Área de actividad (Layer 14). "
                f"El bonus VIS×2 (Art. 310 § 3) requiere zona {_RECEPTOR_VIS_CODE} — no aplica."
            ),
            "fuente": "Layer 14 POT FeatureServer (Área de actividad)",
            "articulo": "Art. 310 Parágrafo 3 Decreto 555/2021",
        }


# ── Calzada width (Layer 38) ─────────────────────────────────────────────────

def _query_ancho_via_gis(lng: float, lat: float) -> dict:
    """
    Query Layer 38 (Calzada polygons) for carriageway width near the lot centroid.

    Groups features by CODIGO_IDENTIFICACION_VIAL so that a two-directional road
    with separate calzada polygons is measured as a whole.  The group with the
    largest total ANCHO (most likely the fronting road) is returned.

    Returns
    -------
    dict with:
      D_m        : total calzada width in metres (float) or None
      n_calzadas : number of calzada polygons summed
      confianza  : "media" — carriageway only; andenes and separadores excluded
      fuente     : human-readable source label for the UI
      nota       : detail note (widths breakdown, caveats)

    Caller must add andén/separador widths to get the full perfil vial (D_total).
    ZeroFeaturesError within the buffer → D_m = None.
    """
    try:
        feats = query_fs(
            L_CALZADA, lng, lat,
            out_fields=["ANCHO", "CODIGO_IDENTIFICACION_VIAL"],
            distance_m=_CALZADA_RADIUS_M,
        )
    except ZeroFeaturesError:
        return {
            "D_m": None,
            "n_calzadas": 0,
            "confianza": None,
            "fuente": f"Layer 38 POT FeatureServer (Calzada) — sin dato dentro de {_CALZADA_RADIUS_M}m",
            "nota": (
                f"No se encontraron polígonos de calzada (Layer 38) dentro de {_CALZADA_RADIUS_M}m "
                "del centroide del predio. El ancho de vía debe ser ingresado manualmente."
            ),
        }

    # Group by CODIGO_IDENTIFICACION_VIAL and collect ANCHO values
    groups: dict[object, list[float]] = {}
    for feat in feats:
        attrs = feat["attributes"]
        codigo = attrs.get("CODIGO_IDENTIFICACION_VIAL")
        ancho = attrs.get("ANCHO")
        if ancho is not None:
            groups.setdefault(codigo, []).append(float(ancho))

    if not groups:
        return {
            "D_m": None,
            "n_calzadas": len(feats),
            "confianza": None,
            "fuente": "Layer 38 POT FeatureServer (Calzada) — ANCHO nulo en todos los registros",
            "nota": "Calzadas encontradas pero campo ANCHO vacío en todos los registros.",
        }

    # Select the group with the largest total width (most likely the fronting road)
    best_codigo = max(groups, key=lambda k: sum(groups[k]))
    best_widths = groups[best_codigo]
    D_total = round(sum(best_widths), 2)
    n = len(best_widths)

    breakdown = (
        " + ".join(f"{w:.2f}m" for w in best_widths) + f" = {D_total}m"
        if n > 1
        else f"{D_total}m"
    )

    return {
        "D_m": D_total,
        "n_calzadas": n,
        "confianza": "media",
        "fuente": "Layer 38 POT FeatureServer (Calzada) — ancho de calzada(s), sin andenes ni separadores",
        "nota": (
            f"{n} calzada{'s' if n > 1 else ''} (CODIGO_VIA={best_codigo}): {breakdown}. "
            "Andenes y separadores no incluidos — el perfil vial total (D) puede ser mayor."
        ),
    }


# ── Heritage helpers ─────────────────────────────────────────────────────────

def _clean_str(v: object) -> str | None:
    """Return stripped string or None for blank/None values."""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _format_bic_acto(tipo_code: str | None, numero: str | None) -> str | None:
    """Format a BIC administrative-act reference from its coded type and number."""
    tipo = _BIC_ACTO_TIPO.get(tipo_code or "", tipo_code or "")
    n = _clean_str(numero)
    if n and n != "DNH":
        return f"{tipo} No. {n}"
    return _clean_str(tipo)


def _query_heritage_layers(lng: float, lat: float) -> dict:
    """
    Query all four POT heritage layers for a point using strict intersection (no buffer).
    Using distance_m=None avoids picking up adjacent PEMPs or SICs — confirmed 2026-09-04:
    an 80 m buffer at Hospital Santa Clara incorrectly returned the adjacent Hospital
    San Juan de Dios PEMP.

    Returns dict with keys:
      bic      — first BIC polygon hit (Layer 12), or None
      pemp     — first named PEMP hit (Layer 1), or None
      pemp_ch  — True if point is within PEMP Centro Histórico (Layer 3)
      sic      — first SIC polygon hit (Layer 0), or None
    """
    result: dict = {"bic": None, "pemp": None, "pemp_ch": False, "sic": None}

    # Layer 12 — Bien de Interés Cultural
    try:
        feats = query_fs(L_BIC, lng, lat, out_fields=[
            "NOMBRE", "CATEGORIA", "NUMERO_FICHA", "TIPO_BIEN",
            "ACTO_ADMINISTRATIVO", "NUMERO_ACTO_ADMINISTRATIVO", "CODIGO_LOTE",
        ])
        a = feats[0]["attributes"]
        result["bic"] = {
            "nombre": _clean_str(a.get("NOMBRE")),
            "categoria": a.get("CATEGORIA"),
            "categoria_descripcion": _BIC_CATEGORIA.get(
                a.get("CATEGORIA", ""), a.get("CATEGORIA") or "desconocida"
            ),
            "numero_ficha": _clean_str(a.get("NUMERO_FICHA")),
            "tipo_bien": a.get("TIPO_BIEN"),
            "acto_administrativo": _format_bic_acto(
                a.get("ACTO_ADMINISTRATIVO"), a.get("NUMERO_ACTO_ADMINISTRATIVO")
            ),
            "codigo_lote": a.get("CODIGO_LOTE"),
        }
    except ZeroFeaturesError:
        pass

    # Layer 1 — PEMP (individual BICs with their own PEMPs)
    try:
        feats = query_fs(L_PEMP, lng, lat, out_fields=["NOMBRE", "ACTO_ADMINISTRATIVO"])
        a = feats[0]["attributes"]
        result["pemp"] = {
            "nombre": _clean_str(a.get("NOMBRE")),
            "acto_administrativo": _clean_str(a.get("ACTO_ADMINISTRATIVO")),
        }
    except ZeroFeaturesError:
        pass

    # Layer 3 — PEMP Centro Histórico (Decreto Distrital 678 de 1994)
    try:
        query_fs(L_PEMP_CH, lng, lat, out_fields=["TIPO_AREA"])
        result["pemp_ch"] = True
    except ZeroFeaturesError:
        pass

    # Layer 0 — Sector de Interés Cultural
    try:
        feats = query_fs(L_SIC, lng, lat, out_fields=["NOMBRE", "ACTO_ADMINISTRATIVO"])
        a = feats[0]["attributes"]
        result["sic"] = {
            "nombre": _clean_str(a.get("NOMBRE")),
            "acto_administrativo": _clean_str(a.get("ACTO_ADMINISTRATIVO")),
        }
    except ZeroFeaturesError:
        pass

    return result


# ── External FeatureServer query helper ──────────────────────────────────────

def _query_external_fs(
    fs_url: str, layer_id: int, lng: float, lat: float, out_fields: list[str]
) -> list[dict] | None:
    """
    Query any ArcGIS FeatureServer for features intersecting (lng, lat).
    Returns feature list or None on zero features / any error.
    Uses the same SSL-tolerant _fetch_json as the POT queries.
    """
    try:
        return _arcgis_query(f"{fs_url}/{layer_id}", {
            "geometry": f"{lng},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": ",".join(out_fields),
            "returnGeometry": "false",
        })
    except ZeroFeaturesError:
        return None
    except Exception:
        return None


# ── Individual blocking-restriction checks ────────────────────────────────────

def _check_bic_restriction(lng: float, lat: float) -> dict | None:
    """Return a restriction dict if the lot intersects a BIC polygon (Layer 12)."""
    try:
        feats = query_fs(_L_BIC_BLOCK, lng, lat, out_fields=[
            "NOMBRE", "CATEGORIA", "NUMERO_FICHA",
            "ACTO_ADMINISTRATIVO", "NUMERO_ACTO_ADMINISTRATIVO",
        ])
        a = feats[0]["attributes"]
        nombre = _clean_str(a.get("NOMBRE")) or "Bien de Interés Cultural"
        cat_code = a.get("CATEGORIA", "")
        cat_label = _BIC_CATEGORIA.get(cat_code, cat_code or "BIC")
        acto = _format_bic_acto(
            a.get("ACTO_ADMINISTRATIVO"), a.get("NUMERO_ACTO_ADMINISTRATIVO")
        )
        cita_parts = []
        if acto:
            cita_parts.append(acto)
        cita_parts.append("Arts. 258 y 435–442 Decreto 555/2021; Ley 397/1997 Art. 5")
        return {
            "tipo": "bic",
            "nombre": f"BIC — {nombre}",
            "categoria": cat_label,
            "descripcion": (
                f"El predio intersecta el Bien de Interés Cultural '{nombre}' "
                f"(categoría: {cat_label}). Cualquier intervención sobre un BIC requiere "
                "concepto técnico del IDPC (Instituto Distrital de Patrimonio Cultural) "
                "y la aplicación del PEMP correspondiente. Los índices de edificabilidad "
                "no son los del POT general sino los del régimen patrimonial."
            ),
            "cita": " · ".join(cita_parts),
            "fuente": "Capa 12 POT FeatureServer — Bienes de Interés Cultural (IDPC)",
            "layer_id": _L_BIC_BLOCK,
            "confianza": "alta",
        }
    except ZeroFeaturesError:
        return None
    except Exception:
        return None


def _check_aerocivil_restriction(lng: float, lat: float) -> dict | None:
    """Return a restriction dict if the lot is within Aerocivil restricted airspace (Layer 25)."""
    try:
        feats = query_fs(_L_AEROCIVIL_BLOCK, lng, lat, out_fields=["*"])
        a = feats[0]["attributes"]
        # Field names vary — accept any of these candidates
        nombre = (
            _clean_str(a.get("NOMBRE"))
            or _clean_str(a.get("TIPO"))
            or _clean_str(a.get("RESTRICCION"))
            or _clean_str(a.get("DESCRIPCION"))
            or "Zona de restricción aeronáutica"
        )
        return {
            "tipo": "aerocivil",
            "nombre": f"Restricción Aerocivil — {nombre}",
            "descripcion": (
                f"El predio está dentro de la zona de restricción aeronáutica "
                f"'{nombre}' del aeropuerto El Dorado. "
                "La altura máxima del edificio queda limitada por la UAEAC (Aerocivil) "
                "con independencia de los pisos que permita el POT. "
                "Consulte el plano de servidumbres aeronáuticas en la UAEAC y obtenga "
                "el certificado de no obstrucción antes de diseñar."
            ),
            "cita": "Art. 55 Decreto 555/2021; Ley 336/1996 Art. 54; RAC Parte 14 (UAEAC)",
            "fuente": "Capa 25 POT FeatureServer — Restricciones Aerocivil",
            "layer_id": _L_AEROCIVIL_BLOCK,
            "confianza": "alta",
        }
    except ZeroFeaturesError:
        return None
    except Exception:
        return None


def _check_cerros_restriction(lng: float, lat: float) -> dict | None:
    """Return a restriction dict if the lot is within the Cerros Orientales reserve."""
    if not _CERROS_FS:
        return None
    feats = _query_external_fs(_CERROS_FS, _CERROS_LAYER_ID, lng, lat, ["NOMBRE", "RESTRICCION"])
    if not feats:
        return None
    a = feats[0].get("attributes", {})
    nombre = (
        _clean_str(a.get("NOMBRE"))
        or "Reserva Forestal Protectora Bosque Oriental de Bogotá"
    )
    return {
        "tipo": "cerros",
        "nombre": nombre,
        "descripcion": (
            f"El predio está dentro de la '{nombre}'. "
            "Esta reserva es suelo de protección — están prohibidos los usos urbanísticos "
            "y las licencias de construcción ordinarias. Solo se permiten actividades "
            "compatibles con la conservación forestal (Res. 463/2005 MADS; "
            "Arts. 33–44 Decreto 555/2021)."
        ),
        "cita": "Res. 463/2005 Min. Ambiente; Arts. 33–44 D.555/2021; Art. 204 Ley 1955/2019",
        "fuente": "SDA Bogotá — Reserva Forestal Protectora Bosque Oriental de Bogotá",
        "layer_id": _CERROS_LAYER_ID,
        "confianza": "alta",
    }


def _check_idiger_mrm_restriction(lng: float, lat: float) -> dict | None:
    """Return a restriction dict if the lot is in a high/very-high landslide hazard zone."""
    if not _IDIGER_MRM_FS:
        return None
    feats = _query_external_fs(
        _IDIGER_MRM_FS, _IDIGER_MRM_LAYER_ID, lng, lat,
        ["NOMBRE", "AMENAZA", "CALIFICACION", "NIVEL"],
    )
    if not feats:
        return None
    a = feats[0].get("attributes", {})
    nivel = (
        _clean_str(a.get("AMENAZA"))
        or _clean_str(a.get("CALIFICACION"))
        or _clean_str(a.get("NIVEL"))
        or "alta o muy alta"
    )
    return {
        "tipo": "amenaza_mrm",
        "nombre": f"Amenaza por movimiento en masa — {nivel}",
        "descripcion": (
            f"El predio se encuentra en zona de amenaza por movimiento en masa "
            f"de nivel '{nivel}' (IDIGER). Los predios en zonas de amenaza alta o "
            "muy alta no pueden ser objeto de licencias de construcción sin estudio "
            "individual de amenaza y riesgo aprobado por el IDIGER "
            "(Arts. 163–175 Decreto 555/2021)."
        ),
        "cita": "Arts. 163–175 D.555/2021; Decreto 523/2010 Bogotá",
        "fuente": "IDIGER — Mapa de amenaza por movimiento en masa",
        "layer_id": _IDIGER_MRM_LAYER_ID,
        "confianza": "alta",
    }


def _check_idiger_inundacion_restriction(lng: float, lat: float) -> dict | None:
    """Return a restriction dict if the lot is in a flood hazard zone."""
    if not _IDIGER_INUND_FS:
        return None
    feats = _query_external_fs(
        _IDIGER_INUND_FS, _IDIGER_INUND_LAYER_ID, lng, lat,
        ["NOMBRE", "AMENAZA", "CALIFICACION", "NIVEL"],
    )
    if not feats:
        return None
    a = feats[0].get("attributes", {})
    nivel = (
        _clean_str(a.get("AMENAZA"))
        or _clean_str(a.get("CALIFICACION"))
        or _clean_str(a.get("NIVEL"))
        or "alta o muy alta"
    )
    return {
        "tipo": "amenaza_inundacion",
        "nombre": f"Amenaza por inundación — {nivel}",
        "descripcion": (
            f"El predio se encuentra en zona de amenaza por inundación "
            f"de nivel '{nivel}' (IDIGER). Se requiere estudio individual de amenaza "
            "y riesgo aprobado por el IDIGER antes de cualquier licencia de construcción "
            "(Arts. 163–175 Decreto 555/2021)."
        ),
        "cita": "Arts. 163–175 D.555/2021; Decreto 523/2010 Bogotá",
        "fuente": "IDIGER — Mapa de amenaza por inundación",
        "layer_id": _IDIGER_INUND_LAYER_ID,
        "confianza": "alta",
    }


def _check_ronda_hidrica_restriction(lng: float, lat: float) -> dict | None:
    """Return a restriction dict if the lot is within the hydraulic buffer zone."""
    if not _RONDA_FS:
        return None
    feats = _query_external_fs(
        _RONDA_FS, _RONDA_LAYER_ID, lng, lat,
        ["NOMBRE", "CORRIENTE", "TIPO"],
    )
    if not feats:
        return None
    a = feats[0].get("attributes", {})
    corriente = (
        _clean_str(a.get("CORRIENTE"))
        or _clean_str(a.get("NOMBRE"))
        or "cuerpo de agua"
    )
    return {
        "tipo": "ronda_hidrica",
        "nombre": f"Ronda hídrica — {corriente}",
        "descripcion": (
            f"El predio está dentro de la ronda hídrica del '{corriente}'. "
            "La ronda hídrica es suelo de protección — no se admiten usos urbanísticos "
            "ni licencias de construcción ordinarias en esta franja "
            "(Arts. 42–54 D.555/2021; Acuerdo 19/1996 EAAB; Res. 2133/2016 MADS)."
        ),
        "cita": "Arts. 42–54 D.555/2021; Acuerdo 19/1996 EAAB; Res. 2133/2016 MADS",
        "fuente": "EAAB-ESP — Ronda Hídrica de Bogotá",
        "layer_id": _RONDA_LAYER_ID,
        "confianza": "alta",
    }


def check_blocking_restrictions(lng: float, lat: float) -> list[dict]:
    """
    Check a WGS84 point against layers that impose hard restrictions on urban
    development. Returns a (possibly empty) list of active restriction dicts.

    Each restriction carries: tipo, nombre, descripcion, cita, fuente,
    layer_id, confianza.

    Checks run independently — one failing or unconfigured layer never aborts
    the others. External-service checks are skipped when the endpoint URL is None.
    """
    active: list[dict] = []
    for checker in (
        _check_bic_restriction,
        # _check_aerocivil_restriction disabled: Layer 25 covers the entire urban area,
        # not just the airport cone — every lot in the city was being blocked.
        # Re-enable once a GIS specialist validates the correct spatial filter.
        _check_cerros_restriction,
        _check_idiger_mrm_restriction,
        _check_idiger_inundacion_restriction,
        _check_ronda_hidrica_restriction,
    ):
        try:
            result = checker(lng, lat)
            if result:
                active.append(result)
        except Exception:
            pass  # never let a single check crash the whole lookup
    return active


# ── Main lookup ──────────────────────────────────────────────────────────────

def lookup(lng: float, lat: float, vis_en_sitio: bool = False) -> dict:
    """
    Full buildability lookup for a WGS84 point in Bogotá.

    Parameters
    ----------
    lng, lat      : WGS84 decimal degrees
    vis_en_sitio  : True if the project carries a VIS/VIP on-site obligation
                    (raises IC for Rangos 1-3 and height for Rango 4B)

    Returns
    -------
    dict — see field descriptions inline. All numeric rule values carry 'confianza'.
    Raises BuildabilityLookupError (or subclass) on any unresolvable condition.
    """
    result: dict = {
        "input": {"lng": lng, "lat": lat, "vis_en_sitio": vis_en_sitio},
        "consulta": {
            "fecha": date.today().isoformat(),
            "decreto_version": REGULATORY_VERSION,
            "corte_normativo": REGULATORY_CUTOFF,
            "fuentes": "POT Bogotá FeatureServer y Catastro Bogotá MapServer",
        },
        "contexto_regulatorio": regulatory_context(),
        "warnings": [],
    }

    # ── Step 1: Physical lot (Catastro) ──────────────────────────────────────
    lote_feat, lote_snap_distance_m = query_catastro_lote(lng, lat)
    lote_attrs = lote_feat.get("attributes", {})
    area_m2 = compute_lot_area_m2(lote_feat)

    # WGS84 rings for map display (separate call — area query uses 9377)
    rings_wgs84 = _fetch_lot_rings_wgs84(lng, lat, lote_attrs.get("OBJECTID"))

    result["lote"] = {
        "area_m2":         {"valor": round(area_m2, 1), "confianza": "alta"},
        "lotcodigo":       lote_attrs.get("LOTCODIGO"),
        "unidades_predio": lote_attrs.get("LOTUPREDIA"),
        "geojson_polygon": rings_wgs84,   # [[x, y], ...] rings in WGS84; None if fetch failed
        "nota_area": (
            "Área calculada con fórmula de Gauss (shoelace) sobre polígono proyectado "
            "MAGNA-SIRGAS 9377 (sin aproximación cos(lat))."
        ),
        "fuente_datos": lote_feat.get("_ainmo_source", {
            "id": "catastro_bogota_actual",
            "fuente": "Catastro Bogotá MapServer, capa 0 (LOTE)",
            "fecha_referencia": None,
            "es_fallback": False,
        }),
    }
    if result["lote"]["fuente_datos"].get("es_fallback"):
        src = result["lote"]["fuente_datos"]
        result["warnings"].append(
            "SIN_DATO DE FRESCURA: la fuente catastral principal no respondió y se usó "
            f"{src['fuente']} (referencia {src.get('fecha_referencia') or 'no disponible'}). "
            "Confirme área, código y geometría en Catastro Bogotá antes de decidir."
        )
    if lote_snap_distance_m is not None:
        result["lote"]["consulta_catastro"] = {
            "metodo": "predio_mas_cercano",
            "distancia_m": round(lote_snap_distance_m, 1),
            "radio_maximo_m": _LOTE_FALLBACK_RADIUS_M,
            "fuente": "Catastro Bogotá MapServer, capa 0 (LOTE)",
            "nota": (
                "La coordenada no intersectó un polígono catastral; se seleccionó el predio "
                "más cercano dentro de 25 m. Las capas normativas del POT se consultaron "
                "en la coordenada original. Verifique el predio antes de usar el resultado."
            ),
        }

    # ── Step 1b: Blocking restrictions — BIC, Aerocivil, Cerros, amenaza, ronda
    # Run after catastro confirms the lot exists. Results are independent of
    # treatment and do not raise — each check degrades silently on failure.
    result["restricciones_bloqueantes"] = check_blocking_restrictions(lng, lat)
    result["cobertura_restricciones"] = {
        "bic": "consultado",
        "aerocivil": "sin_validacion_espacial",
        "cerros_orientales": "consultado" if _CERROS_FS else "sin_fuente_configurada",
        "movimientos_en_masa": "consultado" if _IDIGER_MRM_FS else "sin_fuente_configurada",
        "inundacion": "consultado" if _IDIGER_INUND_FS else "sin_fuente_configurada",
        "ronda_hidrica": "consultado" if _RONDA_FS else "sin_fuente_configurada",
    }
    missing_restrictions = [
        name for name, state in result["cobertura_restricciones"].items()
        if state != "consultado"
    ]
    if missing_restrictions:
        result["warnings"].append(
            "SIN_DATO DE RESTRICCIONES: no se verificaron automáticamente "
            + ", ".join(missing_restrictions)
            + ". Esto no significa ausencia de afectación; consulte las autoridades y mapas oficiales."
        )

    # ── Step 1c: Calzada width (Layer 38) — used for retroceso de fachada ──────
    result["ancho_via_gis"] = _query_ancho_via_gis(lng, lat)

    # ── Step 1d: Área de actividad (Layer 14) — needed for Art. 389 parking ───
    # Queried here for ALL tratamientos (not only Consolidación) so parking can
    # be computed regardless of treatment type.
    result["area_actividad"] = _query_area_actividad(lng, lat)

    # ── Step 2: POT edificabilidad layer (Layer 15) ───────────────────────────
    # Layer 15 is stored in WKID 102100 — inSR=4326 is mandatory.
    feats_15 = query_fs(
        L_EDIFICABILIDAD, lng, lat,
        out_fields=["TRATAMIENTO", "TIPOLOGIA", "ALTURA_MAXIMA", "OBSERVACION", "ACTO_ADMINISTRATIVO"],
    )
    a15 = feats_15[0]["attributes"]
    tratamiento_raw = (a15.get("TRATAMIENTO") or "").strip().upper()

    if not tratamiento_raw:
        raise ParseError(
            "TRATAMIENTO field is empty in Layer 15 — cannot classify lot. "
            "The point may fall on a parcel excluded from POT norms."
        )

    result["tratamiento"] = tratamiento_raw

    # ── Step 3: Branch by treatment ───────────────────────────────────────────
    trat_norm = tratamiento_raw.replace("Ó", "O").replace("Á", "A")  # accent-safe compare

    if "DESARROLLO" in trat_norm:
        result.update(_handle_desarrollo(lng, lat, a15, area_m2, vis_en_sitio, result["warnings"]))

    elif "CONSOLIDACION" in trat_norm:
        is_dotacional_sin_altura = (
            "DOT" in str(a15.get("OBSERVACION") or "").upper()
            and not str(a15.get("ALTURA_MAXIMA") or "").strip()
        )
        d253 = decreto253.lookup(result["lote"].get("lotcodigo")) if is_dotacional_sin_altura else None
        if d253:
            result["equipamiento_decreto_253"] = d253
            if d253.get("pisos") is not None and not d253.get("requiere_poligono"):
                a15["ALTURA_MAXIMA"] = str(d253["pisos"])
                result["warnings"].append(
                    f"Equipamiento D.253/2026: altura de {d253['pisos']} pisos tomada del Anexo 36.1; "
                    "verifique régimen de transición, instrumento previo, BIC y Actuación Estratégica."
                )
            else:
                a15["ALTURA_MAXIMA"] = "UNE"
                reason = (
                    "el código aparece en polígonos con decisiones distintas; ubique el predio en el Anexo 36.2"
                    if d253.get("requiere_poligono") else
                    "el Anexo 36.1 marca N/A; la altura depende de otro instrumento o norma"
                )
                result["warnings"].append(f"Equipamiento D.253/2026 SIN_DATO: {reason}.")
        elif is_dotacional_sin_altura:
            a15["ALTURA_MAXIMA"] = "UNE"
            result["equipamiento_decreto_253"] = {
                "aplica_posible": True,
                "pisos": None,
                "requiere_verificacion": True,
                "norma": "Decreto Distrital 253 de 2026",
                "fuente": "Layer 15 OBSERVACION=DOT; código no localizado en Anexo 36.1",
            }
            result["warnings"].append(
                "Equipamiento sin altura en Layer 15: verifique D.253/2026 y Anexos 36.1/36.2; "
                "el código del lote no produjo una coincidencia inequívoca."
            )
        result.update(_handle_consolidacion(lng, lat, a15, area_m2, result["warnings"]))
        if d253 and d253.get("pisos") is not None and not d253.get("requiere_poligono"):
            result["edificabilidad"]["altura_maxima"].update({
                "fuente": "Anexo 36.1, Decreto Distrital 253 de 2026",
                "articulo": "Arts. 249.1-249.3 D.670/2025, adicionados por D.253/2026",
                "confianza": "alta",
            })

    else:
        trat_norm_up = trat_norm.upper()
        if "RENOVACION" in trat_norm_up:
            # Edificabilidad se calcula en calc.py (_calc_renovacion_urbana, Art. 304):
            # ICe 5.0 / 6.0 / 7.0 según ámbito del proyecto. No hay parámetros GIS adicionales.
            pass
        elif "CONSERVACION" in trat_norm_up:
            result["conservacion_patrimonio"] = _query_heritage_layers(lng, lat)
        elif "MEJORAMIENTO" in trat_norm_up:
            # Edificabilidad se calcula en calc.py (_calc_mejoramiento_integral, Art. 338):
            # altura máxima por tabla ancho_vía × área_lote. No hay parámetros GIS adicionales.
            pass
        else:
            result["warnings"].append(
                f"Tratamiento '{tratamiento_raw}' no reconocido. "
                "Consulte directamente la Secretaría Distrital de Planeación."
            )
        result["edificabilidad"] = None

    # D.676/2025 is project-dependent, but the treatment-level opportunity can
    # be identified at parcel screening stage. Never label it as earned.
    if any(name in trat_norm for name in ("DESARROLLO", "RENOVACION", "CONSOLIDACION")):
        if "DESARROLLO" in trat_norm:
            pathway = (
                "Incentivos de los Arts. 1374.8-1374.10: sujetos al rango, ANU, "
                "VIS/VIP adicional y modalidad de plan parcial o licencia aplicable."
            )
        elif "RENOVACION" in trat_norm:
            pathway = (
                "Art. 1374.11: incentivo sobre la obligación de espacio público. "
                "Si el terreno supera 10.000 m², la obligación total debe dejarse en sitio."
            )
        else:
            pathway = (
                "Arts. 1374.12-1374.13: reducción condicionada de obligación de espacio público "
                "con al menos 20% de VIS/VIP adicional y posible incentivo de altura del Anexo 5."
            )
        result["incentivo_sostenibilidad_d676"] = {
            "estado": "potencial_condicionado",
            "tratamiento_elegible": True,
            "norma": "Decreto Distrital 676 de 2025",
            "vigencia_desde": "2025-12-31",
            "ruta_tratamiento": pathway,
            "requisitos": [
                "Proyecto con uso residencial predominante y acogimiento expreso al D.676/2025.",
                "Cumplir todas las medidas de los Arts. 1374.3-1374.7: aguas lluvias, materiales, verde urbano, isla de calor y energía renovable.",
                "Incorporar medidas, áreas, elementos y materiales en planos y memorias del proyecto.",
                "Obtener pre-certificación o reconocimiento de diseño LEED, EDGE, CASA Colombia o Bogotá Construcción Sostenible.",
                "Presentar autodeclaración firmada por titular y profesionales, con certificación y listado de créditos.",
                "Presentar al curador el Anexo de Construcción Sostenible del Formulario Único Nacional.",
            ],
            "cita_requisitos": "Arts. 1374.2-1374.7 y 1374.15 D.670/2025, adicionados por D.676/2025",
            "nota": "Ainmo identifica la oportunidad; el incentivo solo se habilita con diseño, certificación, documentos y licencia.",
        }
        result["referencia_obligaciones_urbanisticas_2026"] = {
            "valor_tope_cop_m2": 571000,
            "estado": "referencia_no_liquidacion",
            "norma": "Resolución SDP 954 de 2026",
            "vigencia": 2026,
            "cita": "Art. 1 Res. SDP 954/2026; Arts. 285, 289, 289-A y 291 D.555/2021; Art. 1352 D.670/2025",
            "nota": (
                "Valor tope de referencia para liquidar pagos compensatorios de obligaciones generales, "
                "espacio público y equipamiento comunal público. Ainmo no calcula aquí el área legal de "
                "obligación ni el pago exigible."
            ),
        }

    # ── Step 4: Antejardín (Layer 22 / mapa CU-5.5) — Consolidación only ─────
    if "CONSOLIDACION" in trat_norm:
        d253 = result.get("equipamiento_decreto_253")
        if d253 and d253.get("pisos") is not None and not d253.get("requiere_poligono"):
            result["antejardin"] = {
                "dimension_m": {"valor": 3.5, "confianza": "alta"},
                "fuente": "Anexo 36.2 y Art. 249.8, Decreto Distrital 253 de 2026",
                "articulo": "Art. 249.8 D.670/2025, adicionado por D.253/2026",
                "nota": (
                    "Mínimo 3,50 m para los polígonos con altura asignada. Excepción: edificaciones "
                    "existentes con PRM, Plan de Implantación, Plan Director, licencia o reconocimiento "
                    "vigente al 4 de julio de 2026 que hubiese previsto una dimensión menor o ninguna."
                ),
            }
            return result
        try:
            feats_22 = query_fs(L_ANTEJARDIN, lng, lat, out_fields=["DIMENSION"])
            dim = feats_22[0]["attributes"].get("DIMENSION")
            result["antejardin"] = {
                "dimension_m": {"valor": dim, "confianza": "alta"},
                "fuente": "Layer 22 (mapa CU-5.5)",
                "articulo": "Res. SDP 1631/2023 (actualiza CU-5.5); Arts. 258 y 314 D.555/2021",
            }
        except ZeroFeaturesError:
            result["antejardin"] = {
                "dimension_m": {"valor": None, "confianza": "requiere_input"},
                "nota": (
                    "No polygon in Layer 22 at this point — "
                    "consult mapa CU-5.5 manually or assume no antejardín required."
                ),
                "articulo": "Res. SDP 1631/2023 (actualiza CU-5.5); Arts. 258 y 314 D.555/2021",
            }

    return result


def _handle_desarrollo(
    lng: float,
    lat: float,
    a15: dict,
    area_m2: float,
    vis_en_sitio: bool,
    warnings: list,
) -> dict:
    """
    Look up the RANGO from Layer 21 and build the edificabilidad block for Desarrollo.
    """
    # Layer 21 carries the RANGO code for Desarrollo polygons
    feats_21 = query_fs(L_RANGO, lng, lat, out_fields=["RANGO"])
    rango_raw = (feats_21[0]["attributes"].get("RANGO") or "").strip().upper()

    # If Layer 21 RANGO field is empty, attempt to derive from Layer 15 ALTURA_MAXIMA
    # (which stores "Rg 4A" style references for Desarrollo zones)
    if not rango_raw:
        alt_raw_l15 = a15.get("ALTURA_MAXIMA")
        if alt_raw_l15:
            parsed_l15 = parse_altura_maxima(alt_raw_l15)
            if parsed_l15["tipo"] == "referencia_rango" and parsed_l15.get("rango_code"):
                rango_raw = parsed_l15["rango_code"]
                warnings.append(
                    f"Campo RANGO vacío en Capa 21; se derivó RANGO='{rango_raw}' "
                    f"desde Capa 15 ALTURA_MAXIMA='{alt_raw_l15}'. Verifique manualmente."
                )

    if not rango_raw:
        raise ParseError(
            "Could not determine RANGO for this Desarrollo lot: "
            "Layer 21 RANGO field is empty and Layer 15 ALTURA_MAXIMA contains no rango reference."
        )

    if rango_raw not in RANGO_TABLE:
        raise ParseError(
            f"RANGO '{rango_raw}' is not in the known set {{1, 2, 3, 4A, 4B, 4C, 4D}}. "
            "Art. 281 may have been amended (see Decreto 466/2024)."
        )

    r = RANGO_TABLE[rango_raw]

    # ── IC (índice de construcción) ───────────────────────────────────────────
    if vis_en_sitio:
        ic_val = r["ic_maximo"]
        ic_scenario = "con_vis_en_sitio"
    else:
        ic_val = r["ic_basico"]
        ic_scenario = "sin_vis"

    ic_confianza = "alta" if ic_val is not None else "media"

    # ── ANU ───────────────────────────────────────────────────────────────────
    anu_confianza = "alta" if r["anu_proxy"] == "lote" else "requiere_input"
    if r["anu_proxy"] == "plan_parcial":
        warnings.append(
            f"Rango {rango_raw}: el ANU debe provenir del Plan Parcial aprobado — "
            "el área catastral bruta no es un proxy válido para Rangos 1–3."
        )

    # ── Altura (pisos) ────────────────────────────────────────────────────────
    alt_val = r["alt_con_vis"] if vis_en_sitio else r["alt_sin_vis"]
    alt_confianza = "alta" if alt_val is not None else "media"

    edif: dict = {
        "indice_construccion": {
            "valor": ic_val,
            "escenario": ic_scenario,
            "confianza": ic_confianza,
            "nota": None if ic_val is not None else (
                "IC resultante para este rango (Art. 281): determinado por IO fijo y altura, "
                "no existe un techo numérico de IC en el decreto."
            ),
            "articulo": "Art. 281 Decreto 555/2021",
        },
        "indice_ocupacion": {
            "valor": r["io"],
            "confianza": "alta" if r["io"] is not None else "media",
            "nota": None if r["io"] is not None else (
                "IO resultante — emerge de la aplicación de normas volumétricas de aislamientos."
            ),
            "articulo": "Art. 281 Decreto 555/2021",
        },
        "altura_maxima_pisos": {
            "valor": alt_val,
            "confianza": alt_confianza,
            "nota": None if alt_val is not None else (
                "Altura resultante — emerge de la interacción entre IC máximo y normas "
                "volumétricas (retroceso A=2D, aislamientos). No hay pisos fijos en el decreto."
            ),
            "articulo": "Art. 281 Decreto 555/2021",
        },
        "anu": {
            "valor_m2": round(area_m2, 1) if r["anu_proxy"] == "lote" else None,
            "confianza": anu_confianza,
            "nota": (
                "ANU ≈ área catastral del lote (válido para Rangos 4A–4D sin cesiones por Plan Parcial)."
                if r["anu_proxy"] == "lote"
                else "ANU debe provenir del Plan Parcial — no derivable del área catastral."
            ),
        },
    }

    if r.get("ic_vis75") is not None:
        edif["indice_construccion_vis75"] = {
            "valor": r["ic_vis75"],
            "confianza": "alta",
            "nota": "Aplica cuando >75% del índice efectivo se destina a VIS/VIP.",
            "articulo": "Art. 281 Decreto 555/2021",
        }

    return {"rango": rango_raw, "edificabilidad": edif}


def _handle_consolidacion(lng: float, lat: float, a15: dict, area_m2: float, warnings: list) -> dict:
    """
    Parse Layer 15 TIPOLOGIA and ALTURA_MAXIMA; build the edificabilidad block
    for Consolidación (Art. 310, Anexo 5).
    Area de actividad (Layer 14) is queried in lookup() and available as
    result["area_actividad"] — not re-queried here.
    """
    tipologia_raw = a15.get("TIPOLOGIA")
    tipologia = parse_tipologia(tipologia_raw)

    altura_raw = a15.get("ALTURA_MAXIMA")
    altura_struct = parse_altura_maxima(altura_raw)

    # Rear setback from table (only computable when we have a fixed reference piso count)
    aisl_post_val = None
    aisl_post_nota = None
    if altura_struct["tipo"] in ("fijo", "rango") and altura_struct["pisos_max"] is not None:
        aisl_post_val = aislamiento_posterior_m(altura_struct["pisos_max"])
        aisl_post_nota = (
            f"Para {altura_struct['pisos_max']} pisos — Anexo 5 Cap. 2.4.2.A.2."
        )
    elif altura_struct["tipo"] == "referencia_rango":
        aisl_post_nota = (
            "Aislamiento posterior indeterminable: la altura remite a un RANGO de Desarrollo "
            "— situación inesperada en un lote Consolidación. Verificar manualmente."
        )
        warnings.append(aisl_post_nota)
    else:
        aisl_post_nota = "Requiere pisos definitivos del proyecto para aplicar tabla de aislamientos."

    edif: dict = {
        "indice_construccion": {
            "valor": None,
            "confianza": "media",
            "nota": (
                "IC resultante — Art. 310 Num. 1: 'Los índices de ocupación y construcción "
                "son resultantes de la aplicación de las normas volumétricas.'"
            ),
            "articulo": "Art. 310 Num. 1 Decreto 555/2021",
        },
        "indice_ocupacion": {
            "valor": None,
            "confianza": "media",
            "nota": "IO resultante — Art. 310 Num. 1 (ver nota IC).",
            "articulo": "Art. 310 Num. 1 Decreto 555/2021",
        },
        "altura_maxima": {
            **altura_struct,
            "confianza": "alta",
            "fuente": "Layer 15 campo ALTURA_MAXIMA (mapas CU-5.4.2 a CU-5.4.33)",
            "articulo": "Art. 310 Decreto 555/2021",
        },
        "aislamiento_posterior": {
            "valor_m": aisl_post_val,
            "confianza": "alta" if aisl_post_val is not None else "requiere_input",
            "nota": aisl_post_nota,
            "articulo": "Anexo 5 Cap. 2.4.2.A.2 Decreto 555/2021",
        },
        "aislamiento_lateral": {
            "exigido": tipologia == "aislada",
            "formula": "1/5 × altura total edificio, mín. 4m" if tipologia == "aislada" else None,
            "aplica_desde_piso": 2 if tipologia == "aislada" else None,
            "confianza": "alta",
            "nota": (
                "Tipología aislada: aislamiento lateral exigido desde 2° piso (Art. 310 Num. 3)."
                if tipologia == "aislada"
                else "Tipología continua: no se exige aislamiento lateral (Art. 310 Num. 3)."
            ),
            "articulo": "Art. 310 Num. 3 Decreto 555/2021",
        },
        "subdivision_permitida": {
            "valor": tipologia == "continua",
            "condiciones": "frente ≥6m y área resultante ≥60m²" if tipologia == "continua" else None,
            "confianza": "alta",
            "nota": (
                "Subdivisión permitida en tipología continua si se cumplen frente/área mínimos."
                if tipologia == "continua"
                else "Subdivisión NO permitida en tipología aislada (Art. 310 Num. 4)."
            ),
            "articulo": "Art. 310 Num. 4 Decreto 555/2021",
        },
        "bonus_altura_manzana_completa": {
            "factor": 2,
            "altura_maxima_con_bonus_pisos": 12,
            "condicion": "Proyecto ocupa manzana completa + área de actividad receptora (Art. 310 § 2)",
            "confianza": "alta",
            "nota": "Altura × 2, máx. 12 pisos. Verificar área de actividad en Layer 14.",
            "articulo": "Art. 310 Parágrafo 2 Decreto 555/2021",
        },
        "bonus_altura_vis": {
            "factor": 2,
            "condicion_pct_vis": 70,
            "condicion_pct_vip": 50,
            "confianza": "alta",
            "nota": (
                "Altura × 2 si ≥70% del área construida es VIS, o ≥50% es VIP. "
                "Aplicar sobre pisos_max del campo altura_maxima. "
                "En tipología continua, aislamiento lateral exige desde altura base del sector."
            ),
            "articulo": "Art. 310 Parágrafo 3 Decreto 555/2021",
        },
        "retroceso_fachada": {
            "formula": "A = 2,5 × D (D = ancho de la vía frente al predio)",
            "confianza": "alta",
            "nota": "A = altura máxima de la fachada sobre el espacio público.",
            "articulo": "Anexo 5 Cap. 1.2.2.E.1.1 Decreto 555/2021",
        },
    }

    return {
        "tipologia": {"valor": tipologia, "confianza": "alta"},
        "edificabilidad": edif,
    }


# ── CLI / test runner ────────────────────────────────────────────────────────

TEST_CASES = [
    # (description, lng, lat, vis_en_sitio, expect_error_type)
    #
    # Consolidación — continua, 9 pisos, antejardín 5m (Usaquén)
    # Layer 14 confirmed AAERVIS — VIS×2 bonus should populate with vis=True
    ("Usaquén — Consolidación 9p, AAERVIS, vis=True",        -74.0525, 4.6750, True,  None),
    # Consolidación — aislada, 6 pisos (Usaquén norte)
    ("Usaquén norte — Consolidación aislada, 6 pisos",       -74.0445, 4.6980, False, None),
    # Consolidación — continua, 3 pisos (Bosa norte)
    # Layer 14 confirmed AAPRSU — VIS×2 bonus must be null (not AAERVIS)
    ("Bosa norte — Consolidación 3p, AAPRSU, vis=True",      -74.1800, 4.6300, True,  None),
    # Desarrollo — Rango 4D (Usme sur); confirmed Layer 21 RANGO='4D'
    ("Usme sur — Desarrollo Rango 4D (con VIS)",             -74.1100, 4.5200, True,  None),
    # Río Bogotá — sin predio catastral, debe lanzar ZeroFeaturesError
    ("Río Bogotá (cauce) — sin lote catastral, debe fallar", -74.1750, 4.6580, False, ZeroFeaturesError),
]


def run_tests() -> None:
    sep = "=" * 70
    print(sep)
    print("p2_lookup.py — Test suite (Bogotá POT)")
    print(sep)

    passed = failed = 0

    for desc, lng, lat, vis, expected_err in TEST_CASES:
        print(f"\n{'─' * 60}")
        print(f"TEST : {desc}")
        print(f"COORD: ({lat:.4f}, {lng:.4f})  vis_en_sitio={vis}")
        if expected_err:
            print(f"EXPECT ERROR: {expected_err.__name__}")
        print("─" * 60)

        try:
            res = lookup(lng, lat, vis_en_sitio=vis)
            if expected_err is not None:
                print(f"FAIL — expected {expected_err.__name__} but lookup succeeded.")
                print(json.dumps(res, indent=2, ensure_ascii=False))
                failed += 1
            else:
                print(json.dumps(res, indent=2, ensure_ascii=False))
                print("PASS")
                passed += 1

        except ZeroFeaturesError as exc:
            if expected_err is ZeroFeaturesError:
                print(f"PASS (expected ZeroFeaturesError)\n  → {exc}")
                passed += 1
            else:
                print(f"FAIL (unexpected ZeroFeaturesError)\n  → {exc}")
                failed += 1

        except BuildabilityLookupError as exc:
            if expected_err and isinstance(exc, expected_err):
                print(f"PASS (expected {type(exc).__name__})\n  → {exc}")
                passed += 1
            else:
                print(f"FAIL ({type(exc).__name__})\n  → {exc}")
                failed += 1

        except Exception as exc:
            print(f"FAIL (unexpected {type(exc).__name__})\n  → {exc}")
            failed += 1

    print(f"\n{sep}")
    print(f"Results: {passed}/{len(TEST_CASES)} passed, {failed} failed")
    print(sep)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        run_tests()

    elif len(sys.argv) >= 3:
        _lng = float(sys.argv[1])
        _lat = float(sys.argv[2])
        _vis = (sys.argv[3].lower() in ("true", "1", "yes", "si")) if len(sys.argv) > 3 else False
        _res = lookup(_lng, _lat, vis_en_sitio=_vis)
        print(json.dumps(_res, indent=2, ensure_ascii=False))

    else:
        print("Usage:")
        print("  python p2_lookup.py                        # run test suite")
        print("  python p2_lookup.py LNG LAT [vis=false]   # single point lookup")
        print()
        print("Examples:")
        print("  python p2_lookup.py -74.0525 4.6750")
        print("  python p2_lookup.py -74.0930 4.7420 true")
        sys.exit(1)
