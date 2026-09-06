"""
cabida/market_defaults.py — Bogotá residential construction & sale price benchmarks.

REFERENCE DATA ONLY — not a legal valuation, not a commitment, not a guarantee.
Every number here is a market estimate that can and will drift.  The caller
(feasibility engine, PDF report, UI) MUST surface the "nota_uso" string and the
staleness warning to the end-user at every touch-point.

Usage:
    from cabida.market_defaults import MARKET_DEFAULTS, get_sale_price_default, is_stale

    if is_stale():
        # show "Precios de referencia desactualizados — verifique."
        ...
    rec = get_sale_price_default(lng=-74.0525, lat=4.6750)
    # rec = {"localidad": "usaquen", "valor": 7_400_000, "rango": [...], ...}
"""
from __future__ import annotations

import unicodedata
from datetime import datetime

# ── Staleness gate ─────────────────────────────────────────────────────────────
_STALE_THRESHOLD_DAYS = 180   # 6 months


def is_stale() -> bool:
    """True when market_defaults data is >6 months old."""
    fecha = datetime.strptime(MARKET_DEFAULTS["fecha_actualizacion"], "%Y-%m")
    return (datetime.utcnow() - fecha).days > _STALE_THRESHOLD_DAYS


_STALENESS_WARNING = (
    "⚠ Precios de referencia desactualizados — verifique con fuentes de mercado recientes."
)

# ── Main data table ────────────────────────────────────────────────────────────

