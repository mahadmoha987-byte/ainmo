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
