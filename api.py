#!/usr/bin/env python3
"""
api.py — FastAPI backend for the Bogotá buildability tool
Run: uvicorn api:app --reload --port 8765
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import asyncio
import time
import html as _html
import httpx
import p2_lookup, calc, geocode, pdf_report, dxf_export
import db, auth
from cabida import proforma as proforma_mod
from cabida.market_defaults import MARKET_DEFAULTS, get_sale_price_default
from regulatory import context as regulatory_context

# ── GIS response cache (24 h TTL, keyed by rounded coords) ────────────────────
_GIS_CACHE: dict = {}          # key → (timestamp, result_dict)
_GIS_CACHE_TTL = 86_400        # 24 h
_GIS_CACHE_MAX = 2_000         # max entries before LRU-style eviction


def _gis_cache_key(lng: float, lat: float, vis: bool) -> tuple:
    return (round(lng, 5), round(lat, 5), vis)


def _gis_lookup_cached(lng: float, lat: float, vis: bool):
    key = _gis_cache_key(lng, lat, vis)
    entry = _GIS_CACHE.get(key)
    if entry and (time.time() - entry[0]) < _GIS_CACHE_TTL:
        return entry[1]
    result = p2_lookup.lookup(lng, lat, vis_en_sitio=vis)
    if len(_GIS_CACHE) >= _GIS_CACHE_MAX:
        oldest = min(_GIS_CACHE, key=lambda k: _GIS_CACHE[k][0])
        _GIS_CACHE.pop(oldest, None)
    _GIS_CACHE[key] = (time.time(), result)
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    yield
    await db.close()


app = FastAPI(title="Ainmo · Prefactibilidad Bogotá", version="2.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
    allow_credentials=True,
)

_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ── Auth helpers ──────────────────────────────────────────────────────────────

async def get_current_user(request: Request) -> dict | None:
    """Extract and verify Supabase access_token from Authorization header."""
    if auth.dev_mode():
        return None
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    payload = auth.verify_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    email = payload.get("email", "")
    if not user_id:
        return None
    profile = await db.get_profile(user_id)
    if not profile:
        profile = await db.ensure_profile(user_id, email)
    return profile


# ── Static ─────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def landing():
    return FileResponse(os.path.join(os.path.dirname(__file__), "landing.html"))


@app.get("/app", include_in_schema=False)
async def app_tool():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return FileResponse(os.path.join(os.path.dirname(__file__), "dashboard.html"))


# ── Config (public — anon key only) ───────────────────────────────────────────

@app.get("/api/config")
async def config_endpoint():
    return {
        "supabase_url": auth.SUPABASE_URL,
        "supabase_anon_key": auth.SUPABASE_ANON_KEY,
    }


# ── User info ──────────────────────────────────────────────────────────────────

@app.get("/api/me")
async def me(request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"ok": False, "authenticated": False})
    used, limit = await db.get_usage(user["id"])
    return {
        "ok": True,
        "authenticated": True,
        "email": user["email"],
        "name": user.get("name"),
        "avatar_url": user.get("avatar_url"),
        "plan": user["plan"],
        "usage_this_month": used,
        "usage_limit": limit,
    }


# ── Geocode ───────────────────────────────────────────────────────────────────

@app.get("/api/geocode")
async def geocode_endpoint(q: str = Query(..., description="Dirección en Bogotá")):
    try:
        candidates = geocode.geocode(q)
        if not candidates:
            return JSONResponse(status_code=200, content={
                "ok": False, "error": "no_results",
                "message": "No se encontró la dirección. Verifique el formato o ingrese coordenadas manualmente.",
            })
        return {"ok": True, "candidates": candidates}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": str(exc)})


# ── Calc ───────────────────────────────────────────────────────────────────────

@app.get("/api/calc")
async def calc_endpoint(
    request: Request,
    lng: float = Query(...),
    lat: float = Query(...),
    vis_en_sitio: bool = Query(False),
    anu_m2: float | None = Query(None),
    frente_m: float | None = Query(None),
    ancho_via_m: float | None = Query(None),
    address: str = Query(""),
    scenario_only: bool = Query(False),
):
    user = await get_current_user(request)

    if user and not scenario_only:
        allowed = await db.check_usage_allowed(user["id"])
        if not allowed:
            used, limit = await db.get_usage(user["id"])
            return JSONResponse(status_code=200, content={
                "ok": False,
                "error": "usage_limit",
                "message": f"Alcanzaste el límite de {limit} consultas gratuitas este mes. Actualiza a Pro para consultas ilimitadas.",
                "meta": {"plan": user["plan"], "usage_this_month": used, "usage_limit": limit},
            })

    try:
        lu = await asyncio.to_thread(_gis_lookup_cached, lng, lat, vis_en_sitio)
        result = calc.calculate(lu, anu_m2=anu_m2, frente_m=frente_m, ancho_via_m=ancho_via_m)

        analysis_id = None
        usage_this_month = 0
        usage_limit = 0

        if user:
            if scenario_only:
                usage_this_month, usage_limit = await db.get_usage(user["id"])
            else:
                new_count = await db.increment_usage(user["id"])
                usage_this_month = new_count if new_count >= 0 else 0
                _, usage_limit = await db.get_usage(user["id"])

            if user["plan"] == "pro":
                analysis_id = await db.save_analysis(
                    user_id=user["id"],
                    direccion=address,
                    lat=lat,
                    lng=lng,
                    lookup_snapshot=lu,
                    calc_result=result,
                    vis_en_sitio=vis_en_sitio,
                    anu_m2=anu_m2,
                )

        is_pro = user and user["plan"] == "pro"
        if not is_pro and not auth.dev_mode() and "formula_trace" in result:
            del result["formula_trace"]

        return {
            "ok": True,
            "data": result,
            "disclaimer": (
                "Estimación de prefactibilidad basada en datos públicos POT/catastro. "
                "No constituye norma urbanística certificada. "
                "Verifique con Curaduría Urbana o Secretaría Distrital de Planeación "
                "antes de tomar decisiones de transacción, licencia o actuación jurídica."
            ),
            "meta": {
                "plan": user["plan"] if user else "guest",
                "usage_this_month": usage_this_month,
                "usage_limit": usage_limit,
                "analysis_id": analysis_id,
                "authenticated": user is not None,
                "usage_charged": bool(user and not scenario_only),
                "regulatory_context": regulatory_context(),
            },
        }

    except calc.InputRequired as exc:
        field = _detect_missing_field(str(exc))
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "input_required",
            "field": field, "message": str(exc),
        })
    except p2_lookup.ZeroFeaturesError:
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "zero_features",
            "message": "La coordenada no cae sobre ningún predio catastral registrado en Bogotá.",
        })
    except p2_lookup.ParseError as exc:
        layer_info = _extract_layer_info(str(exc))
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "layer_parse_error",
            "layer_name": layer_info["name"], "layer_id": layer_info["id"],
            "message": f"{layer_info['name']} devolvió datos inesperados. Intente de nuevo o reporte el lote si el problema persiste.",
        })
    except p2_lookup.BuildabilityLookupError as exc:
        layer_info = _extract_layer_info(str(exc))
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "layer_error",
            "layer_name": layer_info["name"], "layer_id": layer_info["id"],
            "message": f"{layer_info['name']} no devolvió información para este lote. {layer_info['action']}",
        })
    except Exception:
        return JSONResponse(status_code=500, content={
            "ok": False, "error": "internal",
            "message": "Error interno del servidor. Intente de nuevo en unos momentos.",
        })


# ── Units estimator ────────────────────────────────────────────────────────────

@app.get("/api/units")
async def units_endpoint(
    area_m2: float = Query(...),
    circulacion_pct: float = Query(18.0),
    pct_studio: float = Query(20.0),
    pct_1br:    float = Query(30.0),
    pct_2br:    float = Query(35.0),
    pct_3br:    float = Query(15.0),
    m2_studio:  float = Query(40.0),
    m2_1br:     float = Query(55.0),
    m2_2br:     float = Query(75.0),
    m2_3br:     float = Query(95.0),
):
    total_pct = pct_studio + pct_1br + pct_2br + pct_3br
    if abs(total_pct - 100.0) > 0.5:
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "validation_error",
            "message": f"Los porcentajes de mezcla deben sumar 100% (actual: {total_pct:.1f}%).",
        })
    try:
        mix = {
            "studio": {"pct": pct_studio, "m2_neta": m2_studio, "label": f"Estudio (~{int(m2_studio)} m²)"},
            "1br":    {"pct": pct_1br,    "m2_neta": m2_1br,    "label": f"1 Alcoba (~{int(m2_1br)} m²)"},
            "2br":    {"pct": pct_2br,    "m2_neta": m2_2br,    "label": f"2 Alcobas (~{int(m2_2br)} m²)"},
            "3br":    {"pct": pct_3br,    "m2_neta": m2_3br,    "label": f"3 Alcobas (~{int(m2_3br)} m²)"},
        }
        result = calc.estimate_units(area_m2, circulacion_pct=circulacion_pct, unit_mix=mix)
        return {"ok": True, "data": result}
    except ValueError as exc:
        return JSONResponse(status_code=200, content={"ok": False, "error": "validation_error", "message": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": str(exc)})


# ── Pro-forma (land residual) ─────────────────────────────────────────────────

@app.get("/api/proforma")
async def proforma_endpoint(
    area_m2: float = Query(...),
    lng: float | None = Query(None),
    lat: float | None = Query(None),
    precio_lote_cop: float | None = Query(None),
    precio_venta_cop_m2: float | None = Query(None),
    costo_construccion_cop_m2: float | None = Query(None),
    eficiencia_vendible_pct: float | None = Query(None),
    costos_blandos_pct: float | None = Query(None),
    margen_objetivo_pct: float | None = Query(None),
):
    try:
        fake_calc = {"metrics": {"area_construible_max_m2": {"valor": area_m2}}}
        lookup_snapshot = None
        if lng is not None and lat is not None:
            lookup_snapshot = {"input": {"lng": lng, "lat": lat}}

        inp = proforma_mod.ProformaInput.from_calc(
            calc_result=fake_calc,
            lookup_snapshot=lookup_snapshot,
            precio_lote_cop=precio_lote_cop,
            precio_venta_cop_m2=precio_venta_cop_m2,
            costo_construccion_cop_m2=costo_construccion_cop_m2,
            eficiencia_vendible_pct=eficiencia_vendible_pct,
            costos_blandos_pct=costos_blandos_pct,
            margen_objetivo_pct=margen_objetivo_pct,
        )
        result = proforma_mod.run(inp)

        # Static two-variable screening sensitivity. It deliberately does not
        # masquerade as a discounted cash-flow model.
        sensitivity = []
        base_sale = result.inputs_echo["precio_venta_cop_m2"]["valor"]
        base_cost = result.inputs_echo["costo_construccion_cop_m2"]["valor"]
        efficiency = result.inputs_echo["eficiencia_vendible_pct"]["valor"]
        soft_pct = result.inputs_echo["costos_blandos_pct"]["valor"]
        margin_pct = result.inputs_echo["margen_objetivo_pct"]["valor"]
        for sale_delta in (-0.10, 0.0, 0.10):
            row = {"precio_venta_variacion_pct": sale_delta, "escenarios": []}
            for cost_delta in (-0.10, 0.0, 0.10):
                sale = base_sale * (1 + sale_delta)
                cost = base_cost * (1 + cost_delta)
                revenue = area_m2 * efficiency * sale
                hard = area_m2 * cost
                residual = round(revenue - hard - hard * soft_pct - revenue * margin_pct)
                row["escenarios"].append({
                    "costo_variacion_pct": cost_delta,
                    "valor_residual_lote_cop": residual,
                })
            sensitivity.append(row)

        md = MARKET_DEFAULTS
        # Use localidad-specific price default; valor=None means user must supply it
        if lng is not None and lat is not None:
            pv_meta = get_sale_price_default(lng, lat)
        else:
            pv_meta = {"valor": None, "rango": None, "fuente": None, "fecha": None,
                       "nota": "Sin coordenadas — ingrese el precio de venta manualmente."}
        cc_meta = md["costos_construccion_cop_m2"]["no_vis_estandar"]
        ef_meta = md["eficiencia_vendible_pct"]
        cb_meta = md["costos_blandos_pct"]
        mg_meta = md["margen_objetivo_pct"]

        return {
            "ok": True,
            "data": {
                "area_construible_m2": result.inputs_echo["area_construible_m2"],
                "area_vendible_m2": result.inputs_echo["area_vendible_m2"],
                "ingresos_totales_cop": result.ingresos_totales_cop,
                "costos_duros_cop": result.costos_duros_cop,
                "costos_blandos_cop": result.costos_blandos_cop,
                "costos_totales_cop": result.costos_totales_cop,
                "utilidad_objetivo_cop": result.utilidad_objetivo_cop,
                "valor_residual_lote_cop": result.valor_residual_lote_cop,
                "diferencia_cop": result.diferencia_cop,
                "diferencia_pct": result.diferencia_pct,
                "veredicto": result.veredicto,
                "veredicto_frase": result.veredicto_frase,
                "inputs_echo": result.inputs_echo,
                "sensibilidad": {
                    "matriz": sensitivity,
                    "nota": "Sensibilidad estática del VRL; no incluye financiación, impuestos, cronograma ni absorción.",
                },
                "defaults_meta": {
                    "precio_venta_cop_m2": {
                        "valor": pv_meta.get("valor"),
                        "rango": pv_meta.get("rango"),
                        "fuente": pv_meta.get("fuente"),
                        "fecha": pv_meta.get("fecha"),
                        "nota": pv_meta.get("nota"),
                        "localidad": pv_meta.get("localidad"),
                    },
                    "costo_construccion_cop_m2": {
                        "valor": cc_meta["valor"],
                        "rango": cc_meta["rango"],
                        "fuente": cc_meta["fuente"],
                        "fecha": cc_meta["fecha"],
                    },
                    "eficiencia_vendible_pct": {
                        "valor": ef_meta["valor"],
                        "rango": ef_meta["rango"],
                        "fuente": ef_meta["fuente"],
                        "fecha": ef_meta["fecha"],
                        "nota": ef_meta["nota"],
                    },
                    "costos_blandos_pct": {
                        "valor": cb_meta["valor"],
                        "rango": cb_meta["rango"],
                        "fuente": cb_meta["fuente"],
                        "fecha": cb_meta["fecha"],
                        "nota": cb_meta["nota"],
                    },
                    "margen_objetivo_pct": {
                        "valor": mg_meta["valor"],
                        "rango": mg_meta["rango"],
                        "fuente": mg_meta["fuente"],
                        "fecha": mg_meta["fecha"],
                        "nota": mg_meta["nota"],
                    },
                },
            },
        }
    except proforma_mod.ProformaBlocked as exc:
        return JSONResponse(status_code=200, content={"ok": False, "error": "blocked", "message": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": str(exc)})


# ── PDF report ────────────────────────────────────────────────────────────────

@app.get("/api/report")
async def report_endpoint(
    request: Request,
    lng: float = Query(...),
    lat: float = Query(...),
    vis_en_sitio: bool = Query(False),
    anu_m2: float | None = Query(None),
    frente_m: float | None = Query(None),
    ancho_via_m: float | None = Query(None),
    address: str = Query("Dirección no especificada"),
    preview: bool = Query(False),
):
    user = await get_current_user(request)
    if not user and not auth.dev_mode():
        return JSONResponse(status_code=401, content={
            "ok": False, "error": "auth_required",
            "message": "Inicia sesión para descargar el informe PDF.",
        })
    if user and user["plan"] != "pro" and not auth.dev_mode():
        return JSONResponse(status_code=403, content={
            "ok": False, "error": "pro_required",
            "message": "El informe PDF está disponible en el plan Pro.",
        })
    try:
        lu = await asyncio.to_thread(_gis_lookup_cached, lng, lat, vis_en_sitio)
        result = calc.calculate(lu, anu_m2=anu_m2, frente_m=frente_m, ancho_via_m=ancho_via_m)
        loop = asyncio.get_event_loop()
        pdf_bytes = await loop.run_in_executor(
            None, lambda: pdf_report.generate_pdf(calc_result=result, lookup_snapshot=lu, address=address)
        )
        disposition = "inline" if preview else 'attachment; filename="prefactibilidad.pdf"'
        from fastapi.responses import Response
        return Response(content=pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": disposition})
    except calc.InputRequired as exc:
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "input_required",
            "field": _detect_missing_field(str(exc)), "message": str(exc),
        })
    except p2_lookup.ZeroFeaturesError as exc:
        return JSONResponse(status_code=200, content={"ok": False, "error": "zero_features", "message": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": str(exc)})


@app.get("/api/report/html")
async def report_html_endpoint(
    lng: float = Query(...),
    lat: float = Query(...),
    vis_en_sitio: bool = Query(False),
    anu_m2: float | None = Query(None),
    frente_m: float | None = Query(None),
    ancho_via_m: float | None = Query(None),
    address: str = Query("Dirección no especificada"),
):
    try:
        lu = await asyncio.to_thread(_gis_lookup_cached, lng, lat, vis_en_sitio)
        result = calc.calculate(lu, anu_m2=anu_m2, frente_m=frente_m, ancho_via_m=ancho_via_m)
        html_str = pdf_report.generate_html_preview(calc_result=result, lookup_snapshot=lu, address=address)
        from fastapi.responses import Response
        return Response(content=html_str, media_type="text/html; charset=utf-8")
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": str(exc)})


# ── Saved analyses ────────────────────────────────────────────────────────────

@app.get("/api/portfolio/lots")
async def portfolio_lots_endpoint(request: Request):
    """Full lot data for the portfolio dashboard (includes result_json)."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"ok": False, "error": "auth_required"})
    if not auth.dev_mode() and user["plan"] != "pro":
        return JSONResponse(status_code=403, content={"ok": False, "error": "pro_required"})
    rows = await db.get_portfolio_data(user["id"])
    return {"ok": True, "data": rows}


