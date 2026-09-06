#!/usr/bin/env python3
"""
calc.py — Bogotá buildability calculator
Consumes the output of p2_lookup.lookup() and computes derived metrics
with a complete formula trace and binding-constraint identification.

Usage
-----
  from p2_lookup import lookup
  from calc import calculate

  result = calculate(lookup(-74.0525, 4.6750), anu_m2=None)
  # or, for a lot requiring user-supplied ANU:
  result = calculate(lookup(-74.11, 4.52), anu_m2=8_500)
"""

from __future__ import annotations

import json
import sys
from typing import Any

import p2_lookup
from p2_lookup import BuildabilityLookupError, ZeroFeaturesError


def _fmt_es(value: float, decimals: int = 1) -> str:
    """Format a number for user-facing es-CO text."""
    raw = f"{value:,.{decimals}f}"
    return raw.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


# ── Exceptions ───────────────────────────────────────────────────────────────

class InputRequired(BuildabilityLookupError):
    """A value is needed that cannot be derived from GIS data."""


# ── Formula-trace helper ─────────────────────────────────────────────────────

def _step(
    n: int,
    desc: str,
    expr: str,
    values: dict,
    result,
    unit: str = "m²",
    fuente: str | None = None,
    nota: str | None = None,
) -> dict:
    s: dict = {
        "paso": n,
        "descripcion": desc,
        "expresion": expr,
        "valores": values,
        "resultado": result,
        "unidad": unit,
    }
    if fuente:
        s["fuente"] = fuente
    if nota:
        s["nota"] = nota
    return s


# ── Antejardín parser ─────────────────────────────────────────────────────────

def _parse_antejardin(ant: dict | None) -> dict:
    """
    Parse and validate the antejardin block from lookup().

    "0.00" (string) or 0.0 (float) → confirmed zero: buildable to the front property
    line. This is NOT missing data — it is an explicit datum from Layer 22.

    None / confianza=="requiere_input" / space character → InputRequired.
    The caller must surface this to the user; zero MUST NOT be assumed.
    """
    if ant is None:
        raise InputRequired(
            "Datos de antejardín ausentes — no se puede determinar el retroceso frontal. "
            "Consulte el mapa CU-5.5 (Capa 22) antes de continuar."
        )

    confianza = ant.get("dimension_m", {}).get("confianza", "requiere_input")
    dim_raw = ant.get("dimension_m", {}).get("valor")

    # A requiere_input confidence means Layer 22 had no polygon at this point
    if confianza == "requiere_input" or dim_raw is None:
        raise InputRequired(
            "Dimensión de antejardín no disponible en GIS. "
            "No asuma cero — consulte el mapa CU-5.5 (Capa 22) manualmente."
        )

    # A space or non-numeric string from the layer means missing, not zero
    if isinstance(dim_raw, str) and dim_raw.strip() == "":
        raise InputRequired(
            "La Capa 22 (mapa CU-5.5) no tiene registrada una dimensión de antejardín "
            "para este predio. Esto es una brecha conocida en los datos públicos del SDP: "
            "la Resolución SDP 1631 del 19 de julio de 2023 actualizó el Mapa CU-5.5 con "
            "dimensiones para zonas que quedaron indeterminadas en el Decreto 555/2021, "
            "pero esa actualización aún no ha sido cargada en la capa GIS pública del "
            "FeatureServer. No es un error del usuario. Para obtener la dimensión correcta "
            "consulte directamente a la SDP (www.sdp.gov.co) o revise el mapa físico CU-5.5 "
            "adoptado por la Resolución 1631/2023."
        )

    try:
        dim = float(dim_raw)
    except (TypeError, ValueError):
        raise InputRequired(
            f"No se pudo interpretar la dimensión de la Capa 22 como número: {dim_raw!r}. "
            "Consulte el mapa CU-5.5 manualmente."
        )

    # dim == 0.0 is valid confirmed data: no antejardín required for this sector
    return {
        "exigido": dim > 0.0,
        "dimension_m": dim,
        "confianza": "alta",
        "nota": (
            "Sector sin antejardín obligatorio (mapa CU-5.5): el predio es edificable hasta la línea de propiedad."
            if dim == 0.0
            else f"Antejardín mínimo: {dim:.2f} m (mapa CU-5.5 Layer 22)."
        ),
        "fuente": ant.get("fuente", "Layer 22 mapa CU-5.5"),
        "articulo": ant.get("articulo", "Res. SDP 1631/2023 (actualiza CU-5.5); Arts. 258 y 314 D.555/2021"),
    }


# ── ANU resolver ──────────────────────────────────────────────────────────────

def _resolve_anu(lookup: dict, anu_m2_supplied: float | None) -> tuple[float, str, list]:
    """
    Return (anu_value, fuente_description, warnings).

    Rules:
    • CONSOLIDACIÓN: ANU concept does not apply; lot area used as reference only.
    • DESARROLLO Rangos 1–3: must be user-supplied from Plan Parcial.
    • DESARROLLO Rangos 4A–4D, lot ≤ 10,000 m²: lot area is a valid proxy.
    • DESARROLLO any rango, lot > 10,000 m²: user-supplied ANU required — large lots
      have road dedications and public space that reduce ANU below gross area.
    """
    warnings: list = []
    lot_area = lookup["lote"]["area_m2"]["valor"]
    tratamiento = lookup.get("tratamiento", "").upper().replace("Ó", "O").replace("Á", "A")

    if "CONSOLIDACION" in tratamiento:
        if anu_m2_supplied is not None:
            warnings.append(
                "ANU ingresado para un lote Consolidación — en este tratamiento el IC/IO son resultantes "
                "y no se basan en el ANU. El valor ingresado se usa como referencia."
            )
            return anu_m2_supplied, "usuario (referencia)", warnings
        return (
            lot_area,
            "área catastral del lote (referencia — IC/IO son resultantes en Consolidación)",
            warnings,
        )

    if "DESARROLLO" not in tratamiento:
        # Renovación Urbana, Conservación, Mejoramiento Integral:
        # ANU concept does not apply — IC for RU is over terreno area, not ANU.
        return lot_area, "área catastral del lote (referencia — tratamiento usa área de terreno, no ANU)", warnings

    # DESARROLLO
    edif = lookup.get("edificabilidad") or {}
    anu_data = edif.get("anu", {})
    anu_confianza = anu_data.get("confianza", "requiere_input")

    if anu_confianza == "requiere_input":
        # Rangos 1–3 need Plan Parcial ANU
        if anu_m2_supplied is None:
            raise InputRequired(
                "Se requiere el ANU del Plan Parcial aprobado para Desarrollo Rangos 1–3. "
                "No puede inferirse a partir del área catastral bruta."
            )
        warnings.append(
            f"ANU ingresado por el usuario: {_fmt_es(anu_m2_supplied)} m². "
            "Verifique que coincide con el Área Neta Urbanizable del Plan Parcial aprobado."
        )
        return anu_m2_supplied, "usuario (Plan Parcial — Rango 1/2/3)", warnings

    # Rangos 4A–4D: check the 10,000 m² threshold
    if lot_area > 10_000:
        if anu_m2_supplied is None:
            raise InputRequired(
                f"El área del lote es {_fmt_es(lot_area, 0)} m² (> 10.000 m²); no puede usarse "
                "como ANU en este predio de Desarrollo. Ingrese el ANU verificado del Plan Parcial "
                "o de la licencia de urbanización."
            )
        warnings.append(
            f"Área del lote ({_fmt_es(lot_area, 0)} m²) supera el umbral de 10.000 m². "
            f"Se usa el ANU ingresado ({_fmt_es(anu_m2_supplied)} m²) en lugar del área catastral bruta."
        )
        return anu_m2_supplied, "usuario (lote > 10.000 m² — ANU neta del plan)", warnings

    # ≤ 10,000 m²: lot area is a valid proxy
    rango = lookup.get("rango", "?")
    return (
        lot_area,
        f"área catastral del lote (proxy ANU válido para Rango {rango} ≤ 10.000 m²)",
        warnings,
    )


# Parágrafo 3 VIS×2 bonus — area_actividad block must be present in lookup result.
# If absent (non-Consolidación path), the bonus is not applicable.
_VIS_P3_NO_DATA = (
    "Dato de área de actividad no disponible — "
    "el bonus VIS×2 (Art. 310 § 3) requiere verificar Layer 14 del POT FeatureServer."
)


# ── Retroceso de fachada — D resolver ────────────────────────────────────────

def _resolve_D(
    lookup: dict,
    ancho_via_m: float | None,
) -> tuple[float | None, str, str | None]:
    """
    Determine D (road profile width in metres) from the best available source.

    Priority:
      1. ancho_via_m supplied by user (confianza "alta")
      2. lookup["ancho_via_gis"]["D_m"] from Layer 38 calzada query (confianza "media")
      3. None — caller must show formula without a numeric result

    Returns (D_m, fuente_label, confianza) where D_m is None when no data available.
    """
    if ancho_via_m is not None:
        return (
            float(ancho_via_m),
            "usuario",
            "alta",
        )

    gis = lookup.get("ancho_via_gis") or {}
    D_gis = gis.get("D_m")
    if D_gis is not None:
        return (
            D_gis,
            gis.get("fuente", "Layer 38 POT FeatureServer (Calzada)"),
            gis.get("confianza", "media"),
        )

    return None, "sin_dato", None


# ── Retroceso de fachada — shared helper ─────────────────────────────────────

def _retroceso_fachada(
    lookup: dict,
    ancho_via_m: float | None,
    factor: float,
    fuente_decreto: str,
    trace: list,
    N,
    metrics: dict,
) -> None:
    """
    Compute A = factor × D (retroceso de fachada) and store result in metrics.

    D is resolved via _resolve_D() (user input > Layer 38 GIS > None).
    Writes metrics["retroceso_fachada_A_m"] in-place; adds a trace step.
    """
    D_m, fuente_D, confianza_D = _resolve_D(lookup, ancho_via_m)

    if D_m is not None:
        A_m = round(factor * D_m, 2)
        trace.append(_step(
            N(), "Retroceso de fachada",
            f"A = {factor} × D",
            {"factor": factor, "D_m": D_m, "fuente_D": fuente_D, "confianza_D": confianza_D},
            A_m, "m",
            fuente=fuente_decreto,
            nota=(
                f"D obtenido de: {fuente_D}. "
                + ("" if confianza_D == "alta"
                   else "Confianza media — ancho de calzada(s) únicamente, sin andenes ni separadores. "
                        "Verificar con el perfil vial oficial antes de usar en licencia.")
            ),
        ))
        metrics["retroceso_fachada_A_m"] = {
            "valor": A_m,
            "D_m": D_m,
            "factor": factor,
            "confianza": confianza_D,
            "fuente_D": fuente_D,
        }
    else:
        trace.append({
            "paso": N(),
            "descripcion": "Retroceso de fachada",
            "expresion": f"A = {factor} × D   (D = perfil vial frente al predio)",
            "valores": {"factor": factor, "D": "requerido"},
            "resultado": None, "unidad": "m",
            "fuente": fuente_decreto,
            "nota": (
                "No se pudo determinar el ancho vial automáticamente. "
                "Proporcione ancho_via_m (perfil total: calzada + andenes + separador si aplica) "
                "para obtener el valor numérico del retroceso."
            ),
        })
        metrics["retroceso_fachada_A_m"] = {
            "valor": None,
            "factor": factor,
            "confianza": "requiere_input",
            "fuente_D": "sin_dato",
            "nota": f"A = {factor} × D — proporcione ancho_via_m para calcular.",
        }


# ── Art. 310 Num. 2 bonus frontage gate ──────────────────────────────────────

