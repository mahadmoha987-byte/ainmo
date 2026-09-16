#!/usr/bin/env python3
"""
api.py — FastAPI backend for the Bogotá buildability tool
Run: uvicorn api:app --reload --port 8765
"""
import os, sys, json, logging
sys.path.insert(0, os.path.dirname(__file__))

from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import asyncio
import time
import html as _html
import httpx
import p2_lookup, calc, geocode, pdf_report, dxf_export
import db, auth
import home_news
from cabida import proforma as proforma_mod
from cabida.market_defaults import MARKET_DEFAULTS, get_sale_price_default
from regulatory import context as regulatory_context

logger = logging.getLogger("ainmo.api")

# ── GIS response cache (24 h TTL, keyed by rounded coords) ────────────────────
_GIS_CACHE: dict = {}          # key → (timestamp, result_dict)
_GIS_CACHE_TTL = 86_400        # 24 h
_GIS_CACHE_MAX = 2_000         # max entries before LRU-style eviction


def _gis_cache_key(lng: float, lat: float, vis: bool, expected_lotcodigo: str = "") -> tuple:
    return (round(lng, 5), round(lat, 5), vis, expected_lotcodigo)


def _gis_lookup_cached(lng: float, lat: float, vis: bool, expected_lotcodigo: str = ""):
    key = _gis_cache_key(lng, lat, vis, expected_lotcodigo)
    entry = _GIS_CACHE.get(key)
    if entry and (time.time() - entry[0]) < _GIS_CACHE_TTL:
        return entry[1]
    result = p2_lookup.lookup(
        lng, lat, vis_en_sitio=vis,
        expected_lotcodigo=expected_lotcodigo or None,
    )
    if len(_GIS_CACHE) >= _GIS_CACHE_MAX:
        oldest = min(_GIS_CACHE, key=lambda k: _GIS_CACHE[k][0])
        _GIS_CACHE.pop(oldest, None)
    _GIS_CACHE[key] = (time.time(), result)
    return result


class CadastralMismatchError(RuntimeError):
    def __init__(self, expected: str, resolved: str):
        self.expected = expected
        self.resolved = resolved
        super().__init__(f"Expected lot {expected}, resolved {resolved or 'SIN_DATO'}")


async def _calculate_current_lot(
    *, lng: float, lat: float, vis_en_sitio: bool,
    anu_m2: float | None, frente_m: float | None, ancho_via_m: float | None,
    expected_lotcodigo: str | None,
) -> tuple[dict, dict]:
    """Single GIS + calculation path shared by JSON, HTML and PDF exports."""
    expected = str(expected_lotcodigo or "").strip()
    lookup_snapshot = await asyncio.to_thread(
        _gis_lookup_cached, lng, lat, vis_en_sitio, expected,
    )
    resolved = str((lookup_snapshot.get("lote") or {}).get("lotcodigo") or "").strip()
    if expected and resolved != expected:
        raise CadastralMismatchError(expected, resolved)
    result = calc.calculate(
        lookup_snapshot,
        anu_m2=anu_m2,
        frente_m=frente_m,
        ancho_via_m=ancho_via_m,
    )
    return lookup_snapshot, result


def _apply_address_identity(
    result: dict,
    *,
    address: str = "",
    searched_address: str = "",
    resolved_address: str = "",
    near_match: bool = False,
) -> dict:
    """Attach the address that actually owns the analysed lot.

    A near-match search has two valid identities: what the user entered and
    the official Catastro plate they deliberately selected.  The latter must
    always be the report title; the former is retained only for disclosure.
    """
    # A near match is never allowed to inherit the typed query as its title.
    # If the caller omitted the resolved candidate, use the cadastral lot label
    # instead of silently presenting the unverified input as the analysed place.
    lot_label = f"Predio {(result.get('lote') or {}).get('lotcodigo')}" if (result.get("lote") or {}).get("lotcodigo") else ""
    if near_match:
        actual = str(resolved_address or lot_label or "Predio consultado").strip()
    else:
        actual = str(resolved_address or address or "Consultado por coordenada").strip()
    searched = str(searched_address or actual).strip()
    if actual:
        result["direccion"] = actual
    if near_match and searched and actual and searched.casefold() != actual.casefold():
        result["address_resolution"] = {
            "near_match": True,
            "searched_address": searched,
            "resolved_address": actual,
            "relation": "mismo bloque",
        }
    else:
        result.pop("address_resolution", None)
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    yield
    await db.close()


