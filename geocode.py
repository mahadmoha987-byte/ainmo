"""
geocode.py — Bogotá address → coordinates

Priority:
1. Catastro placadomiciliaria (esriGeometryPoint, authoritative source)
2. Nominatim / OpenStreetMap (street-level fallback)
"""

import re, ssl, json, time, math, unicodedata, urllib.request, urllib.parse, difflib

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_CATASTRO_PLACA = (
    "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services"
    "/catastro/placadomiciliaria/MapServer/0"
)
_CATASTRO_LOTE = (
    "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services"
    "/catastro/lote/MapServer/0"
)
_CATASTRO_PREDIO = (
    "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services"
    "/catastro/lote/MapServer/3"
)
_MAPA_REFERENCIA_VIAS = (
    "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services"
    "/Mapa_Referencia/Mapa_Referencia/MapServer/11"
)
_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_BOGOTA_VIEWBOX = "-74.25,4.45,-73.99,4.83"

# When a street type returns 0 results, also try its avenue counterpart.
# Many major streets are registered under AC/AK instead of CL/KR.
_ALT_CODE: dict[str, str] = {"CL": "AC", "KR": "AK"}

# Via-type keywords used in intersection detection.
_VIA_WORDS_RE = re.compile(
    r'\b(?:CALLE|CLLE|CLL|CL|CARRERA|CRA|CARR|KRA?|CR|DIAGONAL|DIAG|DG|'
    r'TRANSVERSAL|TRANSV|TRV|TV|AVENIDA|AVDA|AVE|AV|AK|AC)\b',
    re.I,
)

# Bogotá's urban bounding box — results outside this are rejected.
_BOGOTA_LAT = (4.45, 4.83)
_BOGOTA_LNG = (-74.25, -73.99)

# UAECD's Código Homologado de Identificación Predial is an 11-character
# cadastral identifier. The current public Predio table stores it in PRECHIP.
_CHIP_RE = re.compile(r"^[A-Z]{3}\d{4}[A-Z]{4}$", re.I)
_LOT_PROPERTIES_CACHE_TTL = 86_400
_lot_properties_cache: dict[str, tuple[float, list[dict]]] = {}
_OUTSIDE_BOGOTA_CITY_RE = re.compile(
    r"\b(?P<city>MEDELLIN|BELLO|ENVIGADO|ITAGUI|SABANETA|RIONEGRO|CALI|PALMIRA|"
    r"BARRANQUILLA|SOLEDAD|CARTAGENA|BUCARAMANGA|FLORIDABLANCA|CUCUTA|"
    r"PEREIRA|DOSQUEBRADAS|MANIZALES|ARMENIA|IBAGUE|VILLAVICENCIO|"
    r"SANTA\s+MARTA|PASTO|MONTERIA|NEIVA|TUNJA|SOACHA|CHIA|CAJICA|COTA|"
    r"FUNZA|MOSQUERA|MADRID|FACATATIVA|ZIPAQUIRA|LA\s+CALERA|SOPO|"
    r"TOCANCIPA|GACHANCIPA|TENJO|TABIO|SIBATE|FUSAGASUGA|BOJACA)\b"
    r"(?:\s+CUNDINAMARCA)?(?:\s+COLOMBIA)?$",
    re.I,
)

_OUTSIDE_CITY_LABELS = {
    "MEDELLIN": "Medellín", "ITAGUI": "Itagüí", "CUCUTA": "Cúcuta",
    "IBAGUE": "Ibagué", "MONTERIA": "Montería", "SOACHA": "Soacha",
    "CHIA": "Chía", "CAJICA": "Cajicá", "FACATATIVA": "Facatativá",
    "ZIPAQUIRA": "Zipaquirá", "LA CALERA": "La Calera", "SOPO": "Sopó",
    "TOCANCIPA": "Tocancipá", "GACHANCIPA": "Gachancipá",
    "SIBATE": "Sibaté", "FUSAGASUGA": "Fusagasugá", "BOJACA": "Bojacá",
}


def _in_bogota(lat: float, lng: float) -> bool:
    return _BOGOTA_LAT[0] <= lat <= _BOGOTA_LAT[1] and _BOGOTA_LNG[0] <= lng <= _BOGOTA_LNG[1]


def _explicit_outside_bogota_city(raw: str) -> str | None:
    """Return a named non-Bogotá locality explicitly supplied by the user.

    This check must run before Bogotá address normalization and Catastro
    matching. Otherwise a suffix such as ``, Soacha`` can be discarded and
    the street plate can be incorrectly searched inside Bogotá.
    """
    probe = unicodedata.normalize("NFD", str(raw or "").upper())
    probe = "".join(c for c in probe if unicodedata.category(c) != "Mn")
    probe = re.sub(r"[,.]", " ", probe)
    probe = re.sub(r"\s+", " ", probe).strip()
    match = _OUTSIDE_BOGOTA_CITY_RE.search(probe)
    if not match:
        return None
    city = re.sub(r"\s+", " ", match.group("city").upper()).strip()
    return _OUTSIDE_CITY_LABELS.get(city, city.title())


def _normalize_chip(raw: str) -> str | None:
    """Return a canonical UAECD CHIP or ``None`` for non-CHIP input."""
    text = re.sub(r"^\s*CHIP\s*:?\s*", "", str(raw or ""), flags=re.I)
    chip = re.sub(r"[\s.-]+", "", text).upper()
    return chip if _CHIP_RE.fullmatch(chip) else None


def _is_intersection_query(raw: str) -> bool:
    """Return True when input describes crossing roads rather than a door plate."""
    text = str(raw or "")
    if re.search(r"\bENTRE\b", text, re.I):
        return True
    if re.search(r"\b(?:CON|Y)\b", text, re.I) and len(_VIA_WORDS_RE.findall(text)) >= 2:
        return True
    # ``CL 82 # 15`` is common listing shorthand for CL 82 con KR 15.  A
    # hyphenated second number remains an ordinary property address.
    if "#" in text:
        return not bool(re.search(r"#\s*\d+[A-Z]*(?:\s+BIS[A-Z]*)?\s*[-–—]\s*\d+", text, re.I))
    # "Avenida Carrera" and "Avenida Calle" are single compound Bogotá via
    # types (AK/AC), not intersections. Collapse them before counting roads.
    probe = re.sub(r"[.,]", " ", raw.upper())
    probe = re.sub(
        r"\b(?:AVENIDA|AVDA|AVE|AV)\s+(?:CARRERA|CRA|CARR|KRA|KR|CALLE|CLLE|CLL|CL)\b",
        "AVENIDA",
        probe,
    )
    return len(_VIA_WORDS_RE.findall(probe)) >= 2


def _plain_text(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "").upper())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = re.sub(r"[.,;:]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_INTERSECTION_ROAD_RE = re.compile(
    r"^(?P<type>AVENIDA\s+CARRERA|AVENIDA\s+CALLE|AVENIDA|AVDA|AV|"
    r"CALLE|CLLE|CLL|CL|CARRERA|CARR|CRA|KRA|KRR|KR|CR|"
    r"DIAGONAL|DIAG|DG|TRANSVERSAL|TRANSV|TRV|TV|AK|AC)\s*(?P<body>.+)$",
    re.I,
)

_NAMED_ROAD_QUERIES = {
    "LA ESPERANZA": "AVENIDA DE LA ESPERANZA",
    "DE LA ESPERANZA": "AVENIDA DE LA ESPERANZA",
    "PRIMERO DE MAYO": "AVENIDA PRIMERO DE MAYO",
    "BOYACA": "AVENIDA BOYACA",
    "CARACAS": "AVENIDA CARACAS",
    "EL DORADO": "AVENIDA EL DORADO",
    "CIUDAD DE QUITO": "AVENIDA CIUDAD DE QUITO",
    "NQS": "AVENIDA CIUDAD DE QUITO",
}
_NAMED_ROAD_LABELS = {
    "AVENIDA DE LA ESPERANZA": ["AC 24"],
    "AVENIDA PRIMERO DE MAYO": ["AC 26 S"],
    "AVENIDA BOYACA": ["AK 72"],
    "AVENIDA CARACAS": ["AK 14"],
    "AVENIDA EL DORADO": ["AC 26"],
    "AVENIDA CIUDAD DE QUITO": ["AK 30"],
}


