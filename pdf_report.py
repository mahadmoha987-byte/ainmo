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
import re
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

def _n(val, dec: int = 1, dash: str = "No disponible") -> str:
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


def _area(val, dash: str = "No disponible") -> str:
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
    post_m = _fval(m, "aislamiento_posterior_m", "valor")
    lat_m_raw = _fval(m, "aislamiento_lateral_m", "valor")
    lat_m  = lat_m_raw if lat_m_raw and lat_m_raw > 0 else None

    is_hbc    = bc in ("height", "footprint_and_height")
    dim_color = "#1B48D4" if is_hbc else "#8D929E"

    W, H, GY = 580, 260, 206
    left_sb  = max(40, (ant_m or 0) * 9) if ant_m else 44
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
    post_label = ""
    if post_m:
        prx = min(W - 34, max(rx + 24, rx + (W - rx) / 2))
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

    def row(cat, campo, valor, fuente, confianza, nota="", estado=None):
        return {"cat": cat, "campo": campo, "valor": valor,
                "fuente": fuente, "confianza": confianza, "nota": nota,
                "estado": estado}

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
                    lote.get("lotcodigo") or "No disponible",
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
                    d.get("tratamiento") or lu.get("tratamiento") or "No disponible",
                    "Layer 15 POT FeatureServer (TRATAMIENTO)", "alta"))
    rows.append(row("Norma", "Tipología predial",
                    tip.get("valor") or "No disponible",
                    "Layer 15 (TIPOLOGIA)", tip.get("confianza") or "alta"))

    alt_ed = ed.get("altura_maxima") or {}
    alt_tipo = alt_ed.get("tipo") or "No disponible"
    alt_min  = alt_ed.get("pisos_min")
    alt_max  = alt_ed.get("pisos_max")
    if alt_min is not None and alt_min == alt_max:
        alt_str = f"{int(alt_min)} pisos (fijo)"
    elif alt_min is not None and alt_max is not None:
        alt_str = f"{int(alt_min)}–{int(alt_max)} pisos (rango)"
    else:
        alt_str = alt_tipo or "No disponible"
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

    derived = dm.get("area_construible_estimada") or {}
    if derived:
        derived_range = derived.get("rango_m2")
        derived_is_valid = derived.get("estado") == "derivado" and derived.get("fuera_de_rango") is not True
        derived_value = "No calculable"
        if derived_is_valid and derived.get("valor_m2") is not None:
            derived_value = f"{_n(derived.get('valor_m2'), 1)} m²"
        elif derived_is_valid and derived_range:
            derived_value = f"{_n(derived_range[0], 1)}–{_n(derived_range[1], 1)} m²"
        rows.append(row(
            "Estimación", "Área construible estimada (huella × pisos)",
            derived_value,
            _metric_source(derived, "Catastro capa 0 + POT capas 15, 22 y 38; norma volumétrica aplicable"),
            derived.get("confianza") or "baja",
            derived.get("motivo") or derived.get("advertencia") or "Estimación derivada; no es un IC/IO fijado por el decreto.",
        ))

    sub_ed = ed.get("subdivision_permitida") or {}
    if sub_ed:
        subdivision_value = sub_ed.get("valor")
        subdivision_display = "Sí" if subdivision_value is True else "No" if subdivision_value is False else "Por confirmar"
        rows.append(row("Norma", "Subdivisión permitida",
                        subdivision_display,
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
                    f"{_n(post_v, 1)} m" if post_v is not None else "No calculable",
                    post_obj.get("articulo") or "Anexo 5 Cap. 2.4.2.A.2 D.555/2021",
                    post_obj.get("confianza") or _get(dm, "aislamiento_posterior_m", "confianza") or "alta",
                    post_obj.get("nota") or ""))

    lat_obj = ed.get("aislamiento_lateral") or {}
    lat_v   = _get(dm, "aislamiento_lateral_m", "valor")
    lat_note = _get(dm, "aislamiento_lateral_m", "nota") or lat_obj.get("nota") or ""
    lateral_metric = dm.get("aislamiento_lateral_m") or {}
    lateral_display = (
        "No exigido" if lat_v == 0 and _state(lateral_metric, value=lat_v) == "no_aplica"
        else f"≥ {_n(lat_v, 1)} m" if lat_v is not None
        else lat_obj.get("formula") or "No calculable"
    )
    rows.append(row("Volumen", "Aislamiento lateral",
                    lateral_display,
                    lat_obj.get("articulo") or "Art. 310 Num. 3 / Anexo 5 D.555/2021",
                    _get(dm, "aislamiento_lateral_m", "confianza") or lat_obj.get("confianza") or "alta",
                    lat_note))

    ret_obj = ed.get("retroceso_fachada") or {}
    ret_v   = _get(dm, "retroceso_fachada_A_m", "valor")
    ret_d   = dm.get("retroceso_fachada_A_m") or {}
    ret_note = ret_d.get("nota") or ret_obj.get("nota") or ""
    rows.append(row("Volumen", "Altura máxima de fachada (A = 2,5 × D)",
                    f"{_n(ret_v, 2)} m" if ret_v is not None else "Sin dato (D no encontrado)",
                    ret_obj.get("articulo") or "Anexo 5 Cap. 1.2.2.E.1.1 D.555/2021",
                    ret_d.get("confianza") or "media",
                    (ret_note if "no se descuenta de la huella" in ret_note.lower()
                     else (ret_note + " A es una altura; no se descuenta de la huella edificable.").strip()),
                    _state(ret_d, value=ret_v)))

    # ── Vía ──
    # The value actually used by calc is echoed in the retroceso metric. Read
    # that first so a manual/shared-calculation input cannot be lost in PDF.
    d_m = ret_d.get("D_m")
    if d_m is None:
        d_m = avia.get("D_m") or avia.get("ancho_m")
    d_source = ret_d.get("fuente_D")
    if d_source in (None, "sin_dato"):
        d_source = avia.get("fuente") or "Layer 38 POT FeatureServer (Calzada)"
    rows.append(row("Vía", "Ancho de calzada (D)",
                    f"{_n(d_m, 2)} m ({avia.get('n_calzadas', '?')} calzada{'s' if (avia.get('n_calzadas') or 1) > 1 else ''})" if d_m else "No encontrado en radio 25 m",
                    d_source,
                    ret_d.get("confianza") or avia.get("confianza") or "sin_dato",
                    ret_d.get("motivo") or ret_d.get("nota") or avia.get("nota") or "Andenes y separadores no incluidos — perfil total puede ser mayor."))

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
        bonus_metric = dm.get("altura_con_bonus_pisos") or {}
        bonus_value = bonus_metric.get("valor")
        bonus_state = _state(bonus_metric, value=bonus_value, default="requiere_concepto")
        if bonus_value is None and bonus_state == "resuelto":
            bonus_state = "requiere_concepto"
        bonus_display = (
            f"Aplica: {int(bonus_value)} pisos" if bonus_value is not None
            else "No aplica a este lote" if bonus_state == "no_aplica"
            else f"Por confirmar: × {bon_man.get('factor','2')} — máx. {bon_man.get('altura_maxima_con_bonus_pisos','?')} pisos"
        )
        rows.append(row("Bonus", "Bonus manzana completa (Art. 310 § 2)",
                        bonus_display,
                        bon_man.get("articulo") or "Art. 310 Parágrafo 2 D.555/2021",
                        bonus_metric.get("confianza") or bon_man.get("confianza") or "media",
                        bonus_metric.get("motivo") or bonus_metric.get("nota") or bon_man.get("condicion") or
                        "Requiere confirmar ocupación de manzana completa, frente, área y perfil vial.",
                        bonus_state))
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
            rules.append(f"Antejardín {_n(ant_dim, 1)} m según la regla citada en la tabla normativa")
        if p == lat_desde and lat_v:
            rules.append(f"Aislamiento lateral ≥ {_n(lat_v, 1)} m inicia según la regla citada")
        if p == n:
            rules.append("Altura máxima — mapa CU-5.4.x")
        rows.append({
            "piso": f"P{p:02d}",
            "nivel": f"+{_n(h_inf, 1)}–+{_n(h_sup, 1)} m*",
            "regla": " · ".join(rules) if rules else "Sin regla específica para este piso",
        })
    return rows


