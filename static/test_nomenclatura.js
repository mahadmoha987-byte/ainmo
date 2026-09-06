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

check("'Cll' abbreviation — no # separator, passes through",
  normalizarDireccion("Cll 85 12 34"), "CL 85 12 34");

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
