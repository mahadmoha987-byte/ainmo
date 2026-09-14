import calc


def test_small_project_sums_fractional_unit_yields_before_rounding():
    result = calc.estimate_units(204.0)

    assert result["total_unidades"] == 2
    assert sum(x["unidades"] for x in result["unidades_por_tipo"].values()) == 2
    unit_steps = [step for step in result["formula_trace"] if step["descripcion"].startswith("Unidades ")]
    assert sum(step["resultado"] for step in unit_steps) == result["total_unidades"]
    assert result["estado"] == "derivado"
    assert "167,3 m² vendibles" in result["motivo"]
    assert result["que_se_necesita"]
    assert result["quien_lo_resuelve"] == "profesional"


def test_zero_units_is_explained_instead_of_bare_zero():
    result = calc.estimate_units(20.0)

    assert result["total_unidades"] == 0
    assert result["estado"] == "derivado"
    assert "no cabe una unidad completa" in result["motivo"]
