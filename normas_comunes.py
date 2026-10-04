"""Verified common volumetric rules exposed separately from lot findings.

These rules describe project-design controls.  They are not silently folded
into a parcel result when the project inputs needed to apply them are absent.
"""

NORMAS_COMUNES = {
    "RENOVACION": {
        "estado": "resuelto",
        "fuente": "Anexo 5, Decreto Distrital 466 de 2024",
        "alturas_por_piso": {
            "min_libre_m": 2.30,
            "max_residencial_m": 3.80,
            "max_comercio_m": 4.20,
            "max_estacionamiento_m": 4.20,
            "nota": "Cada fracción adicional sobre el máximo aplicable cuenta como un piso adicional.",
        },
        "patios": {
            "lado_minimo_m": 3.0,
            "formula": "Un tercio de la mayor altura de las edificaciones que enmarcan el patio.",
        },
        "voladizos": {
            "tabla": [
                {"perfil_hasta_m": 6, "permitido": False, "max_m": 0},
                {"perfil_desde_m": 6, "perfil_hasta_m": 10, "max_m": 0.60},
                {"perfil_desde_m": 10, "perfil_hasta_m": 15, "max_m": 0.80},
                {"perfil_desde_m": 15, "perfil_hasta_m": 22, "max_m": 1.00},
                {"perfil_desde_m": 22, "max_m": 1.50, "nota": "Malla vial arterial."},
            ],
            "fuente": "Anexo 5 D.466/2024, sección 1.8",
        },
        "sotanos": {
            "permitidos": True,
            "semisotano_sobresale_max_m": 1.50,
            "nota": "La reserva vial, cuando aplica, impone la regla especial del artículo 379: sin sótanos ni semisótanos dentro de la franja.",
        },
        "cerramientos": {
            "antejardines": "No se permite cerrar los antejardines.",
            "predios_colindantes_max_m": 3.0,
            "aislamientos_laterales_max_m": 2.5,
            "aislamiento_posterior_max_m": 3.5,
        },
    },
}


def get_normas_comunes(tratamiento: str) -> dict:
    key = str(tratamiento or "").upper()
    if key in NORMAS_COMUNES:
        return NORMAS_COMUNES[key]
    return {
        "estado": "insuficiente",
        "motivo": f"Las normas comunes de {tratamiento or 'este tratamiento'} no están codificadas y verificadas.",
        "que_se_necesita": "Consultar el capítulo aplicable del Anexo 5.",
        "quien_lo_resuelve": "SDP",
    }