def _parse_intersection_road(raw: str) -> dict | None:
    """Parse one road reference into official Mapa de Referencia query terms."""
    text = _plain_text(raw)
    text = re.sub(r"^(?:ESQUINA|EN LA ESQUINA DE|CRUCE DE)\s+", "", text)
    match = _INTERSECTION_ROAD_RE.match(text)
    if not match:
        return None
    road_type = re.sub(r"\s+", " ", match.group("type").upper()).strip()
    body = match.group("body").strip()
    aliases = {
        "CALLE": "CL", "CLLE": "CL", "CLL": "CL", "CL": "CL",
        "CARRERA": "KR", "CARR": "KR", "CRA": "KR", "KRA": "KR",
        "KRR": "KR", "KR": "KR", "CR": "KR",
        "DIAGONAL": "DG", "DIAG": "DG", "DG": "DG",
        "TRANSVERSAL": "TV", "TRANSV": "TV", "TRV": "TV", "TV": "TV",
        "AVENIDA CARRERA": "AK", "AK": "AK",
        "AVENIDA CALLE": "AC", "AC": "AC",
    }
    canonical_type = aliases.get(road_type)
    numbered = re.fullmatch(r"(\d+[A-Z]*(?:\s+BIS[A-Z]*)?(?:\s+[SE])?)", body)
    if numbered:
        number = re.sub(r"\s+BIS\s*", "BIS", numbered.group(1))
        number = re.sub(r"\s+", " ", number).strip()
        if canonical_type in {"CL", "AC"}:
            labels = [f"{canonical_type} {number}"]
            labels.append(f"{'AC' if canonical_type == 'CL' else 'CL'} {number}")
        elif canonical_type in {"KR", "AK"}:
            labels = [f"{canonical_type} {number}"]
            labels.append(f"{'AK' if canonical_type == 'KR' else 'KR'} {number}")
        elif canonical_type:
            labels = [f"{canonical_type} {number}"]
        else:  # Plain ``Av. 68``: Bogotá may encode it as AK or AC.
            labels = [f"AK {number}", f"AC {number}"]
        return {"display": f"{road_type.title()} {body.title()}", "labels": labels, "name": None}

    # A plain avenida followed by words is a named road, not a malformed number.
    if road_type not in {"AVENIDA", "AVDA", "AV"}:
        return None
    normalized_name = re.sub(r"^(?:DE\s+)?LA\s+", "LA ", body)
    official_name = _NAMED_ROAD_QUERIES.get(normalized_name) or _NAMED_ROAD_QUERIES.get(body)
    if not official_name:
        official_name = f"AVENIDA {body}"
    return {
        "display": f"Avenida {body.title()}",
        "labels": list(_NAMED_ROAD_LABELS.get(official_name, [])),
        "name": official_name,
    }


def parse_intersection_query(raw: str) -> dict | None:
    """Parse Bogotá corner and between-streets listing shorthand.

    This parser deliberately runs before ordinary address normalization so a
    listing such as ``CL 82 # 15`` is not mistaken for a partial door plate.
    """
    text = _plain_text(raw)
    text = re.sub(r"^(?:ESQUINA|EN LA ESQUINA DE|CRUCE DE)\s+", "", text)
    between = re.match(r"^(.+?)\s+ENTRE\s+(.+?)\s+Y\s+(.+)$", text)
    if between:
        primary = _parse_intersection_road(between.group(1))
        first = _parse_intersection_road(between.group(2))
        second = _parse_intersection_road(between.group(3))
        if primary and first and second:
            return {"kind": "between", "roads": [primary, first, second], "raw": str(raw).strip()}
        return None

    # Listing shorthand: ``CL 82 # 15`` means Calle 82 at Carrera 15 only
    # when no door-number suffix is present.
    shorthand = re.match(r"^(.+?)\s*#\s*(\d+[A-Z]*(?:\s+BIS[A-Z]*)?(?:\s+[SE])?)$", text)
    if shorthand:
        first = _parse_intersection_road(shorthand.group(1))
        if first:
            first_code = (first.get("labels") or [""])[0].split(" ", 1)[0]
            cross_type = "KR" if first_code in {"CL", "AC", "DG"} else "CL"
            second = _parse_intersection_road(f"{cross_type} {shorthand.group(2)}")
            if second:
                return {"kind": "corner", "roads": [first, second], "raw": str(raw).strip()}

    parts = re.split(r"\s+(?:CON|Y)\s+", text, maxsplit=1)
    if len(parts) == 2:
        first, second = (_parse_intersection_road(part) for part in parts)
        if first and second:
            return {"kind": "corner", "roads": [first, second], "raw": str(raw).strip()}
    # Also accept terse brokerage shorthand without a connector, e.g.
    # ``Calle 60 Carrera 7``. Try every later road token so compound
    # ``Avenida Carrera`` remains a single reference.
    road_tokens = list(_VIA_WORDS_RE.finditer(text))
    for token in road_tokens[1:]:
        first = _parse_intersection_road(text[:token.start()].strip())
        second = _parse_intersection_road(text[token.start():].strip())
        if first and second:
            return {"kind": "corner", "roads": [first, second], "raw": str(raw).strip()}
    return None

# -----------------------------------------------------------------------
# Simple LRU-style cache (avoids repeated network hits)
# -----------------------------------------------------------------------
_cache: dict[str, dict] = {}
_MAX_CACHE = 256


def _cache_get(key: str):
    return _cache.get(key)


def _cache_set(key: str, value: dict):
    if len(_cache) >= _MAX_CACHE:
        # evict oldest
        oldest = next(iter(_cache))
        del _cache[oldest]
    _cache[key] = value


# -----------------------------------------------------------------------
# Address normalisation
# -----------------------------------------------------------------------

# Mirrors static/nomenclatura.js normalizarDireccion — keep in sync.
_VIA_MAP = [
    (re.compile(r"^(AVENIDA\s+CARRERA|AV\s*CRA|AV\s*KR|AK)\b"),  "AK"),
    (re.compile(r"^(AVENIDA\s+CALLE|AV\s*CLL?E?|AC)\b"),          "AC"),
    (re.compile(r"^(TRANSVERSAL|TRANSV|TRV|TV)\b"),               "TV"),
    (re.compile(r"^(DIAGONAL|DIAG|DG)\b"),                        "DG"),
    (re.compile(r"^(CARRERA|CRA|CARR|KRA|KR|CR)\b"),              "KR"),
    (re.compile(r"^(CALLE|CLLE|CLL|CL)\b"),                       "CL"),
    (re.compile(r"^(AVENIDA|AVDA|AVE|AV)\b"),                     "AV"),
]

# Common named avenues as used conversationally. Catastro stores their
# canonical Bogotá nomenclature, not the name printed on a map.
_NAMED_VIA_ALIASES = [
    (re.compile(r"^(?:AVENIDA|AV)\s+BOYACA\b"), "AK 72"),
    (re.compile(r"^(?:AVENIDA|AV)\s+CARACAS\b"), "AK 14"),
    (re.compile(r"^(?:AVENIDA|AV)\s+(?:CIUDAD\s+DE\s+QUITO|NQS)\b|^NQS\b"), "AK 30"),
    (re.compile(r"^(?:AVENIDA|AV)\s+(?:EL\s+DORADO|CALLE\s+26)\b"), "AC 26"),
    (re.compile(r"^(?:AUTOPISTA\s+NORTE)\b"), "AK 45"),
]


