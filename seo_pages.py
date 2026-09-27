"""Curated, indexable landing pages for Ainmo's organic-search library.

The registry is intentionally small.  A page is public only after its copy,
sources and example have been reviewed; arbitrary slugs never generate thin
pages.  Lot-specific calculations still happen in the live application.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path


SITE_URL = "https://ainmo.uk"
CONTENT_UPDATED = date(2026, 9, 26).isoformat()

OFFICIAL_SOURCES = [
    {
        "name": "Decreto Distrital 555 de 2021",
        "detail": "Texto del Plan de Ordenamiento Territorial de Bogotá y referencias de vigencia.",
        "url": "https://www.alcaldiabogota.gov.co/sisjur/normas/Norma1.jsp?i=119582",
    },
    {
        "name": "Datos Abiertos Bogotá · Ordenamiento Territorial",
        "detail": "Cartografía oficial de tratamientos, áreas de actividad y otros componentes del POT.",
        "url": "https://datosabiertos.bogota.gov.co/group/ordenamiento-territorial",
    },
    {
        "name": "Unidad Administrativa Especial de Catastro Distrital",
        "detail": "Identificación y geometría catastral de los predios consultados.",
        "url": "https://www.catastrobogota.gov.co/",
    },
]


PAGES = {
    ("localidad", "chapinero"): {
        "path": "/bogota/chapinero",
        "kind": "localidad",
        "breadcrumb": "Chapinero",
        "title": "Qué se puede construir en Chapinero — norma y edificabilidad",
        "meta_description": (
            "Consulte tratamiento, edificabilidad, altura, aislamientos y restricciones "
            "de un predio en Chapinero con fuentes del POT y Catastro Bogotá."
        ),
        "eyebrow": "NORMA URBANÍSTICA · CHAPINERO",
        "h1": "¿Qué se puede construir en Chapinero?",
        "lede": (
            "La respuesta cambia lote a lote. Ainmo cruza el polígono catastral con la "
            "cartografía del POT y explica qué está resuelto, qué es derivado y qué exige "
            "verificación profesional."
        ),
        "search_placeholder": "Ej. CL 85 # 11-35",
        "intro_title": "Chapinero no tiene una sola regla de edificabilidad",
        "intro": [
            (
                "En una misma localidad pueden coexistir tratamientos, áreas de actividad, "
                "reservas, condiciones patrimoniales e instrumentos especiales distintos. "
                "Por eso una respuesta general por barrio nunca sustituye la consulta del predio."
            ),
            (
                "Ainmo empieza por confirmar el lote y después conserva la trazabilidad de cada "
                "resultado: artículo, capa cartográfica y fecha de consulta."
            ),
        ],
        "facts": [
            {
                "title": "El tratamiento se consulta por polígono",
                "body": (
                    "La cartografía oficial determina si el predio está en Renovación Urbana, "
                    "Consolidación u otro tratamiento; el nombre del barrio no basta."
                ),
                "citation": "POT Bogotá · capa de tratamiento urbanístico",
            },
            {
                "title": "Patrimonio y reservas se muestran aparte",
                "body": (
                    "Una superposición patrimonial o vial puede modificar la lectura inicial. "
                    "Ainmo la presenta como hallazgo y no como una ausencia silenciosa."
                ),
                "citation": "D.555/2021 y cartografía distrital aplicable",
            },
            {
                "title": "En Renovación, el ICe es el control numérico",
                "body": (
                    "El artículo 304 define escenarios de ICe. La altura y el índice de ocupación "
                    "resultan de las reglas volumétricas y del proyecto."
                ),
                "citation": "D.555/2021 · Art. 304 · Anexo 5 D.466/2024",
            },
        ],
        "example": {
            "label": "CASO DE REFERENCIA",
            "title": "CL 85 # 11-35",
            "meta": "Lote 008310012021 · 300,5 m²",
            "result": "Renovación Urbana · ICe base 5,0",
            "status": "Resultado contrastado con un concepto urbanístico oficial",
            "note": (
                "La consulta también identifica ADN La 85, sector consolidado, protección BIC "
                "y una reserva vial parcial. Cada condición debe leerse en su propio alcance."
            ),
            "url": "/app?lat=4.6679584595&lng=-74.052000097&q=CL%2085%20%23%2011-35",
        },
        "faqs": [
            {
                "question": "¿Todo Chapinero permite la misma altura?",
                "answer": (
                    "No. La altura depende del tratamiento, la cartografía aplicable, el perfil "
                    "vial, los aislamientos, las condiciones patrimoniales y el proyecto concreto."
                ),
            },
            {
                "question": "¿Ainmo reemplaza un concepto de norma o una licencia?",
                "answer": (
                    "No. Es una consulta de prefactibilidad para orientar la revisión inicial. "
                    "La decisión y el trámite deben verificarse con los profesionales y autoridades competentes."
                ),
            },
        ],
        "related": [
            ("Renovación Urbana", "/guias/renovacion-urbana-decreto-555"),
            ("Consultar Usaquén", "/bogota/usaquen"),
            ("Guía de Consolidación", "/guias/tratamiento-consolidacion-decreto-555"),
        ],
    },
    ("localidad", "usaquen"): {
        "path": "/bogota/usaquen",
        "kind": "localidad",
        "breadcrumb": "Usaquén",
        "title": "Qué se puede construir en Usaquén — norma y edificabilidad",
        "meta_description": (
            "Analice un predio en Usaquén: tratamiento, altura base, aislamientos, "
            "área estimada y fuentes oficiales del POT de Bogotá."
        ),
        "eyebrow": "NORMA URBANÍSTICA · USAQUÉN",
        "h1": "¿Qué se puede construir en Usaquén?",
        "lede": (
            "Consulte el lote exacto antes de asumir pisos o edificabilidad. Ainmo separa los "
            "límites fijados por la norma de las estimaciones que dependen de la geometría."
        ),
        "search_placeholder": "Ej. Calle 127 # 15-30",
        "intro_title": "Una dirección cercana no equivale al predio correcto",
        "intro": [
            (
                "Usaquén contiene tejidos y condiciones normativas diferentes. Una búsqueda debe "
                "confirmar el polígono catastral y no limitarse a reconocer la calle o el sector."
            ),
            (
                "Cuando Catastro no resuelve el número exacto, Ainmo muestra candidatos o declara "
                "la aproximación. Nunca debería mezclar la dirección buscada con los datos de otro lote."
            ),
        ],
        "facts": [
            {
                "title": "La altura base puede estar resuelta",
                "body": (
                    "En Consolidación, la capa de edificabilidad puede fijar una altura base aun "
                    "cuando los índices de construcción y ocupación sean resultantes."
                ),
                "citation": "D.555/2021 · Art. 310 · POT capa de edificabilidad",
            },
            {
                "title": "El área construible puede ser una estimación",
                "body": (
                    "Cuando la norma no fija un IC numérico, Ainmo puede derivar un rango desde la "
                    "huella y los pisos. El informe lo marca como derivado, nunca como cifra del decreto."
                ),
                "citation": "Método huella × pisos · supuestos visibles",
            },
            {
                "title": "Las faltas de información conservan su estado",
                "body": (
                    "Si una capa no devuelve antejardín, frente o una condición necesaria, el informe "
                    "explica qué falta y quién debe resolverlo."
                ),
                "citation": "Trazabilidad Ainmo · consulta de capas POT y Catastro",
            },
        ],
        "example": {
            "label": "FIXTURE DE CONTROL",
            "title": "Predio 008403014001",
            "meta": "Usaquén · 450,8 m² · Consolidación",
            "result": "Altura base cartográfica: 3 pisos",
            "status": "Identificación aproximada: confianza degradada",
            "note": (
                "Este caso prueba que un ajuste al predio cercano debe quedar visible. La selección "
                "catastral y la volumetría requieren confirmación antes de usar la estimación."
            ),
            "url": "/app",
        },
        "faqs": [
            {
                "question": "¿Por qué dos lotes cercanos pueden tener resultados diferentes?",
                "answer": (
                    "Porque pueden cruzar polígonos normativos distintos, tener geometrías diferentes "
                    "o estar afectados por condiciones específicas que no siguen los límites del barrio."
                ),
            },
            {
                "question": "¿Qué ocurre si la dirección no coincide exactamente?",
                "answer": (
                    "Ainmo debe mostrar candidatos cercanos para que el usuario elija. El informe "
                    "identifica la dirección realmente analizada y conserva la búsqueda original como advertencia."
                ),
            },
        ],
        "related": [
            ("Guía de Consolidación", "/guias/tratamiento-consolidacion-decreto-555"),
            ("Consultar Chapinero", "/bogota/chapinero"),
            ("Consultar Suba", "/bogota/suba"),
        ],
    },
    ("localidad", "suba"): {
        "path": "/bogota/suba",
        "kind": "localidad",
        "breadcrumb": "Suba",
        "title": "Qué se puede construir en Suba — norma y edificabilidad",
        "meta_description": (
            "Consulte el potencial normativo de un lote en Suba y vea tratamiento, área, "
            "altura, restricciones y límites del modelo con fuentes oficiales."
        ),
        "eyebrow": "NORMA URBANÍSTICA · SUBA",
        "h1": "¿Qué se puede construir en Suba?",
        "lede": (
            "El tamaño y la forma del lote importan tanto como su tratamiento. Ainmo automatiza "
            "la lectura inicial y se detiene cuando el predio exige una modelación profesional."
        ),
        "search_placeholder": "Ingrese una dirección o CHIP en Suba",
        "intro_title": "Los lotes grandes no deben tratarse como predios urbanos corrientes",
        "intro": [
            (
                "Una fórmula de huella pensada para un lote convencional puede producir resultados "
                "engañosos en parques, predios institucionales o grandes extensiones."
            ),
            (
                "Ainmo aplica límites de alcance: cuando el tamaño o los resultados quedan fuera del "
                "rango confiable, lo informa en vez de presentar una cifra exagerada como edificabilidad real."
            ),
        ],
        "facts": [
            {
                "title": "Área y geometría se verifican primero",
                "body": (
                    "El polígono catastral aporta el área y la forma que alimentan cualquier cálculo "
                    "posterior; una coincidencia de dirección por sí sola no es suficiente."
                ),
                "citation": "Catastro Bogotá · geometría del lote",
            },
            {
                "title": "El modelo tiene límites explícitos",
                "body": (
                    "Los predios mayores a una hectárea o con resultados volumétricos implausibles "
                    "se señalan fuera del rango del estimador automático."
                ),
                "citation": "Control de alcance de la prefactibilidad Ainmo",
            },
            {
                "title": "Un resultado negativo no se oculta",
                "body": (
                    "La ausencia de una base calculable, una restricción o un supuesto financiero "
                    "inválido debe aparecer como estado y motivo, no como un número aparentemente confiable."
                ),
                "citation": "Estados de resultado y trazabilidad Ainmo",
            },
        ],
        "example": {
            "label": "CASO DE LÍMITE DEL MODELO",
            "title": "Predio 009241036001",
            "meta": "Suba · 7.771,7 m²",
            "result": "El tamaño exige revisar el alcance del estimador",
            "status": "La norma se consulta; la cabida automática no se presenta como definitiva",
            "note": (
                "Este caso sirve para separar una lectura normativa disponible de una modelación "
                "geométrica que requiere información y trabajo profesional adicionales."
            ),
            "url": "/app?lat=4.745&lng=-74.085",
        },
        "faqs": [
            {
                "question": "¿Ainmo calcula automáticamente cualquier lote grande?",
                "answer": (
                    "No. Puede consultar la cartografía y las reglas disponibles, pero detiene o "
                    "degrada las estimaciones que quedan fuera del alcance geométrico confiable."
                ),
            },
            {
                "question": "¿Una estimación es equivalente al área aprobable?",
                "answer": (
                    "No. Una estimación sirve para prefactibilidad y conserva sus supuestos. El área "
                    "aprobable depende del diseño, los instrumentos, las obligaciones y el trámite aplicable."
                ),
            },
        ],
        "related": [
            ("Guía de Consolidación", "/guias/tratamiento-consolidacion-decreto-555"),
            ("Consultar Usaquén", "/bogota/usaquen"),
            ("Renovación Urbana", "/guias/renovacion-urbana-decreto-555"),
        ],
    },
    ("guia", "tratamiento-consolidacion-decreto-555"): {
        "path": "/guias/tratamiento-consolidacion-decreto-555",
        "kind": "guia",
        "breadcrumb": "Tratamiento de Consolidación",
        "title": "Tratamiento de Consolidación en Bogotá — Decreto 555",
        "meta_description": (
            "Entienda cómo se leen altura, IC, IO, aislamientos y área estimada para "
            "predios en tratamiento de Consolidación bajo el Decreto 555."
        ),
        "eyebrow": "GUÍA AINMO · DECRETO 555",
        "h1": "Tratamiento de Consolidación: qué está fijado y qué es resultante",
        "lede": (
            "En Consolidación, una cifra ausente no significa que la herramienta falló. El Decreto "
            "555 establece que el IC y el IO resultan de la volumetría aplicable al predio."
        ),
        "search_placeholder": "Pruebe una dirección en Bogotá",
        "intro_title": "La distinción más importante: norma versus estimación",
        "intro": [
            (
                "El artículo 310 regula la edificabilidad de Consolidación. La altura base puede "
                "provenir de la cartografía, mientras los índices de construcción y ocupación no "
                "necesariamente aparecen como topes numéricos."
            ),
            (
                "Ainmo mantiene el valor normativo como no aplicable cuando corresponde y coloca "
                "cualquier aproximación de área en un campo derivado, con método, rango y supuestos visibles."
            ),
        ],
        "facts": [
            {
                "title": "Altura base",
                "body": (
                    "Se consulta en la capa de edificabilidad. Si existe un rango, el acceso al límite "
                    "superior depende de las condiciones del artículo 310."
                ),
                "citation": "D.555/2021 · Art. 310 · POT capa 15",
            },
            {
                "title": "IC e IO resultantes",
                "body": (
                    "No deben presentarse como un índice inventado. Emergen de la altura, la huella, "
                    "los aislamientos, el antejardín y las demás reglas volumétricas."
                ),
                "citation": "D.555/2021 · Art. 310, numeral 1 · Anexo 5",
            },
            {
                "title": "Área construible estimada",
                "body": (
                    "Puede calcularse como huella por pisos cuando las entradas son suficientes. "
                    "Debe conservar una confianza máxima media y bajar si falta algún retiro."
                ),
                "citation": "Estimación derivada · no es una cifra fijada por el decreto",
            },
        ],
        "example": {
            "label": "EJEMPLO DE CONSOLIDACIÓN",
            "title": "KR 78K 6 35 SUR",
            "meta": "Lote 004514061017 · 91,6 m² · CHIP AAA0044ODRJ",
            "result": "Altura base: 5 pisos · área derivada: 204,0 m²",
            "status": "Altura resuelta; área construible claramente marcada como derivada",
            "note": (
                "El ejemplo muestra por qué el informe conserva por separado la cifra cartográfica "
                "y la estimación volumétrica."
            ),
            "url": "/app?q=AAA0044ODRJ",
        },
        "faqs": [
            {
                "question": "¿Consolidación significa que no se puede construir?",
                "answer": (
                    "No. Significa que la edificabilidad se lee con las reglas propias de ese "
                    "tratamiento. La altura y la volumetría pueden permitir desarrollo, sujeto al predio y al proyecto."
                ),
            },
            {
                "question": "¿Por qué el IC puede aparecer como no aplicable?",
                "answer": (
                    "Porque el decreto no siempre fija un IC numérico para Consolidación. Mostrar cero "
                    "o inventar un índice confundiría silencio normativo con prohibición."
                ),
            },
        ],
        "related": [
            ("Consultar Usaquén", "/bogota/usaquen"),
            ("Consultar Suba", "/bogota/suba"),
            ("Renovación Urbana", "/guias/renovacion-urbana-decreto-555"),
        ],
    },
    ("guia", "renovacion-urbana-decreto-555"): {
        "path": "/guias/renovacion-urbana-decreto-555",
        "kind": "guia",
        "breadcrumb": "Renovación Urbana",
        "title": "Renovación Urbana en Bogotá — ICe y edificabilidad",
        "meta_description": (
            "Guía del tratamiento de Renovación Urbana: ICe 5,0, 6,0 y 7,0, "
            "altura resultante, aislamientos y fuentes del Decreto 555."
        ),
        "eyebrow": "GUÍA AINMO · RENOVACIÓN URBANA",
        "h1": "Renovación Urbana: el ICe controla el área, no un número fijo de pisos",
        "lede": (
            "El artículo 304 define escenarios de índice de construcción efectivo según el ámbito "
            "del proyecto. La altura y la ocupación se resuelven con la volumetría del Anexo 5."
        ),
        "search_placeholder": "Ej. CL 85 # 11-35",
        "intro_title": "Tres escenarios antes del Plan Parcial",
        "intro": [
            (
                "El escenario base usa ICe 5,0 cuando la licencia no incluye todos los predios de "
                "una manzana. Englobar una esquina con la cesión aplicable permite estudiar ICe 6,0; "
                "la manzana completa permite estudiar ICe 7,0."
            ),
            (
                "Superar ICe 7,0 requiere un Plan Parcial y no debe presentarse como un derecho "
                "automático del lote individual."
            ),
        ],
        "facts": [
            {
                "title": "ICe 5,0",
                "body": "Escenario base sin incorporar la totalidad de los predios de una manzana.",
                "citation": "D.555/2021 · Art. 304, numeral 1",
            },
            {
                "title": "ICe 6,0 e ICe 7,0",
                "body": (
                    "Son escenarios condicionados por el ámbito del proyecto: esquina de manzana "
                    "con la cesión aplicable o totalidad de la manzana."
                ),
                "citation": "D.555/2021 · Art. 304, numerales 2 y 3",
            },
            {
                "title": "Altura y aislamientos",
                "body": (
                    "La altura es resultante. Los aislamientos se consultan según la altura efectiva "
                    "y la relación volumétrica definida en el Anexo 5."
                ),
                "citation": "Anexo 5 D.466/2024",
            },
        ],
        "example": {
            "label": "EJEMPLO DE RENOVACIÓN URBANA",
            "title": "CL 85 # 11-35",
            "meta": "Lote 008310012021 · 300,5 m²",
            "result": "ICe 5,0: 1.502,5 m² · ICe 6,0: 1.803,0 m² · ICe 7,0: 2.103,5 m²",
            "status": "Escenario base resuelto; escenarios superiores sujetos a sus condiciones",
            "note": (
                "La altura no se convierte en un número de pisos inventado. El informe presenta la "
                "altura como resultante y conserva las restricciones superpuestas."
            ),
            "url": "/app?lat=4.6679584595&lng=-74.052000097&q=CL%2085%20%23%2011-35",
        },
        "faqs": [
            {
                "question": "¿Todo lote en Renovación Urbana tiene derecho a ICe 7,0?",
                "answer": (
                    "No. El ICe 7,0 corresponde al proyecto que incluye la totalidad de los predios "
                    "de una manzana. El lote aislado conserva el escenario que realmente puede sustentar."
                ),
            },
            {
                "question": "¿Cuántos pisos permite Renovación Urbana?",
                "answer": (
                    "El tratamiento no fija por sí solo un número universal de pisos. La altura resulta "
                    "de la volumetría, el perfil vial, los aislamientos y las restricciones aplicables."
                ),
            },
        ],
        "related": [
            ("Consultar Chapinero", "/bogota/chapinero"),
            ("Guía de Consolidación", "/guias/tratamiento-consolidacion-decreto-555"),
            ("Consultar Suba", "/bogota/suba"),
        ],
    },
}


_CATALOG_PATH = Path(__file__).with_name("data") / "seo_location_catalog.json"
_TREATMENT_NAMES = {
    "Consolidacion": "Consolidación",
    "Renovacion": "Renovación Urbana",
    "Conservacion": "Conservación",
    "Mejoramiento Integral": "Mejoramiento Integral",
    "Desarrollo": "Desarrollo",
}


def _treatment_name(value: str) -> str:
    return _TREATMENT_NAMES.get(value, value)


def _activity_name(value: str) -> str:
    if "Proximidad" in value:
        return "Proximidad"
    if "Estructurante" in value:
        return "Estructurante"
    if "Grandes Servicios" in value:
        return "Grandes Servicios Metropolitanos"
    if "PEMP" in value.upper():
        return "PEMP / protección patrimonial"
    return value.split(" - ")[0].strip()


def _mix_sentence(items: list[dict], formatter) -> str:
    if not items:
        return "La capa consultada no devuelve cobertura para este ámbito. El predio exige verificación individual."
    return " · ".join(
        f"{formatter(item['name'])} {str(item['pct']).replace('.', ',')} %"
        for item in items[:4]
    )


def _catalog_page(item: dict, *, sector: bool, consulted: str) -> dict:
    name = item["name"]
    locality = item.get("locality", {"name": name, "slug": item["slug"]})
    treatment_mix = _mix_sentence(item["treatments"], _treatment_name)
    activity_mix = _mix_sentence(item["activities"], _activity_name)
    dominant = _treatment_name(item["treatments"][0]["name"]) if item["treatments"] else None
    dominant_pct = str(item["treatments"][0]["pct"]).replace(".", ",") if item["treatments"] else None
    if sector:
        path = f"/bogota/{locality['slug']}/{item['slug']}"
        kind = "sector"
        eyebrow = f"SECTOR CATASTRAL · {locality['name'].upper()}"
        h1 = f"Norma urbanística y edificabilidad en {name}"
        context = (
            f"{name} es un sector catastral oficial dentro de {locality['name']}. "
            "La distribución cartográfica ayuda a entender el contexto, pero el resultado "
            "de edificabilidad solo puede afirmarse después de seleccionar el lote exacto."
        )
        related = [
            (f"Ver {locality['name']}", f"/bogota/{locality['slug']}"),
            ("Guía de Consolidación", "/guias/tratamiento-consolidacion-decreto-555"),
            ("Guía de Renovación", "/guias/renovacion-urbana-decreto-555"),
        ]
    else:
        path = f"/bogota/{item['slug']}"
        kind = "localidad"
        eyebrow = f"NORMA URBANÍSTICA · {name.upper()}"
        h1 = f"¿Qué se puede construir en {name}?"
        context = (
            f"{name} contiene varios tratamientos y áreas de actividad. Estas proporciones "
            "describen la superficie cartográfica consultada, no otorgan derechos de construcción "
            "a un predio particular."
        )
        related = [
            ("Todas las localidades", "/bogota"),
            ("Guía de Consolidación", "/guias/tratamiento-consolidacion-decreto-555"),
            ("Guía de Renovación", "/guias/renovacion-urbana-decreto-555"),
        ]

    return {
        "path": path,
        "kind": kind,
        "breadcrumb": name,
        "parent": locality if sector else None,
        "title": f"Qué se puede construir en {name} — POT de Bogotá",
        "meta_description": (
            f"Consulte tratamiento, altura, edificabilidad, aislamientos y restricciones "
            f"para un predio en {name}, Bogotá, con fuentes oficiales del POT y Catastro."
        ),
        "eyebrow": eyebrow,
        "h1": h1,
        "lede": (
            f"En la cartografía consultada, {dominant} es el tratamiento de mayor superficie "
            f"en este ámbito ({dominant_pct} %). La regla aplicable, sin embargo, se confirma lote a lote."
            if dominant
            else "La capa urbana de tratamiento no devuelve cobertura para este ámbito. Ainmo no infiere una regla: el predio debe verificarse en su régimen territorial aplicable."
        ),
        "search_placeholder": f"Dirección, CHIP o intersección en {name}",
        "intro_title": f"El contexto de {name} no reemplaza la consulta predial",
        "intro": [
            context,
            (
                "Ainmo cruza el polígono seleccionado con la cartografía vigente y separa las "
                "cifras resueltas por norma de las derivadas, insuficientes o sujetas a concepto."
            ),
        ],
        "facts": [
            {
                "title": "Mezcla de tratamientos",
                "body": treatment_mix,
                "citation": f"POT capa 15 · consulta {consulted} · proporción de superficie",
            },
            {
                "title": "Áreas de actividad",
                "body": activity_mix,
                "citation": f"POT capa 14 · consulta {consulted} · proporción de superficie",
            },
            {
                "title": "La dirección no decide la norma",
                "body": (
                    "Dos predios cercanos pueden cruzar polígonos normativos o restricciones distintas. "
                    "Seleccione y confirme el lote antes de usar una cifra."
                ),
                "citation": "Catastro Bogotá + cartografía POT · cruce por polígono",
            },
        ],
        "example": {
            "label": "CONSULTA PREDIAL",
            "title": f"Analice un lote en {name}",
            "meta": f"Fuente territorial: {'sector ' + item['code'] if sector else 'localidad ' + item['code']}",
            "result": "Tratamiento, edificabilidad, volumetría y restricciones en un solo informe",
            "status": "El resultado se genera para el polígono catastral elegido",
            "note": (
                "Las proporciones de esta página sirven para contexto. El informe del predio vuelve "
                "a consultar las fuentes y muestra el estado y la cita de cada figura."
            ),
            "url": "/app",
        },
        "faqs": [
            {
                "question": f"¿Todo {name} tiene el mismo tratamiento?",
                "answer": (
                    "No. La cartografía muestra más de un tratamiento dentro de este ámbito. "
                    "El tratamiento del lote se obtiene por intersección con su polígono, no por el nombre del lugar."
                ),
            },
            {
                "question": "¿Estas proporciones indican cuántos pisos puedo construir?",
                "answer": (
                    "No. Son contexto territorial. Los pisos o la altura resultan de la capa de "
                    "edificabilidad, las reglas del tratamiento, la geometría y las restricciones del lote."
                ),
            },
        ],
        "related": related,
    }


def _load_catalog_pages() -> None:
    if not _CATALOG_PATH.exists():
        return
    catalog = json.loads(_CATALOG_PATH.read_text())
    consulted = catalog["consulted"]
    for item in catalog["localities"]:
        PAGES.setdefault(
            ("localidad", item["slug"]),
            _catalog_page(item, sector=False, consulted=consulted),
        )
    for item in catalog["sectors"]:
        PAGES[("sector", f"{item['locality']['slug']}/{item['slug']}")] = _catalog_page(
            item, sector=True, consulted=consulted
        )


_load_catalog_pages()


def get_page(kind: str, slug: str) -> dict | None:
    page = PAGES.get((kind, slug))
    if not page:
        return None
    return {**page, "canonical": f"{SITE_URL}{page['path']}", "updated": CONTENT_UPDATED}


def pages_for_kind(kind: str) -> list[dict]:
    return [get_page(k, slug) for (k, slug) in PAGES if k == kind]


def sitemap_entries() -> list[dict]:
    return [
        {"loc": f"{SITE_URL}{page['path']}", "lastmod": CONTENT_UPDATED}
        for page in PAGES.values()
    ]