# ── Unit estimate ─────────────────────────────────────────────────────────────

def _unit_estimate(d: dict) -> dict | None:
    """Run the shared unit estimator from the same area shown in the report."""
    metrics = d.get("metrics") or {}
    regulatory = metrics.get("area_construible_max_m2") or {}
    area_val = regulatory.get("valor") if regulatory.get("estado") in {"resuelto", "derivado"} else None
    area_is_derived = False
    if not area_val:
        derived = _get(d, "metrics", "area_construible_estimada", default={}) or {}
        if derived.get("estado") == "derivado" and derived.get("fuera_de_rango") is not True:
            area_val = derived.get("valor_m2")
            if area_val is None and derived.get("rango_m2"):
                area_val = derived["rango_m2"][0]
            area_is_derived = area_val is not None
    if not area_val:
        return None
    try:
        from calc import estimate_units
        result = estimate_units(float(area_val))
        result["basado_en_area_estimada"] = area_is_derived
        return result
    except Exception:
        return None


# ── Próximos pasos ────────────────────────────────────────────────────────────

def _next_steps(d: dict, lu: dict) -> list[dict]:
    """Generate a contextual checklist based on the analysis result."""
    steps = []
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
        "IC, IO, aislamientos y la envolvente de fachada dependen de las dimensiones exactas del lote.")

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
                f"La altura máxima de fachada usa A = 2,5 × D. El D actual ({_n(avia.get('D_m') or avia.get('ancho_m'), 2)} m) "
                f"incluye solo calzada(s), sin andenes ni separadores.")

    # Lateral setback note
    lat_conf = _get(m, "aislamiento_lateral_m", "confianza") or ""
    if lat_conf == "media":
        add("Recalcular aislamiento lateral con la altura real definida en diseño (≠ 3 m/piso asumido).",
            "media",
            "Fórmula: máximo entre un quinto de la altura total y 4 m. La altura actual asume 3 m por piso.")

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


# ── Due-diligence report helpers ─────────────────────────────────────────────

_VALID_STATES = {
    "resuelto", "derivado", "insuficiente", "requiere_concepto",
    "no_aplica", "error",
}


def _state(obj: dict | None, *, value: Any = None, default: str = "insuficiente") -> str:
    state = (obj or {}).get("estado")
    if state in _VALID_STATES:
        return state
    if value is not None:
        return "resuelto"
    return default


def _metric_source(obj: dict | None, fallback: str) -> str:
    obj = obj or {}
    parts = []
    for key in ("articulo", "fuente", "fuente_D"):
        value = obj.get(key)
        if value and value not in parts and value not in ("usuario", "sin_dato"):
            parts.append(str(value))
    return " · ".join(parts) or fallback


def _normative_rows(d: dict, lu: dict) -> list[dict]:
    """Required-vs-result rows. Calculated metrics are always authoritative."""
    m = d.get("metrics") or {}
    ed = lu.get("edificabilidad") or {}
    ant = d.get("antejardin") or {}
    parking = d.get("parking") or {}
    rows: list[dict] = []

    def add(parameter: str, required: str, result: str, obj: dict | None,
            source: str, reason: str = "") -> None:
        rows.append({
            "parameter": parameter,
            "required": required,
            "result": result,
            "state": _state(obj, value=None if result in ("Sin dato", "No calculable") else result),
            "reason": (obj or {}).get("motivo") or (obj or {}).get("nota") or reason,
            "source": _metric_source(obj, source),
        })

    height = m.get("altura_base_pisos") or m.get("altura_maxima_pisos") or {}
    height_v = height.get("valor")
    height_rule = ed.get("altura_maxima") or {}
    height_required = height_rule.get("articulo") or "Mapa CU-5.4.x / Art. 310 Decreto 555/2021"
    add("Altura", height_required,
        f"{_n(height_v, 0)} pisos" if height_v is not None else "Resultante",
        height, height_rule.get("fuente") or "Layer 15 POT FeatureServer (ALTURA_MAXIMA)")

    post = m.get("aislamiento_posterior_m") or {}
    post_v = post.get("valor")
    add("Aislamiento posterior", "Según altura efectiva y tipología",
        f"{_n(post_v, 1)} m" if post_v is not None else "Sin dato",
        post, "Anexo 5 Cap. 2.4.2.A.2 Decreto 555/2021")

    lateral = m.get("aislamiento_lateral_m") or {}
    lateral_v = lateral.get("valor")
    lateral_result = (
        "No exigido" if lateral_v == 0 and _state(lateral, value=0) == "no_aplica"
        else f"{_n(lateral_v, 1)} m" if lateral_v is not None else "Sin dato"
    )
    add("Aislamiento lateral", "Según tipología y altura efectiva", lateral_result,
        lateral, "Art. 310 Num. 3 / Anexo 5 Cap. 1.2.2.D.2 Decreto 555/2021")

    ant_dim = ant.get("dimension_m")
    ant_obj = ant_dim if isinstance(ant_dim, dict) else ant
    ant_v = ant_dim.get("valor") if isinstance(ant_dim, dict) else ant_dim
    ant_result = "No exigido" if ant_v == 0 else (f"{_n(ant_v, 1)} m" if ant_v is not None else "Sin dato")
    add("Antejardín", "Mapa CU-5.5 y regla aplicable al predio", ant_result,
        ant_obj, ant.get("articulo") or ant.get("fuente") or "Layer 22 POT FeatureServer")

    io = m.get("planta_maxima_m2") or {}
    io_v = io.get("valor")
    io_rule = ed.get("indice_ocupacion") or {}
    io_req = (
        f"IO máximo {_n(io_rule.get('valor'), 2)}" if io_rule.get("valor") is not None
        else "IO resultante de la norma volumétrica"
    )
    add("Índice de ocupación / huella", io_req,
        f"{_area(io_v)} de huella" if io_v is not None else "No aplica como índice numérico",
        io, io_rule.get("articulo") or "Art. 310 Decreto 555/2021")

    ic = m.get("area_construible_max_m2") or {}
    ic_v = ic.get("valor")
    ic_rule = ed.get("indice_construccion") or {}
    ic_req = (
        f"IC máximo {_n(ic_rule.get('valor'), 2)}" if ic_rule.get("valor") is not None
        else "IC resultante de la norma volumétrica"
    )
    add("Índice de construcción", ic_req,
        _area(ic_v) if ic_v is not None else "No aplica como índice numérico",
        ic, ic_rule.get("articulo") or "Art. 310 Decreto 555/2021")

    retro = m.get("retroceso_fachada_A_m") or {}
    retro_v = retro.get("valor")
    factor = retro.get("factor") or 2.5
    add("Altura máxima de fachada", f"A = {_n(factor, 1)} × D",
        f"{_n(retro_v, 2)} m" if retro_v is not None else "Sin dato",
        retro, "Anexo 5 Cap. 1.2.2.E.1.1 Decreto 555/2021")

    # D is deliberately sourced from the calculated metric used for A, not by
    # re-reading the raw lookup snapshot. This keeps PDF and /api/calc identical.
    road_v = retro.get("D_m")
    road_confidence = retro.get("confianza")
    road_state = "resuelto" if road_v is not None and road_confidence == "alta" else "insuficiente"
    road_obj = {
        "estado": road_state,
        "motivo": (
            "El ancho disponible corresponde a calzada; faltan andenes y separador para resolver el perfil vial total."
            if road_v is not None and road_state == "insuficiente"
            else retro.get("motivo") or retro.get("nota")
        ),
        "que_se_necesita": "El perfil vial completo: calzada, andenes y separador.",
        "quien_lo_resuelve": "SDP",
        "fuente": retro.get("fuente_D"),
    }
    add("Ancho de calzada disponible (D)", "Entrada para la altura máxima de fachada",
        f"{_n(road_v, 2)} m" if road_v is not None else "Sin dato",
        road_obj, "Layer 38 POT FeatureServer (ANCHO)")

    if parking.get("min_pct") is not None:
        parking_area = parking.get("min_m2") or parking.get("max_m2") or parking.get("base_area_m2")
        parking_obj = {
            "estado": (parking.get("estado") or "resuelto") if parking_area is not None else "insuficiente",
            "motivo": (parking.get("motivo") or parking.get("nota")) if parking_area is not None else
                      "Los porcentajes normativos están identificados, pero falta el área cubierta del proyecto para calcular la exigencia.",
            "que_se_necesita": None if parking_area is not None else "El área cubierta del proyecto según el Art. 390.",
            "quien_lo_resuelve": None if parking_area is not None else "profesional",
            "fuente": parking.get("fuente"),
        }
        result = (
            f"mín. {_n(parking.get('min_pct'), 0)}% · "
            f"máx. {_n(parking.get('max_pct'), 0)}% · "
            f"adicional {_n(parking.get('adicional_pct'), 0)}%"
        )
        add("Cupos / área de estacionamientos", "Arts. 389, 390 y 390A", result,
            parking_obj, "Arts. 389, 390 y 390A Decreto 555/2021")
    else:
        add("Cupos / área de estacionamientos", "Arts. 389, 390 y 390A", "Sin dato",
            {"estado": "insuficiente", "motivo": parking.get("nota")},
            "Arts. 389, 390 y 390A Decreto 555/2021")

    return rows