def normalize_address(raw: str) -> str:
    """
    Canonical Bogotá address string — mirrors nomenclatura.js normalizarDireccion.

    Verified cases:
        "Calle 90 # 11-73"              -> "CL 90 # 11-73"
        "cl 90 #11-73"                  -> "CL 90 # 11-73"
        "KR 11 No. 90 - 73"             -> "KR 11 # 90-73"
        "Avenida Carrera 11 Nº 90-73"   -> "AK 11 # 90-73"
        "Cll 85 12 34"                  -> "CL 85 12 34"   (no separator, passes through)
    """
    s = raw.strip().upper()
    # Strip diacritics (NFD decompose, then drop combining marks)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace(",", " ").replace(".", " ")
    s = re.sub(r"\s+", " ", s)
    # Accept compact mobile input such as ``CL85#11-53`` and
    # ``CARRERA123B#17-94``. Without this boundary the via alias is not
    # recognised because the following digit is also a regex word character.
    s = re.sub(
        r"^(AVENIDA\s+CARRERA|AVENIDA\s+CALLE|TRANSVERSAL|DIAGONAL|CARRERA|CALLE|AK|AC|CL|KR|DG|TV|AV)(?=\d)",
        r"\1 ",
        s,
    )
    # City/country suffixes are natural user input but are not part of
    # Catastro's PDOTEXTO field.  Leaving them attached turns exact addresses
    # into false block-level "near matches".
    s = re.sub(r"\s+BOGOTA(?:\s+D\s*C)?(?:\s+COLOMBIA)?\s*$", "", s).strip()
    s = re.sub(r"\s+COLOMBIA\s*$", "", s).strip()
    # Catastro encodes Bogotá directional qualifiers as S/E, while users and
    # official predio exports commonly spell SUR/ESTE in full.
    s = re.sub(r"\bSUR\b", "S", s)
    s = re.sub(r"\bESTE\b", "E", s)
    # Nº / N° / No. / Nro → "#"
    s = re.sub(r"\bN[º°]\s*", "# ", s)
    s = re.sub(r"\b(NO|NRO|NUM|NUMERO)\b\s*", "# ", s)
    # Canonical spaces around "#"
    s = re.sub(r"\s*#\s*", " # ", s)
    for pat, canonical in _NAMED_VIA_ALIASES:
        m = pat.match(s)
        if m:
            s = canonical + s[m.end():]
            break
    # Expand via type prefix
    for pat, abbr in _VIA_MAP:
        m = pat.match(s)
        if m:
            s = abbr + s[m.end():]
            break
    # Normalise cross-reference hyphen: "# 11 - 73" -> "# 11-73"
    s = re.sub(
        r"(#\s*\d+\s*[A-Z]?(?:\s+BIS)?)\s*[-–—]\s*(\d+\s*[A-Z]?)",
        r"\1-\2", s,
    )
    # Collapse remaining space-padded hyphens, tidy "#" and whitespace
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"#\s*", "# ", s, count=1)
    s = re.sub(r"\s+", " ", s).strip()

    # Recover an unambiguous compact lettered plate:
    # ``TV78K#41A04S`` -> ``TV 78K # 41A-04 S``.
    s = re.sub(r"(#\s*\d+[A-Z])(\d{2})([SE])$", r"\1-\2 \3", s)

    # Insert missing '#' separator: "CL 90 11 73" → "CL 90 # 11-73"
    # Pattern: <TYPE> <via_num> <cross_num> <house_num> with no '#' already present
    if "#" not in s:
        m = re.match(
            r"^(AC|AK|CL|KR|DG|TV|AV)\s+(\d+[A-Z]*(?:\s+BIS[A-Z]*)?(?:\s+[SE])?)\s+(\d+[A-Z]*)\s+(\d+[A-Z]*)(.*)",
            s,
        )
        if m:
            via_type, via_num, cross, house, rest = m.groups()
            s = f"{via_type} {via_num} # {cross}-{house}{rest}".strip()
            # Re-apply canonical space around '#'
            s = re.sub(r"\s*#\s*", " # ", s)
            s = re.sub(r"\s+", " ", s).strip()

    return s


def normalize_address_search(raw: str) -> str:
    """Return the stable token form used by autocomplete and its database index.

    ``normalize_address`` remains the single source of truth for Bogotá street
    aliases and directional modifiers.  This second pass removes presentation
    separators so an official Catastro value such as ``11 53`` and a user value
    such as ``11-53`` produce the same indexed string.

    Examples:
        ``Cl. 85 # 11-53`` -> ``CL 85 11 53``
        ``Av. Cra. 68 # 40-15`` -> ``AK 68 40 15``
    """
    canonical = normalize_address(raw)
    canonical = re.sub(r"[^A-Z0-9]+", " ", canonical)
    return re.sub(r"\s+", " ", canonical).strip()


def canonical_catastro_address(pdonvial: str, pdotexto: str) -> str:
    """Format Catastro's split address fields as a human-readable plate."""
    via = re.sub(r"\s+", " ", str(pdonvial or "").strip())
    parts = str(pdotexto or "").strip().split()
    if len(parts) >= 2:
        plate = f"{parts[0]}-{parts[1]}"
        if len(parts) > 2:
            plate += " " + " ".join(parts[2:])
    else:
        plate = " ".join(parts)
    return f"{via} # {plate}".strip()


def _fuzzy_score(query: str, label: str) -> float:
    """SequenceMatcher ratio between normalized forms (case-insensitive, stripped)."""
    a = query.strip().upper()
    b = label.strip().upper()
    return difflib.SequenceMatcher(None, a, b).ratio()


# Ordered: longest/most-specific patterns first
_PREFIX_MAP = [
    # Already-canonical Bogotá avenue codes (normalizer outputs these).
    (re.compile(r"^AK\b", re.I), "AK"),
    (re.compile(r"^AC\b", re.I), "AC"),
    # Avenida Calle (before plain "Avenida")
    (re.compile(r"^av(?:enida)?\.?\s+c(?:alle|l\.?)\b", re.I), "AC"),
    # Avenida Carrera
    (re.compile(r"^av(?:enida)?\.?\s+(?:cra|carrera|kr\.?|kra\.?)\b", re.I), "AK"),
    # Plain "Avenida N" — common for numbered Av. (Av. 68, Av. 1°, etc.)
    (re.compile(r"^av(?:enida)?\.?\s+", re.I), "AK"),
    # Autopista
    (re.compile(r"^(?:autopista)\b", re.I), "AC"),
    # Calle
    (re.compile(r"^(?:calle|cl\.?|c\.?)\b", re.I), "CL"),
    # Carrera
    (re.compile(r"^(?:carrera|cra\.?|cr\.?|kra\.?|kr\.?)\b", re.I), "KR"),
    # Diagonal
    (re.compile(r"^(?:diagonal|dg\.?|diag\.?)\b", re.I), "DG"),
    # Transversal
    (re.compile(r"^(?:transversal|tv\.?|tr\.?)\b", re.I), "TV"),
]


def _parse_address(raw: str) -> tuple[str, str] | None:
    """
    Parse a Bogotá address into (PDONVIAL, PDOTEXTO_prefix).

    Returns None if the via type cannot be detected.

    Examples:
        "Calle 72 # 10-34"      → ("CL 72",  "10 34")
        "Kr 15A # 93-60 Sur"    → ("KR 15A", "93 60 SUR")
        "Diagonal 85 Bis # 20-45" → ("DG 85BIS", "20 45")
        "Av. Calle 26 # 69-76"  → ("AC 26",  "69 76")
        "Av. Carrera 68 # 30-50" → ("AK 68", "30 50")
    """
    raw = raw.strip()

    # Split on '#'
    parts = re.split(r"#", raw, maxsplit=1)
    via_raw = parts[0].strip()
    cross_raw = parts[1].strip() if len(parts) > 1 else ""

    # Detect via prefix
    code = None
    remainder = via_raw
    for pat, abbr in _PREFIX_MAP:
        m = pat.match(via_raw)
        if m:
            code = abbr
            remainder = via_raw[m.end():].strip()
            break
    if code is None:
        return None  # unrecognised via type

    # Normalise via number: "72A Bis" → "72ABIS", "7 Bis A" → "7BISA"
    num_part = remainder.upper()
    direction = ""
    direction_match = re.match(r"^(.*?)(?:\s+)([SE])$", num_part)
    if direction_match:
        num_part, direction = direction_match.groups()
    num_part = re.sub(r"\s+BIS\s*", "BIS", num_part)  # "7 BIS A" → "7BISA"
    num_part = re.sub(r"\s+", "", num_part)             # collapse remaining spaces
    pdonvial = f"{code} {num_part}{(' ' + direction) if direction else ''}"

    # Normalise cross+house: "10-34 Sur" → "10 34 SUR"
    # Strip any trailing city/neighborhood suffix (e.g. ", Bogotá") that callers append
    cross = cross_raw.split(",")[0].upper().replace("-", " ")
    cross = re.sub(r"\bSUR\b", "S", cross)
    cross = re.sub(r"\bESTE\b", "E", cross)
    cross = re.sub(r"\s+", " ", cross).strip()

    return pdonvial, cross