@app.get("/api/portfolio/market-defaults")
async def portfolio_market_defaults():
    """Expose the market-default supuestos for client-side KPI computation."""
    md = MARKET_DEFAULTS
    return {
        "ok": True,
        "data": {
            "version": md.get("version"),
            "fecha_actualizacion": md.get("fecha_actualizacion"),
            "costos_construccion_cop_m2": {
                k: v.get("valor") for k, v in md.get("costos_construccion_cop_m2", {}).items()
            },
            "nota_uso": md.get("nota_uso"),
            # Flat supuesto-compatible fields for client diff display
            "supuestos_referencia": {
                "precio_venta":   None,  # localidad-dependent; no citywide default
                "costo_constr":   md["costos_construccion_cop_m2"]["no_vis_estandar"]["valor"],
                "indirectos_pct": round(md["costos_blandos_pct"]["valor"] * 100),
                "utilidad_pct":   round(md["margen_objetivo_pct"]["valor"] * 100),
                "unidad_m2":      70,
            },
        },
    }


@app.get("/api/analyses")
async def list_analyses_endpoint(request: Request):
    user = await get_current_user(request)
    if not user or (not auth.dev_mode() and user["plan"] != "pro"):
        return JSONResponse(status_code=403, content={"ok": False, "error": "pro_required",
            "message": "El historial de análisis está disponible en el plan Pro."})
    rows = await db.list_analyses(user["id"])
    return {"ok": True, "data": rows}


