"""
cabida/proforma.py — Bogotá residential land-residual pro-forma.

Pure deterministic arithmetic.  No network calls, no LLM inference.
Every output value traces back to an explicit formula step.

Model
-----
  area_vendible      = area × eficiencia_vendible_pct
  ingresos_totales   = area_vendible × precio_venta
  costos_duros       = area × costo_construccion        ← gross area (build everything)
  costos_blandos     = costos_duros × costos_blandos_pct
  costos_totales     = costos_duros + costos_blandos
  utilidad_objetivo  = ingresos_totales × margen_objetivo_pct
  valor_residual     = ingresos_totales − costos_totales − utilidad_objetivo

Usage
-----
    from cabida.proforma import ProformaInput, Param, ProformaBlocked, run

    inp = ProformaInput.from_calc(
        calc_result=res,          # dict from calc.calculate()
        lookup_snapshot=lu,       # dict from p2_lookup.lookup() — resolves localidad
        precio_lote_cop=800_000_000,
    )
    result = run(inp)
    print(result.veredicto_frase)
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── Exception ──────────────────────────────────────────────────────────────────

class ProformaBlocked(Exception):
    """
    Raised when area_construible_m2 is None.

    The proforma cannot produce any output without a defined buildable area.
    Resolve the binding constraint in calc.py first.  For Consolidación lots
    this requires full geometric modelling of the lot (setbacks, footprint,
    typology); the IC is always resultante and area_construible_max_m2 is always
    None until that modelling is done.
    """


# ── Param — value + origin tag ─────────────────────────────────────────────────

@dataclass
class Param:
    """A numeric input together with its provenance."""
    valor: float
    origen: str    # "default" | "usuario"

    def __post_init__(self) -> None:
        if self.origen not in ("default", "usuario"):
            raise ValueError(
                f"Param.origen must be 'default' or 'usuario', got {self.origen!r}"
            )
        if not isinstance(self.valor, (int, float)):
            raise TypeError(f"Param.valor must be numeric, got {type(self.valor)!r}")


# ── Input dataclass ────────────────────────────────────────────────────────────

@dataclass
class ProformaInput:
    """
    All inputs to the land-residual pro-forma.

    area_construible_m2
        Gross buildable area from calc.py (metrics["area_construible_max_m2"]["valor"]).
        REQUIRED — None raises ProformaBlocked when run() is called.  Never pass
        an estimate here; if calc.py returns None the pro-forma is blocked until
        the design resolves it.

    precio_lote_cop
        Optional total land price in COP.  When supplied, run() compares it to
        the residual land value and populates veredicto + veredicto_frase.
    """
    area_construible_m2: float | None
    precio_venta_cop_m2: Param
    costo_construccion_cop_m2: Param
    eficiencia_vendible_pct: Param
    costos_blandos_pct: Param
    margen_objetivo_pct: Param
    precio_lote_cop: float | None = None

    @classmethod
    def from_calc(
        cls,
        calc_result: dict,
        lookup_snapshot: dict | None = None,
        *,
        precio_lote_cop: float | None = None,
        precio_venta_cop_m2: float | None = None,
        costo_construccion_cop_m2: float | None = None,
        eficiencia_vendible_pct: float | None = None,
        costos_blandos_pct: float | None = None,
        margen_objetivo_pct: float | None = None,
    ) -> "ProformaInput":
        """
        Build a ProformaInput from calc.calculate() output and market defaults.

        Any kwarg supplied overrides the market default for that field and is
        tagged origen='usuario'; omitted kwargs use market_defaults.py values
        and are tagged origen='default'.

        lookup_snapshot is used to resolve the localidad-specific sale price
        default via market_defaults.get_sale_price_default().
        """
        from .market_defaults import MARKET_DEFAULTS, get_sale_price_default

        md = MARKET_DEFAULTS

        # area_construible_m2 — directly from calc output
        area_val = (
            (calc_result.get("metrics") or {})
            .get("area_construible_max_m2", {})
            .get("valor")
        )

        # precio_venta: resolve localidad-specific default when lookup provided
        if precio_venta_cop_m2 is not None:
            pv = Param(valor=float(precio_venta_cop_m2), origen="usuario")
        else:
            resolved_pv: float | None = None
            if lookup_snapshot is not None:
                _lng = lookup_snapshot.get("input", {}).get("lng")
                _lat = lookup_snapshot.get("input", {}).get("lat")
                if _lng is not None and _lat is not None:
                    sale_rec = get_sale_price_default(_lng, _lat)
                    resolved_pv = sale_rec.get("valor")  # None when no citable data
            if resolved_pv is None:
                raise ProformaBlocked(
                    "Sin precio de venta de referencia para esta localidad. "
                    "Ingrese el precio de venta por m² manualmente."
                )
            pv = Param(valor=float(resolved_pv), origen="default")

        # costo_construccion: default to no_vis_estandar
        if costo_construccion_cop_m2 is not None:
            cc = Param(valor=float(costo_construccion_cop_m2), origen="usuario")
        else:
            cc = Param(
                valor=float(md["costos_construccion_cop_m2"]["no_vis_estandar"]["valor"]),
                origen="default",
            )

        if eficiencia_vendible_pct is not None:
            ef = Param(valor=float(eficiencia_vendible_pct), origen="usuario")
        else:
            ef = Param(valor=float(md["eficiencia_vendible_pct"]["valor"]), origen="default")

        if costos_blandos_pct is not None:
            cb = Param(valor=float(costos_blandos_pct), origen="usuario")
        else:
            cb = Param(valor=float(md["costos_blandos_pct"]["valor"]), origen="default")

        if margen_objetivo_pct is not None:
            mg = Param(valor=float(margen_objetivo_pct), origen="usuario")
        else:
            mg = Param(valor=float(md["margen_objetivo_pct"]["valor"]), origen="default")

        return cls(
            area_construible_m2=area_val,
            precio_venta_cop_m2=pv,
            costo_construccion_cop_m2=cc,
            eficiencia_vendible_pct=ef,
            costos_blandos_pct=cb,
            margen_objetivo_pct=mg,
            precio_lote_cop=precio_lote_cop,
        )


# ── Output dataclass ───────────────────────────────────────────────────────────

@dataclass
class ProformaResult:
    # Revenue
    ingresos_totales_cop: int
    # Construction costs (land excluded)
    costos_duros_cop: int
    costos_blandos_cop: int
    costos_totales_cop: int         # costos_duros + costos_blandos
    # Developer target margin
    utilidad_objetivo_cop: int
    # Residual land value (can be negative when project is unviable)
    valor_residual_lote_cop: int
    # Land comparison — None when precio_lote_cop not supplied
    diferencia_cop: int | None
    diferencia_pct: float | None    # (precio_lote − VRL) / |VRL|; None when VRL == 0
    veredicto: str | None           # "sobrepago" | "margen" | "en_linea"
    veredicto_frase: str | None     # Spanish, one sentence
    # Audit trail
    formula_trace: list
    # Resolved inputs (for PDF / UI display)
    inputs_echo: dict


# ── Step helper (same shape as calc.py) ───────────────────────────────────────

def _step(
    n: int,
    desc: str,
    expr: str,
    values: dict,
    result,
    unit: str = "COP",
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


# ── Veredicto threshold ────────────────────────────────────────────────────────

_VEREDICTO_THRESHOLD = 0.10   # ±10 % tolerance band around residual land value


# ── Main entry point ───────────────────────────────────────────────────────────

def run(inp: ProformaInput) -> ProformaResult:
    """
    Execute the land-residual pro-forma.

    Raises ProformaBlocked when inp.area_construible_m2 is None.
    All arithmetic uses float internally; COP outputs are rounded to int.
    """
    if inp.area_construible_m2 is None:
        raise ProformaBlocked(
            "area_construible_m2 es None — el pro-forma no puede ejecutarse sin un área "
            "construible definida. Resuelva la restricción regulatoria en calc.py primero. "
            "Para tratamientos Consolidación el área construible requiere modelado geométrico "
            "completo del predio (aislamiento posterior, retroceso de fachada, tipología); "
            "IC y IO son siempre resultantes en Consolidación (Art. 310 Num. 1 D.555/2021)."
        )

    area = float(inp.area_construible_m2)
    pv   = float(inp.precio_venta_cop_m2.valor)
    cc   = float(inp.costo_construccion_cop_m2.valor)
    ef   = float(inp.eficiencia_vendible_pct.valor)
    cb   = float(inp.costos_blandos_pct.valor)
    mg   = float(inp.margen_objetivo_pct.valor)

    trace: list[dict] = []
    _n = [0]

    def N() -> int:
        _n[0] += 1
        return _n[0]

    # ── Paso 1: Parámetros de entrada ─────────────────────────────────────────
    trace.append({
        "paso": N(),
        "descripcion": "Parámetros de entrada",
        "expresion": "—",
        "valores": {
            "area_construible_m2": area,
            "precio_venta_cop_m2":         {"valor": pv, "origen": inp.precio_venta_cop_m2.origen},
            "costo_construccion_cop_m2":   {"valor": cc, "origen": inp.costo_construccion_cop_m2.origen},
            "eficiencia_vendible_pct":     {"valor": ef, "origen": inp.eficiencia_vendible_pct.origen},
            "costos_blandos_pct":          {"valor": cb, "origen": inp.costos_blandos_pct.origen},
            "margen_objetivo_pct":         {"valor": mg, "origen": inp.margen_objetivo_pct.origen},
            "precio_lote_cop": inp.precio_lote_cop,
        },
        "resultado": None,
        "unidad": "—",
        "nota": (
            "origen='default' = valor de market_defaults.py; "
            "origen='usuario' = valor ingresado manualmente."
        ),
    })

    # ── Paso 2: Ingresos totales ───────────────────────────────────────────────
    area_vendible = round(area * ef, 1)
    ingresos = int(round(area_vendible * pv))
    trace.append(_step(
        N(), "Ingresos totales de ventas",
        "área_construible_m2 × eficiencia_vendible_pct × precio_venta_cop_m2",
        {
            "area_construible_m2":        area,
            "eficiencia_vendible_pct":    {"valor": ef, "origen": inp.eficiencia_vendible_pct.origen},
            "area_vendible_m2":           area_vendible,
            "precio_venta_cop_m2":        pv,
            "precio_venta_origen":        inp.precio_venta_cop_m2.origen,
        },
        ingresos,
        nota=(
            f"Área vendible = {area:,.1f} m² × {ef*100:.0f}% eficiencia = {area_vendible:,.1f} m². "
            "La eficiencia descuenta pasillos, hall de ascensores, escaleras y cuartos técnicos. "
            "Rango típico torres residenciales Bogotá: 75-90%."
        ),
    ))

    # ── Paso 3: Costos duros ───────────────────────────────────────────────────
    costos_duros = int(round(area * cc))
    trace.append(_step(
        N(), "Costos duros de construcción",
        "área_construible_m2 × costo_construccion_cop_m2",
        {
            "area_construible_m2":           area,
            "costo_construccion_cop_m2":     cc,
            "costo_construccion_origen":     inp.costo_construccion_cop_m2.origen,
        },
        costos_duros,
        nota=(
            "Costo directo de obra incluyendo AIU del contratista "
            "(Administración + Imprevistos + Utilidad). "
            "Excluye terreno y costos blandos del promotor."
        ),
    ))

    # ── Paso 4: Costos blandos ─────────────────────────────────────────────────
    costos_blandos = int(round(costos_duros * cb))
    trace.append(_step(
        N(), "Costos blandos del promotor",
        "costos_duros_cop × costos_blandos_pct",
        {
            "costos_duros_cop":      costos_duros,
            "costos_blandos_pct":    cb,
            "costos_blandos_origen": inp.costos_blandos_pct.origen,
        },
        costos_blandos,
        nota=(
            "Diseño y estudios (2–4%), licencias y permisos (1–2%), "
            "fiducia y honorarios legales (1–2%), comisiones de ventas (3–5%), "
            "gerencia integral (3–5%), contingencias (2–4%). "
            "Porcentaje aplicado sobre el costo duro de construcción."
        ),
    ))

    # ── Paso 5: Costos totales ─────────────────────────────────────────────────
    costos_totales = costos_duros + costos_blandos
    trace.append(_step(
        N(), "Costos totales de construcción (sin terreno)",
        "costos_duros_cop + costos_blandos_cop",
        {
            "costos_duros_cop":   costos_duros,
            "costos_blandos_cop": costos_blandos,
        },
        costos_totales,
        nota="Terreno no incluido — es la incógnita del modelo residual.",
    ))

    # ── Paso 6: Utilidad objetivo ──────────────────────────────────────────────
    utilidad = int(round(ingresos * mg))
    trace.append(_step(
        N(), "Utilidad objetivo del promotor",
        "ingresos_totales_cop × margen_objetivo_pct",
        {
            "ingresos_totales_cop": ingresos,
            "margen_objetivo_pct":  mg,
            "margen_origen":        inp.margen_objetivo_pct.origen,
        },
        utilidad,
        nota=(
            "Margen calculado sobre ingresos totales (no sobre costos) — "
            "convención de la industria colombiana. "
            "VIS: usar 12–18%. No-VIS estándar: 15–22%. No-VIS premium: 18–30%."
        ),
    ))

    # ── Paso 7: Valor residual del lote ───────────────────────────────────────
    rlv = ingresos - costos_totales - utilidad
    trace.append(_step(
        N(), "Valor residual del lote (VRL)",
        "ingresos_totales − costos_totales − utilidad_objetivo",
        {
            "ingresos_totales_cop": ingresos,
            "costos_totales_cop":   costos_totales,
            "utilidad_objetivo_cop": utilidad,
        },
        rlv,
        nota=(
            "Precio máximo de adquisición del terreno compatible con la viabilidad del proyecto, "
            "dados los supuestos de precio de venta, costos y margen objetivo. "
            "Valor negativo indica inviabilidad: el proyecto no cubriría ni sus costos de "
            "construcción y margen, independientemente del precio del terreno."
            if rlv < 0 else
            "Precio máximo de adquisición del terreno compatible con la viabilidad del proyecto, "
            "dados los supuestos de precio de venta, costos y margen objetivo."
        ),
    ))

    # ── Paso 8 (condicional): Comparación precio lote vs VRL ──────────────────
    diferencia_cop: int | None   = None
    diferencia_pct: float | None = None
    veredicto:      str | None   = None
    frase:          str | None   = None

    if inp.precio_lote_cop is not None:
        precio_lote = float(inp.precio_lote_cop)
        diferencia_cop = int(round(precio_lote - rlv))

        if rlv == 0:
            diferencia_pct = None
            veredicto = "sobrepago" if precio_lote > 0 else "en_linea"
        else:
            diferencia_pct = round(diferencia_cop / abs(rlv), 6)
            if rlv < 0:
                # Project is unviable before land cost; any land price deepens the loss
                veredicto = "sobrepago"
            elif diferencia_pct > _VEREDICTO_THRESHOLD:
                veredicto = "sobrepago"
            elif diferencia_pct < -_VEREDICTO_THRESHOLD:
                veredicto = "margen"
            else:
                veredicto = "en_linea"

        frase = _veredicto_frase(veredicto, precio_lote, rlv, diferencia_cop, diferencia_pct)

        trace.append(_step(
            N(), "Comparación precio lote vs valor residual",
            "diferencia = precio_lote − VRL;   diferencia_pct = diferencia / |VRL|",
            {
                "precio_lote_cop":        int(round(precio_lote)),
                "valor_residual_lote_cop": rlv,
                "diferencia_cop":          diferencia_cop,
                "diferencia_pct":          diferencia_pct,
                "umbral_pct":              _VEREDICTO_THRESHOLD,
            },
            veredicto,
            unit="—",
            nota=frase,
        ))

    inputs_echo = {
        "area_construible_m2":       area,
        "area_vendible_m2":          area_vendible,
        "precio_venta_cop_m2":       {"valor": pv, "origen": inp.precio_venta_cop_m2.origen},
        "costo_construccion_cop_m2": {"valor": cc, "origen": inp.costo_construccion_cop_m2.origen},
        "eficiencia_vendible_pct":   {"valor": ef, "origen": inp.eficiencia_vendible_pct.origen},
        "costos_blandos_pct":        {"valor": cb, "origen": inp.costos_blandos_pct.origen},
        "margen_objetivo_pct":       {"valor": mg, "origen": inp.margen_objetivo_pct.origen},
        "precio_lote_cop": inp.precio_lote_cop,
    }

    return ProformaResult(
        ingresos_totales_cop     = ingresos,
        costos_duros_cop         = costos_duros,
        costos_blandos_cop       = costos_blandos,
        costos_totales_cop       = costos_totales,
        utilidad_objetivo_cop    = utilidad,
        valor_residual_lote_cop  = rlv,
        diferencia_cop           = diferencia_cop,
        diferencia_pct           = diferencia_pct,
        veredicto                = veredicto,
        veredicto_frase          = frase,
        formula_trace            = trace,
        inputs_echo              = inputs_echo,
    )


# ── Verdict phrase ─────────────────────────────────────────────────────────────

def _veredicto_frase(
    veredicto: str,
    precio_lote: float,
    rlv: int,
    diferencia: int,
    diferencia_pct: float | None,
) -> str:
    pct_str = (
        f"{abs(diferencia_pct) * 100:.1f}%"
        if diferencia_pct is not None
        else "—"
    )
    pl_fmt  = _fmt_cop(int(round(precio_lote)))
    rlv_fmt = _fmt_cop(rlv)
    gap_fmt = _fmt_cop(abs(diferencia))

    if rlv < 0:
        return (
            f"El proyecto no es viable a los costos y precios actuales "
            f"(valor residual negativo: {rlv_fmt} COP); "
            f"el precio del lote ofrecido ({pl_fmt} COP) profundiza el déficit."
        )
    if veredicto == "sobrepago":
        return (
            f"El precio del lote ({pl_fmt} COP) supera en {pct_str} el valor residual "
            f"máximo ({rlv_fmt} COP), comprometiendo la viabilidad del proyecto."
        )
    if veredicto == "margen":
        return (
            f"El precio del lote ({pl_fmt} COP) está {pct_str} por debajo del valor "
            f"residual máximo ({rlv_fmt} COP), dejando un margen de {gap_fmt} COP "
            f"respecto a los supuestos actuales."
        )
    # en_linea
    return (
        f"El precio del lote ({pl_fmt} COP) está alineado con el valor residual "
        f"estimado ({rlv_fmt} COP), con una diferencia de {pct_str}."
    )


def _fmt_cop(v: int) -> str:
    """Format a COP integer with Colombian dot-as-thousands-separator."""
    return f"{v:,}".replace(",", ".")
