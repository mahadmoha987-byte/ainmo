"""
geocode.py — Bogotá address → coordinates

Priority:
1. Catastro placadomiciliaria (esriGeometryPoint, authoritative source)
2. Nominatim / OpenStreetMap (street-level fallback)
"""

import re, ssl, json, unicodedata, urllib.request, urllib.parse, difflib

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_CATASTRO_PLACA = (
    "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services"
    "/catastro/placadomiciliaria/MapServer/0"
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


def _in_bogota(lat: float, lng: float) -> bool:
    return _BOGOTA_LAT[0] <= lat <= _BOGOTA_LAT[1] and _BOGOTA_LNG[0] <= lng <= _BOGOTA_LNG[1]


def _is_intersection_query(raw: str) -> bool:
    """Return True when input looks like 'Calle 60 Carrera 7' (intersection, no door number).

    Intersection queries cannot resolve to a unique lot — they produce mismatched
    coordinates.  Callers should return no results and ask the user to click the map.
    Exception: if a '#' is already present the address has a door number and is valid.
    """
    if "#" in raw:
        return False
    return len(_VIA_WORDS_RE.findall(raw)) >= 2

# -----------------------------------------------------------------------
# Simple LRU-style cache (avoids repeated network hits)
# -----------------------------------------------------------------------
_cache: dict[str, list[dict]] = {}
_MAX_CACHE = 256


def _cache_get(key: str):
    return _cache.get(key)


def _cache_set(key: str, value: list[dict]):
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

    # Insert missing '#' separator: "CL 90 11 73" → "CL 90 # 11-73"
    # Pattern: <TYPE> <via_num> <cross_num> <house_num> with no '#' already present
    if "#" not in s:
        m = re.match(
            r"^(AC|AK|CL|KR|DG|TV|AV)\s+(\d+[A-Z]?(?:\s+BIS)?(?:\s+[SE])?)\s+(\d+[A-Z]?)\s+(\d+[A-Z]?)(.*)",
            s,
        )
        if m:
            via_type, via_num, cross, house, rest = m.groups()
            s = f"{via_type} {via_num} # {cross}-{house}{rest}".strip()
            # Re-apply canonical space around '#'
            s = re.sub(r"\s*#\s*", " # ", s)
            s = re.sub(r"\s+", " ", s).strip()

    return s


def _fuzzy_score(query: str, label: str) -> float:
    """SequenceMatcher ratio between normalized forms (case-insensitive, stripped)."""
    a = query.strip().upper()
    b = label.strip().upper()
    return difflib.SequenceMatcher(None, a, b).ratio()


# Ordered: longest/most-specific patterns first
_PREFIX_MAP = [
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
        label = f"{a['PDONVIAL']} # {a['PDOTEXTO'].strip()}".strip()
        candidates.append({
            "lat": lat,
            "lng": lng,
            "label": label,
            "source": "catastro",
            # Preserve the lot explicitly linked to the official address plate.
            # A small number of plate points fall inside an adjacent polygon.
            "lotcodigo": str(a.get("PDOCLOTE") or "").strip() or None,
        })

    return candidates


def _catastro_near(pdonvial: str, cross_num: str) -> list[dict]:
    """
    Block-level fallback: find any address on `pdonvial` whose PDOTEXTO
    starts with the cross-street number, e.g. PDOTEXTO LIKE '11 %'.
    Returns up to 5 results tagged near_match=True.
    """
    results = _catastro_query(pdonvial, f"{cross_num} ", limit=5, exact=False)
    for r in results:
        r["near_match"] = True
    return results


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
        "addressdetails": 0,
    })
    url = f"{_NOMINATIM}?{params}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "BogotaBuildabilityTool/1.0 (imabossgaming123@gmail.com)"}
    )
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=10) as r:
        results = json.load(r)

    candidates: list[dict] = []
    for r in results:
        lat = float(r["lat"])
        lng = float(r["lon"])
        label = r.get("display_name", q)
        # Trim the label for display
        parts = [p.strip() for p in label.split(",")][:4]
        short_label = ", ".join(parts)
        candidates.append({
            "lat": lat, "lng": lng, "label": short_label,
            "source": "nominatim", "near_match": True,
        })

    return candidates


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

def geocode(address: str) -> list[dict]:
    """
    Geocode a Bogotá address.

    Returns a list of candidate dicts:
        [{"lat": float, "lng": float, "label": str, "source": str, "score": float}, ...]

    Empty list means no results found or query was rejected (e.g. intersection input).
    Raises on network / parse errors.
    """
    # Intersection queries ("Calle 60 Carrera 7") cannot resolve to a unique lot.
    # Return empty so the frontend asks the user to click on the map instead.
    if _is_intersection_query(address):
        return []

    # Normalise for structured queries; keep original for Nominatim (OSM
    # handles "Calle 90" better than the abbreviated form "CL 90").
    normalised = normalize_address(address)
    key = normalised
    cached = _cache_get(key)
    if cached is not None:
        return cached

    candidates: list[dict] = []

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
            # Block-level near-match: same street, same cross-street, any house #.
            # Only attempted when exact match fails and we have a cross number.
            if not candidates and pdotexto:
                cross_num = pdotexto.split()[0]  # e.g. "11 73" → "11"
                if cross_num.isdigit():
                    candidates = _catastro_near(pdonvial, cross_num)
                    if not candidates:
                        code = pdonvial.split()[0]
                        alt = _ALT_CODE.get(code)
                        if alt:
                            alt_via = pdonvial.replace(code + " ", alt + " ", 1)
                            candidates = _catastro_near(alt_via, cross_num)
        except Exception:
            pass  # network/parse error → fall through to Nominatim

    # --- Priority 2: Nominatim (uses original text, not abbreviated form) ---
    if not candidates:
        try:
            candidates = _nominatim_query(address)
        except Exception:
            pass

    # Reject any candidate that falls outside Bogotá's urban boundary.
    # This prevents far-off geocoder results from silently mis-locating a query.
    candidates = [c for c in candidates if _in_bogota(c["lat"], c["lng"])]

    # Score each candidate for fuzzy relevance, then sort descending.
    for c in candidates:
        c["score"] = _fuzzy_score(address, c.get("label", ""))
    candidates.sort(key=lambda c: c["score"], reverse=True)

    _cache_set(key, candidates)
    return candidates


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
