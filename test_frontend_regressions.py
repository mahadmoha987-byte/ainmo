from pathlib import Path


HTML = Path(__file__).with_name("index.html").read_text()


def test_map_click_clears_stale_address_and_lookup_freezes_request_identity():
    assert "const requestAddress = document.getElementById('addr-input')" in HTML
    assert "if (addressInput) addressInput.value = '';" in HTML
    assert "const resultAddress = d.direccion || requestAddress || `Predio ${d.lote?.lotcodigo || 'consultado'}`" in HTML


def test_financial_cards_have_no_area_and_timeout_fallbacks():
    assert "No se pudo calcular — área base no disponible" in HTML
    assert "async function _fetchJsonWithTimeout(url, timeoutMs = 12000)" in HTML
    assert "controller.abort()" in HTML


def test_result_sections_are_emitted_in_navigation_order_as_peers():
    pushes = [
        HTML.index('parts.push(`<section class="result-anchor-section" id="result-resumen"'),
        HTML.index('parts.push(`<section class="result-anchor-section" id="result-volumetria"'),
        HTML.index('parts.push(modelSection);'),
        HTML.index('parts.push(`<section class="result-anchor-section" id="result-restricciones"'),
        HTML.index('parts.push(`<section class="result-anchor-section" id="result-financieros"'),
        HTML.index('parts.push(`<section class="result-anchor-section" id="result-trazabilidad"'),
    ]
    assert pushes == sorted(pushes)
    assert '<section class="result-anchor-section" id="result-modelo-3d"' in HTML


def test_osm_tiles_use_native_zoom_ceiling():
    assert "maxNativeZoom: 19" in HTML


def test_manual_proforma_is_always_marked_estimated_and_shows_backend_warning():
    start = HTML.index("async function calcProformaManual()")
    end = HTML.index("function _applyProformaSources", start)
    function = HTML[start:end]
    assert "_lastProformaAreaEstimated = true" in function
    assert "params.set('area_estimada', 'true')" in function
    assert "d.advertencia_area" in HTML


def test_status_summary_uses_full_payload_breakdown_and_has_reset():
    status_js = Path(__file__).with_name("static").joinpath("figure-status.js").read_text()
    assert "const supplied=d.resumen_estados||{}" in status_js
    assert 'data-status-filter="todos"' in status_js
    assert "selected=requested!=='todos'&&selected===requested?'todos':requested" in status_js
    assert "'derivado'" in status_js


def test_trace_gate_explains_plan_for_logged_out_and_free_users():
    assert HTML.count("La trazabilidad completa está disponible en Plan Pro.") == 2
    assert "Inicie sesión para consultar su plan" in HTML
    assert "este plan no incluye las fórmulas" in HTML
    assert "La traza detallada no está incluida en este resultado." not in HTML


def test_manual_sale_price_is_rendered_and_forwarded():
    assert 'id="pf-manual-sale-price"' in HTML
    start = HTML.index("async function calcProformaManual()")
    end = HTML.index("function _markSalePriceRequired", start)
    function = HTML[start:end]
    assert "precio_venta_cop_m2" in function
    assert "salePrice" in function


def test_manual_coordinates_open_from_computed_visibility_on_first_click():
    start = HTML.index("function toggleManualCoords()")
    end = HTML.index("async function runWithConfirmed", start)
    function = HTML[start:end]
    assert "window.getComputedStyle(el).display === 'none'" in function
    assert "aria-expanded" in function


def test_search_supports_chip_and_vis_scenario_is_explained():
    assert 'placeholder="Dirección, CHIP o clic en el mapa…"' in HTML
    assert "No determina por sí solo una obligación VIS/VIP" in HTML
    assert "ni cambia el tratamiento POT" in HTML
    assert "case 'exact_chip': return 'CHIP exacto'" in HTML


def test_blocked_reports_are_not_saved_as_successful_recents():
    helper_start = HTML.index("function _isSuccessfulHistoryResult(data)")
    helper_end = HTML.index("function saveToHistory", helper_start)
    helper = HTML[helper_start:helper_end]
    assert "conservacion_no_soportado" in helper
    assert "tratamiento_no_implementado" in helper
    assert "restriccion_bloqueante" in helper
    lookup_start = HTML.index("async function runLookup")
    lookup_end = HTML.index("/* ═", lookup_start)
    lookup = HTML[lookup_start:lookup_end]
    assert "if (_isSuccessfulHistoryResult(d))" in lookup


def test_api_has_specific_outside_bogota_and_chip_messages():
    api = Path(__file__).with_name("api.py").read_text()
    assert 'resolution == "outside_bogota"' in api
    assert "Ainmo solo cubre predios en Bogotá D.C. por ahora." in api
    assert 'resolution == "chip_not_found"' in api


def test_batch4_result_tabs_have_roles_state_and_keyboard_navigation():
    assert 'role="tablist"' in HTML
    assert HTML.count('role="tab"') == 6
    assert HTML.count('role="tabpanel"') == 6
    assert HTML.count('<h2 class="result-section-label">') == 6
    assert "link.setAttribute('aria-selected', String(isActive))" in HTML
    assert "link.tabIndex = isActive ? 0 : -1" in HTML
    assert "['ArrowRight', 'ArrowLeft', 'Home', 'End']" in HTML


def test_batch4_volumetric_renderer_groups_and_deduplicates():
    status_js = Path(__file__).with_name("static").joinpath("figure-status.js").read_text()
    for label in ("Lote y área", "Edificabilidad (ICe)", "Aislamientos y retrocesos", "Estacionamientos"):
        assert label in status_js
    assert "parking.porcentajes" in status_js
    assert "parking.areas" in status_js
    assert "metrics.area_y_huella_normativas" in status_js
    assert "valores_resumen" in status_js
    assert "<pre>" not in status_js


def test_batch4_homepage_has_one_workflow_and_report_examples():
    landing = Path(__file__).with_name("landing.html").read_text()
    possibilities = landing[landing.index('id="posibilidades"'):landing.index('id="como-funciona"')]
    guide = landing[landing.index('id="como-funciona"'):landing.index('id="normativa"')]
    assert "Tres fragmentos reales del análisis de un lote" in possibilities
    assert "ALTURA BASE" in possibilities
    assert "AISLAMIENTO POSTERIOR" in possibilities
    assert "VALOR RESIDUAL DEL LOTE" in possibilities
    assert "Ubique y confirme" in guide
    assert "Lea lo esencial" in guide
    assert "Un informe, seis lecturas" in landing


def test_batch4_internal_parking_note_is_not_user_facing():
    calc = Path(__file__).with_name("calc.py").read_text()
    assert "sobreestima en" not in calc
    assert "por circularidad" not in calc
    assert "área_construible_max como proxy" not in calc
    assert "Los metros cuadrados " in calc
    assert "solo pueden calcularse cuando el proyecto define esa área cubierta" in calc