def _is_complete_street_plate(normalised: str) -> bool:
    """Require a numbered road plus cross-street and door number.

    Nominatim can map vague input such as ``Carrera`` to an unrelated named
    building. Ainmo is a parcel tool, so incomplete searches must ask for a
    full plate or a map click instead of presenting that building as a match.
    """
    parsed = _parse_address(normalised)
    if not parsed:
        return False
    pdonvial, pdotexto = parsed
    text_parts = pdotexto.split()
    return (
        bool(re.search(r"\d", pdonvial))
        and len(text_parts) >= 2
        and bool(re.match(r"^\d", text_parts[0]))
        and bool(re.match(r"^\d", text_parts[1]))
    )


# -----------------------------------------------------------------------
# Catastro placadomiciliaria query
# -----------------------------------------------------------------------

def _catastro_query(
    pdonvial: str,
    pdotexto: str,
    limit: int = 20,
    *,
    exact: bool = True,
) -> list[dict]:
    pdonvial_sql = pdonvial.replace("'", "''")
    pdotexto_sql = pdotexto.replace("'", "''")
    if pdotexto:
        # Catastro stores PDOTEXTO with trailing spaces and its ArcGIS SQL
        # dialect does not support TRIM(). Query a narrow prefix, then enforce
        # exact equality in Python below.
        # Some official records pad PDONVIAL with spaces (for example "KR 6 ").
        # ArcGIS equality does not consistently ignore that padding, so query a
        # prefix and enforce exact trimmed equality below.
        where = f"PDONVIAL LIKE '{pdonvial_sql}%' AND PDOTEXTO LIKE '{pdotexto_sql}%'"
    else:
        where = f"PDONVIAL LIKE '{pdonvial_sql}%'"

    params = urllib.parse.urlencode({
        "where": where,
        "outFields": "PDONVIAL,PDOTEXTO,PDOCLOTE",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": limit,
        "f": "json",
    })
    url = f"{_CATASTRO_PLACA}/query?{params}"
    with urllib.request.urlopen(url, context=_SSL_CTX, timeout=12) as r:
        data = json.load(r)

    seen_coords: set[tuple] = set()
    candidates: list[dict] = []
    for feat in data.get("features", []):
        a = feat["attributes"]
        if str(a.get("PDONVIAL", "")).strip() != pdonvial.strip():
            continue
        if exact and pdotexto and str(a.get("PDOTEXTO", "")).strip() != pdotexto.strip():
            continue
        g = feat.get("geometry") or {}
        lat = g.get("y")
        lng = g.get("x")
        if lat is None or lng is None:
            continue
        key = (round(lat, 5), round(lng, 5))
        if key in seen_coords:
            continue
        seen_coords.add(key)
        label = canonical_catastro_address(a.get("PDONVIAL"), a.get("PDOTEXTO"))
        candidates.append({
            "lat": lat,
            "lng": lng,
            "label": label,
            "source": "catastro",
            # Preserve the lot explicitly linked to the official address plate.
            # A small number of plate points fall inside an adjacent polygon.
            "lotcodigo": str(a.get("PDOCLOTE") or "").strip() or None,
            "address_text": str(a.get("PDOTEXTO") or "").strip(),
        })

    return candidates


def _catastro_near(pdonvial: str, requested_text: str) -> list[dict]:
    """
    Block-level fallback: find any address on `pdonvial` whose PDOTEXTO
    starts with the cross-street number, e.g. PDOTEXTO LIKE '11 %'.
    Returns up to 5 results tagged near_match=True.
    """
    requested_parts = requested_text.split()
    if not requested_parts:
        return []
    cross_num = requested_parts[0]
    requested_directions = {p for p in requested_parts[2:] if p in {"S", "E"}}
    requested_house = requested_parts[1] if len(requested_parts) > 1 else ""
    results = _catastro_query(pdonvial, f"{cross_num} ", limit=100, exact=False)
    filtered: list[dict] = []
    for r in results:
        parts = r.get("address_text", "").split()
        if not parts or parts[0] != cross_num:
            continue
        candidate_directions = {p for p in parts[2:] if p in {"S", "E"}}
        if candidate_directions != requested_directions:
            continue
        r["near_match"] = True
        r["match_type"] = "same_block"
        r["match_confidence"] = "media"
        house = parts[1] if len(parts) > 1 else ""
        requested_num = int(re.match(r"\d+", requested_house).group()) if re.match(r"\d+", requested_house) else 9999
        house_num = int(re.match(r"\d+", house).group()) if re.match(r"\d+", house) else 9999
        r["house_number_distance"] = abs(house_num - requested_num)
        filtered.append(r)
    filtered.sort(key=lambda r: (r["house_number_distance"], r["label"]))
    for r in filtered:
        r.pop("house_number_distance", None)
    return filtered[:5]


def _point_in_rings(x: float, y: float, rings: list[list[list[float]]]) -> bool:
    """Even/odd polygon test that also respects interior rings (holes)."""
    inside = False
    for ring in rings:
        j = len(ring) - 1
        for i, (xi, yi) in enumerate(ring):
            xj, yj = ring[j]
            if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-20) + xi:
                inside = not inside
            j = i
    return inside


def _polygon_interior_point(rings: list[list[list[float]]]) -> tuple[float, float] | None:
    """Return a point inside a cadastral polygon without optional GIS libraries."""
    if not rings or len(rings[0]) < 3:
        return None
    outer = rings[0]
    area2 = cx = cy = 0.0
    for index, (x1, y1) in enumerate(outer):
        x2, y2 = outer[(index + 1) % len(outer)]
        cross = x1 * y2 - x2 * y1
        area2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area2) > 1e-20:
        centroid = (cx / (3 * area2), cy / (3 * area2))
        if _point_in_rings(*centroid, rings):
            return centroid

    xs = [point[0] for point in outer]
    ys = [point[1] for point in outer]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    centre = ((xmin + xmax) / 2, (ymin + ymax) / 2)
    if _point_in_rings(*centre, rings):
        return centre
    # Concave lots can have an exterior centroid. A deterministic fine grid is
    # sufficient for Bogotá parcel polygons and always stays away from edges.
    grid = []
    for ix in range(1, 24):
        for iy in range(1, 24):
            point = (xmin + (xmax - xmin) * ix / 24, ymin + (ymax - ymin) * iy / 24)
            grid.append((abs(ix - 12) + abs(iy - 12), point))
    for _, point in sorted(grid):
        if _point_in_rings(*point, rings):
            return point
    return None


def _anchor_candidates_to_linked_lots(candidates: list[dict]) -> list[dict]:
    """Move misplaced official address points inside their linked lot polygon."""
    lotcodes = sorted({
        str(candidate.get("lotcodigo") or "") for candidate in candidates
        if candidate.get("source") == "catastro" and re.fullmatch(r"[0-9]{12}", str(candidate.get("lotcodigo") or ""))
    })
    if not lotcodes:
        return candidates
    quoted = ",".join(f"'{code}'" for code in lotcodes)
    params = urllib.parse.urlencode({
        "where": f"LOTCODIGO IN ({quoted})",
        "outFields": "LOTCODIGO",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": max(20, len(lotcodes)),
        "f": "json",
    })
    with urllib.request.urlopen(f"{_CATASTRO_LOTE}/query?{params}", context=_SSL_CTX, timeout=12) as response:
        data = json.load(response)
    polygons = {
        str((feature.get("attributes") or {}).get("LOTCODIGO") or "").strip():
            (feature.get("geometry") or {}).get("rings") or []
        for feature in data.get("features", [])
    }
    for candidate in candidates:
        rings = polygons.get(str(candidate.get("lotcodigo") or ""))
        if not rings or _point_in_rings(candidate["lng"], candidate["lat"], rings):
            continue
        interior = _polygon_interior_point(rings)
        if interior is None:
            continue
        candidate["address_point_lat"] = candidate["lat"]
        candidate["address_point_lng"] = candidate["lng"]
        candidate["lng"], candidate["lat"] = interior
        candidate["coordinate_adjusted_to_lot"] = True
    return candidates