MARKET_DEFAULTS: dict = {
    "version": "2026-Q3",

    "nota_uso": (
        "REFERENCIA DE MERCADO — no sustituye un estudio de mercado profesional ni un "
        "avalúo comercial. Los valores son estimados basados en fuentes públicas y de "
        "industria; la microubicación, el estrato, el estado del inmueble y las "
        "condiciones del mercado local pueden diferir significativamente de estos rangos. "
        "Actualice este archivo si la fecha_actualizacion supera 6 meses."
    ),

    "fuente_general": (
        "Galería Inmobiliaria (Colombia), Habi.co, Mubrick Inmobiliaria, "
        "Goodsyservices Consulting, Portafolio / Metrocuadrado, DANE ICOCED, "
        "Camacol, Banco de la República (IBR), vivienda.com.co, oneestimate.ai. "
        "Ver market_defaults_SOURCES.md para URLs y fechas completas."
    ),

    "fecha_actualizacion": "2026-08",

    # ── Construction hard costs ────────────────────────────────────────────────
    # Direct construction costs per built m².  Include contractor AIU
    # (Administración 10-15 % + Imprevistos 5-8 % + Utilidad 8-15 %).
    # EXCLUDE: land, developer soft costs (below), VAT on land.
    # Source: vivienda.com.co 2026, oneestimate.ai (updated 2026).
    "costos_construccion_cop_m2": {
        "vis": {
            "valor": 2_000_000,
            "rango": [1_800_000, 2_200_000],
            "fuente": (
                "vivienda.com.co 'Precio metro cuadrado construcción Colombia 2026'; "
                "oneestimate.ai 'Costos construcción vivienda Colombia 2024 (actualizado 2026)'"
            ),
            "fecha": "2026",
            "nota": (
                "Aplica a Vivienda de Interés Social (VIS, precio máx. 150 SMMLV) y VIP "
                "(precio máx. 90 SMMLV) en estrato 1-3. Materiales económicos, acabados "
                "básicos, sin áreas comunes premium. Incluye AIU del contratista."
            ),
        },
        "no_vis_estandar": {
            "valor": 2_800_000,
            "rango": [2_200_000, 3_500_000],
            "fuente": (
                "vivienda.com.co 'Precio metro cuadrado construcción Colombia 2026' — "
                "torres de apartamentos estándar; oneestimate.ai 2024/2026 estrato 4."
            ),
            "fecha": "2026",
            "nota": (
                "Corresponde a torres de apartamentos estrato 3-4 con materiales estándar "
                "y áreas comunes funcionales. Incluye AIU. Para casas unifamiliares el "
                "rango es $2.800.000–$4.200.000/m² por menor economía de escala."
            ),
        },
        "no_vis_alto": {
            "valor": 5_500_000,
            "rango": [4_500_000, 7_000_000],
            "fuente": (
                "vivienda.com.co 'Precio metro cuadrado construcción Colombia 2026' — "
                "vivienda premium; oneestimate.ai 2026 estrato 5-6."
            ),
            "fecha": "2026",
            "nota": (
                "Proyectos estrato 5-6 con acabados de alto estándar, materiales "
                "importados y diseño arquitectónico complejo. Incluye AIU. El límite "
                "superior ($7M+) aplica a proyectos boutique con lobbies curados, "
                "pisos de cerámica importada y automatización del hogar."
            ),
        },
    },

    # ── Sale prices by localidad ────────────────────────────────────────────────
    # Weighted-average asking/closing prices for residential apartments (new + used,
    # all stratas within each localidad), blended from multiple sources 2025-2026.
    # CRITICAL: each localidad is a massive heterogeneous territory.  Microubicación
    # (estrato, barrio, vintage, amenities) dominates over localidad averages.
    # Null = no reliable citable data found; override required.
    "precio_venta_cop_m2": {

        # ── North / Premium ──────────────────────────────────────────────────
        "usaquen": {
            "valor": 7_400_000,
            "rango": [4_800_000, 11_000_000],
            "fuente": (
                "Habi.co análisis interno 2025: mediana $5.275.229; "
                "Goodsyservices Consulting oct-2025: rango $5.275.229–$9.000.000; "
                "Mubrick Inmobiliaria 2026; Lonja de Bogotá oct-2025: La Cabrera "
                "$12.900.000/m² (límite superior del rango). Central ajustado al alza "
                "por datos 2026 vs mediana 2025."
            ),
            "fecha": "2025-2026",
            "nota": (
                "Gran dispersión interna: Santa Bárbara / La Cabrera / Chicó alcanzan "
                "$9M–$13M (estrato 6); Cedritos / San Patricio $4.8M–$7M (estrato 4). "
                "El valor central refleja mezcla típica de proyectos nuevos estrato 4-5."
            ),
        },

        "chapinero": {
            "valor": 8_800_000,
            "rango": [6_500_000, 13_000_000],
            "fuente": (
                "Mubrick Inmobiliaria 2026: $8.800.000; Habi.co 2025: mediana $7.000.000; "
                "Goodsyservices oct-2025: $7.800.000–$12.000.000+; "
                "Lonja de Bogotá oct-2025 (Metrocuadrado): El Retiro / La Cabrera "
                "$11.900.000–$12.900.000 (máximo histórico Bogotá)."
            ),
            "fecha": "2025-2026",
            "nota": (
                "Localidad más costosa de Bogotá. Rosales / El Retiro / Zona G: $10M–$13M. "
                "Chapinero Central / Galerías / Granada: $6.5M–$9M (estrato 3-4). "
                "La Macarena (borde con Santa Fe): $7M–$9M gentrificado."
            ),
        },

        "teusaquillo": {
            "valor": 6_500_000,
            "rango": [4_500_000, 9_000_000],
            "fuente": (
                "Habi.co 2025: mediana $5.322.580; "
                "Portafolio / Metrocuadrado 2024-2025: estrato medio $5.400.000; "
                "Mubrick Inmobiliaria 2026: $7.400.000; "
                "Mubrick lista Salitre (Ciudad Salitre Oriental, Teusaquillo): $8.700.000."
            ),
            "fecha": "2025-2026",
            "nota": (
                "Tendencia alcista fuerte 2024-2026 en Quinta Paredes, Palermo, Ciudad "
                "Salitre Oriental. Proyectos nuevos de alta densidad empujan el techo. "
                "El valor central refleja mezcla de producto nuevo y usado."
            ),
        },

        # ── Northwest ────────────────────────────────────────────────────────
        "suba": {
            "valor": 6_000_000,
            "rango": [3_500_000, 9_000_000],
            "fuente": (
                "Goodsyservices oct-2025: $5.400.000; "
                "Mubrick Inmobiliaria 2026: $6.400.000; "
                "Metrocuadrado (búsqueda): Usaquén/Suba favoritas, mezcla amplia de precios. "
                "Colina Campestre/Niza: $7M–$9M (estrato 5-6). Suba-Suba/Tibabuyes: "
                "$3.5M–$4.5M (estrato 2-3)."
            ),
            "fecha": "2025-2026",
            "nota": (
                "Localidad más poblada de Bogotá y muy heterogénea: desde VIS en Bilbao "
                "hasta projetos premium en Colina Campestre. Revisar por sector/UPZ."
            ),
        },

        "barrios_unidos": {
            "valor": 5_000_000,
            "rango": [3_800_000, 7_000_000],
            "fuente": (
                "Habi.co 2025: mediana $4.600.000; "
                "Portafolio / Metrocuadrado 2024-2025: estrato medio $5.000.000."
            ),
            "fecha": "2025",
            "nota": (
                "Localidad compacta estrato 3-4 con valorización estable. "
                "Los Alcázares / Doce de Octubre / Rionegro: $4.2M–$6M."
            ),
        },

        # ── West / Center-west ────────────────────────────────────────────────
        "fontibon": {
            "valor": 4_600_000,
            "rango": [3_500_000, 6_500_000],
            "fuente": (
                "Goodsyservices oct-2025: ~$4.300.000; "
                "Portafolio / Metrocuadrado 2024-2025: estrato medio $5.000.000. "
                "Central ajustado a $4.600.000 como punto medio de ambas fuentes."
            ),
            "fecha": "2025",
            "nota": (
                "Fontibón tiene mix estrato 3-4 con proyectos nuevos en corredores de "
                "la Av. El Dorado. Cercanía al aeropuerto deprecia algunos sectores. "
                "Regiotram proyectado: impulso 2026+."
            ),
        },

        "engativa": {
            "valor": 4_300_000,
            "rango": [3_000_000, 5_800_000],
            "fuente": (
                "Goodsyservices oct-2025: ~$4.300.000; "
                "Portafolio / Metrocuadrado 2024-2025: $4.400.000 estrato medio."
            ),
            "fecha": "2025",
            "nota": (
                "Estrato 2-4. Bolivia / La Española / Minuto de Dios: $3M–$4M. "
                "Santa Cecilia / Boyacá Real: $4.5M–$5.8M. Impacto positivo esperado "
                "de Regiotram de Occidente (apertura estimada 2027-2028)."
            ),
        },

        "puente_aranda": {
            "valor": None,
            "rango": None,
            "fuente": None,
            "fecha": None,
            "nota": (
                "Sin datos de fuente citable específica para Puente Aranda. "
                "Localidad predominantemente industrial (estrato 2-3, zona franca, bodegas). "
                "La oferta residencial es escasa y muy variable. Requiere estudio de "
                "mercado específico antes de usar en prefactibilidad."
            ),
        },

        # ── South-center ─────────────────────────────────────────────────────
        "santa_fe": {
            "valor": 5_500_000,
            "rango": [3_000_000, 8_500_000],
            "fuente": (
                "Habi.co 2025: mediana $5.769.230 (cálculo interno Habi, sin fecha exacta). "
                "Catastro Bogotá: variación catastral +8.2% en 2026."
            ),
            "fecha": "2025",
            "nota": (
                "Muy alta dispersión interna: La Macarena / Lourdes (gentrificación activa) "
                "$7M–$9M vs. barrios periféricos $3M–$4M. La variación catastral alta "
                "refleja el cambio de uso y densificación. Revisar por barrio."
            ),
        },

        "los_martires": {
            "valor": None,
            "rango": None,
            "fuente": None,
            "fecha": None,
            "nota": (
                "Sin datos de fuente citable específica para Los Mártires. "
                "Localidad con mercado residencial escaso, predominantemente estrato 2-3, "
                "con presencia de uso comercial y hotelero en algunas zonas. "
                "Requiere estudio de mercado específico."
            ),
        },

        "antonio_narino": {
            "valor": None,
            "rango": None,
            "fuente": None,
            "fecha": None,
            "nota": (
                "Sin datos de fuente citable específica para Antonio Nariño. "
                "Estrato 2-3, mercado residencial limitado. "
                "Requiere estudio de mercado específico."
            ),
        },

        "la_candelaria": {
            "valor": None,
            "rango": None,
            "fuente": None,
            "fecha": None,
            "nota": (
                "Sin datos de fuente citable específica para La Candelaria. "
                "Centro histórico: mercado inmobiliario atípico con restricciones de "
                "Bienes de Interés Cultural, bajo volumen de transacciones residenciales "
                "nuevas y demanda turística/institucional. No extrapolable a desarrollo "
                "residencial convencional."
            ),
        },

        # ── South ────────────────────────────────────────────────────────────
        "kennedy": {
            "valor": 4_200_000,
            "rango": [2_800_000, 6_500_000],
            "fuente": (
                "Goodsyservices oct-2025: ~$3.500.000 (promedio general); "
                "múltiples fuentes 2025 citan $5.160.855 para unidades estrato 4-5 "
                "(búsqueda Metrocuadrado); Catastro 2025. "
                "Central de $4.200.000 pondera la mezcla amplia de estratos."
            ),
            "fecha": "2025",
            "nota": (
                "Segunda localidad más poblada. Estrato 1-5. Kennedy Central / Carvajal "
                "(estrato 3): $3.2M–$4.5M. Zonas de Desarrollos nuevos hacia Bosa "
                "(estrato 2-3): $2.8M–$3.8M. Sectores renovados Castilla/Timiza: $4.5M–$6.5M."
            ),
        },

        "bosa": {
            "valor": 3_200_000,
            "rango": [2_000_000, 4_500_000],
            "fuente": (
                "Goodsyservices oct-2025: $2.500.000–$4.000.000; "
                "Catastro Bogotá 2026: variación catastral +8.5% (mayor de la ciudad). "
                "Central de $3.200.000 ajusta por dinamismo reciente."
            ),
            "fecha": "2025-2026",
            "nota": (
                "Localidad de crecimiento VIS/No-VIS bajo. Alta demanda de primeros "
                "compradores. El incremento catastral +8.5% refleja formalización y "
                "densificación, no necesariamente precios actuales de venta de nuevos proyectos."
            ),
        },

        "rafael_uribe_uribe": {
            "valor": 4_100_000,
            "rango": [2_800_000, 5_500_000],
            "fuente": (
                "Búsqueda web citando Catastro Bogotá 2025: promedio $4.155.738/m²; "
                "datos de Metrocuadrado 2025 para Diana Turbay y barrios aledaños."
            ),
            "fecha": "2025",
            "nota": (
                "Estrato 1-4. Zona franca de precios VIS/No-VIS bajo. "
                "Diana Turbay / Marco Fidel Suárez: $2.8M–$3.5M. "
                "Zonas más consolidadas: $4M–$5.5M."
            ),
        },

        "san_cristobal": {
            "valor": 3_800_000,
            "rango": [2_500_000, 5_200_000],
            "fuente": (
                "Búsqueda web citando Catastro Bogotá 2025: promedio $4.000.000/m²; "
                "Catastro 2026: variación catastral +10.1% (mayor de la ciudad)."
            ),
            "fecha": "2025-2026",
            "nota": (
                "Estrato 1-3 predominante. La valorización catastral alta (+10.1%) "
                "sugiere potencial de apreciación pero refleja ajuste de avalúos, "
                "no necesariamente transacciones recientes de proyectos nuevos."
            ),
        },

        "tunjuelito": {
            "valor": 3_600_000,
            "rango": [2_500_000, 4_800_000],
            "fuente": (
                "Búsqueda web citando Metrocuadrado 2025: promedio $3.616.667/m²."
            ),
            "fecha": "2025",
            "nota": "Estrato 2-3. Mercado de reposición y primeros compradores.",
        },

        "ciudad_bolivar": {
            "valor": 3_200_000,
            "rango": [1_800_000, 4_500_000],
            "fuente": (
                "Búsqueda web citando Catastro Bogotá 2025: promedio $3.578.623/m²; "
                "valor central ajustado a la baja por mix de informal/formal."
            ),
            "fecha": "2025",
            "nota": (
                "Estrato 1-2 en zonas altas (Lucero, Arborizadora); estrato 2-3 en "
                "Perdomo y Jerusalén. Gran parte del stock es autoconstructión. "
                "Proyectos formales concentrados en sectores bajos como Madelena."
            ),
        },

        "usme": {
            "valor": None,
            "rango": None,
            "fuente": None,
            "fecha": None,
            "nota": (
                "Sin datos de fuente citable para precios de venta de proyectos "
                "residenciales nuevos en Usme. La localidad es predominantemente VIS "
                "y de autoconstrucción; los precios de metros cuadrados catastrales "
                "reflejan construcción existente informal, no mercado de proyectos. "
                "Requiere estudio específico."
            ),
        },

        "sumapaz": {
            "valor": None,
            "rango": None,
            "fuente": None,
            "fecha": None,
            "nota": (
                "Sumapaz es páramo y suelo rural protegido. No aplica desarrollo "
                "residencial urbano. Sin precio de mercado relevante para cabida."
            ),
        },

        # ── City-wide fallback ────────────────────────────────────────────────
        "default_bogota": {
            "valor": 7_500_000,
            "rango": [3_000_000, 13_000_000],
            "fuente": (
                "Ciencuadras 2026 (citando datos de mercado): promedio general Bogotá "
                "$7.421.952/m²; Galería Inmobiliaria mar-2026: No-VIS nuevos $10.198.986/m², "
                "VIS nuevos $5.918.180/m². Central de $7.500.000 refleja mezcla de "
                "producto nuevo y usado, todas las localidades donde hay datos."
            ),
            "fecha": "2026",
            "nota": (
                "Fallback cuando no se puede resolver localidad. Rango muy amplio refleja "
                "la diversidad de Bogotá ($3M en Ciudad Bolívar hasta $13M en Chapinero). "
                "SIEMPRE preferir el valor de la localidad específica cuando esté disponible."
            ),
        },
    },

    # ── Sellable area efficiency ───────────────────────────────────────────────
    # Fraction of gross buildable area (área construible bruta) that generates
    # revenue.  The remainder is consumed by corridors, lobbies, staircases,
    # elevator shafts, mechanical rooms, and other common-area circulation.
    # This figure is applied in the pro-forma as:
    #   área_vendible = área_bruta × eficiencia_vendible_pct
    #   ingresos      = área_vendible × precio_venta_cop_m2
    # It is intentionally kept separate from the unit-count estimator, which
    # uses circulacion_pct (same concept, same 18% default, complementary view).
    "eficiencia_vendible_pct": {
        "valor": 0.82,
        "rango": [0.75, 0.90],
        "fuente": (
            "Supuesto del producto — no existe un estándar publicado por Camacol o la Lonja "
            "para este indicador. Rango 75-90% basado en tipologías residenciales colombianas: "
            "torres de 8+ pisos con doble núcleo de ascensores tienden a 78-82%; proyectos "
            "compactos de 4-6 pisos pueden alcanzar 85-90% al tener menos circulación vertical. "
            "El default 82% corresponde a 1 − 18% circulación (mismo supuesto del estimador "
            "de unidades en calc.py). Validar contra planos reales del proyecto."
        ),
        "fecha": "2026",
        "nota": (
            "Fracción del área bruta construible que se destina a unidades vendibles. "
            "El 18% restante cubre pasillos, hall de ascensores, escaleras y cuartos técnicos. "
            "Para proyectos VIS con plantas más eficientes use 85-88%. "
            "Para proyectos premium con lobbies amplios y doble circulación use 75-80%."
        ),
    },

    # ── Developer soft costs ───────────────────────────────────────────────────
    # As a fraction of direct construction cost (costo de obra, excl. land).
    # Includes: design & engineering (2-4%), permits (1-2%), fiduciary &
    # legal (1-2%), sales & marketing commissions (3-5%), project management
    # / gerencia integral (3-5%), contingencies (2-4%).
    # EXCLUDES: contractor's AIU (already in costos_construccion above).
    "costos_blandos_pct": {
        "valor": 0.18,
        "rango": [0.12, 0.25],
        "fuente": (
            "Práctica de industria colombiana (Camacol, BBVA Situación Inmobiliaria 2025). "
            "No existe una publicación con un único número; el rango 12-25% sobre costo "
            "de obra refleja la dispersión documentada entre proyectos VIS (costos blandos "
            "más bajos) y proyectos premium (gerencia + diseños más costosos)."
        ),
        "fecha": "2025",
        "nota": (
            "Expresado como fracción del costo directo de construcción (sin terreno). "
            "VIS típico: 12-16%. No-VIS estándar: 16-20%. No-VIS alto: 18-25%. "
            "DEBE validarse con presupuesto real del proyecto."
        ),
    },

    # ── Target developer margin ────────────────────────────────────────────────
    # Margin on gross revenue (ventas), NOT on cost.  Industry convention.
    "margen_objetivo_pct": {
        "valor": 0.20,
        "rango": [0.12, 0.30],
        "fuente": (
            "Práctica de industria colombiana; BBVA Situación Inmobiliaria 2025 cita "
            "márgenes de promotores No-VIS entre 15-25% sobre ingresos. "
            "VIS: márgenes estructuralmente más bajos (12-18%) por precio tope regulado."
        ),
        "fecha": "2025",
        "nota": (
            "Margen sobre ingresos totales de ventas (precio × área vendible). "
            "NO es margen sobre costos. VIS: usar 12-18%. No-VIS estándar: 15-22%. "
            "No-VIS premium: 18-30%. Sensible a velocidad de ventas y costo financiero."
        ),
    },

    # ── Discount / opportunity rate ────────────────────────────────────────────
    # Based on IBR overnight (Aug 14 2026: 11.183%).  For project-level DCF,
    # add equity risk premium of 3-6 pp depending on project/developer risk profile.
    "tasa_descuento_anual": {
        "valor": 0.11183,
        "fuente": (
            "Banco de la República — IBR overnight nominal: 11.183% (14-ago-2026). "
            "Tasa de política monetaria del Banco de la República: 12% (jul-2026, +75 pb). "
            "El IBR reemplazará al DTF como tasa de referencia oficial desde 2027 "
            "(Ley del Plan Nacional de Desarrollo 2026-2030)."
        ),
        "fecha": "2026-08",
        "nota": (
            "Tasa base IBR overnight sin prima de riesgo. Para DCF de proyectos "
            "inmobiliarios agregar prima de riesgo sectorial de 300-600 pb "
            "(→ 14%-17% EA como tasa de descuento del promotor). "
            "Actualizar con cada cambio de política monetaria."
        ),
    },

    # ── Default project duration ───────────────────────────────────────────────
    "plazo_meses_default": {
        "valor": 30,
        "fuente": (
            "Camacol / práctica de industria: duración media de un proyecto residencial "
            "en Bogotá (preventa 6-12 meses + construcción 18-24 meses) = ~24-36 meses."
        ),
        "fecha": "2025",
        "nota": (
            "Estimado conservador para proyectos de 80-200 unidades en Bogotá. "
            "VIS simple puede cerrarse en 24 meses; proyectos grandes >200 unidades "
            "o con obras de urbanismo pueden tomar 36-48 meses."
        ),
    },
}