def _check_bonus_frontage_gate(
    tipologia: str,
    frente_m: float | None,
    ancho_via_m: float | None,
    lot_area: float,
    pisos_base: int | None,
) -> tuple[bool, str]:
    """
    Gate for the Art. 310 Num. 2 range bonus (the higher number in a "4-6" map entry).

    Raises InputRequired when required inputs are missing.
    Returns (qualifies, reason) when all inputs are present.

    Rules (Art. 310 Num. 2):
      Tipología continua : frente ≥ 14 m
      Tipología aislada  : frente ≥ 24 m  AND  área ≥ 1 200 m²
                           AND  perfil vial ≥ 22 m  AND  altura base ≥ 8 pisos
    """
    if frente_m is None:
        cond = (
            "frente mínimo ≥14m"
            if tipologia != "aislada"
            else "frente ≥24m + área ≥1.200m² + perfil vial ≥22m + altura base ≥8 pisos"
        )
        raise InputRequired(
            f"Art. 310 Num. 2: el bonus de altura (rango superior del mapa) requiere verificar "
            f"el frente del predio sobre la vía principal. "
            f"Condición para tipología {tipologia}: {cond}. "
            "Proporcione frente_m para determinar si el predio accede al bonus."
        )

    if tipologia == "aislada":
        if ancho_via_m is None:
            raise InputRequired(
                "Art. 310 Num. 2: para tipología aislada el bonus requiere además el perfil vial "
                "(ancho de la vía frente al predio ≥22m). "
                "Mida el ancho del espacio público vial frente al predio y proporcione ancho_via_m."
            )
        checks = {
            "frente_m >= 24":    frente_m >= 24,
            "área >= 1200 m²":   lot_area >= 1_200,
            "perfil_vial >= 22": ancho_via_m >= 22,
            "pisos_base >= 8":   (pisos_base or 0) >= 8,
        }
        if all(checks.values()):
            return True, (
                f"Tipología aislada cumple Art. 310 Num. 2: "
                f"frente={frente_m}m, área={lot_area:.0f}m², vía={ancho_via_m}m, "
                f"pisos_base={pisos_base}"
            )
        failed = [k for k, v in checks.items() if not v]
        return False, (
            f"Tipología aislada NO cumple Art. 310 Num. 2 — condición(es) no satisfecha(s): "
            + ", ".join(failed)
        )

    else:  # continua
        if frente_m >= 14:
            return True, f"Tipología continua cumple Art. 310 Num. 2: frente={frente_m}m ≥ 14m"
        return False, f"Tipología continua NO cumple Art. 310 Num. 2: frente={frente_m}m < 14m mínimo"


# ── Main entry point ──────────────────────────────────────────────────────────

# ── Art. 389 parking table (Decreto 555/2021) ────────────────────────────────
# Keys: área_actividad code (Layer 14 CODIGO field)
# Tuple: (min_pct_no_vis, min_pct_vis_vip, max_pct, adicional_pct)
# Percentages apply to the base area defined in Art. 390.
# PEMP → Art. 339 + Anexo 6 (not in this table — see GAPS.md)
_PARKING_ART389: dict[str, tuple[float, float, float, float]] = {
    "AAERVIS": (0.0, 0.0, 10.0, 15.0),
    "AAERAE":  (0.0, 0.0, 15.0, 15.0),
    "AAPGSU":  (5.0, 5.0, 15.0, 10.0),
    "AAPRSU":  (8.0, 6.0, 20.0, 15.0),
    "AAGSM":   (0.0, 0.0, 20.0, 15.0),
}

_PARKING_AREA_ACTIVIDAD_LABELS: dict[str, str] = {
    "AAERVIS": "Áreas de Actividad Estratégica para Renovación con VIS/VIP",
    "AAERAE":  "Áreas de Actividad Estratégica para Renovación con Actividades Económicas",
    "AAPGSU":  "Áreas de Actividad de Proximidad con Gestión del Suelo Urbano",
    "AAPRSU":  "Áreas de Actividad con Predominancia Residencial del Suelo Urbano",
    "AAGSM":   "Áreas de Actividad de Gestión del Suelo de Mejoramiento",
}


_M2_POR_PARQUEADERO = 30.0  # m² per stall incl. drive aisle (industry standard)


def _excavation_levels(stalls_required: float, footprint_m2: float | None) -> dict | None:
    """
    Estimate basement levels needed to house the parking stalls underground.

    Formula:
        stalls_per_level = footprint_m2 / M2_POR_PARQUEADERO
        levels_needed    = ceil(stalls_required / stalls_per_level)
    """
    if footprint_m2 is None or footprint_m2 <= 0 or stalls_required <= 0:
        return None
    import math
    stalls_per_level = footprint_m2 / _M2_POR_PARQUEADERO
    levels = math.ceil(stalls_required / stalls_per_level)
    return {
        "stalls_required": round(stalls_required, 1),
        "stalls_per_level": round(stalls_per_level, 1),
        "sotanos_estimados": levels,
        "nota": (
            f"Estimación: {stalls_required:.0f} cupos ÷ {stalls_per_level:.1f} cupos/sótano "
            f"= {levels} nivel{'es' if levels != 1 else ''} de sótano. "
            "Asume 100% del área de planta disponible para parqueaderos. Verificar con diseño."
        ),
    }


def _calc_parking(
    area_actividad: dict | None,
    area_construible_max_m2: float | None,
    vis_en_sitio: bool,
    trace: list,
    N,
    footprint_m2: float | None = None,
) -> dict:
    """
    Compute Art. 389 parking area requirements and store in a parking dict.

    Returns a dict with keys: area_actividad_codigo, min_pct, max_pct,
    adicional_pct, min_area_m2, max_area_m2, confianza, fuente.

    Art. 390 defines the base area as total covered area MINUS parking areas
    and sótanos/semisótanos. This creates a circularity for min/max area
    estimation. For prefactibilidad we use area_construible_max_m2 as a
    conservative proxy (overestimates by ≈min_pct%), which is noted in the trace.
    """
    fuente = "Art. 389 Decreto 555/2021"

    # Resolve área de actividad — Layer 14 returns {"codigo": ..., "nombre": ...}
    codigo = None
    aa_confianza = "sin_dato"
    aa_label = "desconocido"
    if area_actividad:
        raw = (area_actividad.get("codigo") or "").strip()
        if raw:
            raw_upper = raw.upper()
            for k in _PARKING_ART389:
                if k == raw_upper or k in raw_upper:
                    codigo = k
                    aa_confianza = "alta"
                    aa_label = _PARKING_AREA_ACTIVIDAD_LABELS.get(k, raw)
                    break
            if codigo is None:
                # Known code not in table (e.g. PEMP zone)
                codigo = raw
                aa_label = area_actividad.get("nombre") or raw
                aa_confianza = "media"

    if codigo and codigo in _PARKING_ART389:
        min_no_vis, min_vis, max_pct, adicional_pct = _PARKING_ART389[codigo]
        min_pct = min_vis if vis_en_sitio else min_no_vis

        parking: dict = {
            "area_actividad_codigo": codigo,
            "area_actividad_label": aa_label,
            "min_pct": min_pct,
            "max_pct": max_pct,
            "adicional_pct": adicional_pct,
            "vis_en_sitio": vis_en_sitio,
            "confianza": aa_confianza,
            "fuente": fuente,
            "nota": (
                "Porcentajes sobre base Art. 390 (área cubierta total excluye "
                "estacionamientos, sótanos y semisótanos). "
                "Área estimada usa área_construible_max como proxy — sobreestima en "
                f"≈{min_pct:.0f}% por circularidad. Verificar con área real del proyecto."
            ),
        }

        if area_construible_max_m2 is not None:
            B = area_construible_max_m2
            min_area = round(B * min_pct / 100, 1)
            max_area = round(B * max_pct / 100, 1)
            adicional_area = round(B * adicional_pct / 100, 1)
            parking["min_area_m2"] = min_area
            parking["max_area_m2"] = max_area
            parking["adicional_area_m2"] = adicional_area
            # Excavation levels for max parking scenario
            stalls_max = max_area / _M2_POR_PARQUEADERO
            excav = _excavation_levels(stalls_max, footprint_m2)
            if excav:
                parking["excavacion"] = excav
            trace.append(_step(
                N(), "Estacionamientos — Art. 389 (mín/máx sobre base Art. 390)",
                f"min = {min_pct}% × B;  máx = {max_pct}% × B;  adicional = {adicional_pct}% × B",
                {
                    "area_actividad": codigo,
                    "vis_en_sitio": vis_en_sitio,
                    "B_proxy_m2": B,
                    "min_pct": min_pct,
                    "max_pct": max_pct,
                    "adicional_pct": adicional_pct,
                },
                {"min_area_m2": min_area, "max_area_m2": max_area, "adicional_area_m2": adicional_area},
                "m²",
                fuente=fuente,
                nota=parking["nota"],
            ))
        else:
            trace.append({
                "paso": N(),
                "descripcion": "Estacionamientos — Art. 389",
                "expresion": f"min = {min_pct}% × B;  máx = {max_pct}% × B",
                "valores": {"area_actividad": codigo, "B_proxy_m2": None},
                "resultado": None, "unidad": "%",
                "fuente": fuente,
                "nota": "área_construible_max_m2 no disponible — solo se reportan porcentajes.",
            })
    else:
        # Unknown or PEMP zone
        is_pemp = "PEMP" in str(codigo or "").upper() or "CONSERVACION" in str(codigo or "").upper()
        parking = {
            "area_actividad_codigo": codigo,
            "area_actividad_label": aa_label,
            "min_pct": None,
            "max_pct": None,
            "adicional_pct": None,
            "confianza": "sin_dato" if not codigo else "requiere_revision",
            "fuente": fuente,
            "nota": (
                "Zona PEMP: estacionamientos regidos por Art. 339 + Anexo 6 Decreto 555/2021 — "
                "no codificados en esta herramienta. Ver GAPS.md §5."
                if is_pemp else
                f"Área de actividad '{codigo}' no encontrada en tabla Art. 389. "
                "Verificar código Layer 14 o consultar Art. 389 directamente."
            ),
        }
        trace.append({
            "paso": N(),
            "descripcion": "Estacionamientos — Art. 389",
            "expresion": "no aplicable",
            "valores": {"area_actividad": codigo},
            "resultado": None, "unidad": None,
            "fuente": fuente,
            "nota": parking["nota"],
        })

    return parking


# ── Unit count estimator (estimate only — not a decree value) ────────────────

_DEFAULT_UNIT_MIX: dict[str, dict] = {
    "studio": {"pct": 20.0, "m2_neta": 40.0, "label": "Estudio (~40 m²)"},
    "1br":    {"pct": 30.0, "m2_neta": 55.0, "label": "1 Alcoba (~55 m²)"},
    "2br":    {"pct": 35.0, "m2_neta": 75.0, "label": "2 Alcobas (~75 m²)"},
    "3br":    {"pct": 15.0, "m2_neta": 95.0, "label": "3 Alcobas (~95 m²)"},
}

UNIT_ESTIMATE_DISCLAIMER = (
    "ESTIMACIÓN — no es un valor del decreto. Basada en supuestos de mezcla "
    "y circulación ingresados por el usuario. El área vendible neta real depende "
    "del diseño arquitectónico."
)