def _catastro_chip_query(chip: str) -> list[dict]:
    """Resolve a UAECD CHIP to its containing cadastral lot.

    CHIP belongs to the non-spatial Predio table (MapServer table 3). Its
    BARMANPRE relationship key is the spatial Lote layer's LOTCODIGO. Querying
    both official resources keeps the returned point inside the selected lot
    and lets `/api/calc` enforce its existing expected-lot guard.
    """
    safe_chip = chip.replace("'", "''")
    predio_params = urllib.parse.urlencode({
        "where": f"PRECHIP='{safe_chip}'",
        "outFields": "PRECHIP,PREDIRECC,BARMANPRE",
        "returnGeometry": "false",
        "resultRecordCount": 50,
        "f": "json",
    })
    with urllib.request.urlopen(
        f"{_CATASTRO_PREDIO}/query?{predio_params}", context=_SSL_CTX, timeout=12
    ) as response:
        predio_data = json.load(response)

    predios_by_lot: dict[str, dict] = {}
    for feature in predio_data.get("features", []):
        attributes = feature.get("attributes") or {}
        lotcodigo = str(attributes.get("BARMANPRE") or "").strip()
        if not re.fullmatch(r"[0-9]{12}", lotcodigo):
            continue
        predios_by_lot.setdefault(lotcodigo, attributes)
    if not predios_by_lot:
        return []

    quoted = ",".join(f"'{code}'" for code in sorted(predios_by_lot))
    lot_params = urllib.parse.urlencode({
        "where": f"LOTCODIGO IN ({quoted})",
        "outFields": "LOTCODIGO",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": max(20, len(predios_by_lot)),
        "f": "json",
    })
    with urllib.request.urlopen(
        f"{_CATASTRO_LOTE}/query?{lot_params}", context=_SSL_CTX, timeout=12
    ) as response:
        lot_data = json.load(response)

    candidates: list[dict] = []
    for feature in lot_data.get("features", []):
        attributes = feature.get("attributes") or {}
        lotcodigo = str(attributes.get("LOTCODIGO") or "").strip()
        rings = (feature.get("geometry") or {}).get("rings") or []
        point = _polygon_interior_point(rings)
        if point is None or not _in_bogota(point[1], point[0]):
            continue
        predio = predios_by_lot.get(lotcodigo) or {}
        official_address = str(predio.get("PREDIRECC") or "").strip()
        candidates.append({
            "lat": point[1],
            "lng": point[0],
            "label": official_address or f"CHIP {chip}",
            "source": "catastro",
            "lotcodigo": lotcodigo,
            "chip": chip,
            "match_type": "exact_chip",
            "match_confidence": "alta",
        })
    return candidates


def catastro_properties_for_lot(lotcodigo: str) -> list[dict]:
    """Return every unique official CHIP associated with a cadastral lot.

    Catastro's spatial lot layer identifies a physical polygon with
    ``LOTCODIGO``.  Property units live in the non-spatial Predio table and
    link back to that polygon through ``BARMANPRE``.  The two identifiers are
    deliberately kept separate here because a PH lot can contain many CHIPs.

    The first request obtains every object id, then fetches records in batches;
    this avoids silently truncating a large propiedad-horizontal building at
    the ArcGIS service's record limit.
    """
    code = str(lotcodigo or "").strip()
    if not re.fullmatch(r"\d{12}", code):
        return []

    cached = _lot_properties_cache.get(code)
    if cached and time.time() - cached[0] < _LOT_PROPERTIES_CACHE_TTL:
        return [dict(item) for item in cached[1]]

    safe_code = code.replace("'", "''")
    id_params = urllib.parse.urlencode({
        "where": f"BARMANPRE='{safe_code}'",
        "returnIdsOnly": "true",
        "f": "json",
    })
    with urllib.request.urlopen(
        f"{_CATASTRO_PREDIO}/query?{id_params}", context=_SSL_CTX, timeout=12
    ) as response:
        id_data = json.load(response)
    if id_data.get("error"):
        raise RuntimeError(f"Catastro Predio error: {id_data['error']}")

    object_ids = sorted({int(value) for value in id_data.get("objectIds", [])})
    records: dict[str, dict] = {}
    for start in range(0, len(object_ids), 200):
        batch = object_ids[start:start + 200]
        record_params = urllib.parse.urlencode({
            "objectIds": ",".join(str(value) for value in batch),
            "outFields": "PRECHIP,PREDIRECC,BARMANPRE",
            "returnGeometry": "false",
            "f": "json",
        })
        with urllib.request.urlopen(
            f"{_CATASTRO_PREDIO}/query?{record_params}", context=_SSL_CTX, timeout=12
        ) as response:
            record_data = json.load(response)
        if record_data.get("error"):
            raise RuntimeError(f"Catastro Predio error: {record_data['error']}")
        for feature in record_data.get("features", []):
            attributes = feature.get("attributes") or {}
            chip = str(attributes.get("PRECHIP") or "").strip().upper()
            linked_lot = str(attributes.get("BARMANPRE") or "").strip()
            if not _CHIP_RE.fullmatch(chip) or linked_lot != code:
                continue
            # PRECHIP is the property-unit identity. Duplicate service rows are
            # collapsed instead of inflating the number of registered units.
            records.setdefault(chip, {
                "chip": chip,
                "direccion": str(attributes.get("PREDIRECC") or "").strip() or None,
                "lotcodigo": code,
            })

    result = sorted(records.values(), key=lambda item: item["chip"])
    _lot_properties_cache[code] = (time.time(), result)
    return [dict(item) for item in result]


# -----------------------------------------------------------------------
# Official road-axis intersection resolver
# -----------------------------------------------------------------------

def _arcgis_query(layer_url: str, params: dict) -> dict:
    query = urllib.parse.urlencode({**params, "f": "json"})
    with urllib.request.urlopen(
        f"{layer_url}/query?{query}", context=_SSL_CTX, timeout=25
    ) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(f"ArcGIS query failed: {payload['error']}")
    return payload


def _road_axis_geometry(road: dict):
    clauses = []
    for label in road.get("labels") or []:
        safe = label.replace("'", "''")
        clauses.append(f"MVIETIQUET='{safe}'")
    if road.get("name"):
        safe = str(road["name"]).replace("'", "''")
        clauses.extend([f"MVINOMBRE='{safe}'", f"MVINALTERN='{safe}'"])
    if not clauses:
        return None
    payload = _arcgis_query(_MAPA_REFERENCIA_VIAS, {
        "where": " OR ".join(f"({clause})" for clause in clauses),
        "outFields": "OBJECTID,MVIETIQUET,MVINOMBRE,MVINALTERN",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": 5000,
    })
    lines = []
    for feature in payload.get("features", []):
        for path in (feature.get("geometry") or {}).get("paths") or []:
            if len(path) >= 2:
                lines.append(LineString(path))
    return unary_union(lines) if lines else None


def _extract_points(geometry) -> list[tuple[float, float]]:
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "Point":
        return [(geometry.x, geometry.y)]
    if geometry.geom_type == "MultiPoint":
        return [(point.x, point.y) for point in geometry.geoms]
    if geometry.geom_type in {"LineString", "LinearRing"}:
        point = geometry.interpolate(0.5, normalized=True)
        return [(point.x, point.y)]
    points: list[tuple[float, float]] = []
    for child in getattr(geometry, "geoms", []):
        points.extend(_extract_points(child))
    return points


def _meters_between(first: tuple[float, float], second: tuple[float, float]) -> float:
    lng1, lat1 = first
    lng2, lat2 = second
    latitude = math.radians((lat1 + lat2) / 2)
    dx = (lng1 - lng2) * 111_320 * math.cos(latitude)
    dy = (lat1 - lat2) * 110_540
    return math.hypot(dx, dy)


def _cluster_intersections(points: list[tuple[float, float]], radius_m: float = 70) -> list[tuple[float, float]]:
    """Collapse divided carriageways into one logical road intersection."""
    clusters: list[list[tuple[float, float]]] = []
    for point in points:
        for cluster in clusters:
            centre = (
                sum(item[0] for item in cluster) / len(cluster),
                sum(item[1] for item in cluster) / len(cluster),
            )
            if _meters_between(point, centre) <= radius_m:
                cluster.append(point)
                break
        else:
            clusters.append([point])
    return [
        (sum(point[0] for point in cluster) / len(cluster),
         sum(point[1] for point in cluster) / len(cluster))
        for cluster in clusters
    ]