@app.get("/api/analyses/{analysis_id}")
async def get_analysis_endpoint(analysis_id: str, request: Request):
    user = await get_current_user(request)
    if not user or user["plan"] != "pro":
        return JSONResponse(status_code=403, content={"ok": False, "error": "pro_required"})
    row = await db.get_analysis(analysis_id, user["id"])
    if not row:
        return JSONResponse(status_code=404, content={"ok": False, "error": "not_found"})
    return {"ok": True, "data": row}


@app.patch("/api/analyses/{analysis_id}")
async def update_analysis_endpoint(analysis_id: str, request: Request):
    user = await get_current_user(request)
    if not user or user["plan"] != "pro":
        return JSONResponse(status_code=403, content={"ok": False, "error": "pro_required"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "message": "JSON inválido"})
    if "tags" in body:
        tags = [str(t) for t in body["tags"] if t]
        await db.update_analysis_tags(analysis_id, user["id"], tags)
    if "notas" in body or "notes" in body:
        notas = str(body.get("notas", body.get("notes", "")))
        await db.update_analysis_notas(analysis_id, user["id"], notas)
    return {"ok": True}


@app.delete("/api/analyses/{analysis_id}")
async def delete_analysis_endpoint(analysis_id: str, request: Request):
    user = await get_current_user(request)
    if not user or user["plan"] != "pro":
        return JSONResponse(status_code=403, content={"ok": False, "error": "pro_required"})
    await db.delete_analysis(analysis_id, user["id"])
    return {"ok": True}