def _used_sources(d: dict, lu: dict, rows: list[dict], param_rows: list[dict]) -> list[dict]:
    """Return a short, grouped source register for this lot."""
    m = d.get("metrics") or {}
    ant = d.get("antejardin") or {}
    ed = lu.get("edificabilidad") or d.get("edificabilidad") or {}
    groups: list[dict] = []

    gis = [
        "Catastro Bogotá · MapServer capa 0 (polígono, área, código de lote y unidades prediales).",
        "SDP · POT FeatureServer capa 15 (tratamiento, tipología y altura máxima).",
    ]
    if ant or _get(m, "area_construible_estimada", default={}):
        gis.append("SDP · POT FeatureServer capa 22, mapa CU-5.5 (antejardín).")
    if _get(m, "retroceso_fachada_A_m", "D_m") is not None:
        gis.append("SDP · POT FeatureServer capa 38, campo ANCHO (ancho de calzada; no equivale al perfil vial total).")
    if lu.get("area_actividad"):
        gis.append("SDP · POT FeatureServer capa 14 (área de actividad y regla de estacionamientos).")
    groups.append({"name": "Capas GIS", "items": gis})

    treatment = str(d.get("tratamiento") or lu.get("tratamiento") or "").upper()
    articles = []
    if "CONSOLID" in treatment:
        articles.append("Decreto Distrital 555 de 2021 · Art. 310 (tratamiento de Consolidación).")
    elif "RENOV" in treatment:
        articles.append("Decreto Distrital 555 de 2021 · Art. 304 (tratamiento de Renovación Urbana).")
    elif "MEJORAMIENTO" in treatment:
        articles.append("Decreto Distrital 555 de 2021 · Art. 338 (tratamiento de Mejoramiento Integral).")
    elif "DESARROLLO" in treatment:
        articles.append("Decreto Distrital 555 de 2021 · Art. 281 (tratamiento de Desarrollo).")
    if d.get("parking"):
        articles.append("Decreto Distrital 555 de 2021 · Arts. 389, 390 y 390A (estacionamientos).")
    for metric_key, fallback in (
        ("aislamiento_posterior_m", "Anexo 5 · aislamiento posterior"),
        ("aislamiento_lateral_m", "Anexo 5 · aislamiento lateral"),
        ("retroceso_fachada_A_m", "Anexo 5 · altura máxima de fachada A"),
    ):
        metric = m.get(metric_key) or {}
        if metric:
            rule_key = {
                "aislamiento_posterior_m": "aislamiento_posterior",
                "aislamiento_lateral_m": "aislamiento_lateral",
                "retroceso_fachada_A_m": "retroceso_fachada",
            }[metric_key]
            rule_source = _metric_source(ed.get(rule_key) or {}, fallback)
            metric_source = _metric_source(metric, "")
            for source in (rule_source, metric_source):
                if source and source not in articles:
                    articles.append(source)
    if articles:
        groups.append({"name": "Decretos y artículos", "items": articles})

    if ant and ant.get("dimension_m") is not None:
        groups.append({"name": "Resoluciones", "items": [
            "Resolución SDP 1631 de 2023 · cartografía operativa del antejardín, cuando la capa 22 aporta el dato."
        ]})
    return groups


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
          <div class="vc-label">{{ area_label }}</div>
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
  {% if profile_blocked_reason %}
  <div class="alert"><strong>Perfil no generado.</strong> {{ profile_blocked_reason }}</div>
  {% else %}{{ profile_svg | safe }}{% endif %}
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
    {% if not profile_blocked_reason %}Zona rayada = retiro exigido. Zona gris = envolvente máxima. Línea azul = limitante ({{ binding_short }}).{% endif %}
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