def _road_intersection_points(first, second) -> list[tuple[float, float]]:
    if first is None or second is None:
        return []
    points = _extract_points(first.intersection(second))
    if not points:
        # Centre lines for divided roads can stop a few metres short of each
        # other. Accept only a genuinely local gap; never snap across blocks.
        point_a, point_b = nearest_points(first, second)
        pair = ((point_a.x, point_a.y), (point_b.x, point_b.y))
        if _meters_between(*pair) <= 45:
            points = [((pair[0][0] + pair[1][0]) / 2, (pair[0][1] + pair[1][1]) / 2)]
    return _cluster_intersections(points)


def _signed_ring_area(ring: list[list[float]]) -> float:
    if len(ring) < 3:
        return 0.0
    area = 0.0
    for index, first in enumerate(ring):
        second = ring[(index + 1) % len(ring)]
        area += first[0] * second[1] - second[0] * first[1]
    return area / 2


def _polygon_measurements(rings: list[list[list[float]]]) -> tuple[float | None, float | None, float | None]:
    """Return area and oriented-envelope dimensions for EPSG:9377 rings."""
    if not rings:
        return None, None, None
    area = abs(sum(_signed_ring_area(ring) for ring in rings))
    try:
        polygon = Polygon(rings[0], holes=rings[1:] or None)
        rectangle = polygon.minimum_rotated_rectangle
        coordinates = list(rectangle.exterior.coords)
        sides = sorted({
            round(math.dist(coordinates[index], coordinates[index + 1]), 3)
            for index in range(len(coordinates) - 1)
            if math.dist(coordinates[index], coordinates[index + 1]) > 0.5
        })
        frontage = sides[0] if sides else None
        depth = sides[-1] if sides else None
    except Exception:
        frontage = depth = None
    return round(area, 1), round(frontage, 1) if frontage else None, round(depth, 1) if depth else None


def _local_polygon(rings: list[list[list[float]]], origin: tuple[float, float]):
    origin_lng, origin_lat = origin
    cosine = math.cos(math.radians(origin_lat))
    converted = []
    for ring in rings:
        converted.append([
            ((lng - origin_lng) * 111_320 * cosine, (lat - origin_lat) * 110_540)
            for lng, lat in ring
        ])
    if not converted:
        return None
    try:
        return Polygon(converted[0], holes=converted[1:] or None)
    except Exception:
        return None


def _properties_for_lots(lotcodes: list[str]) -> dict[str, dict]:
    if not lotcodes:
        return {}
    properties: dict[str, dict] = {}
    # Corridor searches can touch hundreds of lots. Keep ArcGIS URLs below
    # proxy/request-line limits instead of silently dropping the latter codes.
    for offset in range(0, len(lotcodes), 100):
        batch = lotcodes[offset:offset + 100]
        quoted = ",".join(f"'{code}'" for code in batch)
        payload = _arcgis_query(_CATASTRO_PREDIO, {
            "where": f"BARMANPRE IN ({quoted})",
            "outFields": "PRECHIP,PREDIRECC,BARMANPRE",
            "returnGeometry": "false",
            "resultRecordCount": 4000,
        })
        for feature in payload.get("features", []):
            attributes = feature.get("attributes") or {}
            code = str(attributes.get("BARMANPRE") or "").strip()
            if code not in batch:
                continue
            entry = properties.setdefault(code, {"addresses": [], "chips": []})
            address = str(attributes.get("PREDIRECC") or "").strip()
            chip = str(attributes.get("PRECHIP") or "").strip().upper()
            if address and address not in entry["addresses"]:
                entry["addresses"].append(address)
            if _CHIP_RE.fullmatch(chip) and chip not in entry["chips"]:
                entry["chips"].append(chip)
    return properties


def _sample_segment(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    spacing_m: float = 180,
    max_points: int = 24,
) -> list[tuple[float, float]]:
    """Return evenly spaced points covering a complete between-streets span."""
    distance = _meters_between(start, end)
    intervals = max(1, min(max_points - 1, math.ceil(distance / spacing_m)))
    return [
        (
            start[0] + (end[0] - start[0]) * index / intervals,
            start[1] + (end[1] - start[1]) * index / intervals,
        )
        for index in range(intervals + 1)
    ]


def _rank_intersection_candidates(
    candidates: list[dict],
    *,
    listing_area_m2: float | None = None,
    frontage_m: float | None = None,
    limit: int = 8,
) -> list[dict]:
    """Rank transparently and annotate the score shown in the interface."""
    if listing_area_m2:
        candidates.sort(key=lambda candidate: (
            candidate.get("area_difference_m2")
            if candidate.get("area_difference_m2") is not None else 1e9,
            candidate.get("distance_m", 1e9),
            candidate.get("frontage_difference_pct")
            if frontage_m and candidate.get("frontage_difference_pct") is not None else 0,
        ))
    else:
        candidates.sort(key=lambda candidate: (
            candidate.get("distance_m", 1e9),
            candidate.get("area_m2") or 1e9,
        ))

    for index, candidate in enumerate(candidates):
        candidate["ranking_score"] = {
            "rank": index + 1,
            "strategy": "area_then_distance" if listing_area_m2 else "distance",
            "area_difference_m2": candidate.get("area_difference_m2"),
            "area_difference_pct": candidate.get("area_difference_pct"),
            "distance_m": candidate.get("distance_m"),
        }
        # A tolerance is supporting evidence, not eight identical winners.
        candidate["likely_match"] = bool(index < 3 and candidate.get("area_within_10pct"))
    return candidates[:limit]


