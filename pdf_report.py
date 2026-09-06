#!/usr/bin/env python3
"""
pdf_report.py — 4–6 page WeasyPrint PDF for Bogotá edificabilidad lookups.

Structure
---------
p1  Cover + verdict   address · lot ID · date · area · tratamiento
                      headline: área construible + units + binding constraint
                      static lot-polygon map
p2  Building profile  SVG cross-section (ports the web-app diagram)
                      setback table
p3  Parameter table   every GIS value · source layer/article · confianza badge
                      null values show WHY (from nota field)
p4  Per-floor table   floors 1-N with height ranges & applicable rules
                      unit-estimate breakdown (with assumption disclaimer)
p5  Próximos pasos    action checklist + warnings
p6  Formula trace     appendix — every calculation step
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

from jinja2 import Environment, BaseLoader
from regulatory import REGULATORY_VERSION_LONG, REGULATORY_VERSION

# ── WeasyPrint invocation ─────────────────────────────────────────────────────

_WEASYPRINT_CLI = shutil.which("weasyprint") or "weasyprint"
_BREW_LIB = "/opt/homebrew/lib"


def _html_to_pdf(html: str) -> bytes:
    env = dict(os.environ)
    if sys.platform == "darwin" and _BREW_LIB not in env.get("DYLD_LIBRARY_PATH", ""):
        env["DYLD_LIBRARY_PATH"] = _BREW_LIB + ":" + env.get("DYLD_LIBRARY_PATH", "")
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as fh:
        fh.write(html)
        html_path = fh.name
    pdf_path = html_path.replace(".html", ".pdf")
    try:
        subprocess.run([_WEASYPRINT_CLI, html_path, pdf_path],
                       env=env, check=True, capture_output=True)
        with open(pdf_path, "rb") as f:
            return f.read()
    finally:
        for p in (html_path, pdf_path):
            try:
                os.unlink(p)
            except OSError:
                pass


# ── Constants ─────────────────────────────────────────────────────────────────

DECREE_VERSION = REGULATORY_VERSION_LONG
DECREE_SHORT   = REGULATORY_VERSION
DISCLAIMER     = (
    "Estimación de prefactibilidad basada en datos públicos POT/catastro — no es norma urbanística certificada. "
    "Verifique con Curaduría Urbana o Secretaría Distrital de Planeación antes de tomar "
    "decisiones de transacción, licencia o actuación jurídica."
)

_MONTH_ES = {
    "January":"enero","February":"febrero","March":"marzo","April":"abril",
    "May":"mayo","June":"junio","July":"julio","August":"agosto",
    "September":"septiembre","October":"octubre","November":"noviembre","December":"diciembre",
}

# ── Formatting helpers ────────────────────────────────────────────────────────

def _n(val, dec: int = 1, dash: str = "—") -> str:
    """Format a number with Colombian locale (. thousands, , decimal)."""
    if val is None:
        return dash
    try:
        f = float(val)
        if dec == 0:
            s = f"{int(round(f)):,}".replace(",", ".")
        else:
            fmt = f"{{:,.{dec}f}}"
            s = fmt.format(f).replace(",", "X").replace(".", ",").replace("X", ".")
        return s
    except (TypeError, ValueError):
        return str(val)


def _area(val, dash: str = "—") -> str:
    v = _n(val, 1, dash)
    return f"{v} m²" if v != dash else dash


def _get(obj: Any, *keys, default=None) -> Any:
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _date_label(dt: datetime) -> str:
    s = dt.strftime("%d de %B de %Y")
    for en, es in _MONTH_ES.items():
        s = s.replace(en, es)
    return s


# ── Map rendering ─────────────────────────────────────────────────────────────

def _render_map(rings_wgs84, center_lng: float, center_lat: float,
                width: int = 600, height: int = 380) -> str:
    try:
        from staticmap import StaticMap, Polygon as SMPolygon, CircleMarker
        m = StaticMap(width, height,
                      url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        if rings_wgs84:
            exterior = [(pt[0], pt[1]) for pt in rings_wgs84[0]]
            m.add_polygon(SMPolygon(exterior,
                                    fill_color=(27, 72, 212, 48),
                                    outline_color=(27, 72, 212, 230)))
        else:
            m.add_marker(CircleMarker((center_lng, center_lat), "#1B48D4", 14))
        img = m.render(zoom=17)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""


# ── Building profile SVG ──────────────────────────────────────────────────────

def _profile_svg(d: dict) -> str:
    """Port of the web-app buildingProfile() function to Python SVG generation."""
    m   = d.get("metrics") or {}
    bc  = d.get("binding_constraint") or ""
    ant = d.get("antejardin") or {}

    def _fval(obj, *keys):
        v = _get(obj, *keys)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    pisos  = _fval(m, "altura_base_pisos", "valor") or _fval(m, "altura_maxima_pisos", "valor")
    ant_m  = ant.get("dimension_m")
    if isinstance(ant_m, dict):
        ant_m = _fval(ant, "dimension_m", "valor")
    else:
        try:
            ant_m = float(ant_m) if ant_m is not None else None
        except (TypeError, ValueError):
            ant_m = None
    ret_m  = _fval(m, "retroceso_fachada_A_m", "valor")
    post_m = _fval(m, "aislamiento_posterior_m", "valor")
    lat_m_raw = _fval(m, "aislamiento_lateral_m", "valor")
    lat_m  = lat_m_raw if lat_m_raw and lat_m_raw > 0 else None

    is_hbc    = bc in ("height", "footprint_and_height")
    dim_color = "#1B48D4" if is_hbc else "#8D929E"

    W, H, GY = 580, 260, 206
    left_sb  = max(40, (ant_m or 0) * 9) if ant_m else (max(36, (ret_m or 0) * 4) if ret_m else 44)
    right_sb = max(32, (post_m or 0) * 9) if post_m else 40
    lat_sb   = max(18, (lat_m or 0) * 6) if lat_m else 18
    bld_x    = left_sb + lat_sb
    bld_w    = max(90, W - bld_x - right_sb - lat_sb)
    floors   = min(int(pisos), 12) if pisos else 5
    floor_h  = min(22, max(8, int((GY - 22) / max(floors, 1))))
    bld_h    = floors * floor_h
    bld_y    = GY - bld_h
    estim    = pisos is None

    # Floor lines
    floor_lines = "".join(
        f'<line x1="{bld_x}" y1="{bld_y + i * floor_h}" '
        f'x2="{bld_x + bld_w}" y2="{bld_y + i * floor_h}" '
        'stroke="#D5D8DE" stroke-width="0.7"/>'
        for i in range(1, floors)
    )

    # Setback hatch zones
    hid = "hp1"
    # left (antejardín / retroceso)
    lz = f'<rect x="0" y="{bld_y}" width="{left_sb}" height="{bld_h}" fill="url(#{hid})"/>'
    # right (posterior)
    rx = bld_x + bld_w + lat_sb
    rz = f'<rect x="{rx}" y="{bld_y}" width="{W - rx}" height="{bld_h}" fill="url(#{hid})"/>'
    # lateral (lighter)
    latz = ""
    if lat_m:
        latz = (
            f'<rect x="{left_sb}" y="{bld_y}" width="{lat_sb}" height="{bld_h}" '
            f'fill="url(#{hid})" opacity="0.45"/>'
            f'<rect x="{bld_x + bld_w}" y="{bld_y}" width="{lat_sb}" height="{bld_h}" '
            f'fill="url(#{hid})" opacity="0.45"/>'
        )

    # Height dimension line
    cx = bld_x + bld_w / 2
    dim = (
        f'<line x1="{cx:.1f}" y1="{bld_y - 10}" x2="{cx:.1f}" y2="{GY}" '
        f'stroke="{dim_color}" stroke-width="1.2" stroke-dasharray="4,2.5"/>'
        f'<text x="{cx + 6:.1f}" y="{bld_y - 6}" fill="{dim_color}" '
        f'font-family="Courier New,monospace" font-size="11" dominant-baseline="middle">'
        f'{"?" if estim else int(pisos)} pisos{"*" if estim else ""}'
        f'</text>'
    )

    # Labels
    g_label = (
        f'<text x="{cx:.1f}" y="{GY + 15}" fill="#8D929E" '
        'font-family="Courier New,monospace" font-size="9" text-anchor="middle">±0,00 m</text>'
    )
    ant_label = ""
    if ant_m and ant_m > 0:
        ant_label = (
            f'<text x="{left_sb / 2:.1f}" y="{GY + 15}" fill="#5E6370" '
            'font-family="Courier New,monospace" font-size="9" text-anchor="middle">'
            f'ant. {_n(ant_m, 1)}m</text>'
        )
    elif ret_m:
        ant_label = (
            f'<text x="{left_sb / 2:.1f}" y="{GY + 15}" fill="#5E6370" '
            'font-family="Courier New,monospace" font-size="9" text-anchor="middle">'
            f'ret. {_n(ret_m, 2)}m</text>'
        )
    post_label = ""
    if post_m:
        prx = rx + (W - rx) / 2
        post_label = (
            f'<text x="{prx:.1f}" y="{GY + 15}" fill="#5E6370" '
            'font-family="Courier New,monospace" font-size="9" text-anchor="middle">'
            f'post. {_n(post_m, 1)}m</text>'
        )
    lat_label = ""
    if lat_m:
        lat_label = (
            f'<text x="{left_sb + lat_sb / 2:.1f}" y="{bld_y - 5}" fill="#5E6370" '
            'font-family="Courier New,monospace" font-size="8" text-anchor="middle">'
            f'lat. {_n(lat_m, 1)}m</text>'
        )

    ground = f'<line x1="0" y1="{GY}" x2="{W}" y2="{GY}" stroke="#252830" stroke-width="2"/>'

    note = "* Altura estimada" if estim else "Perfil indicativo — no a escala"

    return f"""<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg"
     style="display:block;max-height:260px">
  <defs>
    <pattern id="{hid}" patternUnits="userSpaceOnUse" width="7" height="7" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="7" stroke="#C8D5F8" stroke-width="1.4"/>
    </pattern>
  </defs>
  {lz}{rz}{latz}
  <rect x="{bld_x}" y="{bld_y}" width="{bld_w}" height="{bld_h}"
        fill="#EDF0FC" stroke="#3C404C" stroke-width="1.8"/>
  {floor_lines}
  {ground}
  {dim}
  {g_label}
  {ant_label}
  {post_label}
  {lat_label}
  <text x="4" y="{H - 4}" fill="#B8BBC4" font-family="Helvetica,Arial,sans-serif"
        font-size="7" font-style="italic">{note}</text>