def estimate_units(
    area_construible_m2: float,
    circulacion_pct: float = 18.0,
    unit_mix: dict | None = None,
) -> dict:
    """
    Estimate apartment count from gross buildable area.

    Parameters
    ----------
    area_construible_m2 : gross buildable area (area_construible_max_m2 from metrics)
    circulacion_pct     : % of gross area consumed by circulation/common areas (default 18%)
    unit_mix            : dict keyed by type name, each value must have:
                            pct      — share of net sellable area (%)
                            m2_neta  — net area per unit (m²)
                            label    — display name
                          Defaults to _DEFAULT_UNIT_MIX.
                          Percentages must sum to 100 ± 0.5.

    Returns
    -------
    dict with: disclaimer, estimacion, area_construible_m2, circulacion_pct,
               area_vendible_neta_m2, total_unidades, unidades_por_tipo, formula_trace
    """
    mix = unit_mix if unit_mix is not None else _DEFAULT_UNIT_MIX

    total_pct = sum(v["pct"] for v in mix.values())
    if abs(total_pct - 100.0) > 0.5:
        raise ValueError(
            f"unit_mix percentages must sum to 100% (got {total_pct:.1f}%)"
        )

    ftrace: list[dict] = []
    step_n = [0]

    def SN() -> int:
        step_n[0] += 1
        return step_n[0]

    # Step 1: Gross area
    ftrace.append(_step(
        SN(), "Área construible máxima (bruta)",
        "área_construible_max_m2 del cálculo de edificabilidad",
        {}, area_construible_m2, "m²",
        nota="Tomada directamente del resultado de edificabilidad.",
    ))

    # Step 2: Circulation deduction
    circ_area = round(area_construible_m2 * circulacion_pct / 100, 1)
    ftrace.append(_step(
        SN(), "Deducción por circulación y zonas comunes",
        f"circ = área_bruta × {circulacion_pct}%",
        {"área_bruta_m2": area_construible_m2, "circulacion_pct": circulacion_pct},
        circ_area, "m²",
        nota="Incluye escaleras, pasillos, cuarto de basuras, zona de equipos, etc.",
    ))

    # Step 3: Net sellable area
    area_vendible = round(area_construible_m2 - circ_area, 1)
    ftrace.append(_step(
        SN(), "Área vendible neta estimada",
        "área_vendible = área_bruta − circulación",
        {"área_bruta_m2": area_construible_m2, "circ_area_m2": circ_area},
        area_vendible, "m²",
    ))

    # Step 4: Units per type
    unidades_por_tipo: dict[str, dict] = {}
    total_unidades = 0

    for tipo, cfg in mix.items():
        pct = cfg["pct"]
        m2 = cfg["m2_neta"]
        area_tipo = round(area_vendible * pct / 100, 1)
        n_units = int(area_tipo / m2)
        total_unidades += n_units
        unidades_por_tipo[tipo] = {
            "label": cfg["label"],
            "pct_mezcla": pct,
            "m2_neta_por_unidad": m2,
            "area_asignada_m2": area_tipo,
            "unidades": n_units,
        }
        ftrace.append(_step(
            SN(), f"Unidades {cfg['label']}",
            f"área_{tipo} = {pct}% × área_vendible;  n_{tipo} = área_{tipo} / {m2} m²",
            {"pct": pct, "área_vendible_m2": area_vendible, "m2_por_unidad": m2},
            n_units, "unidades",
            nota=f"área asignada = {area_tipo} m²",
        ))

    ftrace.append(_step(
        SN(), "Total unidades estimadas",
        "total = Σ n_tipo",
        {t: unidades_por_tipo[t]["unidades"] for t in unidades_por_tipo},
        total_unidades, "unidades",
        nota=UNIT_ESTIMATE_DISCLAIMER,
    ))

    return {
        "estimacion": True,
        "disclaimer": UNIT_ESTIMATE_DISCLAIMER,
        "area_construible_m2": area_construible_m2,
        "circulacion_pct": circulacion_pct,
        "circ_area_m2": circ_area,
        "area_vendible_neta_m2": area_vendible,
        "total_unidades": total_unidades,
        "unidades_por_tipo": unidades_por_tipo,
        "formula_trace": ftrace,
    }


def calculate(
    lookup: dict,
    anu_m2: float | None = None,
    frente_m: float | None = None,
    ancho_via_m: float | None = None,
) -> dict:
    """
    Compute buildability metrics for a lot.

    Parameters
    ----------
    lookup      : dict returned by p2_lookup.lookup()
    anu_m2      : user-supplied Área Neta Urbanizable in m² — required when:
                  • DESARROLLO Rangos 1–3 (must come from Plan Parcial)
                  • Any DESARROLLO lot whose gross area > 10,000 m²
    frente_m    : lot frontage in metres (measured along the primary road) — required
                  when a CONSOLIDACIÓN lot has a range height "N-M" in the map, to gate
                  access to the bonus floors per Art. 310 Num. 2.
    ancho_via_m : road profile width in metres — additionally required for tipología
                  aislada when evaluating the Art. 310 Num. 2 bonus gate.

    Returns
    -------
    dict with keys:
      input             — echoed inputs
      lote              — lot identity and area
      antejardin        — parsed front-setback requirement
      anu               — resolved ANU with source note
      metrics           — computed buildability values with confianza
      binding_constraint — "total_area" | "footprint" | "height" |
                           "footprint_and_height" | "total_area_and_footprint" |
                           "anu_required" | "indeterminado" |
                           "tratamiento_no_implementado" | "conservacion_no_soportado"
      formula_trace     — ordered list of calculation steps for hand-verification
      warnings          — non-fatal notes requiring human review
    """
    result: dict = {
        "input": {
            "lng": lookup["input"]["lng"],
            "lat": lookup["input"]["lat"],
            "vis_en_sitio": lookup["input"]["vis_en_sitio"],
            "anu_m2_supplied": anu_m2,
            "frente_m_supplied": frente_m,
            "ancho_via_m_supplied": ancho_via_m,
        },
        "lote": lookup["lote"],
        "consulta": lookup.get("consulta"),
        "tratamiento": lookup.get("tratamiento"),
        "warnings": list(lookup.get("warnings", [])),
        "formula_trace": [],
    }

    trace = result["formula_trace"]
    _n = [0]

    def N() -> int:
        _n[0] += 1
        return _n[0]

    lot_area = lookup["lote"]["area_m2"]["valor"]
    trat = lookup.get("tratamiento", "").upper().replace("Ó", "O").replace("Á", "A")

    # ── Step 1: Lot area ──────────────────────────────────────────────────────
    trace.append(_step(
        N(), "Área del lote",
        "shoelace(anillo_proyectado_MAGNA_SIRGAS_9377)",
        {"LOTCODIGO": lookup["lote"].get("lotcodigo")},
        lot_area, "m²",
        fuente="Catastro MapServer Layer 0, outSR=9377",
    ))

    # ── Antejardín ────────────────────────────────────────────────────────────
    try:
        if "RENOVACION" in trat:
            ant = {
                "exigido": False,
                "dimension_m": 0.0,
                "confianza": "alta",
                "nota": "Renovación Urbana no exige antejardín obligatorio (Art. 307 Decreto 555/2021).",
                "fuente": "Art. 307 Decreto 555/2021",
                "articulo": "Art. 307 Decreto 555/2021",
            }
        else:
            ant = _parse_antejardin(lookup.get("antejardin"))
        result["antejardin"] = ant
        trace.append(_step(
            N(), "Antejardín (Art. 307)" if "RENOVACION" in trat else "Antejardín (mapa CU-5.5)",
            "no_exigido_por_tratamiento" if "RENOVACION" in trat else "Layer_22.DIMENSION",
            {"valor_raw": (lookup.get("antejardin") or {}).get("dimension_m", {}).get("valor")},
            ant["dimension_m"], "m",
            fuente=ant["fuente"],
            nota=ant["nota"],
        ))
    except InputRequired as exc:
        result["antejardin"] = {
            "exigido": None,
            "dimension_m": None,
            "confianza": "requiere_input",
            "error": str(exc),
        }
        trace.append({
            "paso": N(), "descripcion": "Antejardín — DATO REQUERIDO",
            "expresion": "Layer_22.DIMENSION", "valores": {},
            "resultado": None, "unidad": "m", "error": str(exc),
        })
        result["warnings"].append(f"[ANTEJARDÍN] {exc}")

    # ── Step ANU ──────────────────────────────────────────────────────────────
    # InputRequired is NOT caught here — without ANU no metric can be computed,
    # so the exception propagates to the caller who must supply anu_m2.
    anu, anu_fuente, anu_warnings = _resolve_anu(lookup, anu_m2)
    result["warnings"].extend(anu_warnings)
    result["anu"] = {"valor_m2": anu, "fuente": anu_fuente, "confianza": "alta"}
    trace.append(_step(
        N(), "ANU (Área Neta Urbanizable)",
        "ANU = anu_m2_supplied" if anu_m2 else "ANU = área_lote",
        {"área_lote_m2": lot_area, "anu_m2_supplied": anu_m2},
        anu, "m²",
        fuente=anu_fuente,
    ))

    # ── Treatment-specific calcs ──────────────────────────────────────────────
    if "DESARROLLO" in trat:
        metrics, binding = _calc_desarrollo(lookup, anu, trace, N, ancho_via_m)
    elif "CONSOLIDACION" in trat:
        metrics, binding = _calc_consolidacion(lookup, anu, trace, N, frente_m, ancho_via_m)
    elif "RENOVACION" in trat:
        metrics, binding = _calc_renovacion_urbana(lookup, anu, trace, N, ancho_via_m)
    elif "MEJORAMIENTO" in trat:
        metrics, binding = _calc_mejoramiento_integral(lookup, anu, trace, N, ancho_via_m)
    elif "CONSERVACION" in trat:
        metrics, binding = _calc_conservacion(lookup, trace, N)
    else:
        metrics = None
        binding = "tratamiento_no_implementado"
        result["warnings"].append(
            f"Tratamiento '{lookup.get('tratamiento')}' no reconocido. "
            "Consulte directamente la Secretaría Distrital de Planeación."
        )

    result["metrics"] = metrics
    result["binding_constraint"] = binding

    # ── Blocking restrictions — override binding_constraint if hard restrictions present
    # "conservacion_no_soportado" already embeds the BIC/patrimonial logic — don't double-report.
    _blocking = lookup.get("restricciones_bloqueantes", [])
    if _blocking:
        result["blocking_restrictions"] = _blocking
        if binding not in ("conservacion_no_soportado", "tratamiento_no_implementado"):
            result["binding_constraint"] = "restriccion_bloqueante"

    # ── Parking (Art. 389) ────────────────────────────────────────────────────
    area_max_val = (metrics or {}).get("area_construible_max_m2", {}).get("valor")
    footprint_val = (metrics or {}).get("planta_maxima_m2", {}).get("valor")
    result["parking"] = _calc_parking(
        area_actividad=lookup.get("area_actividad"),
        area_construible_max_m2=area_max_val,
        vis_en_sitio=lookup["input"]["vis_en_sitio"],
        trace=trace,
        N=N,
        footprint_m2=footprint_val,
    )

    return result


# ── DESARROLLO calculator ─────────────────────────────────────────────────────

