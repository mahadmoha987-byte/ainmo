#!/usr/bin/env python3
"""
dxf_export.py — DXF plan/section export for edificabilidad results.

Layers:
  LOT          — lot boundary polygon
  SETBACK      — setback zone (dashed)
  FOOTPRINT    — buildable footprint after setbacks
  FLOORS       — floor plate outlines at each level
  FACADE_PLANE — max facade height (A) annotation line
  DIMENSIONS   — dimension annotations

Coordinates: local metric (equirectangular projection centred on lot centroid).
Heights:      vertical in the Z dimension.
"""
from __future__ import annotations

import math
from typing import Any

try:
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
except ImportError as exc:
    raise ImportError("ezdxf >= 1.0 required: pip install ezdxf") from exc


FLOOR_H = 3.0  # metres per floor


# ── Coordinate helpers ────────────────────────────────────────────────────────

def _project_ring(ring: list, lon0: float, lat0: float) -> list[tuple[float, float]]:
    cos_lat = math.cos(math.radians(lat0))
    return [
        ((lon - lon0) * cos_lat * 111_319, (lat - lat0) * 111_319)
        for lon, lat in ring
    ]


def _signed_area(pts: list[tuple]) -> float:
    a = 0.0
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        a += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1]
    return a / 2


def _ensure_ccw(pts: list[tuple]) -> list[tuple]:
    return pts if _signed_area(pts) >= 0 else list(reversed(pts))


def _inset_polygon(pts: list[tuple], d: float) -> list[tuple]:
    if d <= 0:
        return pts
    n = len(pts)
    out = []
    for i in range(n):
        prev = pts[(i - 1 + n) % n]
        curr = pts[i]
        nxt  = pts[(i + 1) % n]
        e1 = (curr[0] - prev[0], curr[1] - prev[1])
        e2 = (nxt[0] - curr[0], nxt[1] - curr[1])
        l1 = math.hypot(*e1) or 1e-9
        l2 = math.hypot(*e2) or 1e-9
        n1 = (e1[1] / l1, -e1[0] / l1)
        n2 = (e2[1] / l2, -e2[0] / l2)
        bx, by = n1[0] + n2[0], n1[1] + n2[1]
        bl = math.hypot(bx, by)
        if bl < 1e-9:
            out.append((curr[0] + d * n1[0], curr[1] + d * n1[1]))
            continue
        dot = (n1[0] * bx + n1[1] * by) / bl
        scale = d / dot if dot > 0.1 else d * 3
        out.append((curr[0] + scale * bx / bl, curr[1] + scale * by / bl))
    return out


