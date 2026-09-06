/* nomenclatura.js — Bogotá address normaliser
 *
 * Exported:  window.normalizarDireccion  (browser)
 *            module.exports              (Node / tests)
 *
 * Input:   any free-form Colombian address string
 * Output:  canonical via-code abbreviation + standard spacing/punctuation
 *
 * Verified cases:
 *   "Calle 90 # 11-73"              -> "CL 90 # 11-73"
 *   "cl 90 #11-73"                  -> "CL 90 # 11-73"
 *   "KR 11 No. 90 - 73"             -> "KR 11 # 90-73"
 *   "Avenida Carrera 11 Nº 90-73"   -> "AK 11 # 90-73"
 *   "Cll 85 12 34"                  -> "CL 85 12 34"   (no separator, passes through)
 *
 * Limitations:
 *   - Does not resolve addresses without a # separator ("CL 85 12 34").
 *   - The abbreviated output (CL/KR/...) may confuse Nominatim; pass the
 *     original user text to OSM queries, not this normalised form.
 *
 * Next step (not yet implemented):
 *   Fuzzy-match the normalised output server-side against the Catastro
 *   PDONVIAL/PDOTEXTO address table and return ranked candidates instead
 *   of a hard "coordinate not found" error when there is no exact match.
 *   The normalised string produced here is the input to that matcher.
 */
(function (global) {
  "use strict";

  var VIAS = [
    [/^(AVENIDA\s+CARRERA|AV\s*CRA|AV\s*KR|AK)\b/, "AK"],
    [/^(AVENIDA\s+CALLE|AV\s*CLL?E?|AC)\b/,         "AC"],
    [/^(TRANSVERSAL|TRANSV|TRV|TV)\b/,              "TV"],
    [/^(DIAGONAL|DIAG|DG)\b/,                       "DG"],
    [/^(CARRERA|CRA|CARR|KRA|KR|CR)\b/,             "KR"],
    [/^(CALLE|CLLE|CLL|CL)\b/,                      "CL"],
    [/^(AVENIDA|AVDA|AVE|AV)\b/,                    "AV"]
  ];

  function normalizarDireccion(raw) {
    if (!raw) return "";
    var s = String(raw).trim().toUpperCase()
      .normalize("NFD").replace(/[̀-ͯ]/g, "")
      .replace(/[.,]/g, " ")
      .replace(/\s+/g, " ");
    // Nº / N° / No. / Nro → "#"
    s = s.replace(/\bN[º°]\s*/g, "# ").replace(/\b(NO|NRO|NUM|NUMERO)\b\s*/g, "# ");
    // Canonicalise spaces around "#"
    s = s.replace(/\s*#\s*/g, " # ");
    // Expand via type
    for (var i = 0; i < VIAS.length; i++) {
      if (VIAS[i][0].test(s)) { s = s.replace(VIAS[i][0], VIAS[i][1]); break; }
    }
    // Normalise cross-reference hyphen: "# 11 - 73" -> "# 11-73"
    s = s.replace(/(#\s*\d+\s*[A-Z]?(?:\s+BIS)?)\s*[-–—]\s*(\d+\s*[A-Z]?)/, "$1-$2");
    // Collapse remaining space-padded hyphens, tidy "#" and whitespace
    s = s.replace(/\s*-\s*/g, "-").replace(/#\s*/, "# ").replace(/\s+/g, " ").trim();
    return s;
  }

  global.normalizarDireccion = normalizarDireccion;

  // CommonJS export for Node (test runner)
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { normalizarDireccion: normalizarDireccion };
  }
}(typeof window !== "undefined" ? window : this));