@app.get("/api/analyses/{analysis_id}/report")
async def analysis_report_endpoint(analysis_id: str, request: Request, preview: bool = Query(False)):
    user = await get_current_user(request)
    if not user or user["plan"] != "pro":
        return JSONResponse(status_code=403, content={"ok": False, "error": "pro_required"})
    row = await db.get_analysis(analysis_id, user["id"])
    if not row:
        return JSONResponse(status_code=404, content={"ok": False, "error": "not_found"})
    stored = row["result_json"]
    lu = stored["lookup_snapshot"]
    result = stored["calc_result"]
    address = row.get("direccion") or "Dirección no especificada"
    loop = asyncio.get_event_loop()
    pdf_bytes = await loop.run_in_executor(
        None, lambda: pdf_report.generate_pdf(calc_result=result, lookup_snapshot=lu, address=address)
    )
    disposition = "inline" if preview else 'attachment; filename="prefactibilidad.pdf"'
    from fastapi.responses import Response
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": disposition})


# ── DXF export ────────────────────────────────────────────────────────────────

@app.get("/api/dxf")
async def dxf_endpoint(
    request: Request,
    lng: float = Query(...),
    lat: float = Query(...),
    vis_en_sitio: bool = Query(False),
    anu_m2: float | None = Query(None),
    frente_m: float | None = Query(None),
    ancho_via_m: float | None = Query(None),
):
    user = await get_current_user(request)
    if not user and not auth.dev_mode():
        return JSONResponse(status_code=401, content={"ok": False, "error": "auth_required"})
    try:
        lu = await asyncio.to_thread(_gis_lookup_cached, lng, lat, vis_en_sitio)
        result = calc.calculate(lu, anu_m2=anu_m2, frente_m=frente_m, ancho_via_m=ancho_via_m)
        loop = asyncio.get_event_loop()
        dxf_bytes = await loop.run_in_executor(
            None, lambda: dxf_export.generate_dxf(calc_result=result, lookup_snapshot=lu)
        )
        return Response(
            content=dxf_bytes,
            media_type="application/dxf",
            headers={"Content-Disposition": 'attachment; filename="edificabilidad.dxf"'},
        )
    except calc.InputRequired as exc:
        return JSONResponse(status_code=200, content={"ok": False, "error": "input_required", "message": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": str(exc)})


# ── VIS/VIP vs No-VIS comparison pro-forma ───────────────────────────────────

@app.get("/api/proforma/vis-comparison")
async def proforma_vis_comparison(
    area_m2: float = Query(...),
    area_vis_m2: float | None = Query(None),   # area_construible_max_vis75_m2 if available
    lng: float | None = Query(None),
    lat: float | None = Query(None),
    precio_lote_cop: float | None = Query(None),
    precio_venta_cop_m2: float | None = Query(None),
    costo_construccion_cop_m2: float | None = Query(None),
    costos_blandos_pct: float | None = Query(None),
    margen_objetivo_pct: float | None = Query(None),
):
    """Run the pro-forma for both the base scenario and the VIS≥75% bonus scenario."""
    md = MARKET_DEFAULTS
    lookup_snapshot = {"input": {"lng": lng, "lat": lat}} if (lng and lat) else None

    def _run(area: float, pv_override: float | None, cc_override: float | None,
             cb_override: float | None, mg_override: float | None) -> dict | None:
        try:
            fake_calc = {"metrics": {"area_construible_max_m2": {"valor": area}}}
            inp = proforma_mod.ProformaInput.from_calc(
                calc_result=fake_calc,
                lookup_snapshot=lookup_snapshot,
                precio_lote_cop=precio_lote_cop,
                precio_venta_cop_m2=pv_override,
                costo_construccion_cop_m2=cc_override,
                costos_blandos_pct=cb_override,
                margen_objetivo_pct=mg_override,
            )
            r = proforma_mod.run(inp)
            return {
                "area_construible_m2": r.inputs_echo["area_construible_m2"],
                "ingresos_totales_cop": r.ingresos_totales_cop,
                "costos_duros_cop": r.costos_duros_cop,
                "costos_blandos_cop": r.costos_blandos_cop,
                "costos_totales_cop": r.costos_totales_cop,
                "utilidad_objetivo_cop": r.utilidad_objetivo_cop,
                "valor_residual_lote_cop": r.valor_residual_lote_cop,
                "diferencia_cop": r.diferencia_cop,
                "diferencia_pct": r.diferencia_pct,
                "veredicto": r.veredicto,
                "veredicto_frase": r.veredicto_frase,
            }
        except proforma_mod.ProformaBlocked:
            return None

    # VIS market defaults — VIS scenario always uses VIS construction cost and margin
    vis_precio = md["precio_venta_cop_m2"].get("bosa", {}).get("valor") or 3_500_000
    vis_costo = md["costos_construccion_cop_m2"]["vis"]["valor"]
    vis_blandos = costos_blandos_pct or md["costos_blandos_pct"]["valor"]
    vis_margen = 0.15  # lower target margin for VIS (Art. 310)

    base_result = _run(area_m2, precio_venta_cop_m2, costo_construccion_cop_m2,
                       costos_blandos_pct, margen_objetivo_pct)
    vis_area = area_vis_m2 or area_m2
    # VIS price uses market VIS default (not user's No-VIS price override)
    vis_result = _run(vis_area, vis_precio, vis_costo, vis_blandos, vis_margen)

    return {
        "ok": True,
        "data": {
            "base": base_result,
            "vis": vis_result,
            "vis_area_m2": vis_area,
            "vis_notas": [
                f"Costo construcción VIS: ${vis_costo:,}/m²".replace(",", "."),
                f"Precio venta VIS: ${vis_precio:,}/m² (referencia mercado Bogotá)".replace(",", "."),
                "Margen objetivo VIS: 15% sobre ingresos (vs. " + f"{int((margen_objetivo_pct or md['margen_objetivo_pct']['valor'])*100)}% base)",
                "Aplica sólo si ≥75% del índice efectivo se destina a VIS/VIP (Art. 310 §3 D.555/2021)",
            ],
        },
    }


# ── Risk hazards: flood, landslide, slope ─────────────────────────────────────

# Bogotá IDRD/IDECA SIG layers for amenazas
_AMENAZA_FS = "https://serviciosgis.ideca.gov.co/arcgis/rest/services"

# Fallback: use the same POT FS — Capa 31 = Localidades, we'll try known amenaza layers
# From POT Mapas Bogotá: amenaza inundación = IDU/SDP services
# Use SIG Catastro for amenaza layers (confirmed endpoints from IDECA open data)
_RISK_FS_BASE = (
    "https://serviciosgis.ideca.gov.co/arcgis/rest/services/Mapa_Referencia"
    "/amenazas_bogota/FeatureServer"
)

# OpenTopoData SRTM 30m
_TOPO_URL = "https://api.opentopodata.org/v1/srtm30m"
_SLOPE_OFFSET_DEG = 0.00045   # ~50 m offset in degrees


async def _fetch_elevation(lat: float, lng: float) -> float | None:
    """Query OpenTopoData for a single point elevation in metres."""
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(_TOPO_URL, params={"locations": f"{lat},{lng}"})
            data = r.json()
            return data["results"][0]["elevation"]
    except Exception:
        return None


async def _calc_slope_pct(lat: float, lng: float) -> float | None:
    """
    Estimate max terrain slope (%) by querying 4 cardinal neighbours ~50 m away.
    Returns maximum of N-S and E-W slopes expressed as percentage.
    """
    d = _SLOPE_OFFSET_DEG
    pts = [(lat + d, lng), (lat - d, lng), (lat, lng + d), (lat, lng - d)]
    locs = ";".join(f"{la},{lo}" for la, lo in pts)
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(_TOPO_URL, params={"locations": locs})
            results = r.json()["results"]
            elev = [x["elevation"] for x in results]
        # N-S: elev[0] vs elev[1] over 2×50m = 100m; E-W: elev[2] vs elev[3]
        dist_m = _SLOPE_OFFSET_DEG * 111_320   # approx metres per degree at equator
        run = 2 * dist_m
        slope_ns = abs(elev[0] - elev[1]) / run * 100
        slope_ew = abs(elev[2] - elev[3]) / run * 100
        return round(max(slope_ns, slope_ew), 1)
    except Exception:
        return None


async def _query_amenaza_layer(layer_id: int, lat: float, lng: float) -> str | None:
    """
    Query a FeatureServer amenaza layer; return first feature's risk field or None.
    Tries IDECA endpoint; silently returns None on any error.
    """
    try:
        params = {
            "geometry": f"{lng},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
        }
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{_RISK_FS_BASE}/{layer_id}/query", params=params)
            feats = r.json().get("features", [])
        if not feats:
            return None
        attrs = feats[0].get("attributes", {})
        # Try common field names for risk category
        for fld in ("AMENAZA", "CATEGORIA", "NIVEL", "CLASIFICACION", "TIPO_AMENAZA"):
            if fld in attrs and attrs[fld]:
                return str(attrs[fld])
        return "presente"
    except Exception:
        return None


@app.get("/api/risk/hazards")
async def risk_hazards_endpoint(
    lat: float = Query(...),
    lng: float = Query(...),
):
    """
    Return flood risk, landslide risk, and slope for a given point.
    Non-blocking: each sub-query times out independently; missing data = None.
    """
    slope_task = asyncio.create_task(_calc_slope_pct(lat, lng))
    flood_task = asyncio.create_task(_query_amenaza_layer(0, lat, lng))   # layer 0 = inundación
    landslide_task = asyncio.create_task(_query_amenaza_layer(1, lat, lng))  # layer 1 = movimientos en masa

    slope = await slope_task
    flood = await flood_task
    landslide = await landslide_task

    # Slope risk classification
    slope_flag = None
    if slope is not None:
        if slope > 25:
            slope_flag = "alta"
        elif slope > 10:
            slope_flag = "media"
        else:
            slope_flag = "baja"

    all_missing = slope is None and flood is None and landslide is None
    return {
        "ok": True,
        "data": {
            "estado": "SIN_DATO" if all_missing else "OK",
            "nota": (
                "Las fuentes de amenaza y pendiente no respondieron; no interprete este resultado como ausencia de riesgo."
                if all_missing else None
            ),
            "slope_pct": slope,
            "slope_riesgo": slope_flag,
            "slope_nota": (
                "Pendiente >10%: se recomienda estudio geotécnico (NSR-10 Cap. H)."
                if slope and slope > 10 else None
            ),
            "inundacion": flood,
            "deslizamiento": landslide,
            "fuente_slope": "OpenTopoData SRTM 30m",
            "fuente_amenaza": "IDECA SIG Bogotá — amenazas_bogota FeatureServer",
        },
    }


# ── Billing (stub — clean interface for future payment integration) ────────────

@app.post("/api/billing/interest")
async def billing_interest_endpoint(request: Request):
    try:
        body = await request.json()
        email = (body.get("email") or "").strip().lower()
        notes = str(body.get("notes", ""))
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "message": "JSON inválido"})
    if not email or "@" not in email:
        return JSONResponse(status_code=400, content={"ok": False, "message": "Email inválido"})
    await db.add_billing_interest(email, notes)
    return {"ok": True, "message": "¡Gracias! Te avisaremos cuando el plan Pro esté disponible."}


