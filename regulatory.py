"""Single source of truth for the regulatory basis disclosed by Ainmo."""

REGULATORY_VERSION = "D.555/2021 · D.670/2025 · D.676/2025 · D.253-254/2026 · Res.962/2026"
REGULATORY_VERSION_LONG = (
    "Decreto Distrital 555 de 2021, Anexo 5 sustituido por el Decreto 466 de 2024, "
    "reglamentación compilada por el Decreto 670 de 2025, incentivos del Decreto 676 "
    "de 2025, modificaciones distritales 253 y 254 de 2026 y actualización "
    "cartográfica de la Resolución SDP 962 de 2026"
)
REGULATORY_CUTOFF = "2026-09-06"

# These are deliberately explicit. They prevent a screening result from being
# mistaken for a complete legal opinion when a special instrument may prevail.
LEGAL_SCOPE_GAPS = [
    "La Resolución SDP 962 de 2026 actualizó cartografía POT asociada al sistema hídrico; la incorporación de cada ajuste en las capas ArcGIS consultadas debe verificarse con SDP.",
    "Los actos expedidos después del Decreto 670 de 2025 deben revisarse según el predio; Ainmo automatiza D.253/2026 y orienta sobre D.676/2025, pero no agota todos los instrumentos.",
    "Actuaciones Estratégicas, UPL, planes parciales y otros instrumentos especiales deben verificarse por separado.",
    "Los PEMP y las normas de protección patrimonial prevalecen cuando resulten aplicables.",
    "La herramienta no certifica títulos, tradición, gravámenes, servidumbres, cargas ni plusvalía.",
    "Las restricciones Aerocivil se identifican de forma informativa; la cota aeronáutica debe confirmarse con la autoridad.",
    "Los datos de amenaza ausentes son SIN_DATO y nunca significan ausencia de riesgo.",
]


def context() -> dict:
    return {
        "version": REGULATORY_VERSION,
        "descripcion": REGULATORY_VERSION_LONG,
        "corte_normativo": REGULATORY_CUTOFF,
        "alcance": "prefactibilidad_no_certificada",
        "verificaciones_pendientes": LEGAL_SCOPE_GAPS,
    }
