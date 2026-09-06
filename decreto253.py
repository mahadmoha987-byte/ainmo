"""Parcel-level lookup for Consolidación/Equipamientos under D.253/2026."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).with_name("rules") / "decreto253_equipamientos.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(_PATH.read_text(encoding="utf-8"))


def lookup(lot_code: str | None) -> dict | None:
    if not lot_code:
        return None
    row = _data()["lotes"].get(str(lot_code))
    if not row:
        return None
    return {
        **row,
        "norma": _data()["norma"],
        "fecha_expedicion": _data()["fecha_expedicion"],
        "vigencia_desde": _data()["vigencia_desde"],
        "antejardin_min_m": _data()["antejardin_min_m"],
        "fuente": _data()["fuente"],
        "articulos": _data()["articulos"],
    }