def _calc_desarrollo(lookup: dict, anu: float, trace: list, N,
                     ancho_via_m: float | None = None) -> tuple[dict, str]:
    edif = lookup["edificabilidad"]
    rango = lookup.get("rango", "?")
    vis = lookup["input"]["vis_en_sitio"]

    ic = edif["indice_construccion"]["valor"]
    io = edif["indice_ocupacion"]["valor"]
    alt = edif["altura_maxima_pisos"]
    alt_val = alt["valor"]      # None when "resultante"
    metrics: dict = {}

    # ── IC → maximum total buildable area ─────────────────────────────────────
    if ic is not None:
        area_max = round(ic * anu, 1)
        trace.append(_step(
            N(), f"Área máxima construible — IC Rango {rango}",
            "IC × ANU",
            {"IC": ic, "escenario": "con_VIS" if vis else "sin_VIS", "ANU_m2": anu},
            area_max, "m²",
            fuente=edif["indice_construccion"]["articulo"],
        ))
        metrics["area_construible_max_m2"] = {
            "valor": area_max, "confianza": "alta",
            "nota": f"IC={ic} × ANU={_fmt_es(anu)} m²",
        }

        # VIS ≥75% bonus (4C / 4D only)
        ic75 = edif.get("indice_construccion_vis75")
        if ic75 and ic75["valor"] is not None:
            area_max_75 = round(ic75["valor"] * anu, 1)
            trace.append(_step(
                N(), f"Área máxima construible — IC >75% VIS, Rango {rango}",
                "IC_vis75 × ANU",
                {"IC_vis75": ic75["valor"], "ANU_m2": anu},
                area_max_75, "m²",
                fuente=ic75["articulo"],
                nota="Aplica sólo si >75% del índice efectivo se destina a VIS/VIP.",
            ))
            metrics["area_construible_max_vis75_m2"] = {
                "valor": area_max_75, "confianza": "alta",
            }
    else:
        trace.append(_step(
            N(), f"Área máxima construible — IC Rango {rango}",
            "IC × ANU  [IC = resultante]",
            {"IC": "resultante", "ANU_m2": anu},
            None, "m²",
            fuente=edif["indice_construccion"]["articulo"],
            nota=edif["indice_construccion"]["nota"],
        ))
        metrics["area_construible_max_m2"] = {
            "valor": None, "confianza": "media",
            "nota": edif["indice_construccion"]["nota"],
        }

    # ── IO → maximum footprint ────────────────────────────────────────────────
    if io is not None:
        fp_max = round(io * anu, 1)
        trace.append(_step(
            N(), f"Planta máxima por piso — IO Rango {rango}",
            "IO × ANU",
            {"IO": io, "ANU_m2": anu},
            fp_max, "m²",
            fuente=edif["indice_ocupacion"]["articulo"],
        ))
        metrics["planta_maxima_m2"] = {
            "valor": fp_max, "confianza": "alta",
            "nota": f"IO={io} × ANU={_fmt_es(anu)} m²",
        }
    else:
        trace.append(_step(
            N(), f"Planta máxima por piso — IO Rango {rango}",
            "IO × ANU  [IO = resultante]",
            {"IO": "resultante", "ANU_m2": anu},
            None, "m²",
            fuente=edif["indice_ocupacion"]["articulo"],
            nota=edif["indice_ocupacion"]["nota"],
        ))
        metrics["planta_maxima_m2"] = {
            "valor": None, "confianza": "media",
            "nota": edif["indice_ocupacion"]["nota"],
        }

    # ── Height ────────────────────────────────────────────────────────────────
    if alt_val is not None:
        trace.append(_step(
            N(), f"Altura máxima — Rango {rango}",
            "pisos_max (dato directo del decreto)",
            {"pisos": alt_val, "vis_en_sitio": vis},
            alt_val, "pisos",
            fuente=alt["articulo"],
            nota=(
                f"Rango {rango}: {alt_val} pisos "
                f"({'con' if vis else 'sin'} obligación VIS/VIP en sitio)."
            ),
        ))
        metrics["altura_maxima_pisos"] = {"valor": alt_val, "confianza": "alta"}
    else:
        # Height is "resultante" — NEVER estimate; return null with reason
        trace.append({
            "paso": N(),
            "descripcion": f"Altura máxima — Rango {rango}",
            "expresion": "no hay pisos fijos para este rango (Art. 281: 'Resultante')",
            "valores": {},
            "resultado": None, "unidad": "pisos",
            "fuente": alt["articulo"],
            "nota": alt["nota"],
        })
        metrics["altura_maxima_pisos"] = {
            "valor": None, "confianza": "media",
            "razon_nulo": alt["nota"],
        }

    # ── Effective area when both IO and height are known ─────────────────────
    if io is not None and alt_val is not None:
        fp = metrics["planta_maxima_m2"]["valor"]
        area_ef = round(fp * alt_val, 1)
        trace.append(_step(
            N(), "Área efectiva (planta máxima × pisos)",
            "planta_max × pisos_max",
            {"planta_max_m2": fp, "pisos_max": alt_val},
            area_ef, "m²",
            nota="Área total si se ocupa la huella máxima en cada piso.",
        ))
        metrics["area_efectiva_m2"] = {"valor": area_ef, "confianza": "alta"}

        # Binding constraint: compare effective area against IC cap (if known)
        if ic is not None:
            area_max = metrics["area_construible_max_m2"]["valor"]
            if area_ef <= area_max:
                binding = "footprint_and_height"
                # The effective achievable area (IO × pisos) is the binding constraint.
                # Preserve the IC ceiling as a reference-only field so the UI can label it.
                metrics["area_techo_ic_m2"] = {
                    "valor": area_max, "confianza": "alta",
                    "nota": (
                        f"Techo teórico IC={ic} × ANU={_fmt_es(anu)} m². "
                        "No es la restricción activa — la combinación IO+altura agota el lote antes."
                    ),
                }
                metrics["area_construible_max_m2"] = {
                    "valor": area_ef, "confianza": "alta",
                    "nota": (
                        f"IO={io} × {_fmt_es(fp)} m² huella × {alt_val} pisos. "
                        f"Alcanzable = {_fmt_es(area_ef)} m² < techo IC = {_fmt_es(area_max)} m² — "
                        "la altura y la huella son los controles activos."
                    ),
                }
                trace.append({
                    "paso": N(),
                    "descripcion": "Restricción dominante — HUELLA y ALTURA",
                    "expresion": "IO×ANU×pisos ≤ IC×ANU → altura e IO agotan el lote antes de agotar el IC",
                    "valores": {
                        "área_alcanzable_m2": area_ef,
                        "techo_IC_m2": area_max,
                        "margen_IC_no_usado_m2": round(area_max - area_ef, 1),
                    },
                    "resultado": "footprint_and_height",
                    "nota": (
                        f"IO={io} y {alt_val} pisos producen {_fmt_es(area_ef)} m² (alcanzable). "
                        f"Techo IC×ANU={_fmt_es(area_max)} m² — no se agota. "
                        "Área construible efectiva = min(IO×ANU×pisos, IC×ANU) = "
                        f"{_fmt_es(area_ef)} m²."
                    ),
                })
            else:
                binding = "total_area"
                trace.append({
                    "paso": N(),
                    "descripcion": "Restricción dominante",
                    "expresion": "planta×pisos > IC×ANU → el IC limita antes de alcanzar la altura",
                    "valores": {
                        "área_efectiva_sin_IC_m2": area_ef,
                        "área_IC_máx_m2": area_max,
                        "exceso_m2": round(area_ef - area_max, 1),
                    },
                    "resultado": "total_area",
                    "nota": (
                        f"Con IO={io} y {alt_val} pisos se llegaría a {_fmt_es(area_ef)} m², "
                        f"pero IC×ANU={_fmt_es(area_max)} m² es el techo regulatorio. "
                        "Se deben reducir pisos o planta hasta cumplir IC."
                    ),
                })
        else:
            # IC is resultante, IO and height are the two known controls
            binding = "footprint_and_height"
            trace.append({
                "paso": N(),
                "descripcion": "Restricción dominante",
                "expresion": "IO y altura son los controles numéricos; IC es resultante",
                "valores": {"IO": io, "pisos_max": alt_val, "área_efectiva_m2": area_ef},
                "resultado": "footprint_and_height",
            })

    elif ic is not None and io is None and alt_val is None:
        # Only IC is known (e.g. Rangos 1–3 after ANU is supplied)
        binding = "total_area"
        trace.append({
            "paso": N(),
            "descripcion": "Restricción dominante",
            "expresion": "IC es el único parámetro numérico; IO y altura son resultantes",
            "valores": {"IC": ic},
            "resultado": "total_area",
            "nota": "El área total construible es el techo regulatorio. Planta y altura emergen de la geometría.",
        })

    elif ic is not None and io is not None and alt_val is None:
        # IC + IO known, height resultante (Rangos 4C/4D)
        binding = "total_area_and_footprint"
        fp = metrics["planta_maxima_m2"]["valor"]
        area_max = metrics["area_construible_max_m2"]["valor"]
        implied_floors = round(area_max / fp, 1) if fp else None
        trace.append({
            "paso": N(),
            "descripcion": "Restricción dominante",
            "expresion": "IC y IO son fijos; altura es resultante (Art. 281: 'Resultante')",
            "valores": {
                "IC": ic, "IO": io, "planta_max_m2": fp, "área_IC_máx_m2": area_max,
                "pisos_implícitos_IC_IO": implied_floors,
            },
            "resultado": "total_area_and_footprint",
            "nota": (
                f"Con IO={io} → planta ≤ {_fmt_es(fp)} m². Con IC={ic} → área total ≤ {_fmt_es(area_max)} m². "
                f"La altura resultante implícita es IC/IO ≈ {implied_floors} pisos — "
                "este número NO está fijado en el decreto y NO debe usarse como altura permitida."
            ),
        })
        metrics["pisos_implicitos_IC_IO"] = {
            "valor": implied_floors, "confianza": "media",
            "nota": (
                "Cálculo orientativo: IC/IO. Este valor NO es la altura máxima del decreto — "
                "es el número de pisos que agotaría ambos índices simultáneamente. "
                "La altura real emerge de la aplicación de las normas volumétricas."
            ),
        }
    else:
        binding = "indeterminado"
        trace.append({
            "paso": N(),
            "descripcion": "Restricción dominante",
            "expresion": "todos los parámetros clave son resultantes",
            "valores": {"IC": ic, "IO": io, "pisos": alt_val},
            "resultado": "indeterminado",
            "nota": "IC, IO y altura son todos resultantes. Se requiere modelado volumétrico.",
        })

    # ── Retroceso de fachada A = 2 × D (Desarrollo) ──────────────────────────
    _retroceso_fachada(lookup, ancho_via_m, factor=2.0,
                       fuente_decreto="Art. 281 / Anexo 5 Cap. 1.2.2.E.1 Decreto 555/2021",
                       trace=trace, N=N, metrics=metrics)

    return metrics, binding


# ── CONSOLIDACIÓN calculator ──────────────────────────────────────────────────