def _intersection_lot_candidates(
    points: list[tuple[float, float]],
    *,
    listing_area_m2: float | None = None,
    frontage_m: float | None = None,
    depth_m: float | None = None,
) -> list[dict]:
    if not points:
        return []
    wgs_lots = {}
    # Ask ArcGIS for every object ID in a 130 m corridor first. ID-only
    # queries are not truncated by the feature transfer limit; geometry is
    # then fetched in bounded batches. This covers the entire segment without
    # the endpoint bias of one large envelope or dozens of serial point calls.
    corridor = {
        "paths": [[[lng, lat] for lng, lat in points]],
        "spatialReference": {"wkid": 4326},
    }
    id_payload = _arcgis_query(_CATASTRO_LOTE, {
        "where": "1=1",
        "geometry": json.dumps(corridor, separators=(",", ":")),
        "geometryType": "esriGeometryPolyline",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": 130,
        "units": "esriSRUnit_Meter",
        "returnIdsOnly": "true",
        "returnGeometry": "false",
    })
    object_ids = id_payload.get("objectIds") or []
    for offset in range(0, len(object_ids), 100):
        batch = object_ids[offset:offset + 100]
        wgs_payload = _arcgis_query(_CATASTRO_LOTE, {
            "objectIds": ",".join(str(value) for value in batch),
            "outFields": "LOTCODIGO,LOTUPREDIA",
            "returnGeometry": "true",
            "outSR": "4326",
            "resultRecordCount": len(batch),
        })
        for feature in wgs_payload.get("features", []):
            attributes = feature.get("attributes") or {}
            code = str(attributes.get("LOTCODIGO") or "").strip()
            rings = (feature.get("geometry") or {}).get("rings") or []
            if re.fullmatch(r"\d{12}", code) and rings:
                wgs_lots[code] = {"rings": rings, "attributes": attributes}
    if not wgs_lots:
        return []

    reference = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )
    local_targets = [
        Point((point[0] - reference[0]) * 111_320 * math.cos(math.radians(reference[1])),
              (point[1] - reference[1]) * 110_540)
        for point in points
    ]
    target_geometry = LineString([point.coords[0] for point in local_targets]) if len(local_targets) > 1 else local_targets[0]

    # Keep only lots genuinely beside the sampled road corridor before the
    # heavier projected-geometry and property-unit lookups.
    corridor_distances = {}
    for code, lot in wgs_lots.items():
        local_polygon = _local_polygon(lot["rings"], reference)
        if local_polygon is not None:
            corridor_distances[code] = max(0.0, float(local_polygon.distance(target_geometry)))
    selected_codes = [
        code for code, _distance in sorted(corridor_distances.items(), key=lambda item: item[1])
        if _distance <= 130
    ][:500]
    wgs_lots = {code: wgs_lots[code] for code in selected_codes}
    if not wgs_lots:
        return []

    projected = {}
    codes = sorted(wgs_lots)
    for offset in range(0, len(codes), 100):
        batch = codes[offset:offset + 100]
        quoted = ",".join(f"'{code}'" for code in batch)
        projected_payload = _arcgis_query(_CATASTRO_LOTE, {
            "where": f"LOTCODIGO IN ({quoted})",
            "outFields": "LOTCODIGO",
            "returnGeometry": "true",
            "outSR": "9377",
            "resultRecordCount": max(1000, len(batch)),
        })
        for feature in projected_payload.get("features", []):
            code = str((feature.get("attributes") or {}).get("LOTCODIGO") or "").strip()
            projected[code] = (feature.get("geometry") or {}).get("rings") or []
    candidates = []
    for code, lot in wgs_lots.items():
        rings = lot["rings"]
        interior = _polygon_interior_point(rings)
        if interior is None:
            continue
        local_polygon = _local_polygon(rings, reference)
        if local_polygon is None:
            continue
        distance_m = corridor_distances.get(code, max(0.0, float(local_polygon.distance(target_geometry))))
        area_m2, estimated_frontage, estimated_depth = _polygon_measurements(projected.get(code) or [])
        area_difference = (
            abs(area_m2 - listing_area_m2) / listing_area_m2 * 100
            if area_m2 is not None and listing_area_m2 and listing_area_m2 > 0 else None
        )
        area_difference_m2 = (
            abs(area_m2 - listing_area_m2)
            if area_m2 is not None and listing_area_m2 and listing_area_m2 > 0 else None
        )
        frontage_difference = (
            abs(estimated_frontage - frontage_m) / frontage_m * 100
            if estimated_frontage is not None and frontage_m and frontage_m > 0 else None
        )
        candidate = {
            "lat": interior[1], "lng": interior[0],
            "label": f"Predio {code}",
            "official_address": None,
            "source": "catastro",
            "lotcodigo": code,
            "chip": None,
            "chip_count": 0,
            "area_m2": area_m2,
            "distance_m": round(distance_m, 1),
            "frontage_estimate_m": estimated_frontage,
            "depth_estimate_m": estimated_depth,
            "area_difference_pct": round(area_difference, 1) if area_difference is not None else None,
            "area_difference_m2": round(area_difference_m2, 1) if area_difference_m2 is not None else None,
            "area_within_10pct": area_difference is not None and area_difference <= 10,
            "frontage_difference_pct": round(frontage_difference, 1) if frontage_difference is not None else None,
            "polygon": rings,
            "near_match": True,
            "approximate_identification": True,
            "match_type": "intersection_candidate",
            "match_confidence": "baja",
        }
        candidates.append(candidate)

    ranked = _rank_intersection_candidates(
        candidates,
        listing_area_m2=listing_area_m2,
        frontage_m=frontage_m,
    )
    # Address/CHIP enrichment is only needed for the eight results users can
    # actually select. Querying every corridor lot made long segments slow.
    properties = _properties_for_lots([candidate["lotcodigo"] for candidate in ranked])
    for candidate in ranked:
        property_data = properties.get(candidate["lotcodigo"]) or {"addresses": [], "chips": []}
        address = property_data["addresses"][0] if property_data["addresses"] else None
        chips = property_data["chips"]
        candidate["label"] = address or f"Predio {candidate['lotcodigo']}"
        candidate["official_address"] = address
        candidate["chip"] = chips[0] if len(chips) == 1 else None
        candidate["chip_count"] = len(chips)
    return ranked


def resolve_intersection(
    raw: str,
    *,
    listing_area_m2: float | None = None,
    frontage_m: float | None = None,
    depth_m: float | None = None,
) -> dict:
    parsed = parse_intersection_query(raw)
    if not parsed:
        return {"candidates": [], "resolution": "intersection_unrecognized"}
    axes = [_road_axis_geometry(road) for road in parsed["roads"]]
    unresolved = []
    points: list[tuple[float, float]] = []
    if parsed["kind"] == "corner":
        points = _road_intersection_points(axes[0], axes[1])
        if not points:
            unresolved = [road["display"] for road, axis in zip(parsed["roads"], axes) if axis is None]
    else:
        endpoint_points: list[tuple[float, float]] = []
        for index in (1, 2):
            endpoint = _road_intersection_points(axes[0], axes[index])
            if endpoint:
                endpoint_points.append(endpoint[0])
            else:
                unresolved.append(parsed["roads"][index]["display"])
                # Preserve the full requested span using the closest point on
                # the primary official axis. The unresolved reference remains
                # explicitly disclosed to the user.
                if axes[0] is not None and axes[index] is not None:
                    primary_point, _cross_point = nearest_points(axes[0], axes[index])
                    endpoint_points.append((primary_point.x, primary_point.y))
        if len(endpoint_points) == 2:
            points = _sample_segment(endpoint_points[0], endpoint_points[1])
        else:
            points = endpoint_points
    points = points if parsed["kind"] == "between" else _cluster_intersections(points)
    candidates = _intersection_lot_candidates(
        points,
        listing_area_m2=listing_area_m2,
        frontage_m=frontage_m,
        depth_m=depth_m,
    )
    for candidate in candidates:
        candidate["intersection_query"] = parsed["raw"]
    return {
        "candidates": candidates,
        "resolution": "intersection_candidates" if candidates else "intersection_not_found",
        "intersection": {
            "kind": parsed["kind"],
            "query": parsed["raw"],
            "points": [
                {"lng": lng, "lat": lat}
                for lng, lat in (
                    [points[0], points[-1]]
                    if parsed["kind"] == "between" and len(points) > 1 else points
                )
            ],
            "sampled_point_count": len(points),
            "unresolved_references": unresolved,
            "source": "Mapa de Referencia de Bogotá · Nomenclatura vial · capa 11",
            "service_url": _MAPA_REFERENCIA_VIAS,
            "listing_area_m2": listing_area_m2,
            "frontage_m": frontage_m,
            "depth_m": depth_m,
        },
    }


# -----------------------------------------------------------------------
# Nominatim fallback
# -----------------------------------------------------------------------

def _nominatim_query(q: str) -> list[dict]:
    params = urllib.parse.urlencode({
        "q": f"{q}, Bogotá, Colombia",
        "format": "json",
        "limit": 5,
        "viewbox": _BOGOTA_VIEWBOX,
        "bounded": 1,
        "addressdetails": 1,
    })
    url = f"{_NOMINATIM}?{params}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "BogotaBuildabilityTool/1.0 (imabossgaming123@gmail.com)"}
    )
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=10) as r:
        results = json.load(r)

    candidates: list[dict] = []
    for r in results:
        address = r.get("address") or {}
        category = str(r.get("class") or r.get("category") or "").lower()
        result_type = str(r.get("type") or "").lower()
        addresstype = str(r.get("addresstype") or "").lower()
        # Never surface a city, district, state, boundary or generic road as if
        # it were the requested property. OSM is accepted only at address or
        # building granularity.
        is_property_level = bool(address.get("house_number")) or (
            category == "building" or result_type in {"house", "building"}
            or addresstype in {"house", "building"}
        )
        if not is_property_level:
            continue
        lat = float(r["lat"])
        lng = float(r["lon"])
        label = r.get("display_name", q)
        # Trim the label for display
        parts = [p.strip() for p in label.split(",")][:4]
        short_label = ", ".join(parts)
        candidates.append({
            "lat": lat, "lng": lng, "label": short_label,
            "source": "nominatim", "near_match": True,
            "match_type": "osm_address",
            "match_confidence": "baja",
        })

    return candidates


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

