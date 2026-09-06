#!/usr/bin/env python3
"""Extract Annex 36.1 of Decreto Distrital 253/2026 into runtime JSON.

Run with the bundled PDF runtime. The output is deterministic and keeps every
polygon attached to a cadastral lot; conflicting polygon decisions remain
explicit instead of being silently collapsed.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/normativa/253_2026_anexo_36_1.pdf"
OUTPUT = ROOT / "rules/decreto253_equipamientos.json"


def modern_lot_code(code: str) -> str:
    """Convert barrio(6)+manzana(2)+predio(2) to BARMANPRE/LOTCODIGO 6+3+3."""
    return f"{code[:6]}0{code[6:8]}0{code[8:10]}"


def main() -> None:
    with pdfplumber.open(SOURCE) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    starts = list(re.finditer(r"(?m)^ID\s+(\d+)\s+(C/(?:N/A|\d+))\s+(N/A|\d+)\s+", text))
    if len(starts) != 596:
        raise RuntimeError(f"Expected 596 polygon rows; extracted {len(starts)}")

    lots: dict[str, list[dict]] = defaultdict(list)
    rows_without_code: list[dict] = []
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[match.start():end]
        polygon_id = int(match.group(1))
        decision = match.group(2)
        floors = None if match.group(3) == "N/A" else int(match.group(3))
        codes = re.findall(r"\b\d{10}\b", block)
        item = {"poligono_id": polygon_id, "decision_mapa": decision, "pisos": floors}
        if not codes:
            rows_without_code.append(item)
        for old_code in codes:
            lots[modern_lot_code(old_code)].append({**item, "codigo_catastral_anexo": old_code})

    entries = {}
    for lot_code, decisions in sorted(lots.items()):
        heights = sorted({d["pisos"] for d in decisions}, key=lambda x: (-1 if x is None else x))
        entries[lot_code] = {
            "aplica": True,
            "pisos": heights[0] if len(heights) == 1 else None,
            "requiere_poligono": len(heights) > 1,
            "decisiones": decisions,
        }

    payload = {
        "norma": "Decreto Distrital 253 de 2026",
        "fecha_expedicion": "2026-07-02",
        "vigencia_desde": "2026-07-04",
        "fuente": "Anexo 36.1 - Identificación de Polígonos y Predios",
        "articulos": ["249.1", "249.2", "249.3", "249.8", "249.9", "249.10", "249.11"],
        "antejardin_min_m": 3.5,
        "total_poligonos": len(starts),
        "total_codigos_extraidos": sum(len(v) for v in lots.values()),
        "total_lotes_unicos": len(entries),
        "filas_sin_codigo_catastral": rows_without_code,
        "lotes": entries,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}: {len(entries)} lots from {len(starts)} polygons")


if __name__ == "__main__":
    main()