</svg>"""


# ── Parameter rows ─────────────────────────────────────────────────────────────

def _param_rows(d: dict, lu: dict) -> list[dict]:
    """Build the full parameter table. Every GIS value with source + confianza."""

    def row(cat, campo, valor, fuente, confianza, nota=""):
        return {"cat": cat, "campo": campo, "valor": valor,
                "fuente": fuente, "confianza": confianza, "nota": nota}

    rows = []
    lote = lu.get("lote") or {}
    ed   = lu.get("edificabilidad") or {}
    ant  = lu.get("antejardin") or {}
    avia = lu.get("ancho_via_gis") or {}
    aa   = lu.get("area_actividad") or {}
    tip  = lu.get("tipologia") or {}
    dant = d.get("antejardin") or {}
    dm   = d.get("metrics") or {}
    pk   = d.get("parking") or {}

    # ── Predio ──
    rows.append(row("Predio", "LOTCODIGO",
                    lote.get("lotcodigo") or "—",
                    "Catastro MapServer Layer 0", "alta"))
    area_val = _get(lote, "area_m2", "valor")
    rows.append(row("Predio", "Área catastral",
                    _area(area_val),
                    "Layer 0 — fórmula de Gauss, MAGNA-SIRGAS 9377",
                    _get(lote, "area_m2", "confianza") or "alta",
                    _get(lote, "nota_area") or ""))
    if lote.get("unidades_predio") is not None:
        rows.append(row("Predio", "Unidades prediales",
                        str(lote["unidades_predio"]),
                        "Layer 0 (LOTUPREDIA)", "alta"))

    # ── Norma ──
    rows.append(row("Norma", "Tratamiento urbanístico",
                    d.get("tratamiento") or lu.get("tratamiento") or "—",
                    "Layer 15 POT FeatureServer (TRATAMIENTO)", "alta"))
    rows.append(row("Norma", "Tipología predial",
                    tip.get("valor") or "—",
                    "Layer 15 (TIPOLOGIA)", tip.get("confianza") or "alta"))

    alt_ed = ed.get("altura_maxima") or {}
    alt_tipo = alt_ed.get("tipo") or "—"
    alt_min  = alt_ed.get("pisos_min")
    alt_max  = alt_ed.get("pisos_max")
    if alt_min is not None and alt_min == alt_max:
        alt_str = f"{int(alt_min)} pisos (fijo)"
    elif alt_min is not None and alt_max is not None:
        alt_str = f"{int(alt_min)}–{int(alt_max)} pisos (rango)"
    else:
        alt_str = alt_tipo or "—"
    rows.append(row("Norma", "Altura máxima (mapa CU-5.4.x)",
                    alt_str,
                    f"{alt_ed.get('fuente','Layer 15 campo ALTURA_MAXIMA')}",
                    alt_ed.get("confianza") or "alta",
                    alt_ed.get("articulo") or "Art. 310 D.555/2021"))

    ic_ed = ed.get("indice_construccion") or {}
    ic_val = ic_ed.get("valor")
    ic_str = _n(ic_val, 2) if ic_val is not None else "Resultante"
    rows.append(row("Norma", "Índice de Construcción (IC)",
                    ic_str,
                    ic_ed.get("articulo") or "Art. 310 Num. 1 D.555/2021",
                    ic_ed.get("confianza") or "media",
                    ic_ed.get("nota") or ""))

    io_ed = ed.get("indice_ocupacion") or {}
    io_val = io_ed.get("valor")
    io_str = _n(io_val, 2) if io_val is not None else "Resultante"
    rows.append(row("Norma", "Índice de Ocupación (IO)",
                    io_str,
                    io_ed.get("articulo") or "Art. 310 Num. 1 D.555/2021",
                    io_ed.get("confianza") or "media",
                    io_ed.get("nota") or ""))

    sub_ed = ed.get("subdivision_permitida") or {}
    if sub_ed:
        rows.append(row("Norma", "Subdivisión permitida",
                        "Sí" if sub_ed.get("valor") else "No",
                        sub_ed.get("articulo") or "Art. 310 Num. 4 D.555/2021",
                        sub_ed.get("confianza") or "alta",
                        sub_ed.get("nota") or ""))

    # ── Volumen ──
    ant_dim = dant.get("dimension_m")
    if isinstance(ant_dim, dict):
        ant_dim = ant_dim.get("valor")
    rows.append(row("Volumen", "Antejardín mínimo",
                    f"{_n(ant_dim, 1)} m" if ant_dim is not None else "No exigido / sin dato",
                    dant.get("fuente") or "Layer 22 (mapa CU-5.5)",
                    dant.get("confianza") or "alta",
                    dant.get("articulo") or "Art. 314 D.555/2021"))

    post_obj = ed.get("aislamiento_posterior") or {}
    post_v = post_obj.get("valor_m") or _get(dm, "aislamiento_posterior_m", "valor")
    rows.append(row("Volumen", "Aislamiento posterior",
                    f"{_n(post_v, 1)} m" if post_v is not None else "—",
                    post_obj.get("articulo") or "Anexo 5 Cap. 2.4.2.A.2 D.555/2021",
                    post_obj.get("confianza") or _get(dm, "aislamiento_posterior_m", "confianza") or "alta",
                    post_obj.get("nota") or ""))

    lat_obj = ed.get("aislamiento_lateral") or {}
    lat_v   = _get(dm, "aislamiento_lateral_m", "valor")
    lat_note = _get(dm, "aislamiento_lateral_m", "nota") or lat_obj.get("nota") or ""
    rows.append(row("Volumen", "Aislamiento lateral",
                    f"≥ {_n(lat_v, 1)} m" if lat_v else (lat_obj.get("formula") or "—"),
                    lat_obj.get("articulo") or "Art. 310 Num. 3 / Anexo 5 D.555/2021",
                    _get(dm, "aislamiento_lateral_m", "confianza") or lat_obj.get("confianza") or "alta",
                    lat_note))

    ret_obj = ed.get("retroceso_fachada") or {}
    ret_v   = _get(dm, "retroceso_fachada_A_m", "valor")
    ret_d   = dm.get("retroceso_fachada_A_m") or {}
    ret_note = ret_d.get("nota") or ret_obj.get("nota") or ""
    rows.append(row("Volumen", "Retroceso de fachada (A = 2,5 × D)",
                    f"{_n(ret_v, 2)} m" if ret_v is not None else "Sin dato (D no encontrado)",
                    ret_obj.get("articulo") or "Anexo 5 Cap. 1.2.2.E.1.1 D.555/2021",
                    ret_d.get("confianza") or "media",
                    ret_note))

    # ── Vía ──
    d_m  = avia.get("D_m") or avia.get("ancho_m")
    rows.append(row("Vía", "Ancho de calzada (D)",
                    f"{_n(d_m, 2)} m ({avia.get('n_calzadas', '?')} calzada{'s' if (avia.get('n_calzadas') or 1) > 1 else ''})" if d_m else "No encontrado en radio 25 m",
                    avia.get("fuente") or "Layer 38 POT FeatureServer (Calzada)",
                    avia.get("confianza") or "sin_dato",
                    avia.get("nota") or "Andenes y separadores no incluidos — perfil total puede ser mayor."))

    # ── Área de actividad ──
    rows.append(row("Actividad", "Área de actividad (Art. 389)",
                    f"{aa.get('codigo','—')} — {aa.get('nombre','')[:60] if aa.get('nombre') else ''}".strip(" —"),
                    aa.get("fuente") or "Layer 14 POT FeatureServer",
                    "alta" if aa.get("codigo") else "sin_dato"))
    rows.append(row("Actividad", "Receptora VIS (Art. 310 § 3)",
                    "Sí" if aa.get("es_receptora_vis") else "No",
                    aa.get("articulo") or "Art. 310 Parágrafo 3 D.555/2021",
                    "alta" if aa.get("codigo") else "sin_dato"))

    # ── Bonus ──
    bon_man = ed.get("bonus_altura_manzana_completa") or {}
    if bon_man:
        rows.append(row("Bonus", "Bonus manzana completa (Art. 310 § 2)",
                        f"× {bon_man.get('factor','2')} — máx. {bon_man.get('altura_maxima_con_bonus_pisos','?')} pisos",
                        bon_man.get("articulo") or "Art. 310 Parágrafo 2 D.555/2021",
                        bon_man.get("confianza") or "alta",
                        bon_man.get("condicion") or ""))
    bon_vis = ed.get("bonus_altura_vis") or {}
    if bon_vis:
        bv_applied = _get(dm, "altura_con_vis_bonus_pisos", "valor")
        bv_nota    = _get(dm, "altura_con_vis_bonus_pisos", "nota") or ""
        rows.append(row("Bonus", "Bonus VIS/VIP (Art. 310 § 3)",
                        f"Aplica — altura con bonus: {int(bv_applied)} pisos" if bv_applied else "No aplica",
                        bon_vis.get("articulo") or "Art. 310 Parágrafo 3 D.555/2021",
                        "alta",
                        bv_nota))

    # ── Estacionamientos ──
    if pk.get("min_pct") is not None:
        rows.append(row("Parking", "Mínimo (Art. 389)",
                        f"{_n(pk['min_pct'], 0)}% sobre base Art. 390",
                        pk.get("fuente") or "Art. 389 D.555/2021",
                        pk.get("confianza") or "alta"))
        rows.append(row("Parking", "Máximo (Art. 389)",
                        f"{_n(pk['max_pct'], 0)}%",
                        "Art. 389 D.555/2021", pk.get("confianza") or "alta"))
        rows.append(row("Parking", "Adicional con compensación (Art. 390A)",
                        f"{_n(pk.get('adicional_pct', 0), 0)}%",
                        "Art. 390A D.555/2021", pk.get("confianza") or "alta",
                        pk.get("nota") or ""))

    return rows


# ── Floor table ────────────────────────────────────────────────────────────────

def _floor_rows(d: dict, lu: dict) -> list[dict]:
    m    = d.get("metrics") or {}
    ant  = d.get("antejardin") or {}
    ed   = lu.get("edificabilidad") or {}
    lat_ed = ed.get("aislamiento_lateral") or {}

    pisos_val = _get(m, "altura_base_pisos", "valor") or _get(m, "altura_maxima_pisos", "valor")
    if not pisos_val:
        return []
    n = int(pisos_val)

    lat_desde = lat_ed.get("aplica_desde_piso") or 2
    lat_v     = _get(m, "aislamiento_lateral_m", "valor")
    post_v    = _get(m, "aislamiento_posterior_m", "valor")
    ant_dim   = ant.get("dimension_m")
    if isinstance(ant_dim, dict):
        ant_dim = None

    rows = []
    for p in range(1, n + 1):
        h_inf = (p - 1) * 3.0
        h_sup = p * 3.0
        rules = []
        if p == 1 and ant_dim:
            rules.append(f"Antejardín {_n(ant_dim, 1)} m (Art. 314)")
        if p == lat_desde and lat_v:
            rules.append(f"Aislamiento lateral ≥ {_n(lat_v, 1)} m inicia (Art. 310 Num. 3)")
        if p == n:
            rules.append("Altura máxima — mapa CU-5.4.x")
        rows.append({
            "piso": f"P{p:02d}",
            "nivel": f"+{_n(h_inf, 1)}–+{_n(h_sup, 1)} m*",
            "regla": " · ".join(rules) if rules else "—",
        })
    return rows


# ── Unit estimate ─────────────────────────────────────────────────────────────

def _unit_estimate(d: dict) -> dict | None:
    """Run estimate_units() if area_construible is available, else return None."""
    area_val = _get(d, "metrics", "area_construible_max_m2", "valor")
    if not area_val:
        return None
    try:
        from calc import estimate_units
        return estimate_units(float(area_val))
    except Exception:
        return None


# ── Próximos pasos ────────────────────────────────────────────────────────────

def _next_steps(d: dict, lu: dict) -> list[dict]:
    """Generate a contextual checklist based on the analysis result."""
    steps = []
    warnings = d.get("warnings") or []
    m  = d.get("metrics") or {}
    bc = d.get("binding_constraint") or ""
    ed = lu.get("edificabilidad") or {}
    avia = lu.get("ancho_via_gis") or {}
    inp = d.get("input") or {}

    def add(text, priority="normal", note=""):
        steps.append({"text": text, "priority": priority, "note": note})

    # Always-present items
    add("Solicitar concepto de norma urbana a la Curaduría Urbana competente.",
        "alta",
        "Este informe es informativo; el concepto oficial es el único documento vinculante.")
    add("Verificar las dimensiones reales del predio con levantamiento topográfico.",
        "alta",
        "IC, IO, aislamientos y retroceso dependen de las dimensiones exactas del lote.")

    # Specific to tratamiento
    trat = d.get("tratamiento") or lu.get("tratamiento") or ""
    if "CONSOLIDAC" in trat.upper():
        add("Modelar volumétría con geometría exacta del lote para determinar IC e IO reales.",
            "alta",
            "En Consolidación, IC e IO son 'resultantes' (Art. 310 Num. 1) — no existen como números fijos.")
    if "DESARROLLO" in trat.upper():
        if not inp.get("anu_m2_supplied"):
            add("Obtener el ANU del Plan Parcial y reejecutar la consulta con ese valor.",
                "alta",
                "El ANU de Plan Parcial puede diferir del área catastral del lote.")

    # Missing inputs
    if not inp.get("frente_m_supplied"):
        bon_v = _get(m, "altura_con_bonus_pisos", "valor")
        if bon_v is None:
            add("Medir el frente del predio e ingresar en la herramienta para verificar bonus de altura (Art. 310 Num. 2).",
                "media",
                "Tipología continua con frente ≥ 14 m puede acceder a altura adicional.")

    # Via data quality
    via_conf = avia.get("confianza") or ""
    if via_conf == "media" or not avia.get("D_m"):
        add("Verificar el perfil vial oficial (ancho total incluyendo andenes y separadores) con SDP.",
                "media",
                f"Retroceso de fachada A = 2,5 × D. El D actual ({_n(avia.get('D_m') or avia.get('ancho_m'), 2)} m) "
                f"incluye solo calzada(s), sin andenes.")

    # Lateral setback note
    lat_conf = _get(m, "aislamiento_lateral_m", "confianza") or ""
    if lat_conf == "media":
        add("Recalcular aislamiento lateral con la altura real definida en diseño (≠ 3 m/piso asumido).",
            "media",
            "Fórmula: max(1/5 × altura_total_m, 4 m). Altura actual asume 3 m/piso.")

    # Warnings from calc
    for w in warnings:
        if w:
            add(str(w), "advertencia")

    # VIS opportunity
    vis_receptora = lu.get("area_actividad", {}).get("es_receptora_vis")
    if vis_receptora:
        add("Evaluar posibilidad de bonus VIS/VIP (Art. 310 § 3): altura × 2 si ≥70% del área es VIS.",
            "info",
            "Este predio está en zona AAERVIS — el bonus VIS aplica.")

    # Subdivision
    sub = _get(lu, "edificabilidad", "subdivision_permitida", "valor")
    if sub is False:
        add("La subdivisión predial NO está permitida para este lote (Art. 310 Num. 4).",
            "info",
            "Confirmar con Curaduría Urbana antes de cualquier maniobra registral.")

    # Large lot
    lot_area = _get(d, "lote", "area_m2", "valor")
    if lot_area and float(lot_area) > 10000:
        add("Predio > 10.000 m²: verificar si aplica plan parcial obligatorio (Art. 273 D.555/2021).",
            "advertencia")

    return steps


# ── HTML template ─────────────────────────────────────────────────────────────

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<style>
/* ── Design tokens (mirrors tokens.css) ───────────────────────────────────── */
:root {
  --ink-0: #111318; --ink-1: #252830; --ink-2: #3C404C;
  --ink-3: #5E6370; --ink-4: #8D929E; --ink-5: #B8BBC4;
  --ink-6: #D5D8DE; --ink-7: #E8EAED; --ink-8: #F2F2F0;
  --paper: #FAFAF8; --paper-1: #F4F4F1; --paper-2: #ECEAE6;
  --accent: #1B48D4; --accent-dim: #C8D5F8; --accent-bg: #EDF0FC;
  --c-a-bg: #E3F2E9; --c-a-fg: #145E35; --c-a-b: #5CB88A;
  --c-m-bg: #FEF2D8; --c-m-fg: #7A4500; --c-m-b: #D09400;
  --c-r-bg: #FDE8E8; --c-r-fg: #831010; --c-r-b: #C83232;
  --ff: Helvetica Neue, Helvetica, Arial, sans-serif;
  --ff-mono: Courier New, Courier, monospace;
}
/* ── Page setup ───────────────────────────────────────────────────────────── */
@page {
  size: A4;
  margin: 11mm 13mm 20mm 13mm;
  @bottom-left {
    content: "{{ disclaimer }}";
    font-size: 6pt; color: var(--c-r-fg);
    font-family: Helvetica Neue, Helvetica, Arial, sans-serif;
    width: 110mm;
  }
  @bottom-center {
    content: "{{ decree_short }}  ·  Generado: {{ timestamp }}";
    font-size: 6pt; color: var(--ink-4);
    font-family: Helvetica Neue, Helvetica, Arial, sans-serif;
  }
  @bottom-right {
    content: "Pág. " counter(page) " / " counter(pages);
    font-size: 7.5pt; font-weight: 700; color: var(--ink-0);
    font-family: Helvetica Neue, Helvetica, Arial, sans-serif;
  }
}
/* ── Base ─────────────────────────────────────────────────────────────────── */
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--ff);
  font-size: 8.5pt;
  color: var(--ink-0);
  line-height: 1.45;
  background: var(--paper);
}
.pb { page-break-before: always; }
.no-break { page-break-inside: avoid; }
.mono { font-family: var(--ff-mono); }

/* ── Cover header ─────────────────────────────────────────────────────────── */
.cover-head {
  background: var(--ink-0);
  color: var(--paper);
  padding: 9pt 12pt;
  margin-bottom: 10pt;
}
.cover-head h1 {
  font-size: 16pt; font-weight: 800; letter-spacing: -0.4pt;
  margin-bottom: 2pt;
}
.cover-head .sub {
  font-size: 8pt; color: var(--ink-5); letter-spacing: 0.5pt;
  text-transform: uppercase;
}
.cover-meta {
  display: flex; gap: 6pt; align-items: baseline;
  margin-top: 5pt; flex-wrap: wrap;
}
.cover-meta .tag {
  font-size: 7pt; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.5pt; color: var(--ink-4);
  padding: 1pt 5pt; border: 0.5pt solid var(--ink-2);
}
.cover-meta .tag span { color: var(--paper); font-weight: 400; margin-left: 3pt; }

/* ── Cover two-column ─────────────────────────────────────────────────────── */
.cover-cols { display: flex; gap: 12pt; margin-bottom: 10pt; }
.cover-left { flex: 0 0 54%; }
.cover-right { flex: 1; }

/* ── Identity table ───────────────────────────────────────────────────────── */
.id-table { width: 100%; border-collapse: collapse; margin-bottom: 8pt; }
.id-table td { padding: 2.5pt 4pt; border-bottom: 0.3pt solid var(--ink-7);
               vertical-align: top; }
.id-table td:first-child { color: var(--ink-4); font-size: 7.5pt;
                            width: 42%; white-space: nowrap; }
.id-table td:last-child { font-weight: 600; font-family: var(--ff-mono);
                           font-size: 8pt; }

/* ── Map ──────────────────────────────────────────────────────────────────── */
.map-img { width: 100%; max-height: 140pt; object-fit: cover;
            border: 0.5pt solid var(--ink-6); display: block; }
.map-cap { font-size: 6.5pt; color: var(--ink-5); text-align: center;
           margin-top: 2pt; font-style: italic; }

/* ── Verdict box ──────────────────────────────────────────────────────────── */
.verdict {
  background: var(--ink-1);
  color: var(--paper);
  padding: 8pt 10pt;
  margin-bottom: 8pt;
}
.verdict-grid { display: flex; gap: 0; }
.verdict-cell {
  flex: 1;
  padding: 6pt 10pt;
  border-left: 1pt solid var(--ink-2);
}
.verdict-cell:first-child { border-left: none; padding-left: 0; }
.verdict-cell .vc-label {
  font-size: 6.5pt; text-transform: uppercase; letter-spacing: 0.7pt;
  color: var(--ink-4); margin-bottom: 3pt;
}
.verdict-cell .vc-val {
  font-family: var(--ff-mono); font-size: 14pt; font-weight: 700;
  color: var(--paper); line-height: 1;
}
.verdict-cell.accent .vc-val { color: var(--accent-dim); }
.verdict-cell .vc-sub {
  font-size: 7pt; color: var(--ink-5); margin-top: 2pt;
}
.verdict-sentence {
  margin-top: 6pt; padding-top: 6pt; border-top: 0.5pt solid var(--ink-2);
  font-size: 8pt; color: var(--ink-6); line-height: 1.5;
}

/* ── Section head ─────────────────────────────────────────────────────────── */
.sh {
  font-size: 6.5pt; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.9pt; color: var(--ink-4);
  border-bottom: 0.5pt solid var(--ink-6);
  padding-bottom: 2pt; margin: 10pt 0 5pt;
}

/* ── Profile page ─────────────────────────────────────────────────────────── */
.profile-page-head {
  font-size: 11pt; font-weight: 800; color: var(--ink-0);
  margin-bottom: 6pt;
}
.profile-inline { margin-top: 4pt; }
.profile-caption {
  font-size: 7.5pt; color: var(--ink-3); margin-top: 8pt;
  line-height: 1.6;
}
.setback-grid {
  display: flex; gap: 8pt; margin-top: 10pt; flex-wrap: wrap;
}
.sb-cell {
  flex: 1 0 22%;
  background: var(--accent-bg);
  border: 0.5pt solid var(--accent-dim);
  padding: 5pt 7pt;
}
.sb-cell .sbc-label { font-size: 7pt; color: var(--ink-3);
                       text-transform: uppercase; letter-spacing: 0.4pt; }
.sb-cell .sbc-val { font-family: var(--ff-mono); font-size: 12pt;
                     font-weight: 700; color: var(--accent); }
.sb-cell .sbc-src { font-size: 6.5pt; color: var(--ink-4); margin-top: 2pt; }

/* ── Data tables ──────────────────────────────────────────────────────────── */
.dt { width: 100%; border-collapse: collapse; font-size: 7.5pt; }
.dt thead { display: table-header-group; }
.dt tr { break-inside: avoid; page-break-inside: avoid; }
.dt thead tr { background: var(--ink-0); color: var(--paper); }
.dt thead td { padding: 3pt 5pt; font-size: 7pt; font-weight: 700;
               letter-spacing: 0.3pt; }
.dt tbody tr:nth-child(even) { background: var(--paper-1); }
.dt tbody td { padding: 2.5pt 5pt; border-bottom: 0.3pt solid var(--ink-7);
               vertical-align: top; }
.dt .cat-head td { background: var(--ink-7); color: var(--ink-2);
                   font-weight: 700; font-size: 7pt; text-transform: uppercase;
                   letter-spacing: 0.5pt; padding: 2pt 5pt; }
.dt td.mono { font-family: var(--ff-mono); font-size: 7pt; }
.dt td.nota { font-size: 6.5pt; color: var(--ink-4); font-style: italic; }

/* ── Confianza badges ─────────────────────────────────────────────────────── */
.badge {
  display: inline-block; font-size: 6pt; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.3pt;
  padding: 1pt 4pt; border-radius: 10pt;
}
.b-alta { background: var(--c-a-bg); color: var(--c-a-fg); border: 0.5pt solid var(--c-a-b); }
.b-media { background: var(--c-m-bg); color: var(--c-m-fg); border: 0.5pt solid var(--c-m-b); }
.b-req { background: var(--c-r-bg); color: var(--c-r-fg); border: 0.5pt solid var(--c-r-b); }

/* ── Floor table ──────────────────────────────────────────────────────────── */
.floor-note { font-size: 7pt; color: var(--ink-4); margin-top: 4pt; font-style: italic; }
.units-disclaimer {
  background: var(--c-m-bg); border: 0.5pt solid var(--c-m-b);
  padding: 5pt 8pt; margin-bottom: 8pt; font-size: 7.5pt; color: var(--c-m-fg);
}

/* ── Próximos pasos ───────────────────────────────────────────────────────── */
.step { padding: 4pt 0 4pt 10pt; border-bottom: 0.3pt solid var(--ink-7);
        page-break-inside: avoid; position: relative; }
.step::before { content: ""; position: absolute; left: 0; top: 7pt;
                width: 5pt; height: 5pt; border: 1pt solid var(--ink-3); }
.step.alta::before { background: var(--c-r-b); border-color: var(--c-r-b); }
.step.media::before { background: var(--c-m-b); border-color: var(--c-m-b); }
.step.info::before { background: var(--accent-dim); border-color: var(--accent); }
.step.advertencia::before { background: var(--c-r-bg); border-color: var(--c-r-b); }
.step-text { font-size: 8.5pt; color: var(--ink-0); font-weight: 500; }
.step-note { font-size: 7pt; color: var(--ink-3); margin-top: 1pt; }
.legend { display: flex; gap: 12pt; margin-bottom: 8pt; }
.legend-item { display: flex; align-items: center; gap: 4pt;
               font-size: 7pt; color: var(--ink-3); }
.legend-sq { width: 7pt; height: 7pt; flex-shrink: 0; }

/* ── Warnings ─────────────────────────────────────────────────────────────── */
.warn-box { background: var(--c-r-bg); border-left: 2pt solid var(--c-r-b);
            padding: 4pt 7pt; margin-bottom: 4pt; font-size: 8pt;
            color: var(--c-r-fg); page-break-inside: avoid; }
.no-warn { color: var(--c-a-fg); background: var(--c-a-bg);
           border-left: 2pt solid var(--c-a-b);
           padding: 4pt 7pt; font-size: 8pt; }

/* ── Trace ────────────────────────────────────────────────────────────────── */
.trace-h { font-size: 11pt; font-weight: 800; color: var(--ink-0);
           margin-bottom: 8pt; }
.ts {
  border-left: 2pt solid var(--accent-dim);
  padding: 4pt 0 4pt 8pt;
  margin-bottom: 5pt;
  page-break-inside: avoid;
}
.ts.ts-err { border-color: var(--c-r-b); }
.ts-step { font-size: 6.5pt; font-weight: 700; text-transform: uppercase;
           letter-spacing: 0.6pt; color: var(--accent); margin-bottom: 1pt; }
.ts-desc { font-weight: 600; font-size: 8.5pt; }
.ts-expr { font-family: var(--ff-mono); font-size: 7.5pt; color: var(--ink-1);
           background: var(--paper-1); padding: 1pt 4pt; display: inline-block;
           margin: 2pt 0; }
.ts-vals { font-family: var(--ff-mono); font-size: 7pt; color: var(--ink-3);
           background: var(--paper-2); padding: 2pt 5pt; margin: 2pt 0;
           white-space: pre-wrap; }
.ts-res { font-family: var(--ff-mono); font-size: 9pt; font-weight: 700;
          color: var(--ink-0); margin-top: 2pt; }
.ts-note { font-size: 7pt; color: var(--ink-3); font-style: italic; margin-top: 2pt; }
.ts-src  { font-size: 6.5pt; color: var(--accent); margin-top: 1pt; }
.ts-err-msg { color: var(--c-r-fg); font-weight: 600; }
</style>
</head>
<body>

{# ═══════════════════════════════════════════════════════════════════════════ #}
{# PAGE 1 — Cover + Verdict                                                   #}
{# ═══════════════════════════════════════════════════════════════════════════ #}

<div class="cover-head">
  <h1>Prefactibilidad Edificatoria</h1>
  <div class="sub">Edificabilidad Bogotá · {{ decree_short }}</div>
  <div class="cover-meta">
    <div class="tag">Dir <span>{{ address }}</span></div>
    <div class="tag">Lote <span class="mono">{{ lotcodigo }}</span></div>
    <div class="tag">Fecha <span>{{ date_label }}</span></div>
    <div class="tag">Área <span class="mono">{{ lot_area }}</span></div>
    <div class="tag">Trat. <span>{{ tratamiento }}</span></div>
  </div>
</div>

<div class="cover-cols no-break">
  <!-- Left: identity + verdict -->
  <div class="cover-left">
    <div class="sh">Identificación del predio</div>
    <table class="id-table">
      <tr><td>LOTCODIGO</td><td>{{ lotcodigo }}</td></tr>
      <tr><td>Área catastral</td><td>{{ lot_area }}</td></tr>
      <tr><td>Tratamiento</td><td>{{ tratamiento }}</td></tr>
      <tr><td>Tipología</td><td>{{ tipologia }}</td></tr>
      <tr><td>Área de actividad</td><td>{{ area_actividad }}</td></tr>
      {% if anu_usado %}<tr><td>ANU utilizada</td><td>{{ anu_usado }}</td></tr>{% endif %}
      <tr><td>Consulta GIS</td><td>{{ consultation_date }} · {{ decree_short }}</td></tr>
      <tr><td>Lat / Lng</td><td>{{ lat }}, {{ lng }}</td></tr>
      {% if rango %}<tr><td>Rango (Art. 281)</td><td>{{ rango }}</td></tr>{% endif %}
    </table>

    <!-- Verdict -->
    <div class="verdict no-break">
      <div class="verdict-grid">
        <div class="verdict-cell accent">
          <div class="vc-label">Área construible máx.</div>
          <div class="vc-val">{{ area_max }}</div>
          {% if area_max_unit %}<div class="vc-sub">{{ area_max_unit }}</div>{% endif %}
        </div>
        <div class="verdict-cell">
          <div class="vc-label">Unidades estimadas</div>
          <div class="vc-val">{{ units_est }}</div>
          <div class="vc-sub">{{ units_sub }}</div>
        </div>
        <div class="verdict-cell">
          <div class="vc-label">Limitante operativo</div>
          <div class="vc-val" style="font-size:11pt">{{ binding_short }}</div>
          <div class="vc-sub">{{ binding_detail }}</div>
        </div>
      </div>
      <div class="verdict-sentence">{{ verdict_sentence }}</div>
    </div>
  </div>

  <!-- Right: map -->
  <div class="cover-right">
    <div class="sh">Ubicación del predio</div>
    {% if map_img %}
    <img class="map-img" src="{{ map_img }}" alt="Polígono del lote">
    {% else %}
    <div style="background:var(--paper-2);height:120pt;display:flex;align-items:center;
                justify-content:center;color:var(--ink-4);font-size:8pt;
                border:0.5pt dashed var(--ink-6)">Mapa no disponible</div>
    {% endif %}
    <p class="map-cap">© OpenStreetMap contributors — solo referencia espacial</p>
  </div>
</div>

<!-- Perfil Volumétrico — inlined on page 1 -->
<div class="profile-inline">
  <div class="sh" style="margin-top:8pt">Perfil Volumétrico</div>
  {{ profile_svg | safe }}
  <div class="setback-grid no-break" style="margin-top:6pt">
    {% for sb in setbacks %}
    <div class="sb-cell">
      <div class="sbc-label">{{ sb.label }}</div>
      <div class="sbc-val mono">{{ sb.val }}</div>
      <div class="sbc-src">{{ sb.src }}</div>
    </div>
    {% endfor %}
  </div>
  <p class="profile-caption" style="margin-top:4pt">
    * Altura en metros asume <span class="mono">3,0 m/piso</span> como referencia —
    la altura real del proyecto puede variar.
    El diagrama es indicativo y no está a escala.
    Zona rayada = retiro exigido. Zona gris = envolvente máxima. Línea azul = limitante ({{ binding_short }}).
  </p>
</div>

{# ═══════════════════════════════════════════════════════════════════════════ #}
{# PAGE 2 — Parameter table                                                   #}
{# ═══════════════════════════════════════════════════════════════════════════ #}

<div>
  <div class="sh" style="margin-top:0">Tabla de parámetros — fuentes y confianza</div>
  <table class="dt">
    <thead>
      <tr>
        <td style="width:18%">Campo</td>
        <td style="width:18%">Valor</td>
        <td style="width:38%">Fuente / Artículo</td>
        <td style="width:10%">Conf.</td>
        <td style="width:16%">Nota</td>
      </tr>
    </thead>
    <tbody>
      {% set ns = namespace(last_cat="") %}
      {% for r in param_rows %}
        {% if r.cat != ns.last_cat %}
          {% set ns.last_cat = r.cat %}
          <tr class="cat-head"><td colspan="5">{{ r.cat }}</td></tr>
        {% endif %}
        <tr>
          <td>{{ r.campo }}</td>
          <td class="mono">{{ r.valor }}</td>
          <td style="font-size:7pt;color:var(--ink-3)">{{ r.fuente }}</td>
          <td>
            {% if r.confianza == "alta" %}<span class="badge b-alta">Alta</span>
            {% elif r.confianza == "media" %}<span class="badge b-media">Media</span>
            {% elif r.confianza in ("sin_dato", "requiere_input") %}<span class="badge b-req">Sin dato</span>
            {% else %}<span style="font-size:7pt;color:var(--ink-4)">{{ r.confianza }}</span>
            {% endif %}
          </td>
          <td class="nota">{{ r.nota[:110] if r.nota else "" }}</td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
</div>

{# ═══════════════════════════════════════════════════════════════════════════ #}
{# PAGE 4 — Per-floor table + unit estimate                                   #}
{# ═══════════════════════════════════════════════════════════════════════════ #}

<div>
  {% if floor_rows %}
  <div class="sh" style="margin-top:0">Tabla por piso — niveles y normas aplicables</div>
  <table class="dt" style="margin-bottom:4pt">
    <thead>
      <tr>
        <td style="width:12%">Piso</td>
        <td style="width:28%">Nivel aprox. (m)*</td>
        <td>Norma / observación</td>
      </tr>
    </thead>
    <tbody>
      {% for r in floor_rows %}
      <tr>
        <td class="mono" style="font-weight:700">{{ r.piso }}</td>
        <td class="mono">{{ r.nivel }}</td>
        <td>{{ r.regla }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  <p class="floor-note">* 3,0 m/piso es una referencia común — el proyecto puede usar alturas distintas.</p>
  {% endif %}

  <!-- Unit estimate -->
  <div class="sh" style="margin-top:10pt">Estimación de unidades</div>
  {% if unit_est %}
  <div class="units-disclaimer no-break">
    <strong>ESTIMACIÓN — NO es un parámetro del decreto.</strong>
    Basada en mezcla tipológica y circulación estándar (18%).
    El área vendible real depende del diseño arquitectónico.
  </div>
  <table class="dt no-break">
    <thead>
      <tr>
        <td>Concepto</td>
        <td style="width:22%">Valor</td>
        <td>Supuesto / Fuente</td>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Área construible bruta</td>
        <td class="mono">{{ unit_est.area_construible_m2 | n1 }} m²</td>
        <td>IC × ANU — resultado del cálculo</td>
      </tr>
      <tr>
        <td>Deducción circulación ({{ unit_est.circulacion_pct }}%)</td>
        <td class="mono">{{ unit_est.circ_area_m2 | n1 }} m²</td>
        <td>Pasillos, escaleras, zonas comunes (supuesto)</td>
      </tr>
      <tr>
        <td><strong>Área vendible neta estimada</strong></td>
        <td class="mono"><strong>{{ unit_est.area_vendible_neta_m2 | n1 }} m²</strong></td>
        <td>Bruta − circulación</td>
      </tr>
      {% for tipo, info in unit_est.unidades_por_tipo.items() %}
      <tr>
        <td>{{ info.label }}</td>
        <td class="mono">{{ info.unidades }} unid. ({{ info.area_asignada_m2 | n1 }} m²)</td>
        <td>{{ info.pct_mezcla }}% mezcla · {{ info.m2_neta_por_unidad }} m²/unidad (supuesto)</td>
      </tr>
      {% endfor %}
      <tr style="background:var(--accent-bg)">
        <td><strong>TOTAL unidades estimadas</strong></td>
        <td class="mono"><strong>{{ unit_est.total_unidades }}</strong></td>
        <td>Suma tipologías — solo referencia</td>
      </tr>
    </tbody>
  </table>
  {% else %}
  <div style="background:var(--paper-2);border:0.5pt solid var(--ink-6);
              padding:8pt 10pt;font-size:8pt;color:var(--ink-3)">
    <strong>No disponible</strong> — el área construible máxima no pudo determinarse
    automáticamente (IC/IO son resultantes en este tratamiento). Para estimar unidades,
    realice el modelado volumétrico del predio e ingrese el área construible resultante.
  </div>
  {% endif %}
</div>

{# ═══════════════════════════════════════════════════════════════════════════ #}
{# PAGE 5 — Próximos pasos + warnings                                         #}
{# ═══════════════════════════════════════════════════════════════════════════ #}

<div>
  <div class="sh" style="margin-top:0">Próximos pasos</div>
  <div class="legend">
    <div class="legend-item">
      <div class="legend-sq" style="background:var(--c-r-b)"></div> Acción prioritaria
    </div>
    <div class="legend-item">
      <div class="legend-sq" style="background:var(--c-m-b)"></div> Verificación recomendada
    </div>
    <div class="legend-item">
      <div class="legend-sq" style="background:var(--accent-dim)"></div> Información adicional
    </div>
  </div>
  {% for s in next_steps %}
  <div class="step {{ s.priority }}">
    <div class="step-text">{{ s.text }}</div>
    {% if s.note %}<div class="step-note">{{ s.note }}</div>{% endif %}
  </div>
  {% endfor %}

  <div class="sh" style="margin-top:12pt">Advertencias del cálculo</div>
  {% if warnings %}
    {% for w in warnings %}
    <div class="warn-box">{{ w }}</div>
    {% endfor %}
  {% else %}
  <div class="no-warn">Sin advertencias — el cálculo completó sin condiciones especiales.</div>
  {% endif %}
</div>

{# ═══════════════════════════════════════════════════════════════════════════ #}
{# PAGE 6 — Formula trace appendix                                            #}
{# ═══════════════════════════════════════════════════════════════════════════ #}

{% if trace %}
<div class="pb">
  <div class="trace-h">Apéndice — Trazabilidad del cálculo</div>
  {% for step in trace %}
  <div class="ts {% if step.error %}ts-err{% endif %}">
    <div class="ts-step">Paso {{ step.paso }}</div>
    <div class="ts-desc">{{ step.descripcion }}</div>
    {% if step.expresion %}
    <div class="ts-expr">{{ step.expresion }}</div>
    {% endif %}
    {% if step.valores %}
    <div class="ts-vals">{{ step.valores | pretty_kv }}</div>
    {% endif %}
    {% if step.resultado is not none %}
    <div class="ts-res">
      = {% if step.resultado is mapping %}
        {% for k, v in step.resultado.items() %}{{ k }}: {{ v }}  {% endfor %}
      {% elif step.resultado is iterable and step.resultado is not string %}
        {{ step.resultado | join(", ") }}
      {% else %}
        {{ step.resultado }}
      {% endif %}
      {{ " " + step.unidad if step.unidad else "" }}
    </div>
    {% endif %}
    {% if step.nota %}<div class="ts-note">{{ step.nota }}</div>{% endif %}
    {% if step.fuente %}<div class="ts-src">Fuente: {{ step.fuente }}</div>{% endif %}
    {% if step.error %}<div class="ts-err-msg">Error: {{ step.error }}</div>{% endif %}
  </div>
  {% endfor %}
</div>
{% endif %}

</body>
</html>
"""


