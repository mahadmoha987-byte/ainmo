import json
from pathlib import Path

import decreto253


def test_known_dotacional_receives_height_and_antejardin():
    row = decreto253.lookup("005617052003")
    assert row is not None
    assert row["pisos"] == 4
    assert row["antejardin_min_m"] == 3.5
    assert row["requiere_poligono"] is False


def test_conflicting_polygon_decisions_are_not_collapsed():
    row = decreto253.lookup("009257002002")
    assert row is not None
    assert row["pisos"] is None
    assert row["requiere_poligono"] is True
    assert {d["pisos"] for d in row["decisiones"]} == {5, 6}


def test_na_decision_remains_missing_not_zero():
    row = decreto253.lookup("002401029096")
    assert row is not None
    assert row["pisos"] is None
    assert row["requiere_poligono"] is False


def test_generated_dataset_has_complete_annex_counts():
    data = json.loads(
        (Path(__file__).with_name("rules") / "decreto253_equipamientos.json").read_text()
    )
    assert data["total_poligonos"] == 596
    assert data["total_codigos_extraidos"] == 1118
    assert data["total_lotes_unicos"] == 1102
