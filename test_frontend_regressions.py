from pathlib import Path


HTML = Path(__file__).with_name("index.html").read_text()


def test_map_click_clears_stale_address_and_lookup_freezes_request_identity():
    assert "const requestAddress = document.getElementById('addr-input')" in HTML
    assert "if (addressInput) addressInput.value = '';" in HTML
    assert "const resultAddress = d.direccion || `Predio ${d.lote?.lotcodigo || 'consultado'}`" in HTML


def test_manual_and_url_coordinate_lookups_clear_stale_address_identity():
    start = HTML.index("async function submitManualCoords()")
    end = HTML.index("async function submitAnchoVia", start)
    manual = HTML[start:end]
    assert "_activeSearchAddress = '';" in manual
    assert "_selectedAddressContext = null;" in manual
    assert "addressInput.value = '';" in manual

    start = HTML.index("(function _autoLaunchFromUrl()")
    end = HTML.index("/* ── Helpers", start)
    launch = HTML[start:end]
    assert "if (q)" in launch
    assert "_selectedAddressContext = null;" in launch
    assert "_expectedLotCodigo = null;" in launch


def test_near_match_keeps_searched_and_resolved_addresses_distinct():
    assert "const requestResolvedAddress = requestAddressContext?.resolved || requestAddress" in HTML
    assert "params.set('searched_address', requestSearchedAddress)" in HTML
    assert "params.set('resolved_address', requestResolvedAddress)" in HTML
    assert "params.set('near_match', 'true')" in HTML
    assert "const searchedAddressLabel = formatAddressLabel(addressResolution.searched_address)" in HTML
    assert "${escHtml(searchedAddressLabel)}</span>" in HTML
    assert "Analizando" in HTML
    assert 'class="notranslate" translate="no"' in HTML
    assert "_selectedAddressContext = null;" in HTML


def test_user_copy_avoids_literal_browser_mistranslations():
    api = Path(__file__).with_name("api.py").read_text()
    assert "Verifique la dirección o seleccione el predio en el mapa." in api
    assert "Verifique la placa o seleccione el lote en el mapa." not in api
    assert "No podemos calcular este predio todavía" in HTML
    assert "No podemos calcular este lote todavía" not in HTML
    assert "function formatAddressLabel(value)" in HTML


def test_unconfigured_restriction_coverage_does_not_force_amber_verdict():
    start = HTML.index("function _verdictBlockers(d)")
    end = HTML.index("function verdictBanner(d)", start)
    function = HTML[start:end]
    assert "sin dato de restricciones" in function
    assert "actionableWarnings" in function
    verdict_start = HTML.index("function verdictBanner(d)")
    verdict_end = HTML.index("function blockedBanner", verdict_start)
    verdict = HTML[verdict_start:verdict_end]
    assert "_bindingMetricNeedsReview(d)" in verdict
    assert "Object.values(m).some" not in verdict
    assert "Coberturas aún no automatizadas" in HTML
    assert "Estas coberturas no cuentan como hallazgos" in HTML


def test_derived_buildable_area_is_a_conditional_not_green_verdict():
    verdict_start = HTML.index("function verdictBanner(d)")
    verdict_end = HTML.index("function blockedBanner", verdict_start)
    verdict = HTML[verdict_start:verdict_end]
    assert "const hasDerivedArea = m.area_construible_estimada?.estado === 'derivado'" in verdict
    assert "|| _bindingMetricNeedsReview(d) || hasDerivedArea || outOfRange" in verdict


def test_figure_cards_deduplicate_identical_source_text():
    status_js = Path(__file__).with_name("static").joinpath("figure-status.js").read_text()
    assert "function citationLine(f)" in status_js
    assert "raw.indexOf(value)===index" in status_js
    assert "f.fuente_verificada||f.articulo," in status_js
    assert ".replace(/\\baltura_m\\b/gi,'altura en metros')" in status_js


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


def test_traceability_is_public_and_has_no_login_or_plan_gate():
    assert "traceBody = formulaTrace(d.formula_trace)" in HTML
    assert "La trazabilidad completa está disponible en Plan Pro." not in HTML
    assert "Inicie sesión para consultar su plan" not in HTML
    assert "showAuthModal" not in HTML
    assert "showUpgradeModal" not in HTML
    assert "typeof value === 'object'" in HTML
    assert "'No exigido'" in HTML
    assert "[object Object]" not in HTML


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


def test_chip_is_preserved_and_multi_unit_identity_is_rendered():
    assert "_selectedChip = candidate.chip || null" in HTML
    assert "params.set('searched_chip', requestChip)" in HTML
    assert "d.lote?.identidad_predial?.chip_consultado" in HTML
    assert "params.set('searched_chip', _lastResolvedChip)" in HTML
    assert "function cadastralIdentityBlock(d)" in HTML
    assert "Código de lote (LOTCODIGO)" in HTML
    assert "Identificación predial (CHIP)" in HTML
    assert "identificaciones prediales (CHIP) registradas" in HTML
    assert "No se eligió un CHIP arbitrariamente" in HTML


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


def test_public_beta_history_is_unbounded_scrollable_and_keeps_years_visible():
    assert "const _HIST_MAX" not in HTML
    assert "hist = hist.slice" not in HTML
    assert "sp-history-list" in HTML
    assert "overflow-y:auto" in HTML
    assert "year:'numeric'" in HTML
    assert "Recientes · ${hist.length}" in HTML