@app.post("/api/billing/upgrade")
async def billing_upgrade_endpoint(request: Request):
    # Stub: wire Stripe / MercadoPago here. Call db.set_plan(user_id, "pro") on webhook.
    return JSONResponse(status_code=200, content={
        "stub": True,
        "message": "Pagos aún no disponibles. Te notificaremos cuando el plan Pro esté listo.",
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

_LAYER_NAMES = {
    "14": ("Área de actividad (Capa 14)", "Ingrese el uso del suelo manualmente."),
    "15": ("Edificabilidad POT (Capa 15)", "Verifique las coordenadas e intente de nuevo."),
    "21": ("Rango de desarrollo (Capa 21)", "Ingrese el rango del Plan Parcial manualmente."),
    "22": ("Antejardín (Capa 22)", "Intente de nuevo o ingrese el antejardín manualmente."),
    "25": ("Restricciones Aerocivil (Capa 25)", "Consulte directamente el mapa de Aerocivil."),
    "38": ("Calzada vial (Capa 38)", "Ingrese el ancho de vía manualmente."),
    "0":  ("Catastro Bogotá (lote)", "Verifique que las coordenadas estén dentro de Bogotá."),
}


def _extract_layer_info(msg: str) -> dict:
    for lid, (name, action) in _LAYER_NAMES.items():
        if f"Layer {lid}" in msg or f"layer {lid}" in msg or f"Capa {lid}" in msg:
            return {"id": lid, "name": name, "action": action}
    return {"id": None, "name": "Capa GIS", "action": "Intente de nuevo."}


def _detect_missing_field(msg: str) -> str:
    if "anu_m2" in msg or ("anu" in msg.lower() and "frente" not in msg.lower()):
        return "anu_m2"
    if "frente_m" in msg:
        return "frente_m"
    if "ancho_via_m" in msg or "perfil vial" in msg.lower():
        return "ancho_via_m"
    if "antejard" in msg.lower():
        return "antejardin"
    return "unknown"


# ── Share links ───────────────────────────────────────────────────────────────

@app.post("/api/analyses/{analysis_id}/share")
async def create_share_endpoint(
    analysis_id: str,
    request: Request,
    include_proforma: bool = Query(False),
):
    user = await get_current_user(request)
    if not user or (not auth.dev_mode() and user["plan"] != "pro"):
        return JSONResponse(status_code=403, content={"ok": False, "error": "pro_required"})
    row = await db.get_analysis(analysis_id, user["id"])
    if not row:
        return JSONResponse(status_code=404, content={"ok": False, "error": "not_found"})
    try:
        token = await db.create_share_token(analysis_id, include_proforma)
        app_url = os.environ.get("APP_URL", "").rstrip("/")
        url = f"{app_url}/s/{token}" if app_url else f"/s/{token}"
        return {"ok": True, "token": token, "url": url}
    except Exception:
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal",
                            "message": "No se pudo crear el enlace. Verifique que la tabla share_tokens exista."})