# ── Jinja2 environment ─────────────────────────────────────────────────────────

def _make_env() -> Environment:
    import json as _json

    env = Environment(loader=BaseLoader(), autoescape=True)

    def pretty_kv(obj):
        if not obj:
            return ""
        try:
            lines = []
            for k, v in obj.items():
                lines.append(f"{k}: {v}")
            return "\n".join(lines)
        except Exception:
            return str(obj)

    def n1(val):
        return _n(val, 1)

    env.filters["pretty_kv"] = pretty_kv
    env.filters["n1"] = n1
    env.tests["none"] = lambda v: v is None
    env.tests["mapping"] = lambda v: isinstance(v, dict)
    env.tests["iterable"] = lambda v: hasattr(v, "__iter__")
    env.tests["string"] = lambda v: isinstance(v, str)

    return env


# ── Binding constraint helpers ─────────────────────────────────────────────────

_BC_SHORT = {
    "height":               "ALTURA",
    "footprint":            "HUELLA (IO)",
    "total_area":           "ÁREA TOTAL (IC)",
    "footprint_and_height": "HUELLA + ALTURA",
    "total_area_and_footprint": "IC + IO",
    "indeterminado":        "INDETERMINADO",
    "anu_required":         "ANU REQUERIDA",
    "tratamiento_no_implementado": "TRAT. NO IMPL.",
    "conservacion_no_soportado":   "CONSERVACIÓN",
}