def _bbox(pts: list[tuple]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


# ── DXF builder ───────────────────────────────────────────────────────────────

def generate_dxf(calc_result: dict, lookup_snapshot: dict) -> bytes:
    """Return a DXF file as bytes from calc + lookup data."""
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()

    # Layer definitions
    _layers = {
        "LOT":          {"color": 7, "linetype": "CONTINUOUS"},
        "SETBACK":      {"color": 1, "linetype": "DASHED"},
        "FOOTPRINT":    {"color": 3, "linetype": "CONTINUOUS"},
        "FLOORS":       {"color": 4, "linetype": "DASHED2"},
        "FACADE_PLANE": {"color": 2, "linetype": "DASHDOT"},
        "DIMENSIONS":   {"color": 5, "linetype": "CONTINUOUS"},
        "NOTES":        {"color": 8, "linetype": "CONTINUOUS"},
    }
    for name, props in _layers.items():
        layer = doc.layers.new(name=name)
        layer.color = props["color"]
        try:
            layer.linetype = props["linetype"]
        except Exception:
            pass

    rings = (lookup_snapshot or {}).get("lote", {}).get("geojson_polygon")
    if not rings or not rings[0] or len(rings[0]) < 3:
        _add_note(msp, "No hay polígono de lote disponible.", (0, 0, 0))
        return _to_bytes(doc)

    raw = rings[0]
    lon0 = sum(p[0] for p in raw) / len(raw)
    lat0 = sum(p[1] for p in raw) / len(raw)
    lot_pts = _ensure_ccw(_project_ring(raw, lon0, lat0))

    metrics = (calc_result or {}).get("metrics") or {}
    ant      = (calc_result or {}).get("antejardin", {}).get("dimension_m")
    aisl_post = metrics.get("aislamiento_posterior_m", {}).get("valor")
    aisl_lat  = metrics.get("aislamiento_lateral_m", {}).get("valor")
    pisos     = (
        metrics.get("altura_base_pisos", {}).get("valor")
        or metrics.get("altura_maxima_pisos", {}).get("valor")
    )
    height_m   = pisos * FLOOR_H if pisos else None
    facade_a   = metrics.get("retroceso_fachada_A_m", {}).get("valor")

    known = [v for v in (ant, aisl_post, aisl_lat or 0) if v is not None and v >= 0]
    uniform_inset = max(known) if known else 0
    footprint = _inset_polygon(lot_pts, uniform_inset) if uniform_inset > 0.5 else lot_pts

    # ── Plan view (Z = 0) ─────────────────────────────────────────────────────

    # LOT boundary
    _polyline(msp, lot_pts, layer="LOT", closed=True, z=0)

    # SETBACK zone outline (if any)
    if uniform_inset > 0.5 and footprint is not lot_pts:
        _polyline(msp, footprint, layer="SETBACK", closed=True, z=0)

    # FOOTPRINT (at grade)
    if footprint is not lot_pts:
        _polyline(msp, footprint, layer="FOOTPRINT", closed=True, z=0)

    # ── 3-D view: floor plates and top ────────────────────────────────────────
    if height_m and pisos:
        for f in range(1, int(pisos) + 1):
            z = f * FLOOR_H
            _polyline(msp, footprint, layer="FLOORS", closed=True, z=z)
        # Top of building
        _polyline(msp, footprint, layer="FOOTPRINT", closed=True, z=height_m)
        # Vertical edges (4 corners of bounding box)
        xmin, xmax, ymin, ymax = _bbox(footprint)
        corners = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
        for cx_, cy_ in corners:
            msp.add_line((cx_, cy_, 0), (cx_, cy_, height_m), dxfattribs={"layer": "FOOTPRINT"})

    # FACADE PLANE — horizontal rectangle at height A
    if facade_a and facade_a > 0:
        xmin, xmax, ymin, ymax = _bbox(lot_pts)
        plane_pts = [
            (xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax),
        ]
        _polyline(msp, plane_pts, layer="FACADE_PLANE", closed=True, z=facade_a)

    # ── Dimension annotations ─────────────────────────────────────────────────
    xmin, xmax, ymin, ymax = _bbox(lot_pts)
    cx_ = (xmin + xmax) / 2
    cy_ = (ymin + ymax) / 2
    offset = max(xmax - xmin, ymax - ymin) * 0.12 + 1

    notes: list[str] = []
    lote_area = (calc_result or {}).get("lote", {}).get("area_m2", {}).get("valor")
    if lote_area:
        notes.append(f"Área lote: {lote_area:,.0f} m²")
    if uniform_inset > 0:
        notes.append(f"Aislamientos (conservador, uniforme): {uniform_inset:.1f} m")
        if ant is None:
            notes.append("  · Antejardín: sin datos")
        else:
            notes.append(f"  · Antejardín: {ant:.1f} m")
        if aisl_post is None:
            notes.append("  · Aislamiento posterior: sin datos")
        else:
            notes.append(f"  · Aislamiento posterior: {aisl_post:.1f} m")
        if aisl_lat is None:
            notes.append("  · Aislamiento lateral: sin datos")
        else:
            notes.append(f"  · Aislamiento lateral: {aisl_lat:.1f} m")
    if pisos:
        notes.append(f"Altura: {pisos} pisos × {FLOOR_H} m = {height_m:.0f} m")
    if facade_a:
        notes.append(f"Retroceso fachada A: {facade_a:.1f} m (altura máx fachada sobre espacio público)")
    fp_area = abs(_signed_area(footprint)) if footprint else None
    if fp_area:
        notes.append(f"Planta libre aprox.: {fp_area:,.0f} m²")

    y_note = ymax + offset
    for line in notes:
        _add_note(msp, line, (xmin, y_note, 0), height=0.4)
        y_note += 0.7

    # Persistent disclaimer — must survive export/print workflows
    disclaimer = (
        "PREFACTIBILIDAD: Estimacion basada en datos publicos POT/catastro. "
        "No constituye norma urbanistica certificada. "
        "Verifique con Curaduria Urbana o SDP antes de transaccion, licencia o actuacion juridica."
    )
    _add_note(msp, disclaimer, (xmin, y_note + 0.5, 0), height=0.35)

    return _to_bytes(doc)


# ── DXF helpers ───────────────────────────────────────────────────────────────

def _polyline(msp, pts: list[tuple], *, layer: str, closed: bool, z: float) -> None:
    pts3d = [(x, y, z) for x, y in pts]
    if closed:
        pts3d.append(pts3d[0])
    msp.add_polyline3d(pts3d, dxfattribs={"layer": layer})


def _add_note(msp, text: str, pos: tuple, height: float = 0.5) -> None:
    msp.add_text(
        text,
        dxfattribs={"layer": "NOTES", "height": height, "insert": pos},
    )


def _to_bytes(doc) -> bytes:
    import io
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")