@app.get("/api/share/{token}")
async def get_share_data_endpoint(token: str):
    share = await db.get_share_token_analysis(token)
    if not share:
        return JSONResponse(status_code=404, content={"ok": False, "error": "not_found",
                            "message": "Enlace inválido o expirado."})
    analysis = share["analysis"]
    result_json = analysis.get("result_json", {})
    return {
        "ok": True,
        "data": {
            "calc_result": result_json.get("calc_result", {}),
            "lookup_snapshot": result_json.get("lookup_snapshot", {}),
            "vis_en_sitio": result_json.get("vis_en_sitio", False),
            "direccion": analysis.get("direccion", ""),
            "lat": analysis.get("lat"),
            "lng": analysis.get("lng"),
            "include_proforma": share["include_proforma"],
        },
    }


@app.get("/s/{token}", include_in_schema=False)
async def share_page(token: str):
    share = await db.get_share_token_analysis(token)
    if not share:
        return Response(
            content="<h1>Enlace inválido o expirado</h1><p><a href='/'>Volver al inicio</a></p>",
            media_type="text/html; charset=utf-8",
            status_code=404,
        )
    analysis = share["analysis"]
    result_json = analysis.get("result_json", {})
    calc_result = result_json.get("calc_result", {})
    address = analysis.get("direccion") or "Bogotá"
    trat = calc_result.get("tratamiento", "")
    metrics = calc_result.get("metrics", {})
    height_obj = metrics.get("altura_base_pisos") or metrics.get("altura_maxima_pisos") or {}
    height = height_obj.get("valor")
    ic_obj = (metrics.get("ic_basico") or {})
    ic = ic_obj.get("valor")
    parts = []
    if trat: parts.append(trat.title())
    if height: parts.append(f"{height} pisos")
    if ic: parts.append(f"IC {ic}")
    og_desc = ", ".join(parts) or "Consulta de edificabilidad Bogotá D.555/2021"
    og_title = _html.escape(f"Edificabilidad {address} — D.555/2021")
    og_desc_esc = _html.escape(og_desc)

    try:
        with open("index.html", "r", encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return Response(content="index.html not found", status_code=500)

    og_tags = (
        f'<meta property="og:title" content="{og_title}">\n'
        f'<meta property="og:description" content="{og_desc_esc}">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta name="twitter:card" content="summary">\n'
    )
    html = html.replace("<!-- og-placeholder -->", og_tags)
    share_js = (
        f'<script>window._SHARE_TOKEN="{token}";'
        f'window._SHARE_INCLUDE_PROFORMA={str(share["include_proforma"]).lower()};</script>\n'
    )
    html = html.replace("<!-- share-placeholder -->", share_js)
    return Response(content=html, media_type="text/html; charset=utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8765, reload=True)