app = FastAPI(title="Ainmo · Prefactibilidad Bogotá", version="2.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
    # Authentication is carried explicitly in the Authorization header; the
    # public API does not use cross-origin cookies. Wildcard origins and
    # credentialed CORS are an invalid/ambiguous browser combination.
    allow_credentials=False,
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


@app.get("/api/home-news")
async def homepage_news():
    return JSONResponse(await home_news.get_news(), headers={"Cache-Control": "public, max-age=300"})


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    # The account-backed portfolio is intentionally out of the public-beta
    # surface. Keep the old URL useful without presenting an auth/paywall.
    return RedirectResponse(url="/app?history=1", status_code=302)


# ── Normative update pages (public, server-rendered for SEO) ─────────────────

_NORMATIVE_ARTICLES = {
    "decreto-253-2026": {
        "title": "Decreto 253 de 2026: altura de equipamientos en Consolidación",
        "date": "2 de julio de 2026",
        "kind": "Decreto Distrital",
        "summary": (
            "Adiciona al Decreto Único 670 de 2025 reglas de altura máxima en pisos "
            "para predios identificados como Equipamientos en tratamiento de Consolidación."
        ),
        "sections": [
            ("Qué cambia", "El acto asigna alturas a los polígonos y predios señalados en los mapas CU-5.4.2 a CU-5.4.33 y sus anexos. No es una altura general para cualquier equipamiento de Bogotá."),
            ("Cómo lo usa Ainmo", "Ainmo cruza el código catastral del lote con el Anexo 36.1. Cuando un código aparece ligado a decisiones distintas, o la tabla indica N/A, devuelve SIN_DATO y exige confirmar el polígono en el Anexo 36.2."),
            ("Qué todavía debe verificarse", "Instrumentos previos, régimen de transición, bienes de interés cultural, Actuaciones Estratégicas, Franja de Adecuación y demás reglas volumétricas que puedan prevalecer para el predio."),
        ],
        "source": "https://www.alcaldiabogota.gov.co/sisjur/normas/Norma1.jsp?i=193583",
    },
    "resolucion-962-2026": {
        "title": "Resolución SDP 962 de 2026: actualización cartográfica del sistema hídrico",
        "date": "8 de mayo de 2026 · publicada en Registro Distrital 8614 de julio de 2026",
        "kind": "Resolución SDP",
        "summary": (
            "Actualiza y precisa mapas POT de suelo de protección, estructura ecológica, sistema "
            "hídrico, tratamientos urbanísticos y áreas de actividad respecto del sistema hídrico."
        ),
        "sections": [
            ("Qué cambia", "La resolución modifica cartografía oficial asociada al sistema hídrico y su reflejo en varios mapas del Decreto 555 de 2021, incluidos CU-5.1 y CU-5.2."),
            ("Cómo lo trata Ainmo", "El informe advierte que debe verificarse si las capas ArcGIS consultadas ya incorporan el ajuste aplicable al punto. La ausencia de una respuesta automática nunca se presenta como ausencia de ronda o afectación."),
            ("Qué todavía debe verificarse", "El mapa oficial vigente, la delimitación aplicable al predio, estudios y conceptos ambientales y cualquier condición de riesgo o manejo exigida por la autoridad competente."),
        ],
        "source": "https://www.alcaldiabogota.gov.co/sisjur/normas/Norma1.jsp?i=193584",
    },
    "decreto-676-2025": {
        "title": "Decreto 676 de 2025: incentivos de construcción sostenible",
        "date": "30 de diciembre de 2025",
        "kind": "Decreto Distrital",
        "summary": (
            "Adiciona al Decreto 670 de 2025 incentivos urbanísticos condicionados al cumplimiento "
            "de medidas de ecourbanismo, certificación o reconocimiento y documentos de soporte."
        ),
        "sections": [
            ("Qué cambia", "Crea rutas diferenciadas para Desarrollo, Renovación Urbana, Consolidación y Grandes Servicios Metropolitanos. La posibilidad y el tipo de incentivo dependen del tratamiento y del proyecto."),
            ("Cómo lo usa Ainmo", "Ainmo identifica una oportunidad potencial cuando el tratamiento puede ser elegible. No suma automáticamente área ni pisos: la interfaz exige revisar medidas, planos, certificación, autodeclaración y trámite ante curaduría."),
            ("Qué todavía debe verificarse", "Uso residencial predominante cuando corresponda, acogimiento expreso, totalidad de las medidas aplicables, pre-certificación o reconocimiento y consistencia con la licencia y el Anexo de Construcción Sostenible."),
        ],
        "source": "https://www.alcaldiabogota.gov.co/sisjur/normas/Norma1.jsp?i=191995",
    },
}


def _normative_shell(title: str, description: str, body: str, canonical: str) -> str:
    title_esc = _html.escape(title)
    desc_esc = _html.escape(description)
    canonical_esc = _html.escape(canonical, quote=True)
    structured = json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": description,
        "publisher": {"@type": "Organization", "name": "Ainmo"},
        "inLanguage": "es-CO",
    }, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang='es-CO'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>{title_esc} · Ainmo</title><link rel='icon' href='/static/favicon.svg' type='image/svg+xml'>