# ── Localidad GIS resolver ─────────────────────────────────────────────────────

_L_LOCALIDAD = 31   # POT FeatureServer Layer 31 — Localidad D.555/2021

# Map from normalised GIS NOMBRE → MARKET_DEFAULTS key
_LOCALIDAD_MAP: dict[str, str] = {
    "USAQUEN":           "usaquen",
    "CHAPINERO":         "chapinero",
    "SANTA FE":          "santa_fe",
    "SAN CRISTOBAL":     "san_cristobal",
    "USME":              "usme",
    "TUNJUELITO":        "tunjuelito",
    "BOSA":              "bosa",
    "KENNEDY":           "kennedy",
    "FONTIBON":          "fontibon",
    "ENGATIVA":          "engativa",
    "SUBA":              "suba",
    "BARRIOS UNIDOS":    "barrios_unidos",
    "LOS MARTIRES":      "los_martires",
    "ANTONIO NARINO":    "antonio_narino",
    "PUENTE ARANDA":     "puente_aranda",
    "LA CANDELARIA":     "la_candelaria",
    "RAFAEL URIBE URIBE": "rafael_uribe_uribe",
    "CIUDAD BOLIVAR":    "ciudad_bolivar",
    "SUMAPAZ":           "sumapaz",
    "TEUSAQUILLO":       "teusaquillo",
}