_BC_SENTENCE = {
    "height":
        "La altura máxima en pisos es la restricción operativa fijada por los mapas CU-5.4.x. "
        "El área construible (IC) y la huella (IO) son resultantes de la geometría de aislamientos — "
        "no existen como topes numéricos en el decreto.",
    "footprint":
        "La huella máxima (IO) es la restricción operativa. El IC no es el control activo.",
    "total_area":
        "El área construible máxima (IC × ANU) es la restricción operativa. "
        "La altura y la huella son resultantes de la geometría.",
    "footprint_and_height":
        "La huella (IO) y la altura en pisos operan simultáneamente como restricción doble.",
    "total_area_and_footprint":
        "El IC y el IO están ambos fijados numéricamente y actúan en conjunto como restricciones activas.",
    "indeterminado":
        "La restricción operativa no pudo determinarse automáticamente. "
        "Se requiere modelado volumétrico con las dimensiones exactas del predio.",
    "anu_required":
        "No es posible calcular sin el Área Neta Urbanizable (ANU) del Plan Parcial.",
    "tratamiento_no_implementado":
        "El tratamiento urbanístico de este predio no está implementado en esta versión.",
    "conservacion_no_soportado":
        "Los predios en Conservación requieren la ficha BIC individual (IDPC/SDCRD). "
        "Consulte directamente la Curaduría Urbana.",
}


