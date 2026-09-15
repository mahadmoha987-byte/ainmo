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
