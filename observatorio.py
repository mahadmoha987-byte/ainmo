"""Curated, evidence-led articles for the public Observatorio Ainmo.

Unlike the locality SEO pages, these articles document reproducible checks
against primary material.  They are deliberately hand-reviewed and finite;
arbitrary slugs must never generate indexable thin content.
"""

from __future__ import annotations

from datetime import date


SITE_URL = "https://ainmo.uk"
CONTENT_UPDATED = date(2026, 10, 4).isoformat()


ARTICLES = {
    "ainmo-frente-concepto-urbanistico-cl-85": {
        "slug": "ainmo-frente-concepto-urbanistico-cl-85",
        "kicker": "CASO 001 · AUDITORÍA REPRODUCIBLE",
        "title": "Un lote, dos lecturas: Ainmo frente a un concepto urbanístico oficial",
        "meta_description": (
            "Comparamos la lectura de Ainmo para CL 85 # 11-35 con el concepto "
            "urbanístico CU3-24-2182 de Bogotá, hallazgo por hallazgo y con fuentes visibles."
        ),
        "dek": (
            "Tomamos un predio real de Chapinero y contrastamos su identificación, tratamiento "
            "y afectaciones con el concepto CU3-24-2182. La comparación muestra dónde coincide "
            "la prefactibilidad automática y qué todavía exige verificación profesional."
        ),
        "published": "2026-10-04",
        "published_label": "4 de octubre de 2026",
        "document_date": "24 de mayo de 2024",
        "address": "CL 85 # 11-35",
        "locality": "Chapinero",
        "lotcodigo": "008310012021",
        "concept": "CU3-24-2182",
        "app_url": "/app?lat=4.667958459499999&lng=-74.05200009700002",
        "summary": {
            "compared": 9,
            "matches": 9,
            "differences": 0,
            "pending": 0,
        },
        "comparison": [
            {
                "field": "Identificación del predio",
                "official": "CL 85 # 11-35 · Localidad de Chapinero",
                "ainmo": "CL 85 # 11-35 · Chapinero · lote 008310012021",
                "status": "Coincide",
                "note": "Ainmo conserva por separado la dirección y el código físico del lote.",
            },
            {
                "field": "Tratamiento urbanístico",
                "official": "Renovación Urbana",
                "ainmo": "Renovación Urbana",
                "status": "Coincide",
                "note": "La lectura se realiza sobre el polígono catastral, no por el nombre del barrio.",
            },
            {
                "field": "Área de actividad",
                "official": "Grandes Servicios Metropolitanos",
                "ainmo": "Grandes Servicios Metropolitanos (AAGSM)",
                "status": "Coincide",
                "note": "La sigla es el código cartográfico que Ainmo conserva para aplicar las reglas de usos y parqueo.",
            },
            {
                "field": "Sector consolidado",
                "official": "Incluido en el mapa CU-5.3 Sectores Consolidados",
                "ainmo": "Aplica · hallazgo cartográfico resuelto",
                "status": "Coincide",
                "note": "Es un hallazgo distinto del tratamiento; no se sustituyen entre sí.",
            },
            {
                "field": "Área de Desarrollo Naranja",
                "official": "Distrito Creativo La 85",
                "ainmo": "Distrito Creativo La 85",
                "status": "Coincide",
                "note": "El resultado identifica el polígono; no presume que una actividad específica quede autorizada.",
            },
            {
                "field": "Protección patrimonial",
                "official": "Dentro del área de protección de 100 m de un BIC",
                "ainmo": "Intersección detectada · requiere concepto del IDPC",
                "status": "Coincide",
                "note": "Ainmo muestra el hallazgo, pero no reemplaza la aprobación patrimonial previa al licenciamiento.",
            },
            {
                "field": "Reserva vial",
                "official": "Reserva parcial por la Av. José María Escrivá de Balaguer",
                "ainmo": "Reserva parcial · 76,6 m², equivalentes al 25,5 % del lote",
                "status": "Coincide",
                "note": "La medición automática agrega magnitud al hallazgo; la delimitación debe verificarse antes de diseñar o comprar.",
            },
            {
                "field": "Antejardín",
                "official": "No se exige de manera general en Renovación Urbana, salvo reglas de empate",
                "ainmo": "No aplica · dimensión general 0,0 m, con advertencia sobre empates",
                "status": "Coincide",
                "note": "Cero es una regla explícita en este contexto, no un dato faltante.",
            },
            {
                "field": "Aislamiento lateral",
                "official": "Se exige desde 11,40 m; dimensión mínima de 1/5 de la altura, nunca menor de 4 m",
                "ainmo": "Umbral 11,40 m · max(1/5 × altura, 4,00 m)",
                "status": "Coincide",
                "note": "La dimensión final depende de la altura que alcance el proyecto.",
            },
        ],
        "method": [
            "Se identificó el lote por dirección, coordenada y LOTCODIGO antes de comparar resultados.",
            "Se transcribieron únicamente afirmaciones expresas del concepto CU3-24-2182 y se agruparon por variable.",
            "Se ejecutó el mismo camino de cálculo que alimenta el informe web y el PDF de Ainmo.",
            "Cada fila se marcó como coincidencia solo cuando ambos lados describían la misma condición del predio.",
        ],
        "limits": [
            {
                "title": "La protección patrimonial no queda resuelta por un cruce espacial",
                "body": "El hallazgo activa una consulta profesional. El concepto revisado exige aprobación del IDPC antes del licenciamiento.",
            },
            {
                "title": "La reserva vial exige confirmar el plano aplicable",
                "body": "Ainmo calcula 76,6 m² dentro de la franja, pero una decisión de inversión debe usar la delimitación oficial vigente y revisión técnica.",
            },
            {
                "title": "La lectura normativa no es una licencia",
                "body": "El resultado sirve para prefactibilidad y priorización. No certifica derechos de construcción ni reemplaza a la curaduría o a la autoridad competente.",
            },
        ],
        "sources": [
            {
                "name": "Concepto de norma CU3-24-2182",
                "detail": "Curaduría Urbana 3 de Bogotá · 24 de mayo de 2024 · documento de 32 páginas revisado para este caso.",
                "url": None,
            },
            {
                "name": "Decreto Distrital 555 de 2021",
                "detail": "Plan de Ordenamiento Territorial de Bogotá y reglas citadas en el concepto.",
                "url": "https://www.alcaldiabogota.gov.co/sisjur/normas/Norma1.jsp?i=119582",
            },
            {
                "name": "Decreto Distrital 466 de 2024",
                "detail": "Modificación normativa y Anexo 5 usados por Ainmo para la lectura vigente de antejardines y aislamientos.",
                "url": "https://www.alcaldiabogota.gov.co/sisjur/normas/Norma1.jsp?i=161605",
            },
            {
                "name": "Cartografía POT e identificación catastral",
                "detail": "Capas públicas de la SDP y Catastro Bogotá consultadas por el motor para el polígono del lote.",
                "url": "https://datosabiertos.bogota.gov.co/group/ordenamiento-territorial",
            },
        ],
    },
}


def get_article(slug: str) -> dict | None:
    article = ARTICLES.get(slug)
    if article is None:
        return None
    return {
        **article,
        "path": f"/observatorio/{slug}",
        "canonical": f"{SITE_URL}/observatorio/{slug}",
        "updated": CONTENT_UPDATED,
    }


def article_list() -> list[dict]:
    return [get_article(slug) for slug in ARTICLES]


def sitemap_entries() -> list[dict]:
    return [
        {"loc": f"{SITE_URL}/observatorio", "lastmod": CONTENT_UPDATED},
        *[
            {"loc": f"{SITE_URL}/observatorio/{slug}", "lastmod": CONTENT_UPDATED}
            for slug in ARTICLES
        ],
    ]