def _calc_consolidacion(
    lookup: dict,
    anu: float,
    trace: list,
    N,
    frente_m: float | None = None,
    ancho_via_m: float | None = None,
) -> tuple[dict, str]:
    edif = lookup["edificabilidad"]
    alt_struct = edif.get("altura_maxima", {})
    tipo = alt_struct.get("tipo")
    tipologia = lookup.get("tipologia", {}).get("valor", "continua")
    metrics: dict = {}

    # ── IC/IO are resultante in all Consolidación lots ────────────────────────
    trace.append({
        "paso": N(),
        "descripcion": "IC e IO — resultantes (Art. 310 Num. 1)",
        "expresion": "IC = IO = f(aislamientos, tipología, antejardín, retroceso)",
        "valores": {
            "tipologia": tipologia,
            "área_lote_m2": lookup["lote"]["area_m2"]["valor"],
        },
        "resultado": None, "unidad": "—",
        "fuente": "Art. 310 Num. 1 Decreto 555/2021",
        "nota": (
            "Los índices de construcción y ocupación no están fijados numéricamente. "
            "Emergen de: aislamiento posterior (tabla Art. 310 / Anexo 5 p.57), "
            "aislamiento lateral (si tipología aislada: 1/5 × altura total, mín. 4m), "
            "antejardín (mapa CU-5.5), y retroceso de fachada (A = 2.5 × D). "
            "Para obtener IC e IO reales es necesario modelar la geometría del lote con sus dimensiones exactas."
        ),
    })
    metrics["area_construible_max_m2"] = {
        "valor": None, "confianza": "media",
        "nota": "IC resultante — requiere modelado geométrico completo.",
    }
    metrics["planta_maxima_m2"] = {
        "valor": None, "confianza": "media",
        "nota": "IO resultante — requiere modelado geométrico completo.",
    }

    # ── Height ────────────────────────────────────────────────────────────────
    if tipo == "fijo":
        pisos = alt_struct["pisos_max"]
        trace.append(_step(
            N(), "Altura máxima (mapa CU-5.4.x)",
            "Layer_15.ALTURA_MAXIMA — tipo fijo",
            {"ALTURA_MAXIMA_raw": str(pisos), "tipo": "fijo"},
            pisos, "pisos",
            fuente=alt_struct.get("fuente", "Layer 15 campo ALTURA_MAXIMA"),
        ))
        metrics["altura_base_pisos"] = {"valor": pisos, "confianza": "alta"}
        metrics["altura_con_bonus_pisos"] = {
            "valor": None, "confianza": "alta",
            "nota": "Mapa indica altura fija — no hay separación base/bonus para este lote.",
        }
        binding = "height"

        # VIS×2 bonus (Art. 310 § 3) — gated on AAERVIS receptor zone (Layer 14)
        area_act = lookup.get("area_actividad", {})
        es_receptora = area_act.get("es_receptora_vis", False)
        vis = lookup["input"]["vis_en_sitio"]
        vis_bonus_pisos = pisos * 2

        if es_receptora and vis:
            trace.append({
                "paso": N(),
                "descripcion": "Altura con bonus VIS×2 (Art. 310 § 3) — APLICA",
                "expresion": "pisos_base × 2",
                "valores": {
                    "pisos_base": pisos, "factor": 2,
                    "area_actividad_codigo": area_act.get("codigo"),
                    "vis_en_sitio": vis,
                },
                "resultado": vis_bonus_pisos, "unidad": "pisos",
                "fuente": "Art. 310 Parágrafo 3 Decreto 555/2021",
                "nota": (
                    f"Predio en zona AAERVIS ({area_act.get('nombre', '')}). "
                    "Condición adicional: proyecto debe destinar ≥70% del área construida a VIS "
                    "o ≥50% a VIP — verificar en el anteproyecto arquitectónico."
                ),
            })
            metrics["altura_con_vis_bonus_pisos"] = {
                "valor": vis_bonus_pisos, "confianza": "alta",
                "condicion": "≥70% VIS o ≥50% VIP · zona AAERVIS (Art. 310 § 3)",
            }
        elif es_receptora and not vis:
            trace.append({
                "paso": N(),
                "descripcion": "Altura con bonus VIS×2 (Art. 310 § 3) — zona elegible, VIS no marcado",
                "expresion": "pisos_base × 2  [vis_en_sitio=False]",
                "valores": {"pisos_base": pisos, "area_actividad_codigo": area_act.get("codigo")},
                "resultado": None, "unidad": "pisos",
                "fuente": "Art. 310 Parágrafo 3 Decreto 555/2021",
                "nota": (
                    "Predio en zona AAERVIS — elegible para bonus VIS×2. "
                    "Active 'Obligación VIS/VIP en sitio' si el proyecto destina ≥70% a VIS o ≥50% a VIP."
                ),
            })
            metrics["altura_con_vis_bonus_pisos"] = {
                "valor": None, "confianza": "alta",
                "nota": (
                    f"Zona AAERVIS elegible — bonus disponible si vis_en_sitio=True y "
                    "proyecto destina ≥70% a VIS o ≥50% a VIP (Art. 310 § 3)."
                ),
            }
        else:
            codigo = area_act.get("codigo") or "sin zona"
            trace.append({
                "paso": N(),
                "descripcion": "Altura con bonus VIS×2 (Art. 310 § 3) — NO aplica",
                "expresion": "N/A — predio fuera de zona AAERVIS",
                "valores": {"area_actividad_codigo": codigo},
                "resultado": None, "unidad": "pisos",
                "fuente": "Art. 310 Parágrafo 3 Decreto 555/2021",
                "nota": (
                    f"Predio en zona '{codigo}' (no es AAERVIS). "
                    "El bonus VIS×2 del Art. 310 § 3 solo aplica en zonas AAERVIS."
                ),
            })
            metrics["altura_con_vis_bonus_pisos"] = {
                "valor": None, "confianza": "alta",
                "nota": (
                    f"No aplica: predio en zona '{codigo}' — "
                    "Art. 310 § 3 requiere zona AAERVIS (Área de Actividad Estructurante "
                    "Receptora de vivienda de interés social)."
                ),
            }

    elif tipo == "rango":
        # "4-6" — base height applies unconditionally; bonus height requires frontage gate
        # per Art. 310 Num. 2.  Raises InputRequired when frente_m is absent.
        p_base = alt_struct["pisos_min"]
        p_bonus = alt_struct["pisos_max"]
        lot_area = lookup["lote"]["area_m2"]["valor"]

        trace.append({
            "paso": N(),
            "descripcion": "Altura base (mapa CU-5.4.x) — aplica sin condiciones adicionales",
            "expresion": f"altura_base = {p_base}",
            "valores": {"ALTURA_MAXIMA_raw": f"{p_base}-{p_bonus}", "altura_base_pisos": p_base},
            "resultado": p_base,
            "unidad": "pisos",
            "fuente": alt_struct.get("fuente", "Layer 15 campo ALTURA_MAXIMA"),
        })
        metrics["altura_base_pisos"] = {"valor": p_base, "confianza": "alta"}
        # Upper bound of the range — always emitted so the client can estimate
        # total buildable area (footprint × p_bonus) even before frente is known.
        metrics["altura_maxima_rango_pisos"] = {"valor": p_bonus, "confianza": "media",
            "nota": f"Límite superior del rango — aplica si se cumple Art. 310 Num. 2 (frente mínimo)."}
        binding = "height"

        # ── Art. 310 Num. 2 frontage gate ────────────────────────────────────
        # InputRequired is caught here so base metrics are returned even without
        # frente_m / ancho_via_m — the bonus simply remains indeterminate.
        _bonus_qualifies: bool | None
        try:
            _bonus_qualifies, gate_reason = _check_bonus_frontage_gate(
                tipologia, frente_m, ancho_via_m, lot_area, p_base
            )
        except InputRequired as _exc_fr:
            _bonus_qualifies = None
            gate_reason = str(_exc_fr)
        bonus_qualifies = _bonus_qualifies

        trace.append({
            "paso": N(),
            "descripcion": "Verificación frente mínimo — bonus altura Art. 310 Num. 2",
            "expresion": (
                "frente >= 14m" if tipologia != "aislada"
                else "frente >= 24m AND área >= 1200m² AND vía >= 22m AND base >= 8p"
            ),
            "valores": {
                "tipologia": tipologia,
                "frente_m": frente_m,
                "ancho_via_m": ancho_via_m,
                "área_lote_m2": lot_area,
                "pisos_base": p_base,
            },
            "resultado": (
                "PENDIENTE — proporcione frente_m" if bonus_qualifies is None
                else ("CUMPLE" if bonus_qualifies else "NO CUMPLE")
            ),
            "unidad": "—",
            "fuente": "Art. 310 Numeral 2 Decreto 555/2021",
            "nota": gate_reason,
        })

        if bonus_qualifies is None:
            metrics["altura_con_bonus_pisos"] = {
                "valor": None, "confianza": "requiere_input",
                "nota": gate_reason,
            }
        elif bonus_qualifies:
            metrics["altura_con_bonus_pisos"] = {
                "valor": p_bonus, "confianza": "alta",
                "condicion": gate_reason,
            }
        else:
            metrics["altura_con_bonus_pisos"] = {
                "valor": None, "confianza": "alta",
                "nota": f"No aplica: {gate_reason}",
            }

        # VIS×2 bonus (Art. 310 § 3) — gated on AAERVIS receptor zone (Layer 14)
        area_act = lookup.get("area_actividad", {})
        es_receptora = area_act.get("es_receptora_vis", False)
        vis = lookup["input"]["vis_en_sitio"]
        codigo = area_act.get("codigo") or "sin zona"

        if es_receptora and vis:
            base_vis = p_base * 2
            bonus_vis = p_bonus * 2 if bonus_qualifies is True else None
            _zona_nota = (
                f"Predio en zona AAERVIS ({area_act.get('nombre', '')}). "
                "Condición adicional: proyecto debe destinar ≥70% del área construida a VIS "
                "o ≥50% a VIP — verificar en el anteproyecto arquitectónico."
            )
            trace.append({
                "paso": N(),
                "descripcion": "Altura con bonus VIS×2 (Art. 310 § 3) — APLICA",
                "expresion": "pisos_base × 2  [y pisos_bonus × 2 si cumple Num. 2]",
                "valores": {
                    "pisos_base": p_base, "pisos_bonus": p_bonus if bonus_qualifies is True else None,
                    "base_vis": base_vis, "bonus_vis": bonus_vis,
                    "area_actividad_codigo": codigo, "vis_en_sitio": vis,
                },
                "resultado": base_vis, "unidad": "pisos",
                "fuente": "Art. 310 Parágrafo 3 Decreto 555/2021",
                "nota": _zona_nota,
            })
            metrics["altura_base_con_vis_bonus_pisos"] = {
                "valor": base_vis, "confianza": "alta",
                "condicion": "≥70% VIS o ≥50% VIP · zona AAERVIS (Art. 310 § 3)",
            }
            metrics["altura_bonus_manzana_con_vis_bonus_pisos"] = {
                "valor": bonus_vis, "confianza": "alta",
                "nota": (
                    _zona_nota if bonus_qualifies is True
                    else (
                        f"Pendiente — ingrese frente_m para determinar: {gate_reason}"
                        if bonus_qualifies is None
                        else f"No aplica — predio no accede al bonus Num. 2: {gate_reason}"
                    )
                ),
            }
        elif es_receptora and not vis:
            trace.append({
                "paso": N(),
                "descripcion": "Altura con bonus VIS×2 (Art. 310 § 3) — zona elegible, VIS no marcado",
                "expresion": "pisos_base × 2  [vis_en_sitio=False]",
                "valores": {"pisos_base": p_base, "area_actividad_codigo": codigo},
                "resultado": None, "unidad": "pisos",
                "fuente": "Art. 310 Parágrafo 3 Decreto 555/2021",
                "nota": (
                    "Predio en zona AAERVIS — elegible para bonus VIS×2. "
                    "Active 'Obligación VIS/VIP en sitio' si el proyecto destina ≥70% a VIS o ≥50% a VIP."
                ),
            })
            metrics["altura_base_con_vis_bonus_pisos"] = {
                "valor": None, "confianza": "alta",
                "nota": (
                    "Zona AAERVIS elegible — bonus disponible si vis_en_sitio=True y "
                    "proyecto destina ≥70% a VIS o ≥50% a VIP (Art. 310 § 3)."
                ),
            }
            metrics["altura_bonus_manzana_con_vis_bonus_pisos"] = {
                "valor": None, "confianza": "alta",
                "nota": (
                    "Zona AAERVIS elegible — bonus disponible si vis_en_sitio=True."
                    if bonus_qualifies
                    else f"No aplica — predio no accede al bonus Num. 2: {gate_reason}"
                ),
            }
        else:
            trace.append({
                "paso": N(),
                "descripcion": "Altura con bonus VIS×2 (Art. 310 § 3) — NO aplica",
                "expresion": "N/A — predio fuera de zona AAERVIS",
                "valores": {"area_actividad_codigo": codigo},
                "resultado": None, "unidad": "pisos",
                "fuente": "Art. 310 Parágrafo 3 Decreto 555/2021",
                "nota": (
                    f"Predio en zona '{codigo}' (no es AAERVIS). "
                    "El bonus VIS×2 del Art. 310 § 3 solo aplica en zonas AAERVIS."
                ),
            })
            metrics["altura_base_con_vis_bonus_pisos"] = {
                "valor": None, "confianza": "alta",
                "nota": (
                    f"No aplica: predio en zona '{codigo}' — "
                    "Art. 310 § 3 requiere zona AAERVIS."
                ),
            }
            metrics["altura_bonus_manzana_con_vis_bonus_pisos"] = {
                "valor": None, "confianza": "alta",
                "nota": (
                    f"No aplica: predio en zona '{codigo}' — Art. 310 § 3 requiere zona AAERVIS."
                    if bonus_qualifies
                    else f"No aplica — predio no accede al bonus Num. 2: {gate_reason}"
                ),
            }

    elif tipo == "referencia_rango":
        rango_ref = alt_struct.get("rango_code")
        trace.append({
            "paso": N(),
            "descripcion": "Altura — remisión a RANGO Desarrollo",
            "expresion": f"ALTURA_MAXIMA = 'Rg {rango_ref}' → aplicar Art. 281 Rango {rango_ref}",
            "valores": {"rango_code": rango_ref},
            "resultado": None, "unidad": "pisos",
            "nota": (
                f"Layer 15 registra 'Rg {rango_ref}' para este lote. "
                "Inusual en Consolidación — verificar tratamiento manualmente antes de continuar."
            ),
        })
        metrics["altura_base_pisos"] = {
            "valor": None, "confianza": "requiere_input",
            "nota": f"Referencia a RANGO Desarrollo {rango_ref}. Consultar Art. 281.",
        }
        metrics["altura_con_bonus_pisos"] = {"valor": None, "confianza": "requiere_input"}
        binding = "indeterminado"

    elif tipo == "especial":
        codigo = alt_struct.get("codigo", "?")
        trace.append({
            "paso": N(),
            "descripcion": f"Altura — código especial '{codigo}'",
            "expresion": f"ALTURA_MAXIMA = '{codigo}'",
            "valores": {"codigo": codigo},
            "resultado": None, "unidad": "pisos",
            "nota": (
                f"'{codigo}' indica una restricción fuera del alcance de este sistema "
                "(Unidad Normativa Especial o Bien de Interés Cultural). "
                "Requiere concepto técnico de la SDP o del IDPC."
            ),
        })
        metrics["altura_base_pisos"] = {
            "valor": None, "confianza": "requiere_input",
            "nota": f"Código especial '{codigo}' — requiere concepto técnico.",
        }
        metrics["altura_con_bonus_pisos"] = {"valor": None, "confianza": "requiere_input"}
        binding = "indeterminado"

    else:
        metrics["altura_base_pisos"] = {"valor": None, "confianza": "requiere_input"}
        metrics["altura_con_bonus_pisos"] = {"valor": None, "confianza": "requiere_input"}
        binding = "indeterminado"

    # ── Aislamiento posterior ─────────────────────────────────────────────────
    aisl_post = edif.get("aislamiento_posterior", {})
    aisl_val = aisl_post.get("valor_m")

    if aisl_val is not None:
        ref_pisos = alt_struct.get("pisos_max") or alt_struct.get("pisos_min")
        trace.append(_step(
            N(), "Aislamiento posterior mínimo (Anexo 5 Cap. 2.4.2.A.2)",
            "tabla_aisl_posterior[pisos_max]",
            {"pisos_ref": ref_pisos},
            aisl_val, "m",
            fuente="Anexo 5 Cap. 2.4.2.A.2 Decreto 555/2021",
            nota=aisl_post.get("nota"),
        ))
        metrics["aislamiento_posterior_m"] = {"valor": aisl_val, "confianza": "alta"}

    # ── Aislamiento lateral ───────────────────────────────────────────────────
    aisl_lat = edif.get("aislamiento_lateral", {})

    if aisl_lat.get("exigido"):
        ref_pisos = (
            metrics.get("altura_base_pisos", {}).get("valor")
            or alt_struct.get("pisos_min")
        )
        if ref_pisos is not None:
            # 1/5 × altura_total (asumiendo 3m/piso), mín 4m
            altura_m_aprox = ref_pisos * 3.0
            al_raw = altura_m_aprox / 5.0
            al_final = max(al_raw, 4.0)
            trace.append(_step(
                N(), "Aislamiento lateral (tipología aislada)",
                "max( (1/5) × (pisos × 3m/piso), 4m )",
                {
                    "pisos_base": ref_pisos,
                    "altura_aprox_m": altura_m_aprox,
                    "1/5_×_altura": round(al_raw, 2),
                    "mínimo_decreto": 4.0,
                },
                al_final, "m",
                fuente="Art. 310 Num. 3 / Anexo 5 Cap. 1.2.2.D.2 Decreto 555/2021",
                nota=(
                    "Altura en metros estimada asumiendo 3m por piso. "
                    "Recalcular con la altura real del proyecto cuando se defina."
                ),
            ))
            metrics["aislamiento_lateral_m"] = {
                "valor": al_final, "confianza": "media",
                "nota": "Asume 3m/piso — recalcular con altura exacta.",
            }
        else:
            trace.append({
                "paso": N(),
                "descripcion": "Aislamiento lateral (tipología aislada)",
                "expresion": "max( (1/5) × altura_total_m, 4m ) — altura_total desconocida",
                "valores": {"tipologia": "aislada"},
                "resultado": None, "unidad": "m",
                "nota": "Requiere pisos o altura real del proyecto para calcular.",
            })
            metrics["aislamiento_lateral_m"] = {
                "valor": None, "confianza": "requiere_input",
                "nota": "Tipología aislada: aislamiento lateral exigido pero altura del proyecto desconocida.",
            }
    else:
        trace.append(_step(
            N(), "Aislamiento lateral (tipología continua)",
            "tipología_continua → no exigido",
            {"tipologia": tipologia},
            0, "m",
            fuente="Art. 310 Num. 3 Decreto 555/2021",
            nota="Tipología continua: sin aislamiento lateral.",
        ))
        metrics["aislamiento_lateral_m"] = {"valor": 0, "confianza": "alta"}

    # ── Retroceso de fachada A = 2.5 × D (Consolidación) ────────────────────
    _retroceso_fachada(lookup, ancho_via_m, factor=2.5,
                       fuente_decreto="Anexo 5 Cap. 1.2.2.E.1.1 Decreto 555/2021",
                       trace=trace, N=N, metrics=metrics)

    return metrics, binding