# ── Main render function ───────────────────────────────────────────────────────

def _render_html(
    calc_result: dict,
    lookup_snapshot: dict,
    address: str = "Dirección no especificada",
) -> str:
    d  = calc_result
    lu = lookup_snapshot
    m  = d.get("metrics") or {}
    lote = d.get("lote") or {}
    inp  = d.get("input") or {}
    pk   = d.get("parking") or {}
    bc   = d.get("binding_constraint") or "indeterminado"
    trat = d.get("tratamiento") or lu.get("tratamiento") or "—"

    # Dates
    now = datetime.now(tz=timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")
    date_label = _date_label(now)
    consultation_date = _get(d, "consulta", "fecha") or _get(lu, "consulta", "fecha") or now.strftime("%Y-%m-%d")

    # Map
    rings   = (lu.get("lote") or {}).get("geojson_polygon")
    map_img = _render_map(rings, inp.get("lng", 0), inp.get("lat", 0))

    # Identity
    lotcodigo  = lote.get("lotcodigo") or "—"
    lot_area   = _area(_get(lote, "area_m2", "valor"))
    tip        = _get(lu, "tipologia", "valor") or "—"
    aa_code    = _get(lu, "area_actividad", "codigo") or "—"
    aa_name    = _get(lu, "area_actividad", "nombre") or ""
    area_act   = f"{aa_code}" + (f" — {aa_name[:45]}" if aa_name else "")
    anu_val    = _get(d, "anu", "valor_m2")
    anu_usado  = _area(anu_val) if anu_val else None
    rango      = d.get("rango") or lu.get("rango")
    lat        = f'{inp.get("lat", 0):.5f}'
    lng        = f'{inp.get("lng", 0):.5f}'

    # Verdict
    area_max_val = _get(m, "area_construible_max_m2", "valor")
    if area_max_val:
        area_max  = _n(area_max_val, 1)
        area_unit = "m²"
    else:
        nota_ic = _get(m, "area_construible_max_m2", "nota") or ""
        area_max  = "IC resultante"
        area_unit = nota_ic[:55] if nota_ic else "requiere modelado geométrico"

    pisos_val = _get(m, "altura_base_pisos", "valor") or _get(m, "altura_maxima_pisos", "valor")
    bc_short   = _BC_SHORT.get(bc, bc.upper())
    bc_detail  = f"{int(pisos_val)} pisos" if pisos_val and bc in ("height", "footprint_and_height") else ""
    verdict_s  = _BC_SENTENCE.get(bc, f"Restricción: {bc}")

    unit_est_data = _unit_estimate(d)
    if unit_est_data:
        units_est = str(unit_est_data["total_unidades"])
        units_sub = "estimación — ver supuestos p.4"
    else:
        units_est = "—"
        units_sub = "IC resultante — sin área construible"

    # Profile SVG
    profile_svg = _profile_svg(d)

    # Setback summary cards
    ant_dim = (d.get("antejardin") or {}).get("dimension_m")
    if isinstance(ant_dim, dict):
        ant_dim = None
    post_v  = _get(m, "aislamiento_posterior_m", "valor")
    lat_v   = _get(m, "aislamiento_lateral_m", "valor")
    ret_v   = _get(m, "retroceso_fachada_A_m", "valor")

    setbacks = []
    if ant_dim is not None:
        setbacks.append({"label": "Antejardín", "val": f"{_n(ant_dim, 1)} m",
                          "src": "Art. 314 / Layer 22"})
    if ret_v is not None:
        setbacks.append({"label": "Retroceso fachada", "val": f"{_n(ret_v, 2)} m",
                          "src": "Anx. 5 Cap. 1.2.2.E.1.1"})
    if post_v is not None:
        setbacks.append({"label": "Aislamiento posterior", "val": f"{_n(post_v, 1)} m",
                          "src": "Anx. 5 Cap. 2.4.2.A.2"})
    if lat_v and lat_v > 0:
        setbacks.append({"label": "Aislamiento lateral", "val": f"≥ {_n(lat_v, 1)} m",
                          "src": "Art. 310 Num. 3"})
    if not setbacks:
        setbacks.append({"label": "Retiros", "val": "Sin dato",
                          "src": "Verificar con Curaduría"})

    # Param rows, floor rows, unit estimate, next steps
    param_rows = _param_rows(d, lu)
    floor_rows = _floor_rows(d, lu)
    next_steps_list = _next_steps(d, lu)
    warnings   = [w for w in (d.get("warnings") or []) if w]
    catastro_snap = lote.get("consulta_catastro") or (lu.get("lote") or {}).get("consulta_catastro")
    if catastro_snap:
        warnings.insert(0, (
            f"PREDIO APROXIMADO: la coordenada no intersectó Catastro y se seleccionó el lote "
            f"más cercano, a {_n(catastro_snap.get('distancia_m'), 1)} m. Confirme el predio "
            "antes de usar este informe."
        ))
    trace      = d.get("formula_trace") or []

    # Render
    env  = _make_env()
    tmpl = env.from_string(_TEMPLATE)
    return tmpl.render(
        disclaimer   = DISCLAIMER,
        decree_short = DECREE_SHORT,
        timestamp    = timestamp,
        date_label   = date_label,
        consultation_date = consultation_date,
        address      = address,
        lotcodigo    = lotcodigo,
        lot_area     = lot_area,
        tratamiento  = trat,
        tipologia    = tip,
        area_actividad = area_act,
        anu_usado    = anu_usado,
        rango        = rango,
        lat          = lat,
        lng          = lng,
        map_img      = map_img,
        area_max     = area_max,
        area_max_unit = area_unit,
        units_est    = units_est,
        units_sub    = units_sub,
        binding_short  = bc_short,
        binding_detail = bc_detail,
        verdict_sentence = verdict_s,
        profile_svg  = profile_svg,
        setbacks     = setbacks,
        param_rows   = param_rows,
        floor_rows   = floor_rows,
        unit_est     = unit_est_data,
        next_steps   = next_steps_list,
        warnings     = warnings,
        trace        = trace,
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_pdf(
    calc_result: dict,
    lookup_snapshot: dict,
    address: str = "Dirección no especificada",
    extra_inputs: list[dict] | None = None,
) -> bytes:
    html = _render_html(calc_result, lookup_snapshot, address)
    return _html_to_pdf(html)


def generate_html_preview(
    calc_result: dict,
    lookup_snapshot: dict,
    address: str = "Dirección no especificada",
) -> str:
    return _render_html(calc_result, lookup_snapshot, address)
