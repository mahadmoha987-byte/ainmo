#!/usr/bin/env node
/* test_nomenclatura.js — Unit tests for nomenclatura.js normalizarDireccion
 *
 * Run with:
 *   node static/test_nomenclatura.js
 */
"use strict";

const { normalizarDireccion } = require("./nomenclatura.js");

let passed = 0, failed = 0;

function check(label, got, expected) {
  if (got === expected) {
    passed++;
    console.log("  PASS  " + label);
  } else {
    failed++;
    console.log("  FAIL  " + label);
    console.log("        got      " + JSON.stringify(got));
    console.log("        expected " + JSON.stringify(expected));
  }
}

// Five canonical cases from the spec
check("full-word 'Calle' with spaced #",
  normalizarDireccion("Calle 90 # 11-73"), "CL 90 # 11-73");

check("lowercase abbreviated 'cl' with no-space #",
  normalizarDireccion("cl 90 #11-73"), "CL 90 # 11-73");

check("'No.' separator with spaced hyphen",
  normalizarDireccion("KR 11 No. 90 - 73"), "KR 11 # 90-73");

check("'Avenida Carrera' long form with 'Nº'",
  normalizarDireccion("Avenida Carrera 11 Nº 90-73"), "AK 11 # 90-73");

check("'Cll' abbreviation — inserts missing separator",
  normalizarDireccion("Cll 85 12 34"), "CL 85 # 12-34");

check("'Av. Cra.' canonicalizes to AK",
  normalizarDireccion("Av. Cra. 68 # 40-15"), "AK 68 # 40-15");

check("Sur and Este modifiers are canonicalized",
  normalizarDireccion("Cl. 11 Sur # 1-60 Este"), "CL 11 S # 1-60 E");

check("named Avenida Boyacá becomes cadastral AK 72",
  normalizarDireccion("Avenida Boyacá # 63-20"), "AK 72 # 63-20");

check("named Avenida El Dorado becomes cadastral AC 26",
  normalizarDireccion("Av. El Dorado # 69-76"), "AC 26 # 69-76");

// Extra robustness cases
check("'Avenida Calle' -> AC",
  normalizarDireccion("Avenida Calle 26 # 69-76"), "AC 26 # 69-76");

check("'Diagonal' -> DG",
  normalizarDireccion("Diagonal 85 # 85A-37"), "DG 85 # 85A-37");

check("'Transversal' -> TV",
  normalizarDireccion("Transversal 15 # 22-30"), "TV 15 # 22-30");

check("empty string returns empty",
  normalizarDireccion(""), "");

console.log("\n" + (failed === 0 ? "OK" : "FAIL") + "  " + passed + "/" + (passed + failed) + " passed");
process.exit(failed > 0 ? 1 : 0);