# ── RENOVACIÓN URBANA calculator ─────────────────────────────────────────────
#
# Art. 304 DD 555/2021: único control numérico es el ICe máximo según ámbito.
# IO y altura son resultantes del Anexo 5.  La única modalidad en el POT vigente
# es "revitalización" (las modalidades redesarrollo/reactivación del Decreto 190/
# 2004 no existen en el Decreto 555/2021).
#
_RU_AMBITOS = [
    (5.0, "sin_manzana_completa",
     "Licencia urbanística sin incluir la totalidad de los predios de una manzana "
     "(Art. 304 Num. 1 Decreto 555/2021)."),
    (6.0, "esquina_manzana",
     "Licencia urbanística englobando como mínimo una esquina de manzana y cesión "
     "de suelo para EP mayor a 400 m² (Art. 304 Num. 2 Decreto 555/2021)."),
    (7.0, "manzana_completa",
     "Proyecto que incluye la totalidad de los predios de una manzana "
     "(Art. 304 Num. 3 Decreto 555/2021)."),
]

_RU_IO_ALTURA_NULL = (
    "IO y altura no están fijados numéricamente para Renovación Urbana. "
    "Son resultantes de las normas volumétricas del Anexo 5 "
    "(retroceso de fachada A=2.5×D; aislamientos según altura efectiva). "
    "El IC efectivo máximo es el control regulatorio operativo."
)

# Aislamientos posteriores RU — table keyed by max height in metres
# Source: Anexo 5 D.466/2024, SECCIÓN 3.1.b (verified by OCR, 2026-09-03)
_RU_AISL_POST_TABLA = [
    (None, 12,  4),
    (12,   18,  5),
    (18,   27,  6),
    (27,   36,  8),
    (36,   45, 10),
    (45,   54, 12),
    (54,   66, 14),
    (66,   75, 16),
    (75,   84, 18),
    (84,  None, 20),
]  # (altura_desde_m_exclusive, altura_hasta_m_inclusive, aislamiento_min_m)

_RU_AISL_LATERAL_UMBRAL_M = 11.40

_RU_AISL_ENTRE_FORMULA = "2/5 × altura promedio de las edificaciones que se aíslan"
_RU_AISL_ENTRE_MIN_M = 6


def _ru_lookup_aisl_post(altura_m: float) -> int:
    """Return the minimum posterior setback (m) for the given building height (m)."""
    for desde, hasta, valor in _RU_AISL_POST_TABLA:
        if (desde is None or altura_m > desde) and (hasta is None or altura_m <= hasta):
            return valor
    return 20  # safe fallback: ≥ 84 m


def _calc_renovacion_urbana(
    lookup: dict,
    anu: float,
    trace: list,
    N,
    ancho_via_m: float | None = None,
) -> tuple[dict, str]:
    lot_area = lookup["lote"]["area_m2"]["valor"]
    metrics: dict = {}

    # Art. 304: tres tiers de ICe + Plan Parcial (sin límite fijo)
    for ice_max, ambito, condicion in _RU_AMBITOS:
        area_cap = round(ice_max * lot_area, 1)
        trace.append(_step(
            N(), f"Área construible máxima — ICe {ice_max} ({ambito})",
            f"ICe_max × área_lote",
            {"ICe_max": ice_max, "ambito": ambito, "área_lote_m2": lot_area},
            area_cap, "m²",
            fuente="Artículo 304, Decreto Distrital 555 de 2021",
            nota=condicion,
        ))
        metrics[f"area_construible_max_{ambito}_m2"] = {
            "valor": area_cap,
            "confianza": "alta",
            "ice_max": ice_max,
            "condicion": condicion,
        }

    trace.append({
        "paso": N(),
        "descripcion": "Área construible — Plan Parcial (ICe > 7.0)",
        "expresion": "ICe > 7.0 → sin límite fijo; requiere Plan Parcial previo (mín. 3 ha)",
        "valores": {"área_lote_m2": lot_area},
        "resultado": None, "unidad": "m²",
        "fuente": "Artículo 303, Decreto Distrital 555 de 2021",
        "nota": "Superar ICe 7.0 requiere adopción previa de Plan Parcial de mínimo 3 ha.",
    })
    metrics["area_construible_plan_parcial_m2"] = {
        "valor": None, "confianza": "alta",
        "nota": "Sin límite fijo — ICe resultante del Plan Parcial. Área mínima del plan: 3 ha.",
    }

    # IO y altura: resultantes
    for key, label in [("planta_maxima_m2", "IO"), ("altura_maxima_pisos", "Altura")]:
        trace.append({
            "paso": N(),
            "descripcion": f"{label} — resultante (no fijado en decreto)",
            "expresion": f"{label} = f(IC, aislamientos, retroceso_A=2.5D)",
            "valores": {},
            "resultado": None, "unidad": "m²" if "planta" in key else "pisos",
            "fuente": "Artículos 304–307 y Anexo 5, Decreto Distrital 555 de 2021",
            "nota": _RU_IO_ALTURA_NULL,
        })
    metrics["planta_maxima_m2"] = {"valor": None, "confianza": "media", "nota": _RU_IO_ALTURA_NULL}
    metrics["altura_maxima_pisos"] = {"valor": None, "confianza": "media", "nota": _RU_IO_ALTURA_NULL}

    trace.append({
        "paso": N(),
        "descripcion": "Restricción dominante — Renovación Urbana",
        "expresion": "binding = total_area (IC efectivo es el único control numérico fijo)",
        "valores": {
            "ice_max_sin_manzana": 5.0,
            "ice_max_esquina": 6.0,
            "ice_max_manzana": 7.0,
        },
        "resultado": "total_area",
        "nota": (
            "El ICe máximo (5.0 / 6.0 / 7.0 según ámbito del proyecto) es el control "
            "regulatorio operativo. IO y altura son resultantes del Anexo 5. "
            "Retroceso de fachada: A = 2.5 × D (mismo que Consolidación)."
        ),
    })

    # ── Retroceso de fachada A = 2.5 × D (Renovación Urbana) ────────────────
    _retroceso_fachada(lookup, ancho_via_m, factor=2.5,
                       fuente_decreto="Anexo 5 D.466/2024 SECCIÓN 1.11 / Decreto 555/2021",
                       trace=trace, N=N, metrics=metrics)

    # ── Aislamientos posteriores — tabla por altura (Anexo 5 D.466/2024 §3.1.b) ──
    # Since height is resultante we cannot pre-compute a single number.
    # Output the table as a lookup tool so the user can read off the required setback
    # once the architectural height is known.
    aisl_post_tabla_serialized = [
        {
            **({"altura_desde_m_exclusive": d} if d is not None else {}),
            **({"altura_hasta_m_inclusive": h} if h is not None else {"nota": "sin límite superior"}),
            "aislamiento_min_m": v,
        }
        for d, h, v in _RU_AISL_POST_TABLA
    ]
    trace.append({
        "paso": N(),
        "descripcion": "Aislamiento posterior mínimo — tabla por altura (Anexo 5 D.466/2024 §3.1.b)",
        "expresion": "aislamiento_post_m = tabla_RU[altura_m]  [altura resultante → tabla de referencia]",
        "valores": {"nota": "La altura de la edificación es resultante — consulte la tabla al definir los pisos"},
        "resultado": None, "unidad": "m",
        "fuente": "Anexo 5 Decreto Distrital 466 de 2024, SECCIÓN 3.1.b",
        "nota": (
            "RU usa metros de altura (no pisos) para determinar el aislamiento posterior. "
            "Empate hasta 11,40 m con edificaciones colindantes sin aislamiento existente. "
            "En predios esquineros el aislamiento se configura como patio en la esquina interior."
        ),
        "tabla_referencia": aisl_post_tabla_serialized,
    })
    metrics["aislamiento_posterior_tabla"] = {
        "valor": None,
        "confianza": "alta",
        "nota": (
            "Aislamiento posterior depende de la altura final del proyecto (resultante). "
            "Aplique la tabla al definir los pisos: ≤12m→4m; >12≤18m→5m; >18≤27m→6m; "
            ">27≤36m→8m; >36≤45m→10m; >45≤54m→12m; >54≤66m→14m; >66≤75m→16m; "
            ">75≤84m→18m; >84m→20m."
        ),
        "fuente": "Anexo 5 D.466/2024, SECCIÓN 3.1.b",
        "tabla": aisl_post_tabla_serialized,
    }

    # ── Aislamientos laterales — umbral 11,40 m (Anexo 5 D.466/2024 §3.2.a) ──
    trace.append({
        "paso": N(),
        "descripcion": "Aislamiento lateral — umbral de exigencia (Anexo 5 D.466/2024 §3.2.a)",
        "expresion": "Si altura ≤ 11,40 m → no exigido; si altura > 11,40 m → max(1/5 × altura_m, 4m)",
        "valores": {"umbral_m": _RU_AISL_LATERAL_UMBRAL_M},
        "resultado": None, "unidad": "m",
        "fuente": "Anexo 5 Decreto Distrital 466 de 2024, SECCIÓN 3.2.a",
        "nota": (
            "En edificaciones con altura ≤ 11,40 m no se exige aislamiento lateral. "
            "A partir de 11,40 m: aislamiento = max(1/5 × altura_m, 4,00 m), "
            "medido desde el nivel de exigencia. La altura final es resultante."
        ),
    })
    metrics["aislamiento_lateral_umbral_m"] = {
        "valor": _RU_AISL_LATERAL_UMBRAL_M,
        "confianza": "alta",
        "nota": (
            "No exigido si altura ≤ 11,40 m. "
            "Exigido si altura > 11,40 m: max(1/5 × altura, 4,00 m). "
            "Altura resultante — calcular al definir los pisos del proyecto."
        ),
        "fuente": "Anexo 5 D.466/2024, SECCIÓN 3.2.a",
    }

    # ── Aislamientos entre edificaciones (Anexo 5 D.466/2024 §1.10.b) ─────────
    trace.append({
        "paso": N(),
        "descripcion": "Aislamiento entre edificaciones (Anexo 5 D.466/2024 §1.10.b)",
        "expresion": f"{_RU_AISL_ENTRE_FORMULA};  mín {_RU_AISL_ENTRE_MIN_M} m",
        "valores": {"formula": _RU_AISL_ENTRE_FORMULA, "minimo_m": _RU_AISL_ENTRE_MIN_M},
        "resultado": None, "unidad": "m",
        "fuente": "Anexo 5 Decreto Distrital 466 de 2024, SECCIÓN 1.10.b",
        "nota": "Aplica a edificaciones del mismo proyecto en distintos lotes. Mismo estándar que Consolidación.",
    })
    metrics["aislamiento_entre_edificaciones"] = {
        "formula": _RU_AISL_ENTRE_FORMULA,
        "minimo_m": _RU_AISL_ENTRE_MIN_M,
        "confianza": "alta",
        "fuente": "Anexo 5 D.466/2024, SECCIÓN 1.10.b",
    }

    return metrics, "total_area"