<meta name='description' content='{desc_esc}'><link rel='canonical' href='{canonical_esc}'>
<meta property='og:title' content='{title_esc} · Ainmo'><meta property='og:description' content='{desc_esc}'>
<meta property='og:type' content='article'><script type='application/ld+json'>{structured}</script>
<link rel='preconnect' href='https://fonts.googleapis.com'><link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>
<link href='https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800&family=IBM+Plex+Mono:wght@400;500&family=Source+Sans+3:wght@400;500;600&display=swap' rel='stylesheet'>
<style>:root{{--ink:#101318;--muted:#68707e;--line:#dfe3e8;--soft:#f5f6f7;--blue:#307bea;--rainbow:linear-gradient(90deg,#ff6a35,#ffb21c,#42b768,#20a4b6,#307bea,#7566ea)}}*{{box-sizing:border-box}}body{{margin:0;color:var(--ink);font:18px/1.65 'Source Sans 3',sans-serif}}a{{color:var(--blue)}}nav{{height:70px;border-bottom:1px solid var(--line);display:flex;align-items:center}}.wrap{{width:min(920px,calc(100% - 36px));margin:auto}}.navin{{width:min(1180px,calc(100% - 36px));margin:auto;display:flex;align-items:center;justify-content:space-between}}.logo{{font:800 29px 'Archivo';letter-spacing:-.07em;background:var(--rainbow);-webkit-background-clip:text;color:transparent;text-decoration:none}}.tool{{font:700 13px 'Archivo';color:#fff;background:var(--ink);padding:11px 16px;text-decoration:none}}header{{padding:92px 0 62px;background:var(--soft);border-bottom:1px solid var(--line)}}.crumb,.meta{{font:500 11px 'IBM Plex Mono';text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}}h1{{font:800 clamp(42px,7vw,72px)/1.02 'Archivo';letter-spacing:-.055em;margin:15px 0 22px}}.lead{{font-size:23px;line-height:1.45;max-width:790px}}main{{padding:66px 0 100px}}article section{{padding:27px 0;border-top:1px solid var(--line)}}h2{{font:700 27px 'Archivo';margin:0 0 10px}}.source{{margin-top:40px;padding:25px;border:1px solid var(--line);background:var(--soft)}}.warning{{margin-top:36px;border-left:5px solid #ffb21c;padding:18px 22px;background:#fff8e4;font-size:15px}}footer{{background:var(--ink);color:#aab1bb;padding:30px 0;font-size:13px}}footer a{{color:#fff}}@media(max-width:600px){{header{{padding-top:60px}}h1{{font-size:43px}}.lead{{font-size:20px}}}}</style></head>
<body><nav><div class='navin'><a class='logo' href='/'>ainmo</a><a class='tool' href='/app'>Consultar lote →</a></div></nav>{body}<footer><div class='wrap'>Ainmo · Prefactibilidad urbanística, no concepto oficial ni licencia. · <a href='/normativa'>Índice normativo</a></div></footer></body></html>"""


@app.get("/normativa", include_in_schema=False)
async def normativa_index():
    cards = []
    for slug, item in _NORMATIVE_ARTICLES.items():
        cards.append(
            f"<section><div class='meta'>{_html.escape(item['kind'])} · {_html.escape(item['date'])}</div>"
            f"<h2><a href='/normativa/{slug}'>{_html.escape(item['title'])}</a></h2>"
            f"<p>{_html.escape(item['summary'])}</p></section>"
        )
    body = (
        "<header><div class='wrap'><div class='crumb'><a href='/'>Inicio</a> / Normativa</div>"
        "<h1>Actualizaciones normativas que Ainmo rastrea</h1>"
        "<p class='lead'>Lecturas breves con enlace al acto oficial y una explicación precisa de qué automatiza la herramienta y qué debe seguir verificándose.</p></div></header>"
        "<main><article class='wrap'>" + "".join(cards) +
        "<div class='source'><strong>Cadena base:</strong> D.555/2021; Anexo 5 sustituido por D.466/2024; compilación D.670/2025; correcciones formales D.254/2026. El acto oficial prevalece siempre.</div></article></main>"
    )
    return Response(content=_normative_shell(
        "Actualizaciones normativas POT Bogotá",
        "Índice de cambios normativos distritales rastreados por Ainmo.",
        body, "https://ainmo.uk/normativa",
    ), media_type="text/html; charset=utf-8")


@app.get("/normativa/{slug}", include_in_schema=False)
async def normativa_article(slug: str):
    item = _NORMATIVE_ARTICLES.get(slug)
    if not item:
        return Response(content="Página normativa no encontrada", status_code=404)
    sections = "".join(
        f"<section><h2>{_html.escape(heading)}</h2><p>{_html.escape(copy)}</p></section>"
        for heading, copy in item["sections"]
    )
    body = (
        f"<header><div class='wrap'><div class='crumb'><a href='/'>Inicio</a> / <a href='/normativa'>Normativa</a></div>"
        f"<div class='meta' style='margin-top:45px'>{_html.escape(item['kind'])} · {_html.escape(item['date'])}</div>"
        f"<h1>{_html.escape(item['title'])}</h1><p class='lead'>{_html.escape(item['summary'])}</p></div></header>"
        f"<main><article class='wrap'>{sections}<div class='source'><strong>Fuente oficial</strong><br>"
        f"<a href='{_html.escape(item['source'], quote=True)}' target='_blank' rel='noopener'>Consultar el texto en Bogotá Jurídica ↗</a></div>"
        "<div class='warning'><strong>Alcance:</strong> esta nota orienta una consulta de prefactibilidad. No reemplaza el texto oficial, un concepto de norma, la licencia ni el análisis profesional del expediente.</div></article></main>"
    )
    return Response(content=_normative_shell(
        item["title"], item["summary"], body, f"https://ainmo.uk/normativa/{slug}"
    ), media_type="text/html; charset=utf-8")


# ── Public-beta config ─────────────────────────────────────────────────────────

@app.get("/api/config")
async def config_endpoint():
    return {
        "beta_access": "public",
        "authentication_required": False,
    }


# ── User info ──────────────────────────────────────────────────────────────────

@app.get("/api/me")
async def me(request: Request):
    return {
        "ok": True,
        "authenticated": False,
        "beta_access": "public",
        "message": "Ainmo está abierto durante la beta; no se requiere cuenta.",
    }


# ── Geocode ───────────────────────────────────────────────────────────────────

@app.get("/api/geocode")
async def geocode_endpoint(q: str = Query(..., description="Dirección en Bogotá")):
    try:
        # The full resolver performs blocking government-GIS requests. Keep it
        # off the event loop so autocomplete and other users stay responsive.
        result = await asyncio.to_thread(geocode.geocode_detailed, q)
        candidates = result["candidates"]
        if not candidates:
            resolution = result.get("resolution")
            if resolution == "street_recognized":
                message = "Catastro reconoce la vía, pero no encontró ese número de puerta. Verifique la dirección o seleccione el predio en el mapa."
            elif resolution == "intersection":
                message = "Una intersección no identifica un predio único. Ingrese una dirección predial completa o seleccione el predio en el mapa."
            elif resolution == "incomplete_address":
                message = "Ingrese una dirección completa con vía, cruce y número de puerta (por ejemplo: Calle 85 # 11-53), o seleccione el predio en el mapa."
            elif resolution == "outside_bogota":
                message = "Ainmo solo cubre predios en Bogotá D.C. por ahora."
            elif resolution == "chip_not_found":
                message = "Catastro Bogotá no encontró ese CHIP. Verifique sus 11 caracteres o seleccione el lote en el mapa."
            else:
                message = "No se encontró una dirección predial confiable. Verifique el formato o seleccione el lote en el mapa."
            return JSONResponse(status_code=200, content={
                "ok": False, "error": "no_results",
                "resolution": resolution,
                "message": message,
            })
        return {"ok": True, "candidates": candidates, "resolution": result.get("resolution")}
    except Exception:
        logger.exception("Geocoding failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": "No se pudo consultar el servicio de direcciones. Intente de nuevo."})


@app.get("/api/address-suggest")
async def address_suggest_endpoint(
    q: str = Query("", max_length=120, description="Dirección parcial en Bogotá"),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    recent_lots: str = Query("", max_length=200),
):
    """Fast, database-only typeahead; never calls Catastro or Nominatim."""
    started = time.perf_counter()
    outside_city = geocode._explicit_outside_bogota_city(q)
    if outside_city:
        return {
            "ok": True,
            "suggestions": [],
            "resolution": "outside_bogota",
            "locality": outside_city,
            "normalized_query": geocode.normalize_address_search(q),
            "index_ready": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1_000, 1),
        }
    normalized = geocode.normalize_address_search(q)
    tokens = normalized.split()
    has_numbered_bogota_road = (
        bool(tokens)
        and tokens[0] in {"AC", "AK", "AV", "CL", "DG", "KR", "TV"}
        and any(character.isdigit() for character in " ".join(tokens[1:]))
    )
    # A road name alone can match tens of thousands of plates and is not yet
    # useful for choosing a lot. Wait for road, cross street and at least the
    # beginning of the door number before asking the citywide index.
    has_property_prefix = len(tokens) >= 4
    if (
        len(normalized) < 3
        or not has_numbered_bogota_road
        or not has_property_prefix
    ):
        return {
            "ok": True,
            "suggestions": [],
            "normalized_query": normalized,
            "index_ready": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1_000, 1),
        }

    # Viewport proximity is useful only inside the product's Bogotá envelope.
    if lat is None or lng is None or not geocode._in_bogota(lat, lng):
        lat = lng = None
    recent = [part.strip() for part in recent_lots.split(",")]
    recent = [code for code in recent if code.isdigit() and len(code) == 12][:6]

    try:
        suggestions, index_ready = await db.search_address_index(
            normalized,
            lat=lat,
            lng=lng,
            recent_lot_codes=recent,
            limit=8,
        )
    except Exception:
        # Autocomplete is additive. A database/index incident must never take
        # down the existing submit-to-geocode workflow.
        suggestions, index_ready = [], False

    # Typeahead is a direct route into a cadastral calculation, so it is
    # intentionally stricter than the explicit geocoder. Keep only exact
    # indexed plates or genuine completions of what the user already typed.
    # Road-level fuzzy matches remain available in the labelled geocoder flow.
    safe_suggestions = []
    for suggestion in suggestions:
        candidate = str(suggestion.get("normalized_address") or "").strip()
        if candidate == normalized:
            match_type = "exact_address"
        elif candidate.startswith(normalized):
            match_type = "address_completion"
        else:
            continue
        safe = dict(suggestion)
        safe["match_type"] = match_type
        safe_suggestions.append(safe)
    return {
        "ok": True,
        "suggestions": safe_suggestions,
        "normalized_query": normalized,
        "index_ready": index_ready,
        "elapsed_ms": round((time.perf_counter() - started) * 1_000, 1),
    }


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
    searched_address: str = Query(""),
    resolved_address: str = Query(""),
    near_match: bool = Query(False),
    expected_lotcodigo: str | None = Query(None),
    scenario_only: bool = Query(False),
):
    try:
        lu, result = await _calculate_current_lot(
            lng=lng, lat=lat, vis_en_sitio=vis_en_sitio,
            anu_m2=anu_m2, frente_m=frente_m, ancho_via_m=ancho_via_m,
            expected_lotcodigo=expected_lotcodigo,
        )
        _apply_address_identity(
            result,
            address=address,
            searched_address=searched_address,
            resolved_address=resolved_address,
            near_match=near_match,
        )

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
                "beta_access": "public",
                "analysis_id": None,
                "authenticated": False,
                "usage_charged": False,
                "regulatory_context": regulatory_context(),
            },
        }

    except CadastralMismatchError as exc:
        return JSONResponse(status_code=200, content={
            "ok": False,
            "error": "cadastral_mismatch",
            "message": (
                "La placa domiciliaria de Catastro identifica el lote "
                f"{exc.expected}, pero su punto publicado cae dentro del lote "
                f"{exc.resolved or 'un lote sin código resuelto'}. No se calcularon normas para evitar "
                "mostrar por error las del predio vecino. Seleccione el polígono correcto "
                "en el mapa o verifique el CHIP/código de lote en Catastro."
            ),
            "expected_lotcodigo": exc.expected,
            "resolved_lotcodigo": exc.resolved or None,
        })
    except calc.InputRequired as exc:
        field = _detect_missing_field(str(exc))
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "input_required",
            "estado": "insuficiente", "motivo": str(exc),
            "que_se_necesita": _missing_field_label(field), "quien_lo_resuelve": "profesional",
            "field": field, "message": str(exc),
        })
    except p2_lookup.ZeroFeaturesError as exc:
        layer_info = _extract_layer_info(str(exc))
        if layer_info["id"] is not None:
            return JSONResponse(status_code=200, content={
                "ok": False,
                "error": "layer_zero_features",
                "estado": "insuficiente", "motivo": "La capa consultada no contiene un registro aplicable para este predio.",
                "que_se_necesita": "Confirmar el dato en la cartografía oficial.", "quien_lo_resuelve": "SDP",
                "layer_name": layer_info["name"],
                "layer_id": layer_info["id"],
                "message": (
                    f"El predio existe en Catastro, pero {layer_info['name']} no contiene "
                    "un registro aplicable en este punto. El dato no está disponible; esto no significa que la restricción no exista."
                ),
            })
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "zero_features",
            "estado": "insuficiente", "motivo": "No se identificó un predio catastral en la coordenada.",
            "que_se_necesita": "Confirmar ubicación y polígono catastral.", "quien_lo_resuelve": "Catastro",
            "message": "La coordenada no cae sobre ningún predio catastral registrado en Bogotá.",
        })
    except p2_lookup.ParseError as exc:
        layer_info = _extract_layer_info(str(exc))
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "layer_parse_error",
            "estado": "error", "motivo": "No fue posible interpretar la respuesta de la capa GIS.",
            "que_se_necesita": "Reintentar y reportar el predio a soporte Ainmo si persiste.", "quien_lo_resuelve": None,
            "layer_name": layer_info["name"], "layer_id": layer_info["id"],
            "message": f"{layer_info['name']} devolvió datos inesperados. Intente de nuevo o reporte el lote si el problema persiste.",
        })
    except p2_lookup.AmbiguousRegulationError as exc:
        return JSONResponse(status_code=200, content={
            "ok": False,
            "error": "ambiguous_regulation",
            "estado": "requiere_concepto", "motivo": "La cartografía devuelve normas contradictorias para el predio.",
            "que_se_necesita": "Concepto sobre la norma aplicable.", "quien_lo_resuelve": "SDP / curaduría",
            "layer_name": "Edificabilidad POT",
            "layer_id": p2_lookup.L_EDIFICABILIDAD,
            "options": exc.options,
            "message": (
                "El mapa oficial de edificabilidad contiene normas superpuestas y contradictorias "
                "en este punto. Ainmo no eligió una automáticamente. Verifique el predio con la "
                "Secretaría Distrital de Planeación o una Curaduría Urbana."
            ),
        })
    except p2_lookup.BuildabilityLookupError as exc:
        layer_info = _extract_layer_info(str(exc))
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "layer_error",
            "estado": "error", "motivo": "Falló la consulta de una capa GIS.",
            "que_se_necesita": "Reintentar la consulta; reportar a soporte Ainmo si persiste.", "quien_lo_resuelve": None,
            "layer_name": layer_info["name"], "layer_id": layer_info["id"],
            "message": f"{layer_info['name']} no devolvió información para este lote. {layer_info['action']}",
        })
    except Exception:
        logger.exception("Calculation endpoint failed")
        return JSONResponse(status_code=500, content={
            "ok": False, "error": "internal",
            "estado": "error", "motivo": "El motor no pudo completar el cálculo por un error interno.",
            "que_se_necesita": "Reintentar y reportar el error a soporte Ainmo.", "quien_lo_resuelve": None,
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
    except Exception:
        logger.exception("Unit estimation failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": "No se pudo calcular la mezcla de unidades. Intente de nuevo."})


# ── Pro-forma (land residual) ─────────────────────────────────────────────────

@app.get("/api/proforma")
async def proforma_endpoint(
    area_m2: float = Query(...),
    area_estimada: bool = Query(False),
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
                "basado_en_area_estimada": area_estimada,
                "advertencia_area": (
                    "Basado en área estimada: el VRL y la sensibilidad dependen de una "
                    "derivación volumétrica, no de un IC/IO numérico fijado por el Decreto 555."
                    if area_estimada else None
                ),
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
    except Exception:
        logger.exception("Pro-forma failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": "No se pudo calcular el escenario financiero. Intente de nuevo."})


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
    address: str = Query(""),
    searched_address: str = Query(""),
    resolved_address: str = Query(""),
    near_match: bool = Query(False),
    expected_lotcodigo: str | None = Query(None),
    preview: bool = Query(False),
):
    # The current-analysis PDF is a public product export. Saved-history PDFs
    # remain protected by ownership checks on /api/analyses/{id}/report.
    try:
        lu, result = await _calculate_current_lot(
            lng=lng, lat=lat, vis_en_sitio=vis_en_sitio,
            anu_m2=anu_m2, frente_m=frente_m, ancho_via_m=ancho_via_m,
            expected_lotcodigo=expected_lotcodigo,
        )
        _apply_address_identity(
            result,
            address=address,
            searched_address=searched_address,
            resolved_address=resolved_address,
            near_match=near_match,
        )
        loop = asyncio.get_event_loop()
        pdf_bytes = await loop.run_in_executor(
            None, lambda: pdf_report.generate_pdf(
                calc_result=result,
                lookup_snapshot=lu,
                address=result.get("direccion") or address,
            )
        )
        disposition = "inline" if preview else 'attachment; filename="prefactibilidad.pdf"'
        from fastapi.responses import Response
        return Response(content=pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": disposition})
    except CadastralMismatchError as exc:
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "cadastral_mismatch",
            "expected_lotcodigo": exc.expected,
            "resolved_lotcodigo": exc.resolved or None,
            "message": "El punto no corresponde al lote esperado; no se generó el informe.",
        })
    except calc.InputRequired as exc:
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "input_required",
            "field": _detect_missing_field(str(exc)), "message": str(exc),
        })
    except p2_lookup.ZeroFeaturesError as exc:
        return JSONResponse(status_code=200, content={"ok": False, "error": "zero_features", "message": str(exc)})
    except p2_lookup.BuildabilityLookupError:
        return JSONResponse(status_code=200, content={"ok": False, "error": "layer_error", "message": "Una fuente cartográfica no respondió. Intente de nuevo."})
    except Exception:
        logger.exception("PDF report generation failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": "No se pudo generar el informe PDF."})


@app.get("/api/report/html")
async def report_html_endpoint(
    lng: float = Query(...),
    lat: float = Query(...),
    vis_en_sitio: bool = Query(False),
    anu_m2: float | None = Query(None),
    frente_m: float | None = Query(None),
    ancho_via_m: float | None = Query(None),
    address: str = Query(""),
    searched_address: str = Query(""),
    resolved_address: str = Query(""),
    near_match: bool = Query(False),
    expected_lotcodigo: str | None = Query(None),
):
    try:
        lu, result = await _calculate_current_lot(
            lng=lng, lat=lat, vis_en_sitio=vis_en_sitio,
            anu_m2=anu_m2, frente_m=frente_m, ancho_via_m=ancho_via_m,
            expected_lotcodigo=expected_lotcodigo,
        )
        _apply_address_identity(
            result,
            address=address,
            searched_address=searched_address,
            resolved_address=resolved_address,
            near_match=near_match,
        )
        html_str = pdf_report.generate_html_preview(
            calc_result=result,
            lookup_snapshot=lu,
            address=result.get("direccion") or address,
        )
        from fastapi.responses import Response
        return Response(content=html_str, media_type="text/html; charset=utf-8")
    except CadastralMismatchError as exc:
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "cadastral_mismatch",
            "expected_lotcodigo": exc.expected,
            "resolved_lotcodigo": exc.resolved or None,
            "message": "El punto no corresponde al lote esperado; no se generó la vista previa.",
        })
    except calc.InputRequired as exc:
        field = _detect_missing_field(str(exc))
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "input_required", "field": field,
            "message": str(exc), "que_se_necesita": _missing_field_label(field),
        })
    except p2_lookup.ZeroFeaturesError as exc:
        return JSONResponse(status_code=200, content={"ok": False, "error": "zero_features", "message": str(exc)})
    except p2_lookup.BuildabilityLookupError:
        return JSONResponse(status_code=200, content={"ok": False, "error": "layer_error", "message": "Una fuente cartográfica no respondió. Intente de nuevo."})
    except Exception:
        logger.exception("HTML report generation failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": "No se pudo generar la vista previa del informe."})


# ── Saved analyses ────────────────────────────────────────────────────────────

def _beta_local_history_response() -> JSONResponse:
    """Retire account-backed storage without exposing one user's rows to another."""
    return JSONResponse(status_code=410, content={
        "ok": False,
        "error": "beta_local_history",
        "message": (
            "Durante la beta, el historial se guarda localmente en Recientes y no requiere cuenta. "
            "Vuelva a consultar el predio para generar PDF, DXF o un enlace público."
        ),
    })

@app.get("/api/portfolio/lots")
async def portfolio_lots_endpoint(request: Request):
    return _beta_local_history_response()


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
    return _beta_local_history_response()


@app.get("/api/analyses/{analysis_id}")
async def get_analysis_endpoint(analysis_id: str, request: Request):
    return _beta_local_history_response()


@app.patch("/api/analyses/{analysis_id}")
async def update_analysis_endpoint(analysis_id: str, request: Request):
    return _beta_local_history_response()


@app.delete("/api/analyses/{analysis_id}")
async def delete_analysis_endpoint(analysis_id: str, request: Request):
    return _beta_local_history_response()


@app.get("/api/analyses/{analysis_id}/report")
async def analysis_report_endpoint(analysis_id: str, request: Request, preview: bool = Query(False)):
    return _beta_local_history_response()


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
    expected_lotcodigo: str | None = Query(None),
):
    try:
        lu, result = await _calculate_current_lot(
            lng=lng, lat=lat, vis_en_sitio=vis_en_sitio,
            anu_m2=anu_m2, frente_m=frente_m, ancho_via_m=ancho_via_m,
            expected_lotcodigo=expected_lotcodigo,
        )
        loop = asyncio.get_event_loop()
        dxf_bytes = await loop.run_in_executor(
            None, lambda: dxf_export.generate_dxf(calc_result=result, lookup_snapshot=lu)
        )
        return Response(
            content=dxf_bytes,
            media_type="application/dxf",
            headers={"Content-Disposition": 'attachment; filename="edificabilidad.dxf"'},
        )
    except CadastralMismatchError as exc:
        return JSONResponse(status_code=200, content={
            "ok": False, "error": "cadastral_mismatch",
            "expected_lotcodigo": exc.expected, "resolved_lotcodigo": exc.resolved or None,
            "message": "El punto no corresponde al lote esperado; no se generó el DXF.",
        })
    except calc.InputRequired as exc:
        return JSONResponse(status_code=200, content={"ok": False, "error": "input_required", "message": str(exc)})
    except Exception:
        logger.exception("DXF generation failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "internal", "message": "No se pudo generar el archivo DXF."})


# ── VIS/VIP vs No-VIS comparison pro-forma ───────────────────────────────────

@app.get("/api/proforma/vis-comparison")
async def proforma_vis_comparison(
    area_m2: float = Query(...),
    area_vis_m2: float | None = Query(None),   # area_construible_max_vis75_m2 if available
    lng: float | None = Query(None),
    lat: float | None = Query(None),
    precio_lote_cop: float | None = Query(None),
    precio_venta_cop_m2: float | None = Query(None),
    precio_venta_vis_cop_m2: float | None = Query(None),
    costo_construccion_cop_m2: float | None = Query(None),
    costos_blandos_pct: float | None = Query(None),
    margen_objetivo_pct: float | None = Query(None),
):
    """Run the pro-forma for both the base scenario and the VIS≥75% bonus scenario."""
    md = MARKET_DEFAULTS
    lookup_snapshot = {"input": {"lng": lng, "lat": lat}} if (lng is not None and lat is not None) else None

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

    # Keep the comparison auditable: never inject a city-wide VIS sale price or
    # a special target margin that the user did not provide. By default both
    # scenarios use the same explicit sale-price and margin assumptions; only
    # area and construction-cost typology differ.
    vis_precio = precio_venta_vis_cop_m2 if precio_venta_vis_cop_m2 is not None else precio_venta_cop_m2
    vis_costo = md["costos_construccion_cop_m2"]["vis"]["valor"]
    vis_blandos = costos_blandos_pct if costos_blandos_pct is not None else md["costos_blandos_pct"]["valor"]
    vis_margen = margen_objetivo_pct if margen_objetivo_pct is not None else md["margen_objetivo_pct"]["valor"]

    base_result = _run(area_m2, precio_venta_cop_m2, costo_construccion_cop_m2,
                       costos_blandos_pct, margen_objetivo_pct)
    vis_area = area_vis_m2 or area_m2
    vis_result = _run(vis_area, vis_precio, vis_costo, vis_blandos, vis_margen) if vis_precio is not None else None

    return {
        "ok": True,
        "data": {
            "base": base_result,
            "vis": vis_result,
            "vis_area_m2": vis_area,
            "vis_notas": [
                f"Costo construcción VIS: ${vis_costo:,}/m²".replace(",", "."),
                (f"Precio de venta VIS: ${vis_precio:,.0f}/m² — supuesto ingresado para el escenario".replace(",", ".")
                 if vis_precio is not None else "Precio de venta VIS pendiente: ingréselo para comparar escenarios"),
                f"Margen objetivo: {int(vis_margen*100)}% en ambos escenarios; es un supuesto financiero, no una regla del Art. 310.",
                "Aplica sólo si ≥75% del índice efectivo se destina a VIS/VIP (Art. 310 §3 D.555/2021)",
            ],
        },
    }


# ── Risk hazards: flood, landslide, slope ─────────────────────────────────────

# OpenTopoData SRTM 30m
_TOPO_URL = "https://api.opentopodata.org/v1/srtm30m"
_SLOPE_OFFSET_DEG = 0.00045   # ~50 m offset in degrees


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


@app.get("/api/risk/hazards")
async def risk_hazards_endpoint(
    lat: float = Query(...),
    lng: float = Query(...),
):
    """
    Return the one configured terrain-screening signal. Flood and landslide
    remain explicit coverage gaps until verified official layers are wired.
    """
    slope = await _calc_slope_pct(lat, lng)

    # Slope risk classification
    slope_flag = None
    if slope is not None:
        if slope > 25:
            slope_flag = "alta"
        elif slope > 10:
            slope_flag = "media"
        else:
            slope_flag = "baja"

    return {
        "ok": True,
        "data": {
            "estado": "insuficiente",
            "nota": "La pendiente es una aproximación topográfica. Ainmo aún no tiene fuentes oficiales automatizadas para inundación ni movimientos en masa; estos vacíos no significan ausencia de amenaza.",
            "slope_pct": slope,
            "slope_riesgo": slope_flag,
            "slope_nota": (
                "Pendiente >10%: se recomienda estudio geotécnico (NSR-10 Cap. H)."
                if slope and slope > 10 else None
            ),
            "inundacion": None,
            "deslizamiento": None,
            "verificaciones": {
                "pendiente": {"estado": "derivado" if slope is not None else "insuficiente", "valor_pct": slope, "fuente": "OpenTopoData SRTM 30m"},
                "inundacion": {"estado": "insuficiente", "motivo": "No hay una fuente oficial automatizada configurada para esta verificación."},
                "movimientos_en_masa": {"estado": "insuficiente", "motivo": "No hay una fuente oficial automatizada configurada para esta verificación."},
            },
            "fuente_slope": "OpenTopoData SRTM 30m",
            "fuente_amenaza": None,
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


def _missing_field_label(field: str) -> str:
    return {
        "anu_m2": "el Área Neta Urbanizable (ANU) aprobada en el Plan Parcial",
        "frente_m": "la medida del frente del lote en metros",
        "ancho_via_m": "el perfil vial completo en metros",
        "antejardin": "la dimensión oficial del antejardín",
    }.get(field, "el dato requerido indicado por el cálculo")


# ── Share links ───────────────────────────────────────────────────────────────

@app.post("/api/analyses/{analysis_id}/share")
async def create_share_endpoint(
    analysis_id: str,
    request: Request,
    include_proforma: bool = Query(False),
):
    return _beta_local_history_response()


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