def test_search_has_compact_address_abbreviation_help():
    for text in ("CL</code> Calle", "KR/CRA</code> Carrera", "DG</code> Diagonal",
                 "TV</code> Transversal", "AC</code> Avenida Calle", "AK</code> Avenida Carrera"):
        assert text in HTML
    assert '<details class="address-help">' in HTML
    assert ".address-help[open] .address-help-card" in HTML


def test_public_beta_has_no_active_account_or_paywall_surface():
    api = Path(__file__).with_name("api.py").read_text()
    assert "@supabase/supabase-js" not in HTML
    assert 'id="login-btn"' not in HTML
    assert 'id="auth-modal"' not in HTML
    assert 'id="upgrade-modal"' not in HTML
    assert '"beta_access": "public"' in api
    assert '"authentication_required": False' in api
    assert 'RedirectResponse(url="/app?history=1", status_code=302)' in api
    assert '"error": "beta_local_history"' in api
    dxf_start = api.index('@app.get("/api/dxf")')
    dxf_end = api.index('# ── VIS/VIP', dxf_start)
    assert "auth_required" not in api[dxf_start:dxf_end]
    calc_start = api.index('@app.get("/api/calc")')
    calc_end = api.index('# ── Units', calc_start)
    assert "usage_limit" not in api[calc_start:calc_end]
    assert 'del result["formula_trace"]' not in api[calc_start:calc_end]


def test_api_has_specific_outside_bogota_and_chip_messages():
    api = Path(__file__).with_name("api.py").read_text()
    assert 'resolution == "outside_bogota"' in api
    assert "Ainmo solo cubre predios en Bogotá D.C. por ahora." in api
    assert 'resolution == "chip_not_found"' in api
    assert "def _apply_address_identity" in api


def test_outside_bogota_message_has_an_explicit_frontend_path():
    assert "json.resolution === 'outside_bogota' ? 'outside_bogota'" in HTML
    assert "if (json.resolution === 'outside_bogota')" in HTML
    assert "if (kind === 'outside_bogota')" in HTML
    assert "La dirección parece corresponder a otra ciudad de Colombia" in HTML


def test_result_tab_click_scrolls_the_real_section_into_view():
    start = HTML.index("function _initResultAnchors()")
    end = HTML.index("/* ── KPI strip", start)
    function = HTML[start:end]
    assert "target.scrollIntoView({ block:'start', inline:'nearest' })" in function
    assert "target.getBoundingClientRect().top - root.getBoundingClientRect().top" not in function


def test_megalot_and_negative_residuals_render_as_stops_not_estimates():
    assert "area_construible_estimada?.fuera_de_rango" in HTML
    assert "Alto — fuera del rango del modelo" in HTML
    assert "function _kpiNegative" in HTML
    assert "Alto — valor residual negativo" in HTML
    assert "La vista 3D se detuvo" in HTML


def test_financial_area_requires_a_valid_server_status():
    start = HTML.index("function _areaForFinancials(d)")
    end = HTML.index("function kpiStrip(d)", start)
    function = HTML[start:end]
    assert "estimate?.estado === 'derivado'" in function
    assert "!estimate?.fuera_de_rango" in function


def test_render_errors_do_not_expose_internal_exception_text():
    start = HTML.index("function showRenderError(err)")
    end = HTML.index("function showError", start)
    function = HTML[start:end]
    assert "console.error" in function
    assert "Detalle técnico" not in function
    assert "<pre" not in function


def test_internal_antejardin_citation_note_is_not_user_facing():
    status = Path(__file__).with_name("figure_status.py").read_text()
    assert "la referencia heredada al Art. 307 no sustenta esta regla" not in status


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


def test_dead_browser_footprint_calculator_was_removed():
    for obsolete in ("_computeLotFootprint", "_bestPisos", "_updateKpiFromFootprint", "_tryComputeFootprintArea"):
        assert obsolete not in HTML


def test_financial_and_height_kpis_require_explicit_valid_statuses():
    start = HTML.index("function _areaForFinancials(d)")
    end = HTML.index("function kpiStrip(d)", start)
    area_function = HTML[start:end]
    assert "regulatoryMetric?.estado || 'resuelto'" not in area_function
    assert "['resuelto','derivado'].includes(regulatoryMetric?.estado)" in area_function
    assert "estimate?.estado === 'derivado'" in area_function
    kpi = HTML[end:HTML.index("function acquisitionBrief", end)]
    assert "['resuelto','derivado'].includes(heightMetric?.estado)" in kpi


def test_dashboard_uses_resolved_identity_and_status_checked_area():
    dashboard = Path(__file__).with_name("dashboard.html").read_text()
    assert "function canonicalLotAddress(lot)" in dashboard
    assert "return storedCalc(lot)?.direccion" in dashboard
    assert "function portfolioArea(metrics)" in dashboard
    assert "['resuelto', 'derivado'].includes(obj.estado)" in dashboard
    assert "derived?.fuera_de_rango !== true" in dashboard
    assert "window.open(`/app?lat=" in dashboard
    assert "window.open('/?lat=" not in dashboard
    assert "ProformaBlocked" not in dashboard