_DUE_DILIGENCE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<style>
:root {
  --ink:#15171b; --muted:#61656d; --line:#cfd2d6; --soft:#f3f3f1; --paper:#fff;
  --green-bg:#e7f3eb; --green:#195d35; --green-b:#71ad88;
  --amber-bg:#fff1d5; --amber:#724500; --amber-b:#c59024;
  --grey-bg:#eceeef; --grey:#4f555e; --grey-b:#a8adb4;
  --neutral-bg:#f5f5f3; --neutral:#55585e; --neutral-b:#c8c9c7;
  --red-bg:#f9e5e5; --red:#8b1717; --red-b:#c94b4b;
}
@page {
  size:A4; margin:14mm 14mm 18mm 14mm;
  @bottom-left { content:"Ainmo · prefactibilidad, no licencia"; font:6.5pt Arial; color:#6d7178; }
  @bottom-center { content:"{{ decree_short }} · {{ timestamp }}"; font:6.5pt Arial; color:#6d7178; }
  @bottom-right { content:"Página " counter(page) " de " counter(pages); font:7pt Arial; color:#15171b; }
}
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:var(--paper); font:8.5pt/1.42 Arial,Helvetica,sans-serif; }
h1,h2,h3,p { margin:0; }
h1 { font-size:24pt; line-height:1.08; letter-spacing:-.4pt; }
h2 { font-size:14pt; margin-bottom:8pt; }
h3 { font-size:9pt; margin-bottom:5pt; text-transform:uppercase; letter-spacing:.45pt; }
.mono { font-family:"Courier New",monospace; }
.page { margin-top:18pt; }
.toc-page { min-height:240mm; page-break-before:always; page-break-after:always; }
.appendix-page { page-break-before:always; }
.avoid { page-break-inside:avoid; }
.eyebrow { font-size:7pt; font-weight:700; letter-spacing:1.1pt; text-transform:uppercase; color:var(--muted); }
.section-no { float:right; color:var(--muted); font:7pt "Courier New",monospace; }
.section-head { padding-bottom:5pt; border-bottom:1pt solid var(--ink); margin:16pt 0 10pt; page-break-after:avoid; }
.note { color:var(--muted); font-size:7.5pt; }
.source { color:var(--muted); font-size:6.7pt; margin-top:2pt; line-height:1.3; }
.source::before { content:"Fuente: "; font-weight:700; color:var(--ink); }
.status { display:inline-block; padding:1.5pt 5pt; border:0.5pt solid; border-radius:8pt; font-size:6.4pt; font-weight:700; text-transform:uppercase; letter-spacing:.3pt; white-space:nowrap; }
.s-resuelto { background:var(--green-bg); color:var(--green); border-color:var(--green-b); }
.s-derivado,.s-requiere_concepto { background:var(--amber-bg); color:var(--amber); border-color:var(--amber-b); }
.s-insuficiente { background:var(--grey-bg); color:var(--grey); border-color:var(--grey-b); }
.s-no_aplica { background:var(--neutral-bg); color:var(--neutral); border-color:var(--neutral-b); }
.s-error { background:var(--red-bg); color:var(--red); border-color:var(--red-b); }
table { width:100%; border-collapse:collapse; }
th { text-align:left; font-size:6.7pt; text-transform:uppercase; letter-spacing:.35pt; background:var(--soft); border:0.5pt solid var(--line); padding:4pt; }
td { vertical-align:top; border:0.5pt solid var(--line); padding:4pt; }
.kv td:first-child { width:37%; color:var(--muted); }
.kv td:last-child { font-weight:700; }

/* Cover */
.cover { page-break-after:always; padding-top:10mm; }
.cover-rule { border-top:3pt solid var(--ink); margin-bottom:22mm; }
.cover .eyebrow { margin-bottom:10pt; }
.cover h1 { max-width:160mm; }
.cover-sub { font-size:11pt; color:var(--muted); margin-top:8pt; }
.cover-kv { width:135mm; margin-top:20mm; }
.cover-kv td { padding:5pt 0; border:0; border-bottom:.5pt solid var(--line); }
.cover-kv td:first-child { color:var(--muted); width:52mm; padding-right:7pt; }
.cover-disclaimer { margin-top:14mm; padding-top:7pt; border-top:1pt solid var(--ink); font-size:7.5pt; color:var(--muted); }
.site-assumption { margin-top:7pt; padding:7pt 9pt; border:1pt solid var(--amber-b); background:var(--amber-bg); color:var(--amber); page-break-inside:avoid; }
.address-substitution { margin-top:9pt; padding:7pt 9pt; border:1pt solid var(--amber-b); border-left:3pt solid var(--amber); background:var(--amber-bg); color:var(--amber); font-size:8pt; line-height:1.4; page-break-inside:avoid; }
.address-substitution strong { color:var(--ink); }

/* TOC */
.toc { margin-top:18pt; }
.toc-row { display:flex; border-bottom:.5pt solid var(--line); padding:7pt 0; }
.toc-n { width:13mm; font:bold 9pt "Courier New",monospace; }
.toc-label { flex:1; font-size:10pt; }

/* Summary */
.summary-grid { display:flex; gap:12pt; }
.summary-left { flex:1; }
.summary-right { width:72mm; }
.map { width:100%; max-height:78mm; object-fit:cover; border:.5pt solid var(--line); }
.verdict { margin-top:10pt; padding:8pt 10pt; border:1pt solid var(--ink); }
.verdict strong { display:block; font-size:10pt; margin-bottom:3pt; }

/* Comparison */
.comparison { table-layout:fixed; }
.comparison th:nth-child(1) { width:20%; }
.comparison th:nth-child(2) { width:28%; }
.comparison th:nth-child(3) { width:31%; }
.comparison th:nth-child(4) { width:21%; }
.comparison .result { font:bold 8.5pt "Courier New",monospace; }
.comparison .reason { color:var(--muted); font-size:6.7pt; margin-top:3pt; }
.comparison .row-source { color:var(--muted); font-size:6.3pt; margin-top:3pt; }
.comparison tr { page-break-inside:avoid; }

/* Buildability */
.callout-grid { display:flex; border:1pt solid var(--ink); margin-bottom:10pt; }
.callout { flex:1; padding:10pt; border-left:.5pt solid var(--line); }
.callout:first-child { border-left:0; }
.callout-label { font-size:6.5pt; text-transform:uppercase; letter-spacing:.5pt; color:var(--muted); }
.callout-value { font:bold 16pt/1.05 "Courier New",monospace; margin:4pt 0; }
.warning { padding:6pt 8pt; border-left:2pt solid var(--amber-b); background:var(--amber-bg); color:var(--amber); margin:7pt 0; }
.data-table tr { page-break-inside:avoid; }
.data-table .cat td { background:var(--soft); font-weight:700; text-transform:uppercase; letter-spacing:.3pt; font-size:6.7pt; }
.data-table .value { font-family:"Courier New",monospace; }

/* Profile and actions */
.profile-wrap { border:.5pt solid var(--line); padding:8pt; }
.setbacks { display:flex; margin-top:7pt; }
.setback { flex:1; padding:5pt; border:.5pt solid var(--line); margin-left:-.5pt; }
.setback:first-child { margin-left:0; }
.setback b { display:block; font:10pt "Courier New",monospace; }
.step { padding:6pt 7pt; border:.5pt solid var(--line); border-left:3pt solid var(--grey-b); margin-bottom:5pt; page-break-inside:avoid; }
.step.alta,.step.advertencia { border-left-color:var(--red-b); }
.step.media { border-left-color:var(--amber-b); }
.step.info { border-left-color:var(--green-b); }
.step b { display:block; margin-bottom:2pt; }
.alert { padding:5pt 7pt; color:var(--red); background:var(--red-bg); border-left:2pt solid var(--red-b); margin-bottom:4pt; page-break-inside:avoid; }

/* Sources and trace */
.source-group { margin:0 0 10pt; page-break-inside:avoid; }
.sources { margin:0; padding-left:17pt; }
.sources li { margin:0 0 4pt; padding-left:2pt; }
.trace { border-left:2pt solid var(--line); padding:4pt 0 4pt 8pt; margin-bottom:6pt; page-break-inside:avoid; }
.trace-step { font:bold 6.5pt "Courier New",monospace; color:var(--muted); text-transform:uppercase; }
.trace-expr,.trace-values { font:7pt "Courier New",monospace; background:var(--soft); padding:3pt 5pt; margin-top:3pt; white-space:pre-wrap; }
.trace-result { font:bold 9pt "Courier New",monospace; margin-top:3pt; }
</style>
</head>
<body>

<!-- 1. Cover -->
<section class="cover">
  <div class="cover-rule"></div>
  <div class="eyebrow">Informe de prefactibilidad urbanística · Bogotá D.C.</div>
  <h1>{{ report_title }}</h1>
  <p class="cover-sub">Análisis de edificabilidad y controles volumétricos</p>
  {% if near_match %}<div class="address-substitution"><strong>Buscó {{ searched_address }}.</strong> No existe. Analizando <strong>{{ resolved_address }}</strong> ({{ address_relation }}).</div>{% endif %}
  <table class="cover-kv">
    <tr><td>Fecha del informe</td><td>{{ date_label }}</td></tr>
    <tr><td>Coordenadas WGS84</td><td class="mono">{{ lat }}, {{ lng }} · grados decimales</td></tr>
    <tr><td>CHIP / código de lote</td><td class="mono">{{ lotcodigo }}</td></tr>
    <tr><td>Tratamiento</td><td>{{ tratamiento }}</td></tr>
    <tr><td>Área del lote</td><td class="mono">{{ lot_area }}</td></tr>
    <tr><td>Unidades prediales registradas</td><td class="mono">{{ predial_units }}</td></tr>
  </table>
  {% if existing_units_warning %}<div class="site-assumption"><strong>Predio probablemente desarrollado.</strong> {{ existing_units_warning }}</div>{% endif %}
  <div class="cover-disclaimer"><strong>Alcance.</strong> {{ disclaimer }}</div>
</section>

<!-- 2. TOC -->
<section class="toc-page">
  <div class="section-head"><span class="section-no">02</span><div class="eyebrow">Contenido</div><h2>Tabla de contenido</h2></div>
  <div class="toc">
    {% for item in toc %}<div class="toc-row"><div class="toc-n">{{ "%02d"|format(loop.index) }}</div><div class="toc-label">{{ item }}</div></div>{% endfor %}
  </div>
</section>

<!-- 3. Property summary -->
<section class="page">
  <div class="section-head"><span class="section-no">03</span><div class="eyebrow">Identificación</div><h2>Resumen del predio</h2></div>
  <div class="summary-grid">
    <div class="summary-left">
      <table class="kv">
        <tr><td>Dirección</td><td>{{ address_display }}</td></tr>
        <tr><td>CHIP / código de lote</td><td class="mono">{{ lotcodigo }}</td></tr>
        <tr><td>Localidad / UPZ</td><td>{{ locality_upz }}</td></tr>
        <tr><td>Tratamiento</td><td>{{ tratamiento }}</td></tr>
        <tr><td>Tipología</td><td>{{ tipologia }}</td></tr>
        <tr><td>Área de actividad</td><td>{{ area_actividad }}</td></tr>
        <tr><td>Área catastral</td><td class="mono">{{ lot_area }}</td></tr>
        <tr><td>Uso actual</td><td>{{ current_use }}</td></tr>
        {% if anu_usado %}<tr><td>ANU usada</td><td class="mono">{{ anu_usado }}</td></tr>{% endif %}
      </table>
      <div class="verdict">
        <strong>Lectura de prefactibilidad</strong>
        {{ verdict_sentence }}
        <div class="source">{{ decree_short }} · consulta GIS {{ consultation_date }}</div>
      </div>
    </div>
    <div class="summary-right">
      {% if map_img %}<img class="map" src="{{ map_img }}" alt="Polígono catastral del predio">{% else %}<div class="map" style="padding:30mm 8mm;text-align:center;color:var(--muted)">Mapa no disponible</div>{% endif %}
      <p class="source">Catastro Bogotá · OpenStreetMap como referencia cartográfica</p>
    </div>
  </div>
</section>

<!-- 4. Required vs result -->
<section class="page">
  <div class="section-head"><span class="section-no">04</span><div class="eyebrow">Control normativo</div><h2>Normativa aplicable - exigencia vs. resultado</h2></div>
  <table class="comparison">
    <thead><tr><th>Parámetro</th><th>Exigido por norma</th><th>Resultado para este predio</th><th>Estado</th></tr></thead>
    <tbody>
    {% for row in normative_rows %}
      <tr>
        <td><strong>{{ row.parameter }}</strong></td>
        <td>{{ row.required }}</td>
        <td><div class="result">{{ row.result }}</div><div class="row-source">{{ row.source }}</div></td>
        <td><span class="status s-{{ row.state }}">{{ row.state|replace('_',' ') }}</span>{% if row.reason %}<div class="reason">{{ row.reason }}</div>{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>

  <h3 style="margin-top:12pt">Registro completo de parámetros</h3>
  <table class="data-table">
    <thead><tr><th>Campo</th><th>Valor</th><th>Fuente / artículo</th><th>Nota</th></tr></thead>
    <tbody>
    {% set ns = namespace(last_cat="") %}
    {% for row in param_rows %}
      {% if row.cat != ns.last_cat %}{% set ns.last_cat = row.cat %}<tr class="cat"><td colspan="4">{{ row.cat }}</td></tr>{% endif %}
      <tr><td>{{ row.campo }}</td><td class="value">{{ row.valor }}{% if row.estado %}<br><span class="status s-{{ row.estado }}">{{ row.estado|replace('_',' ') }}</span>{% endif %}</td><td>{{ row.fuente }}</td><td class="note">{{ row.nota }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
</section>

<!-- 5. Area and units -->
<section class="page">
  <div class="avoid">
    <div class="section-head"><span class="section-no">05</span><div class="eyebrow">Cabida preliminar</div><h2>Área construible / unidades estimadas</h2></div>
    <div class="site-assumption"><strong>Supuesto de sitio libre.</strong> {{ vacant_site_warning }}{% if existing_units_warning %}<br><strong>Alerta catastral:</strong> {{ existing_units_warning }}{% endif %}</div>
    <div class="callout-grid">
    <div class="callout">
      <div class="callout-label">{{ area_label }}</div>
      <div class="callout-value">{{ area_max }} {{ area_max_unit }}</div>
      <span class="status s-{{ area_state }}">{{ area_state|replace('_',' ') }}</span>
      <div class="source">{{ area_source }}</div>
    </div>
    <div class="callout">
      <div class="callout-label">Unidades estimadas</div>
      <div class="callout-value">{{ units_est }}</div>
      <span class="status s-{{ units_state }}">{{ units_state|replace('_',' ') }}</span>
      <div class="source">{{ units_source }}</div>
    </div>
    <div class="callout">
      <div class="callout-label">Restricción operativa</div>
      <div class="callout-value" style="font-size:12pt">{{ binding_short }}</div>
      <div class="note">{{ binding_detail }}</div>
      <div class="source">{{ decree_short }}</div>
    </div>
    </div>
  </div>
  {% if area_warning %}<div class="warning"><strong>Advertencia.</strong> {{ area_warning }}</div>{% endif %}

  {% if unit_est %}
  <table class="data-table avoid">
    <thead><tr><th>Concepto</th><th>Resultado</th><th>Supuesto / fuente</th></tr></thead>
    <tbody>
      <tr><td>Área construible bruta</td><td class="value">{{ unit_est.area_construible_m2|n1 }} m²</td><td>{{ "Área derivada de huella × pisos" if unit_est.basado_en_area_estimada else "Resultado de edificabilidad" }}</td></tr>
      <tr><td>Circulación y zonas comunes</td><td class="value">{{ unit_est.circ_area_m2|n1 }} m²</td><td>{{ unit_est.circulacion_pct }}% - supuesto del estimador</td></tr>
      <tr><td>Área vendible neta estimada</td><td class="value"><strong>{{ unit_est.area_vendible_neta_m2|n1 }} m²</strong></td><td>Área bruta menos circulación</td></tr>
      {% for tipo, info in unit_est.unidades_por_tipo.items() %}<tr><td>{{ info.label }}</td><td class="value">{{ info.unidades }} unidades</td><td>{{ info.pct_mezcla }}% de mezcla · {{ info.m2_neta_por_unidad }} m²/unidad</td></tr>{% endfor %}
      <tr><td><strong>Total estimado</strong></td><td class="value"><strong>{{ unit_est.total_unidades }} unidades</strong></td><td>No es un parámetro del decreto</td></tr>
    </tbody>
  </table>
  {% endif %}

  {% if floor_rows %}
  <h3 style="margin-top:12pt">Lectura por piso</h3>
  <table><thead><tr><th>Piso</th><th>Nivel aproximado</th><th>Regla aplicable</th></tr></thead><tbody>{% for row in floor_rows %}<tr><td class="mono">{{ row.piso }}</td><td class="mono">{{ row.nivel }}</td><td>{{ row.regla }}</td></tr>{% endfor %}</tbody></table>
  <p class="note" style="margin-top:4pt">La equivalencia usa 3,0 m por piso como referencia; el proyecto puede definir alturas distintas.</p>
  {% endif %}
  <h3 style="margin-top:12pt">Alcance financiero</h3>
  <p class="note">Este PDF regulatorio no incorpora el pro-forma ni el valor residual del suelo. La omisión es deliberada: esos resultados dependen de supuestos editables de mercado, financiación, impuestos, cronograma y absorción, y no forman parte de la norma urbanística. Consúltelos y expórtelos como un escenario financiero separado.</p>
</section>

<!-- 6. Profile -->
<section class="page">
  <div class="section-head"><span class="section-no">06</span><div class="eyebrow">Forma edificable</div><h2>Perfil volumétrico</h2></div>
  {% if profile_blocked_reason %}
  <div class="alert"><strong>Perfil no generado.</strong> {{ profile_blocked_reason }}</div>
  {% else %}<div class="profile-wrap">{{ profile_svg|safe }}</div>{% endif %}
  <div class="setbacks">{% for sb in setbacks %}<div class="setback"><span class="note">{{ sb.label }}</span><b>{{ sb.val }}</b><span class="source">{{ sb.src }}</span></div>{% endfor %}</div>
  {% if facade_height %}<div class="verdict"><strong>Altura máxima de fachada (A = 2,5 × D): {{ facade_height }}</strong>Es una altura sobre el espacio público; no es un retiro horizontal y no se descuenta de la huella edificable.<div class="source">Anexo 5 Cap. 1.2.2.E.1.1</div></div>{% endif %}
  <p class="note" style="margin-top:8pt">Diagrama indicativo, no a escala. La altura en metros supone 3,0 m por piso. Debe verificarse con la geometría y el diseño del proyecto.</p>
</section>

<!-- 7. Next steps -->
<section class="page">
  <div class="section-head"><span class="section-no">07</span><div class="eyebrow">Debida diligencia</div><h2>Próximos pasos</h2></div>
  {% for step in next_steps %}<div class="step {{ step.priority }}"><b>{{ step.text }}</b>{% if step.note %}<div class="note">{{ step.note }}</div>{% endif %}</div>{% endfor %}
  <h3 style="margin-top:12pt">Advertencias del cálculo</h3>
  {% if warnings %}{% for warning in warnings %}<div class="alert">{{ warning }}</div>{% endfor %}{% else %}<p class="note">No se registraron advertencias adicionales.</p>{% endif %}
</section>

<!-- 8. Sources -->
<section class="page">
  <div class="section-head"><span class="section-no">08</span><div class="eyebrow">Verificación independiente</div><h2>Fuentes consultadas</h2></div>
  <p style="margin-bottom:10pt">Solo se listan las fuentes que aportaron datos o reglas a este predio.</p>
  {% for group in used_sources %}<div class="source-group"><h3>{{ group.name }}</h3><ol class="sources">{% for source in group["items"] %}<li>{{ source }}</li>{% endfor %}</ol></div>{% endfor %}
  <p class="note" style="margin-top:14pt">Fecha de consulta GIS: {{ consultation_date }}. Para una decisión vinculante, confirme la información con la SDP, Catastro Bogotá y la Curaduría Urbana competente.</p>
</section>

<!-- 9. Trace appendix -->
<section class="page appendix-page">
  <div class="section-head"><span class="section-no">09</span><div class="eyebrow">Apéndice</div><h2>Trazabilidad del cálculo</h2></div>
  {% if trace %}{% for step in trace %}<div class="trace"><div class="trace-step">Paso {{ step.paso }}</div><strong>{{ step.descripcion }}</strong>{% if step.expresion %}<div class="trace-expr">{{ step.expresion }}</div>{% endif %}{% if step.valores %}<div class="trace-values">{{ step.valores|pretty_kv }}</div>{% endif %}{% if step.resultado is not none %}<div class="trace-result">= {% if step.resultado is mapping %}{% for key,value in step.resultado.items() %}{{ key|human_label }}: {{ value }} {% endfor %}{% elif step.resultado is iterable and step.resultado is not string %}{{ step.resultado|join(', ') }}{% else %}{{ step.resultado }}{% endif %} {{ step.unidad or '' }}</div>{% endif %}{% if step.nota %}<div class="note">{{ step.nota }}</div>{% endif %}{% if step.fuente %}<div class="source">{{ step.fuente }}</div>{% endif %}</div>{% endfor %}{% else %}<p class="note">La traza detallada no está incluida en este resultado guardado.</p>{% endif %}
</section>
</body>
</html>"""


# ── Jinja2 environment ─────────────────────────────────────────────────────────

def _make_env() -> Environment:
    import json as _json

    env = Environment(loader=BaseLoader(), autoescape=True)

    label_overrides = {
        "factor": "Factor",
        "D_m": "Perfil vial usado D (m)",
        "fuente_D": "Fuente de D",
        "confianza_D": "Confianza de D",
        "retroceso_aplicado_a_huella": "Altura de fachada descontada de la huella",
        "retroceso_m": "Altura máxima de fachada A (m)",
        "area_lote": "Área del lote",
        "huella_calculada": "Huella calculada",
        "pisos": "Pisos",
        "anu_m2_supplied": "ANU aportada (m²)",
        "area_lote_m2": "Área del lote (m²)",
        "tabla_aisl_posterior": "Tabla de aislamiento posterior",
        "pisos_max": "Pisos de referencia",
        "rango_huella_m2": "Rango de huella (m²)",
        "posterior_m": "Aislamiento posterior (m)",
        "lateral_m": "Aislamiento lateral (m)",
        "antejardin_m": "Antejardín (m)",
        "altura_maxima_de_fachada_A_m": "Altura máxima de fachada A (m)",
        "area_construible_max_m2": "Área construible máxima (m²)",
        "B_proxy_m2": "Área base para estacionamientos (m²)",
        "area_actividad_codigo": "Código del área de actividad",
        "area_actividad": "Área de actividad",
        "pisos_ref": "Pisos de referencia",
    }

    def human_label(value):
        key = str(value)
        return label_overrides.get(key, key.replace("_", " ").strip().capitalize())

    def plain_value(value):
        if value is None:
            return "No disponible"
        if isinstance(value, bool):
            return "Sí" if value else "No"
        if isinstance(value, dict):
            return ", ".join(f"{human_label(k)}: {plain_value(v)}" for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return ", ".join(plain_value(v) for v in value)
        return (str(value)
                .replace("sin_fuente_configurada", "sin fuente automatizada")
                .replace("SIN_DATO", "SIN DATO")
                .replace("area_construible_max_m2", "área construible máxima"))

    def pretty_kv(obj):
        if not obj:
            return ""
        try:
            lines = []
            for k, v in obj.items():
                lines.append(f"{human_label(k)}: {plain_value(v)}")
            return "\n".join(lines)
        except Exception:
            return str(obj)

    def n1(val):
        return _n(val, 1)

    env.filters["pretty_kv"] = pretty_kv
    env.filters["plain_value"] = plain_value
    env.filters["n1"] = n1
    env.filters["human_label"] = human_label
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
    trat = d.get("tratamiento") or lu.get("tratamiento") or "No disponible"

    # Dates
    now = datetime.now(tz=timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")
    date_label = _date_label(now)
    consultation_date = _get(d, "consulta", "fecha") or _get(lu, "consulta", "fecha") or now.strftime("%Y-%m-%d")

    # Map
    rings   = (lu.get("lote") or {}).get("geojson_polygon")
    map_img = _render_map(rings, inp.get("lng", 0), inp.get("lat", 0))

    # Identity
    lotcodigo  = lote.get("lotcodigo") or "No disponible"
    lot_area   = _area(_get(lote, "area_m2", "valor"))
    tip        = _get(lu, "tipologia", "valor") or "No disponible"
    aa_code    = _get(lu, "area_actividad", "codigo") or "No disponible"
    aa_name    = _get(lu, "area_actividad", "nombre") or ""
    area_act   = f"{aa_code}" + (f" — {aa_name[:45]}" if aa_name else "")
    address_text = (address or "").strip()
    address_resolution = d.get("address_resolution") or {}
    near_match = bool(address_resolution.get("near_match"))
    searched_address = str(address_resolution.get("searched_address") or "").strip()
    resolved_address = str(address_resolution.get("resolved_address") or address_text).strip()
    address_relation = str(address_resolution.get("relation") or "mismo bloque").strip()
    coordinate_query = (
        not address_text
        or address_text.lower().startswith("predio ")
        or address_text.lower().startswith("dirección no")
        or address_text.lower().startswith("consultado por coordenada")
    )
    locality   = lu.get("localidad") or _get(lu, "lote", "localidad")
    if not locality and " · " in address_text:
        locality = address_text.split(" · ", 1)[1].split(",", 1)[0].strip()
    if not locality and coordinate_query:
        try:
            from cabida.market_defaults import resolve_localidad
            locality = resolve_localidad(float(inp.get("lng")), float(inp.get("lat")))
        except Exception:
            locality = None
    locality_labels = {
        "usaquen": "Usaquén", "santa_fe": "Santa Fe", "san_cristobal": "San Cristóbal",
        "fontibon": "Fontibón", "engativa": "Engativá", "los_martires": "Los Mártires",
        "antonio_narino": "Antonio Nariño", "la_candelaria": "La Candelaria",
        "ciudad_bolivar": "Ciudad Bolívar", "barrios_unidos": "Barrios Unidos",
    }
    if locality:
        locality = locality_labels.get(str(locality).lower(), str(locality).replace("_", " ").title())
    upz        = lu.get("upz") or lu.get("upl") or _get(lu, "lote", "upz")
    locality_upz = str(locality) if locality else "Localidad no resuelta"
    locality_upz += f" · UPZ {upz}" if upz else " · UPZ no incluida en las capas consultadas"
    current_use = lu.get("uso_actual") or _get(lu, "lote", "uso_actual") or "No incluido en las capas consultadas"
    anu_val    = _get(d, "anu", "valor_m2")
    anu_usado  = _area(anu_val) if anu_val else None
    rango      = d.get("rango") or lu.get("rango")
    lat        = f'{inp.get("lat", 0):.5f}'
    lng        = f'{inp.get("lng", 0):.5f}'
    address_display = (
        "Consultado por coordenada; sin dirección catastral asociada"
        if coordinate_query else address_text
    )
    report_title = (
        f"Predio {lotcodigo}" + (f" · {locality}, Bogotá D.C." if locality else " · Bogotá D.C.")
        if coordinate_query else address_text
    )
    lot_units = lote.get("unidades_predio")
    if lot_units is None:
        lot_units = _get(lu, "lote", "unidades_predio")
    try:
        lot_units_count = int(float(lot_units)) if lot_units is not None else None
    except (TypeError, ValueError):
        lot_units_count = None
    predial_units = str(lot_units_count) if lot_units_count is not None else "No disponible"
    vacant_site_warning = (
        "La cabida supone un lote libre, disponible y sin cargas. No incorpora edificaciones existentes, "
        "demolición, englobe, desenglobe, propiedad horizontal, servidumbres ni afectaciones de título."
    )
    existing_units_warning = ""
    if lot_units_count is not None and lot_units_count > 1:
        existing_units_warning = (
            f"Este lote registra {lot_units_count} unidades prediales. Es probable que se trate de un predio ya "
            "desarrollado en propiedad horizontal; la cabida estimada asume un lote libre y no considera "
            "demolición, englobe ni el régimen de propiedad horizontal existente."
        )

    # Verdict
    area_max_obj = m.get("area_construible_max_m2") or {}
    area_max_val = area_max_obj.get("valor")
    derived_area = m.get("area_construible_estimada") or {}
    area_label = "Área construible máx."
    area_warning = ""
    if area_max_val is not None:
        area_max  = _n(area_max_val, 1)
        area_unit = "m²"
        area_state = _state(area_max_obj, value=area_max_val)
        area_source = _metric_source(area_max_obj, "Cálculo de edificabilidad Ainmo")
    elif derived_area.get("estado") == "derivado" and derived_area.get("fuera_de_rango") is not True and derived_area.get("valor_m2") is not None:
        area_label = "Área estimada · derivada"
        area_max = _n(derived_area["valor_m2"], 1)
        area_unit = "m² · no es tope normativo"
        area_state = _state(derived_area, value=derived_area.get("valor_m2"), default="derivado")
        area_source = _metric_source(derived_area, "Catastro capa 0 · POT capas 15, 22 y 38 · norma volumétrica aplicable")
        area_warning = derived_area.get("advertencia") or derived_area.get("motivo") or ""
    elif derived_area.get("estado") == "derivado" and derived_area.get("fuera_de_rango") is not True and derived_area.get("rango_m2"):
        area_label = "Área estimada · rango"
        derived_range = derived_area["rango_m2"]
        area_max = f"{_n(derived_range[0], 1)}–{_n(derived_range[1], 1)}"
        area_unit = "m² · no es tope normativo"
        area_state = _state(derived_area, value=derived_range, default="derivado")
        area_source = _metric_source(derived_area, "Catastro capa 0 · POT capas 15, 22 y 38 · norma volumétrica aplicable")
        area_warning = derived_area.get("advertencia") or derived_area.get("motivo") or ""
    else:
        nota_ic = area_max_obj.get("motivo") or area_max_obj.get("nota") or ""
        area_max  = "IC resultante"
        area_unit = ""
        area_state = _state(area_max_obj)
        area_source = _metric_source(area_max_obj, "Art. 310 Decreto 555/2021")
        area_warning = nota_ic or "Requiere modelado geométrico."

    pisos_val = _get(m, "altura_base_pisos", "valor") or _get(m, "altura_maxima_pisos", "valor")
    bc_short   = _BC_SHORT.get(bc, bc.upper())
    bc_detail  = f"{int(pisos_val)} pisos" if pisos_val and bc in ("height", "footprint_and_height") else ""
    verdict_s  = _BC_SENTENCE.get(bc, f"Restricción: {bc}")

    unit_est_data = _unit_estimate(d)
    if unit_est_data:
        units_est = str(unit_est_data["total_unidades"])
        units_sub = "estimación — ver supuestos p.4"
        units_state = "derivado"
        units_source = (
            "Estimador Ainmo sobre área derivada · mezcla y circulación declaradas"
            if unit_est_data.get("basado_en_area_estimada")
            else "Estimador Ainmo sobre área construible resuelta"
        )
    else:
        units_est = "No calculable"
        units_sub = "IC resultante — sin área construible"
        units_state = "insuficiente"
        units_source = "Requiere un área construible base"

    # The simple profile model is only valid for ordinary urban lots. Reusing
    # it on a parcel that the calculation engine stopped as out-of-range would
    # create a plausible-looking but unsupported envelope in the PDF.
    profile_blocked_reason = None
    if derived_area.get("fuera_de_rango") is True or derived_area.get("estado") == "fuera_de_rango":
        profile_blocked_reason = derived_area.get("motivo") or "El lote está fuera del rango de aplicación del modelo volumétrico simplificado."
    profile_svg = "" if profile_blocked_reason else _profile_svg(d)

    # Setback summary cards
    ant_dim = (d.get("antejardin") or {}).get("dimension_m")
    if isinstance(ant_dim, dict):
        ant_dim = ant_dim.get("valor")
    post_v  = _get(m, "aislamiento_posterior_m", "valor")
    lat_v   = _get(m, "aislamiento_lateral_m", "valor")
    ret_v   = _get(m, "retroceso_fachada_A_m", "valor")

    setbacks = []
    ed_rules = lu.get("edificabilidad") or d.get("edificabilidad") or {}
    ant_obj = d.get("antejardin") or {}
    if ant_dim is not None:
        setbacks.append({"label": "Antejardín", "val": f"{_n(ant_dim, 1)} m",
                          "src": _metric_source(ant_obj, "Capa 22 POT FeatureServer")})
    if post_v is not None:
        setbacks.append({"label": "Aislamiento posterior", "val": f"{_n(post_v, 1)} m",
                          "src": _metric_source(ed_rules.get("aislamiento_posterior") or {},
                                                _metric_source(m.get("aislamiento_posterior_m"), "Fuente en la tabla normativa"))})
    if lat_v and lat_v > 0:
        setbacks.append({"label": "Aislamiento lateral", "val": f"≥ {_n(lat_v, 1)} m",
                          "src": _metric_source(ed_rules.get("aislamiento_lateral") or {},
                                                _metric_source(m.get("aislamiento_lateral_m"), "Fuente en la tabla normativa"))})
    if not setbacks:
        setbacks.append({"label": "Retiros", "val": "No calculables",
                          "src": "La tabla normativa explica el dato faltante y quién debe resolverlo"})
    facade_height = f"{_n(ret_v, 2)} m" if ret_v is not None else None

    # Tables and report sections
    param_rows = _param_rows(d, lu)
    normative_rows = _normative_rows(d, lu)
    used_sources = _used_sources(d, lu, normative_rows, param_rows)
    floor_rows = _floor_rows(d, lu)
    next_steps_list = _next_steps(d, lu)
    def humanize_warning(value: Any) -> str:
        text = str(value)
        replacements = {
            "SIN_DATO": "SIN DATO",
            "aerocivil": "restricciones aeronáuticas",
            "cerros_orientales": "Cerros Orientales",
            "movimientos_en_masa": "remoción en masa",
            "inundacion": "amenaza de inundación",
            "ronda_hidrica": "ronda hídrica",
            "area_construible_max_m2": "área construible máxima",
            "área_construible_max_m2": "área construible máxima",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    warnings   = [humanize_warning(w) for w in (d.get("warnings") or []) if w]
    catastro_snap = lote.get("consulta_catastro") or (lu.get("lote") or {}).get("consulta_catastro")
    if catastro_snap:
        warnings.insert(0, (
            f"PREDIO APROXIMADO: la coordenada no intersectó Catastro y se seleccionó el lote "
            f"más cercano, a {_n(catastro_snap.get('distancia_m'), 1)} m. Confirme el predio "
            "antes de usar este informe."
        ))
    trace = []
    for raw_step in d.get("formula_trace") or []:
        step = dict(raw_step)
        if step.get("descripcion") == "Retroceso de fachada":
            step["descripcion"] = "Altura máxima de fachada"
        if isinstance(step.get("nota"), str):
            step["nota"] = humanize_warning(step["nota"]).replace(
                "valor numérico del retroceso", "valor numérico de la altura máxima de fachada"
            )
        if isinstance(step.get("expresion"), str):
            expression_replacements = {
                "anillo_proyectado_MAGNA_SIRGAS_9377": "anillo proyectado en MAGNA-SIRGAS 9377",
                "Layer_22.DIMENSION": "Capa 22 · campo DIMENSION",
                "área_lote": "área del lote",
                "retroceso_A": "altura de fachada A",
                "retroceso": "altura máxima de fachada",
                "altura_total_m": "altura total en metros",
                "ancho_via_m": "perfil vial en metros",
                "N/A": "No aplica",
                "huella_poligono_con_retiros": "huella del polígono con aislamientos",
                "pisos_base": "pisos base",
                "B_proxy_m2": "área base B",
                "tabla_aisl_posterior[pisos_max]": "tabla de aislamiento posterior según pisos",
            }
            for old, new in expression_replacements.items():
                step["expresion"] = step["expresion"].replace(old, new)
            step["expresion"] = re.sub(
                r"\b[\wÀ-ÿ]*_[\wÀ-ÿ_]+\b",
                lambda match: match.group(0).replace("_", " "),
                step["expresion"],
            )
        if isinstance(step.get("nota"), str):
            step["nota"] = re.sub(
                r"\b[\wÀ-ÿ]*_[\wÀ-ÿ_]+\b",
                lambda match: match.group(0).replace("_", " "),
                step["nota"],
            )
        if step.get("estado") == "no_aplica":
            step["resultado"] = "No aplica"
            step["unidad"] = ""
        trace.append(step)
    toc = [
        "Resumen del predio",
        "Normativa aplicable - exigencia vs. resultado",
        "Área construible / unidades estimadas",
        "Perfil volumétrico",
        "Próximos pasos",
        "Fuentes consultadas",
        "Apéndice: trazabilidad del cálculo",
    ]

    # Render
    env  = _make_env()
    tmpl = env.from_string(_DUE_DILIGENCE_TEMPLATE)
    return tmpl.render(
        disclaimer   = DISCLAIMER,
        decree_short = DECREE_SHORT,
        timestamp    = timestamp,
        date_label   = date_label,
        consultation_date = consultation_date,
        address      = address_text,
        address_display = address_display,
        report_title = report_title,
        near_match = near_match,
        searched_address = searched_address,
        resolved_address = resolved_address,
        address_relation = address_relation,
        lotcodigo    = lotcodigo,
        lot_area     = lot_area,
        tratamiento  = trat,
        tipologia    = tip,
        area_actividad = area_act,
        locality_upz = locality_upz,
        current_use = current_use,
        anu_usado    = anu_usado,
        rango        = rango,
        lat          = lat,
        lng          = lng,
        predial_units = predial_units,
        vacant_site_warning = vacant_site_warning,
        existing_units_warning = existing_units_warning,
        map_img      = map_img,
        area_max     = area_max,
        area_label   = area_label,
        area_max_unit = area_unit,
        area_state   = area_state,
        area_source  = area_source,
        area_warning = area_warning,
        units_est    = units_est,
        units_sub    = units_sub,
        units_state  = units_state,
        units_source = units_source,
        binding_short  = bc_short,
        binding_detail = bc_detail,
        verdict_sentence = verdict_s,
        profile_svg  = profile_svg,
        profile_blocked_reason = profile_blocked_reason,
        setbacks     = setbacks,
        facade_height = facade_height,
        param_rows   = param_rows,
        normative_rows = normative_rows,
        used_sources = used_sources,
        floor_rows   = floor_rows,
        unit_est     = unit_est_data,
        next_steps   = next_steps_list,
        warnings     = warnings,
        trace        = trace,
        toc          = toc,
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