# ── MEJORAMIENTO INTEGRAL calculator ─────────────────────────────────────────
#
# Art. 338 DD 555/2021: altura máxima en tabla (ancho_via × área_lote).
# IC e IO son resultantes. Condición adicional: relación 2:1 (altura:ancho_vía).
#
# Tabla reconstruida desde OCR del PDF — marcada con confianza="media".
# Filas = ancho_vía; Columnas = área_terreno (m²).
_MI_ALTURA_TABLA = [
    #  lim_via_inf, lim_via_sup, pisos_por_area_col[<240, 240-800, 800-2000, >2000]
    (0,  12, [3, 3, 3, 3]),
    (12, 22, [5, 5, 5, 5]),
    (22, None, [5, 8, 12, 12]),
]

_MI_AREA_UMBRALES = [240, 800, 2000]  # upper bounds for cols 0-2; col 3 is > 2000

_MI_CONDICION_ENGLOBE = (
    "Para alturas de 8 o 12 pisos: se debe cumplir englobe o integración predial "
    "para la totalidad del frente de la manzana, excluyendo predios con edificaciones "
    "existentes de más de 5 pisos (Art. 338 Decreto 555/2021)."
)

_MI_CONDICION_2_1 = (
    "Relación 2:1 obligatoria: altura_edificio_m ≤ 2 × ancho_via_m. "
    "Si la altura máxima de tabla supera el doble del perfil vial, "
    "la fachada debe retroceder desde el tercer piso (máximo un retroceso) "
    "hasta cumplir la relación 2:1 (Art. 338 Decreto 555/2021)."
)

_MI_IO_IC_NULL = (
    "IC e IO no están fijados numéricamente para Mejoramiento Integral. "
    "Son resultantes de la altura máxima permitida y las normas volumétricas del Anexo 5."
)

_MI_TABLA_ADVERTENCIA = (
    "Tabla reconstruida desde texto extraído por OCR del PDF del Decreto 555/2021. "
    "Confianza: media. Verificar contra el texto oficial impreso antes de usarla en licencia."
)

# Aislamientos posteriores MI (Anexo 5 D.466/2024, SECCIÓN 4.2.b)
# Table by pisos (not metres). Source: OCR-reconstructed from PDF page 88.
_MI_AISL_POST_TABLA = [
    #  pisos_hasta (inclusive) → aislamiento_min_m (None = no se exige)
    (3,   None),
    (6,   5),
    (9,   6),
    (12,  8),
]


def _mi_lookup_aisl_post(pisos: int) -> int | None:
    """Return minimum posterior setback (m) for MI, by pisos. None = no se exige."""
    for pisos_hasta, valor in _MI_AISL_POST_TABLA:
        if pisos <= pisos_hasta:
            return valor
    return 8  # safe fallback: capped at 12 pisos in MI


def _mi_lookup_altura(lot_area: float, ancho_via_m: float) -> tuple[int, str]:
    """
    Return (pisos_max, label_row) from the Art. 338 table.
    ancho_via_m and lot_area must be provided (validated before calling).
    """
    col = 3
    for i, umb in enumerate(_MI_AREA_UMBRALES):
        if lot_area < umb:
            col = i
            break

    for lim_inf, lim_sup, alturas in _MI_ALTURA_TABLA:
        if lim_sup is None or ancho_via_m < lim_sup:
            if ancho_via_m >= lim_inf:
                label = (
                    f"<{lim_sup}m" if lim_inf == 0 else
                    f"≥{lim_inf}m" if lim_sup is None else
                    f"≥{lim_inf}m y <{lim_sup}m"
                )
                return alturas[col], label

    return 3, "<12m"  # safe fallback


def _calc_mejoramiento_integral(
    lookup: dict,
    anu: float,
    trace: list,
    N,
    ancho_via_m: float | None,
) -> tuple[dict, str]:
    lot_area = lookup["lote"]["area_m2"]["valor"]
    metrics: dict = {}

    # IC e IO: resultantes
    trace.append({
        "paso": N(),
        "descripcion": "IC e IO — resultantes (Mejoramiento Integral)",
        "expresion": "IC = IO = f(altura_máx, aislamientos)",
        "valores": {"área_lote_m2": lot_area},
        "resultado": None, "unidad": "—",
        "fuente": "Artículo 338, Decreto Distrital 555 de 2021",
        "nota": _MI_IO_IC_NULL,
    })
    metrics["area_construible_max_m2"] = {"valor": None, "confianza": "media", "nota": _MI_IO_IC_NULL}
    metrics["planta_maxima_m2"] = {"valor": None, "confianza": "media", "nota": _MI_IO_IC_NULL}

    # Altura: requiere ancho_via_m
    if ancho_via_m is None:
        raise InputRequired(
            "Mejoramiento Integral: la altura máxima se determina con la tabla del Art. 338, "
            "que requiere el ancho de vía frente al predio. "
            "Proporcione ancho_via_m para obtener la altura permitida."
        )

    pisos, via_label = _mi_lookup_altura(lot_area, ancho_via_m)

    # Determine área column label for trace
    area_label = (
        f"< {_MI_AREA_UMBRALES[0]} m²" if lot_area < _MI_AREA_UMBRALES[0] else
        f"≥ {_MI_AREA_UMBRALES[0]} y < {_MI_AREA_UMBRALES[1]} m²" if lot_area < _MI_AREA_UMBRALES[1] else
        f"≥ {_MI_AREA_UMBRALES[1]} y ≤ {_MI_AREA_UMBRALES[2]} m²" if lot_area <= _MI_AREA_UMBRALES[2] else
        f"> {_MI_AREA_UMBRALES[2]} m²"
    )

    trace.append(_step(
        N(), "Altura máxima — tabla Art. 338 (ancho vía × área terreno)",
        "tabla_MI[ancho_via][area_terreno]",
        {
            "ancho_via_m": ancho_via_m,
            "área_lote_m2": lot_area,
            "columna_ancho_vía": via_label,
            "columna_área_terreno": area_label,
        },
        pisos, "pisos",
        fuente="Artículo 338, Decreto Distrital 555 de 2021",
        nota=_MI_TABLA_ADVERTENCIA,
    ))
    metrics["altura_maxima_pisos"] = {
        "valor": pisos,
        "confianza": "media",
        "nota": _MI_TABLA_ADVERTENCIA,
        "ancho_via_m_usado": ancho_via_m,
        "area_lote_m2_usado": lot_area,
    }

    # 2:1 ratio check
    altura_m_aprox = pisos * 3.0
    ratio_ok = altura_m_aprox <= 2 * ancho_via_m
    trace.append({
        "paso": N(),
        "descripcion": "Verificación relación 2:1 altura:ancho_vía (Art. 338)",
        "expresion": "altura_m ≤ 2 × ancho_via_m",
        "valores": {
            "pisos_tabla": pisos,
            "altura_aprox_m": altura_m_aprox,
            "ancho_via_m": ancho_via_m,
            "2x_ancho_m": 2 * ancho_via_m,
        },
        "resultado": "CUMPLE" if ratio_ok else "RETROCESO REQUERIDO",
        "unidad": "—",
        "fuente": "Artículo 338, Decreto Distrital 555 de 2021",
        "nota": _MI_CONDICION_2_1 if not ratio_ok else
                f"La altura aprox. {altura_m_aprox:.0f}m ≤ 2 × {ancho_via_m}m = {2*ancho_via_m:.0f}m — cumple.",
    })
    if not ratio_ok:
        metrics["altura_maxima_pisos"]["condicion_retroceso"] = _MI_CONDICION_2_1

    # Englobe condition for > 5 pisos
    if pisos > 5:
        trace.append({
            "paso": N(),
            "descripcion": "Condición de englobe — alturas > 5 pisos (Art. 338)",
            "expresion": "englobe total del frente de manzana requerido",
            "valores": {"pisos": pisos},
            "resultado": "REQUERIDO",
            "unidad": "—",
            "fuente": "Artículo 338, Decreto Distrital 555 de 2021",
            "nota": _MI_CONDICION_ENGLOBE,
        })
        metrics["condicion_englobe_manzana"] = {
            "requerido": True,
            "nota": _MI_CONDICION_ENGLOBE,
        }
    else:
        metrics["condicion_englobe_manzana"] = {"requerido": False}

    # VIP obligation for lots >= 2000 m²
    if lot_area >= 2000:
        trace.append({
            "paso": N(),
            "descripcion": "Obligación VIP — predio ≥ 2.000 m² (Art. 338)",
            "expresion": "20% del área vendible destinada a VIP",
            "valores": {"área_lote_m2": lot_area},
            "resultado": 0.20,
            "unidad": "fracción área vendible",
            "fuente": "Artículo 338, Decreto Distrital 555 de 2021",
            "nota": "Excepción: si se vincula mínimo el 70% de los moradores al proyecto, no se exige el área VIP.",
        })
        metrics["obligacion_vip"] = {
            "aplica": True,
            "porcentaje_area_vendible": 0.20,
            "nota": "20% del área vendible destinada a VIP. Exento si vincula ≥70% de moradores.",
        }
    else:
        metrics["obligacion_vip"] = {"aplica": False}

    # Cesiones espacio público (Art. 338)
    if lot_area > 5000:
        cesion_pct = 0.30
        cesion_m2 = round(lot_area * cesion_pct, 1)
        trace.append(_step(
            N(), "Cesión espacio público — predio > 5.000 m² (Art. 338)",
            "área_lote × 30%",
            {"área_lote_m2": lot_area},
            cesion_m2, "m²",
            fuente="Artículo 338, Decreto Distrital 555 de 2021",
            nota="Predio > 5.000 m²: cesión obligatoria del 30% del área del lote como espacio público.",
        ))
        metrics["cesion_espacio_publico_m2"] = {
            "valor": cesion_m2,
            "porcentaje": cesion_pct,
            "confianza": "alta",
        }
    elif lot_area > 2000:
        cesion_pct = 0.25
        cesion_m2 = round(lot_area * cesion_pct, 1)
        trace.append(_step(
            N(), "Cesión espacio público — predio > 2.000 m² y ≤ 5.000 m² (Art. 338)",
            "área_lote × 25%",
            {"área_lote_m2": lot_area},
            cesion_m2, "m²",
            fuente="Artículo 338, Decreto Distrital 555 de 2021",
            nota="Predio > 2.000 m² y ≤ 5.000 m²: cesión obligatoria del 25% del área del lote como espacio público.",
        ))
        metrics["cesion_espacio_publico_m2"] = {
            "valor": cesion_m2,
            "porcentaje": cesion_pct,
            "confianza": "alta",
        }
    else:
        metrics["cesion_espacio_publico_m2"] = {"valor": None, "porcentaje": None, "confianza": "alta",
                                                 "nota": "Predio ≤ 2.000 m²: no se exige cesión de espacio público."}

    # Aislamientos posteriores (Anexo 5 D.466/2024, SECCIÓN 4.2.b)
    aisl_post_m = _mi_lookup_aisl_post(pisos)
    if aisl_post_m is None:
        trace.append({
            "paso": N(),
            "descripcion": "Aislamiento posterior MI — ≤ 3 pisos: no se exige (SECCIÓN 4.2.b)",
            "expresion": "pisos ≤ 3 → No se exige",
            "valores": {"pisos": pisos},
            "resultado": "No se exige",
            "unidad": "—",
            "fuente": "Anexo 5 D.466/2024, SECCIÓN 4.2.b",
            "nota": "Para uso residencial: garantizar habitabilidad mediante patios (área mín. 6 m², lado mín. 2 m).",
        })
        metrics["aislamiento_posterior_m"] = {
            "valor": None,
            "confianza": "alta",
            "nota": "≤ 3 pisos: no se exige aislamiento posterior. Aplican condiciones de patio habitabilidad.",
        }
    else:
        trace.append(_step(
            N(), f"Aislamiento posterior MI — {pisos} pisos → {aisl_post_m} m (SECCIÓN 4.2.b)",
            "tabla_MI_aisl_post[pisos]",
            {"pisos": pisos},
            aisl_post_m, "m",
            fuente="Anexo 5 D.466/2024, SECCIÓN 4.2.b",
            nota="Aislamiento exigido desde nivel de terreno (o placa superior de semisótano, o nivel de empate). "
                 "Tabla OCR-reconstruida — verificar texto oficial.",
        ))
        metrics["aislamiento_posterior_m"] = {
            "valor": aisl_post_m,
            "confianza": "media",
            "nota": "Tabla por pisos (Anexo 5 D.466/2024, SECCIÓN 4.2.b). Exento en: esquineros/medianeros con lindero posterior=lateral vecino; fondo ≤ 8 m; geometría irregular.",
        }

    # Aislamientos laterales: >5 pisos → exigido desde placa superior piso 3 (SECCIÓN 4.3.a)
    if pisos > 5:
        trace.append({
            "paso": N(),
            "descripcion": f"Aislamiento lateral MI — {pisos} pisos > 5: exigido desde placa superior piso 3 (SECCIÓN 4.3.a)",
            "expresion": "pisos > 5 → aislamiento lateral desde placa_sup_piso_3",
            "valores": {"pisos": pisos},
            "resultado": "REQUERIDO desde placa superior del tercer piso",
            "unidad": "—",
            "fuente": "Anexo 5 D.466/2024, SECCIÓN 4.3.a",
            "nota": "Dimensión = 1/5 × altura_m (fórmula general Anexo 5 SECCIÓN 1.9.b), mín. 3 m.",
        })
        metrics["aislamiento_lateral_nivel_exigencia"] = {
            "nivel": "placa_superior_piso_3",
            "minimo_m": 3,
            "confianza": "alta",
        }
    else:
        metrics["aislamiento_lateral_nivel_exigencia"] = {
            "nivel": "no_aplica_regla_especial",
            "minimo_m": 3,
            "confianza": "alta",
            "nota": "≤ 5 pisos: no aplica exigencia de aislamiento lateral (normas comunes aplican).",
        }

    trace.append({
        "paso": N(),
        "descripcion": "Restricción dominante — Mejoramiento Integral",
        "expresion": "binding = height (altura de tabla Art. 338 es el control)",
        "valores": {"pisos_max": pisos, "ancho_via_m": ancho_via_m, "area_lote_m2": lot_area},
        "resultado": "height",
        "nota": "La altura máxima en pisos (tabla Art. 338) es el control fijo. IC e IO son resultantes.",
    })

    return metrics, "height"