def geocode_detailed(
    address: str,
    *,
    listing_area_m2: float | None = None,
    frontage_m: float | None = None,
    depth_m: float | None = None,
) -> dict:
    """
    Geocode a Bogotá address.

    Returns a list of candidate dicts:
        [{"lat": float, "lng": float, "label": str, "source": str, "score": float}, ...]

    Empty list means no results found or query was rejected (e.g. intersection input).
    Raises on network / parse errors.
    """
    # CHIP is not an address plate. Resolve it before the street-format guard.
    chip = _normalize_chip(address)
    if chip:
        key = f"chip:{chip}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            candidates = _catastro_chip_query(chip)
            resolution = "exact_chip" if candidates else "chip_not_found"
        except Exception:
            return {"candidates": [], "resolution": "catastro_unavailable"}
        result = {
            "candidates": candidates,
            "resolution": resolution,
        }
        _cache_set(key, result)
        return result

    # An explicit non-Bogotá city is a coverage answer, not part of the street
    # plate. Stop before querying Catastro so Ainmo can never strip the city and
    # offer a similarly numbered Bogotá property.
    outside_city = _explicit_outside_bogota_city(address)
    if outside_city:
        return {
            "candidates": [],
            "resolution": "outside_bogota",
            "locality": outside_city,
        }

    # Intersections deliberately return multiple cadastral lots. They never
    # fall through to address matching, which could silently select one corner.
    if _is_intersection_query(address):
        cache_key = (
            f"intersection:{_plain_text(address)}:{listing_area_m2 or ''}:"
            f"{frontage_m or ''}:{depth_m or ''}"
        )
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        result = resolve_intersection(
            address,
            listing_area_m2=listing_area_m2,
            frontage_m=frontage_m,
            depth_m=depth_m,
        )
        _cache_set(cache_key, result)
        return result

    # Normalise for structured queries; keep original for Nominatim (OSM
    # handles "Calle 90" better than the abbreviated form "CL 90").
    normalised = normalize_address(address)
    key = normalised
    cached = _cache_get(key)
    if cached is not None:
        return cached


    if not _is_complete_street_plate(normalised):
        result = {"candidates": [], "resolution": "incomplete_address"}
        _cache_set(key, result)
        return result

    candidates: list[dict] = []
    resolution = "unrecognized_street"

    # --- Priority 1: Catastro placadomiciliaria (uses normalised form) ---
    parsed = _parse_address(normalised)
    if parsed:
        pdonvial, pdotexto = parsed
        try:
            candidates = _catastro_query(pdonvial, pdotexto)
            # Many major streets are stored under their avenue code (AC/AK).
            if not candidates:
                code = pdonvial.split()[0]
                alt = _ALT_CODE.get(code)
                if alt:
                    alt_via = pdonvial.replace(code + " ", alt + " ", 1)
                    candidates = _catastro_query(alt_via, pdotexto)
            if candidates:
                resolution = "exact_address"
                for candidate in candidates:
                    candidate["match_type"] = "exact_address"
                    candidate["match_confidence"] = "alta"
            # Block-level near-match: same street, same cross-street, any house #.
            # Only attempted when exact match fails and we have a cross number.
            if not candidates and pdotexto:
                cross_num = pdotexto.split()[0]  # e.g. "11 73" → "11"
                if cross_num.isdigit():
                    candidates = _catastro_near(pdonvial, pdotexto)
                    if not candidates:
                        code = pdonvial.split()[0]
                        alt = _ALT_CODE.get(code)
                        if alt:
                            alt_via = pdonvial.replace(code + " ", alt + " ", 1)
                            candidates = _catastro_near(alt_via, pdotexto)
                if candidates:
                    resolution = "same_block"
            if not candidates:
                # Distinguish a valid Catastro street with an unresolved door
                # number from a street token absent from the address registry.
                # A one-row LIKE query is unsafe here: ArcGIS may return
                # ``CL 106A`` before ``CL 106``, which our exact Python filter
                # correctly discards and then falsely labels the real street as
                # unknown. Inspect a useful page before deciding the street is
                # absent from Catastro.
                recognized = bool(_catastro_query(pdonvial, "", limit=100, exact=False))
                if not recognized:
                    code = pdonvial.split()[0]
                    alt = _ALT_CODE.get(code)
                    if alt:
                        alt_via = pdonvial.replace(code + " ", alt + " ", 1)
                        recognized = bool(_catastro_query(alt_via, "", limit=100, exact=False))
                resolution = "street_recognized" if recognized else "unrecognized_street"
        except Exception:
            resolution = "catastro_unavailable"

    if candidates and any(candidate.get("source") == "catastro" for candidate in candidates):
        try:
            candidates = _anchor_candidates_to_linked_lots(candidates)
        except Exception:
            # Keep the original official address points. The calculation API's
            # lot-code mismatch guard will still refuse a neighboring parcel.
            pass

    # --- Priority 2: Nominatim (uses original text, not abbreviated form) ---
    if not candidates:
        try:
            candidates = _nominatim_query(address)
            if candidates:
                resolution = "osm_address"
        except Exception:
            pass

    # Reject any candidate that falls outside Bogotá's urban boundary.
    # This prevents far-off geocoder results from silently mis-locating a query.
    candidates = [c for c in candidates if _in_bogota(c["lat"], c["lng"])]

    # Score each candidate for fuzzy relevance, then sort descending.
    for c in candidates:
        c.pop("address_text", None)
        c["score"] = _fuzzy_score(normalised, normalize_address(c.get("label", "")))
    candidates.sort(key=lambda c: c["score"], reverse=True)

    result = {"candidates": candidates, "resolution": resolution}
    _cache_set(key, result)
    return result


def geocode(address: str) -> list[dict]:
    """Backward-compatible candidate-only API used by tests and scripts."""
    return geocode_detailed(address)["candidates"]


# -----------------------------------------------------------------------
# Quick self-test
# -----------------------------------------------------------------------

_TEST_ADDRESSES = [
    # (address, approx_lat, approx_lng, max_dist_deg, expect_empty)
    # max_dist_deg: None = just verify no crash; expect_empty: True = must return []
    ("Calle 72 # 10-34",          4.657,  -74.058,  0.02, False),  # Chapinero
    ("Carrera 15 # 93-60",        4.677,  -74.052,  0.02, False),  # Chico Norte
    ("Calle 100 # 19-61",         4.686,  -74.053,  0.02, False),  # Chicó
    ("Cl 57 # 8B-29",             4.644,  -74.064,  0.02, False),  # Chapinero bajo
    ("Diagonal 85 # 85A-37",      4.707,  -74.098,  0.02, False),  # Engativá
    ("Calle 26 # 69A-51",         4.659,  -74.108,  0.02, False),  # El Dorado (AC 26)
    ("Carrera 30 # 45A-55",       4.636,  -74.080,  0.02, False),  # NQS (AK/KR 30)
    ("Calle 127 # 93D-50",        4.721,  -74.097,  0.02, False),  # Suba
    # Regression: 3-number address without '#' — must insert separator correctly
    ("Calle 90 11 73",            4.649,  -74.063,  0.03, False),  # Chapinero (fixed)
    # Regression: intersection query — MUST return empty (no mismatched lot)
    ("calle 60 carrera 7",        None,   None,     None, True),   # intersection
    ("Calle 72 con Carrera 15",   None,   None,     None, True),   # intersection
]


def _run_tests():
    import time
    print("Geocoder self-test\n" + "="*50)
    failures = 0
    for addr, exp_lat, exp_lng, max_dist, expect_empty in _TEST_ADDRESSES:
        t0 = time.time()
        try:
            results = geocode(addr)
            elapsed = time.time() - t0
            if expect_empty:
                if results:
                    print(f"FAIL  {addr!r}: expected empty (intersection), got {len(results)} result(s)")
                    failures += 1
                else:
                    print(f"PASS  {addr!r} → empty (intersection correctly rejected) [{elapsed:.1f}s]")
            elif results:
                r = results[0]
                if exp_lat is not None and exp_lng is not None:
                    dist = ((r["lat"] - exp_lat)**2 + (r["lng"] - exp_lng)**2) ** 0.5
                    if dist > max_dist:
                        print(f"FAIL  {addr!r}: dist={dist:.4f}° > {max_dist}° (got {r['lat']:.5f},{r['lng']:.5f})")
                        failures += 1
                    else:
                        print(f"PASS  {addr!r} → ({r['lat']:.5f},{r['lng']:.5f}) via {r['source']} dist={dist:.4f}° [{elapsed:.1f}s]")
                else:
                    print(f"OK  {addr!r} → ({r['lat']:.5f},{r['lng']:.5f}) via {r['source']} [{elapsed:.1f}s]")
                if len(results) > 1:
                    print(f"    + {len(results)-1} more candidates")
            else:
                print(f"EMPTY {addr!r} [{elapsed:.1f}s]")
        except Exception as e:
            print(f"ERR {addr!r}: {e}")
        print()
    print(f"\n{'All tests passed.' if failures == 0 else f'{failures} test(s) FAILED.'}")


if __name__ == "__main__":
    _run_tests()