def _normalize(s: str) -> str:
    """Uppercase + strip combining accents → canonical ASCII for dict lookup."""
    nfkd = unicodedata.normalize("NFKD", s.upper())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def resolve_localidad(lng: float, lat: float) -> str | None:
    """
    Return the MARKET_DEFAULTS key for the Bogotá localidad containing (lng, lat).

    Uses POT FeatureServer Layer 31 (same host as p2_lookup).
    Returns None if the point is outside Bogotá or the query fails.
    """
    try:
        import p2_lookup  # relative import — market_defaults lives alongside p2_lookup
        feats = p2_lookup.query_fs(
            _L_LOCALIDAD, lng, lat,
            out_fields=["NOMBRE", "CODIGO_LOCALIDAD"],
        )
    except Exception:
        return None

    if not feats:
        return None

    nombre_raw = (feats[0].get("attributes") or {}).get("NOMBRE") or ""
    key = _LOCALIDAD_MAP.get(_normalize(nombre_raw))
    return key   # None if unrecognised name


def get_sale_price_default(lng: float, lat: float) -> dict:
    """
    Return the sale-price record for the localidad containing (lng, lat).

    Always returns a dict; falls back to "default_bogota" if localidad
    is unresolvable or has no citable data (valor=None).
    The caller must check record["valor"] — if None, user input is required.

    Return shape:
        {
          "localidad": str | None,   # resolved key, or None
          "valor": int | None,
          "rango": list | None,
          "fuente": str | None,
          "fecha": str | None,
          "nota": str,
          "stale": bool,
          "stale_warning": str | None,
        }
    """
    localidad_key = resolve_localidad(lng, lat)
    precios = MARKET_DEFAULTS["precio_venta_cop_m2"]

    rec: dict | None = None
    if localidad_key:
        rec = precios.get(localidad_key)

    if rec is None:
        # Localidad unresolvable — no reliable default exists
        rec = {
            "valor": None,
            "rango": None,
            "fuente": None,
            "fecha": None,
            "nota": (
                "No se pudo resolver la localidad para este predio. "
                "Ingrese el precio de venta manualmente para continuar."
            ),
        }
    # When rec["valor"] is None (localidad found but no citable data), return as-is.
    # The caller must check valor — if None, user input is required.

    stale = is_stale()
    return {
        "localidad":     localidad_key,
        "valor":         rec.get("valor"),   # None = no citable default; user must supply
        "rango":         rec.get("rango"),
        "fuente":        rec.get("fuente"),
        "fecha":         rec.get("fecha"),
        "nota":          rec.get("nota", ""),
        "stale":         stale,
        "stale_warning": _STALENESS_WARNING if stale else None,
    }