# ── CONSERVACIÓN calculator ───────────────────────────────────────────────────
#
# Conservación aplica a BIC (Bienes de Interés Cultural). Las normas de
# edificabilidad (IC, IO, altura) son per-lote y dependen de: (1) ficha de
# valoración individual (IDPC/SDCRD), (2) nivel de intervención 1–4 (DUR 1080/
# 2015), (3) PEMP del sector si existe, (4) notas en mapas CU-5.4.2-CU-5.4.33
# para nivel 4. No hay valores globales codificables.
#
# p2_lookup queries Layers 0, 1, 3, 12 to identify the specific declaratoria.
# Even with that identification, numeric norms remain per-predio at IDPC.
#
_CONSERVACION_NOTA = (
    "Las normas de IC, IO, altura y aislamientos en Conservación son per-lote; dependen de: "
    "(1) ficha de valoración BIC individual (IDPC/SDCRD, Art. 345 D.555/2021); "
    "(2) nivel de intervención 1–4 (DUR 1080/2015); "
    "(3) el PEMP del sector cuando existe (Art. 348 § 1, prevalece sobre el POT); "
    "(4) notas en mapas CU-5.4.2 a CU-5.4.33 para predios Nivel 4 (Art. 348 § 2). "
    "Contactar: Instituto Distrital de Patrimonio Cultural (IDPC) — www.idpc.gov.co"
)


def _conservacion_declaratoria_desc(pat: dict) -> str:
    """Build a human-readable description of the heritage constraint from GIS data."""
    bic = pat.get("bic")
    pemp = pat.get("pemp")
    pemp_ch = pat.get("pemp_ch", False)
    sic = pat.get("sic")

    parts: list[str] = []

    if bic:
        ambito = (
            "BIC de ámbito nacional" if bic.get("tipo_bien") == 1
            else "BIC de ámbito distrital" if bic.get("tipo_bien") == 4
            else "Bien de Interés Cultural"
        )
        n = bic.get("nombre")
        nombre = (' “' + n + '”') if n else ""
        cat = bic.get("categoria_descripcion") or bic.get("categoria") or ""
        acto = (" (" + bic["acto_administrativo"] + ")") if bic.get("acto_administrativo") else ""
        ficha = (" — ficha IDPC: " + bic["numero_ficha"]) if bic.get("numero_ficha") else ""
        parts.append(ambito + nombre + " — " + cat + acto + ficha)

    if pemp:
        n = pemp.get("nombre")
        nombre = (' “' + n + '”') if n else ""
        acto = (" (" + pemp["acto_administrativo"] + ")") if pemp.get("acto_administrativo") else ""
        parts.append("PEMP" + nombre + acto + " — normas del PEMP prevalecen sobre POT (Art. 348 § 1 D.555/2021)")
    elif pemp_ch:
        parts.append(
            "PEMP Centro Histórico de Bogotá (Decreto Distrital 678 de 1994 y modificaciones) "
            "— normas del PEMP prevalecen sobre POT (Art. 348 § 1 D.555/2021)"
        )

    if sic:
        n = sic.get("nombre")
        nombre = (' “' + n + '”') if n else ""
        parts.append(
            "Sector de Interés Cultural" + nombre + " (Art. 343 D.555/2021) "
            "— normas en mapas CU-5.4.2 a CU-5.4.33 (Art. 348 § 2)"
        )

    if not parts:
        return "Tratamiento CONSERVACIÓN — declaratoria no identificada en capas GIS (consultar IDPC)"
    return "; ".join(parts)


def _calc_conservacion(lookup: dict, trace: list, N) -> tuple[dict | None, str]:
    pat = lookup.get("conservacion_patrimonio") or {}
    desc = _conservacion_declaratoria_desc(pat)

    trace.append({
        "paso": N(),
        "descripcion": "Tratamiento CONSERVACIÓN — identificación de declaratoria patrimonial",
        "expresion": "conservacion_no_soportado",
        "valores": {
            "bic": pat.get("bic"),
            "pemp": pat.get("pemp"),
            "pemp_ch": pat.get("pemp_ch", False),
            "sic": pat.get("sic"),
        },
        "resultado": desc,
        "unidad": "—",
        "fuente": (
            "Artículos 343–359, Decreto Distrital 555 de 2021; "
            "Layer 12 (BIC), Layer 1 (PEMP), Layer 3 (PEMP Centro Histórico), Layer 0 (SIC)"
        ),
        "nota": _CONSERVACION_NOTA,
    })
    return None, "conservacion_no_soportado"


# ── CLI / test runner ─────────────────────────────────────────────────────────

_CALC_TESTS = [
    # (description, lng, lat, vis, anu_m2, frente_m, ancho_via_m, expect_error)
    ("Usaquén — Consolidación continua 9p",                -74.0525, 4.6750, False, None,   None, None, None),
    # AAERVIS gate: Usaquén is in AAERVIS — with vis=True the VIS×2 bonus should activate
    ("Usaquén — Consolidación 9p + VIS×2 bonus (AAERVIS)", -74.0525, 4.6750, True,  None,   None, None, None),
    ("Usaquén norte — Consolidación aislada 6p",           -74.0445, 4.6980, False, None,   None, None, None),
    # AAERVIS gate: Bosa norte is in AAPRSU (not AAERVIS) — VIS×2 bonus must NOT activate
    ("Bosa norte — Consolidación 3p, AAPRSU, VIS×2 null", -74.1800, 4.6300, True,  None,   None, None, None),
    ("Usme — Desarrollo 4D, lote 190k m² (>10k)",         -74.1100, 4.5200, True,  None,   None, None, InputRequired),
    ("Usme — Desarrollo 4D con ANU explícito",             -74.1100, 4.5200, True,  8_500,  None, None, None),
    ("Quinta Camacho — rango 9-14 aislada, sin frente",   -74.0550, 4.6540, False, None,   None, None, InputRequired),
    ("Quinta Camacho — rango 9-14 aislada, cumple",       -74.0550, 4.6540, False, None,   26,   24,   None),
    ("Río Bogotá — debe fallar en lookup",                 -74.1750, 4.6580, False, None,   None, None, ZeroFeaturesError),
]


def run_tests() -> None:
    sep = "=" * 72
    print(sep)
    print("calc.py — Test suite")
    print(sep)

    passed = failed = 0

    for desc, lng, lat, vis, anu_override, frente_override, ancho_via_override, expected_err in _CALC_TESTS:
        print(f"\n{'─' * 60}")
        print(f"TEST : {desc}")
        print(f"COORD: ({lat:.4f}, {lng:.4f})  vis={vis}  anu_m2={anu_override}  frente_m={frente_override}  ancho_via_m={ancho_via_override}")
        if expected_err:
            print(f"EXPECT: {expected_err.__name__}")
        print("─" * 60)

        try:
            lu = p2_lookup.lookup(lng, lat, vis_en_sitio=vis)
            res = calculate(lu, anu_m2=anu_override, frente_m=frente_override, ancho_via_m=ancho_via_override)
            if expected_err is not None:
                print(f"FAIL — expected {expected_err.__name__} but succeeded")
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
        except InputRequired as exc:
            if expected_err is InputRequired:
                print(f"PASS (expected InputRequired)\n  → {exc}")
                passed += 1
            else:
                print(f"FAIL (unexpected InputRequired)\n  → {exc}")
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
            import traceback; traceback.print_exc()
            failed += 1

    print(f"\n{sep}")
    print(f"Results: {passed}/{len(_CALC_TESTS)} passed, {failed} failed")
    print(sep)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        run_tests()
    elif len(sys.argv) >= 3:
        _lng = float(sys.argv[1])
        _lat = float(sys.argv[2])
        _vis = (sys.argv[3].lower() in ("true","1","yes","si")) if len(sys.argv) > 3 else False
        _anu = float(sys.argv[4]) if len(sys.argv) > 4 else None
        _lu = p2_lookup.lookup(_lng, _lat, vis_en_sitio=_vis)
        _res = calculate(_lu, anu_m2=_anu)
        print(json.dumps(_res, indent=2, ensure_ascii=False))
    else:
        print("Usage:")
        print("  python3 calc.py                              # test suite")
        print("  python3 calc.py LNG LAT [vis] [anu_m2]      # single lookup")
        sys.exit(1)
