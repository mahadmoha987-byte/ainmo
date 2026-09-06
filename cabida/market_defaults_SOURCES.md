# market_defaults_SOURCES.md

Every figure in `cabida/market_defaults.py` is listed below with its source, URL (where available), and date.  
Figures marked **null** lack a citable source; user input is required before use.

---

## 1. Construction hard costs (`costos_construccion_cop_m2`)

| Key | Value (COP/m²) | Source | URL | Date |
|-----|---------------|--------|-----|------|
| `vis` | 2,000,000 (range 1.8M–2.2M) | vivienda.com.co "Precio metro cuadrado construcción Colombia 2026" | https://www.vivienda.com.co/precio-metro-cuadrado-construccion-colombia/ | 2026 |
| `vis` | 2,000,000 (corroboration) | oneestimate.ai "Costos de construcción de vivienda en Colombia 2024 (actualizado 2026)" | https://oneestimate.ai/costos-construccion-vivienda-colombia/ | 2024, updated 2026 |
| `no_vis_estandar` | 2,800,000 (range 2.2M–3.5M) | vivienda.com.co — torres de apartamentos estándar estrato 4 | https://www.vivienda.com.co/precio-metro-cuadrado-construccion-colombia/ | 2026 |
| `no_vis_estandar` | 2,800,000 (corroboration) | oneestimate.ai 2024/2026 — estrato 4 | https://oneestimate.ai/costos-construccion-vivienda-colombia/ | 2024, updated 2026 |
| `no_vis_alto` | 5,500,000 (range 4.5M–7M) | vivienda.com.co — vivienda premium | https://www.vivienda.com.co/precio-metro-cuadrado-construccion-colombia/ | 2026 |
| `no_vis_alto` | 5,500,000 (corroboration) | oneestimate.ai 2026 — estrato 5-6 | https://oneestimate.ai/costos-construccion-vivienda-colombia/ | 2026 |

**DANE ICOCED context:** Boletín ICOCED feb-2026 (DANE) reported the national construction cost index rose +3.61% in 2025, consistent with the above ranges being updated relative to prior-year figures.  
Source: DANE — Índice de Costos de Construcción de Edificaciones — Boletín febrero 2026 | https://www.dane.gov.co/index.php/estadisticas-por-tema/construccion/indice-de-costos-de-la-construccion

---

## 2. Sale prices by localidad (`precio_venta_cop_m2`)

### Usaquén
| Aspect | Detail |
|--------|--------|
| Central value | 7,400,000 COP/m² |
| Range | 4,800,000 – 11,000,000 |
| Source 1 | Habi.co análisis interno 2025: mediana $5,275,229/m² | URL: https://www.habi.co/analisis-mercado/ | Date: 2025 |
| Source 2 | Goodsyservices Consulting "Análisis del mercado inmobiliario de Bogotá" oct-2025: rango $5,275,229–$9,000,000 | URL: https://goodsyservices.com/analisis-del-mercado-inmobiliario-de-bogota-octubre-2025/ | Date: 2025-10-30 |
| Source 3 | Mubrick Inmobiliaria 2026 — proyectos activos en Usaquén | URL: https://mubrick.co/ | Date: 2026 |
| Source 4 (range ceiling) | Lonja de Propiedad Raíz de Bogotá oct-2025 (via Metrocuadrado): La Cabrera $12,900,000/m² — historical maximum for Bogotá | URL: https://www.metrocuadrado.com/ | Date: 2025-10 |

### Chapinero
| Aspect | Detail |
|--------|--------|
| Central value | 8,800,000 COP/m² |
| Range | 6,500,000 – 13,000,000 |
| Source 1 | Mubrick Inmobiliaria 2026: $8,800,000 | URL: https://mubrick.co/ | Date: 2026 |
| Source 2 | Habi.co 2025: mediana $7,000,000 | URL: https://www.habi.co/analisis-mercado/ | Date: 2025 |
| Source 3 | Goodsyservices oct-2025: $7,800,000–$12,000,000+ | URL: https://goodsyservices.com/analisis-del-mercado-inmobiliario-de-bogota-octubre-2025/ | Date: 2025-10-30 |
| Source 4 (range ceiling) | Lonja/Metrocuadrado oct-2025: El Retiro / La Cabrera $11,900,000–$12,900,000 — Bogotá's historical record | URL: https://www.metrocuadrado.com/ | Date: 2025-10 |

### Teusaquillo
| Aspect | Detail |
|--------|--------|
| Central value | 6,500,000 COP/m² |
| Range | 4,500,000 – 9,000,000 |
| Source 1 | Habi.co 2025: mediana $5,322,580 | URL: https://www.habi.co/analisis-mercado/ | Date: 2025 |
| Source 2 | Portafolio / Metrocuadrado 2024-2025: estrato medio $5,400,000 | URL: https://www.portafolio.co/economia/finanzas/cuanto-cuesta-el-metro-cuadrado-en-bogota/ | Date: 2024-2025 |
| Source 3 | Mubrick Inmobiliaria 2026: $7,400,000 | URL: https://mubrick.co/ | Date: 2026 |
| Source 4 | Mubrick: Ciudad Salitre Oriental (Teusaquillo) $8,700,000 | URL: https://mubrick.co/ | Date: 2026 |

### Suba
| Aspect | Detail |
|--------|--------|
| Central value | 6,000,000 COP/m² |
| Range | 3,500,000 – 9,000,000 |
| Source 1 | Goodsyservices oct-2025: $5,400,000 | URL: https://goodsyservices.com/analisis-del-mercado-inmobiliario-de-bogota-octubre-2025/ | Date: 2025-10-30 |
| Source 2 | Mubrick Inmobiliaria 2026: $6,400,000 | URL: https://mubrick.co/ | Date: 2026 |
| Note | Sector breakdowns: Colina Campestre/Niza estrato 5-6: $7M–$9M; Suba-Suba/Tibabuyes estrato 2-3: $3.5M–$4.5M |

### Barrios Unidos
| Aspect | Detail |
|--------|--------|
| Central value | 5,000,000 COP/m² |
| Range | 3,800,000 – 7,000,000 |
| Source 1 | Habi.co 2025: mediana $4,600,000 | URL: https://www.habi.co/analisis-mercado/ | Date: 2025 |
| Source 2 | Portafolio / Metrocuadrado 2024-2025: estrato medio $5,000,000 | URL: https://www.portafolio.co/economia/finanzas/cuanto-cuesta-el-metro-cuadrado-en-bogota/ | Date: 2024-2025 |

### Fontibón
| Aspect | Detail |
|--------|--------|
| Central value | 4,600,000 COP/m² |
| Range | 3,500,000 – 6,500,000 |
| Source 1 | Goodsyservices oct-2025: ~$4,300,000 | URL: https://goodsyservices.com/analisis-del-mercado-inmobiliario-de-bogota-octubre-2025/ | Date: 2025-10-30 |
| Source 2 | Portafolio / Metrocuadrado 2024-2025: estrato medio $5,000,000 | URL: https://www.portafolio.co/economia/finanzas/cuanto-cuesta-el-metro-cuadrado-en-bogota/ | Date: 2024-2025 |

### Engativá
| Aspect | Detail |
|--------|--------|
| Central value | 4,300,000 COP/m² |
| Range | 3,000,000 – 5,800,000 |
| Source 1 | Goodsyservices oct-2025: ~$4,300,000 | URL: https://goodsyservices.com/analisis-del-mercado-inmobiliario-de-bogota-octubre-2025/ | Date: 2025-10-30 |
| Source 2 | Portafolio / Metrocuadrado 2024-2025: $4,400,000 estrato medio | URL: https://www.portafolio.co/economia/finanzas/cuanto-cuesta-el-metro-cuadrado-en-bogota/ | Date: 2024-2025 |

### Puente Aranda
| Aspect | Detail |
|--------|--------|
| Central value | **null** |
| Reason | No citable residential market data found. Predominantly industrial (estrato 2-3, free trade zone, warehouses). Requires site-specific market study. |

### Santa Fe
| Aspect | Detail |
|--------|--------|
| Central value | 5,500,000 COP/m² |
| Range | 3,000,000 – 8,500,000 |
| Source 1 | Habi.co 2025: mediana $5,769,230 | URL: https://www.habi.co/analisis-mercado/ | Date: 2025 |
| Source 2 | Catastro Bogotá: catastral variation +8.2% in 2026 (contextual — not a sale price) | URL: https://www.catastrobogota.gov.co/ | Date: 2026 |

### Los Mártires
| Aspect | Detail |
|--------|--------|
| Central value | **null** |
| Reason | No citable residential sale-price data found. Predominantly commercial/hotel use with limited new residential transactions. |

### Antonio Nariño
| Aspect | Detail |
|--------|--------|
| Central value | **null** |
| Reason | No citable residential sale-price data found. Estrato 2-3, limited residential development pipeline. |

### La Candelaria
| Aspect | Detail |
|--------|--------|
| Central value | **null** |
| Reason | Historic district with Bienes de Interés Cultural restrictions; atypical market (tourism, institutional). Not comparable to conventional residential development. |

### Kennedy
| Aspect | Detail |
|--------|--------|
| Central value | 4,200,000 COP/m² |
| Range | 2,800,000 – 6,500,000 |
| Source 1 | Goodsyservices oct-2025: ~$3,500,000 (general average) | URL: https://goodsyservices.com/analisis-del-mercado-inmobiliario-de-bogota-octubre-2025/ | Date: 2025-10-30 |
| Source 2 | Metrocuadrado 2025 (search results): strata 4-5 units ~$5,160,855 | URL: https://www.metrocuadrado.com/ | Date: 2025 |
| Source 3 | Catastro Bogotá 2025 | URL: https://www.catastrobogota.gov.co/ | Date: 2025 |

### Bosa
| Aspect | Detail |
|--------|--------|
| Central value | 3,200,000 COP/m² |
| Range | 2,000,000 – 4,500,000 |
| Source 1 | Goodsyservices oct-2025: $2,500,000–$4,000,000 | URL: https://goodsyservices.com/analisis-del-mercado-inmobiliario-de-bogota-octubre-2025/ | Date: 2025-10-30 |
| Source 2 | Catastro Bogotá 2026: catastral variation +8.5% (contextual) | URL: https://www.catastrobogota.gov.co/ | Date: 2026 |

### Rafael Uribe Uribe
| Aspect | Detail |
|--------|--------|
| Central value | 4,100,000 COP/m² |
| Range | 2,800,000 – 5,500,000 |
| Source 1 | Catastro Bogotá 2025: promedio $4,155,738/m² | URL: https://www.catastrobogota.gov.co/ | Date: 2025 |
| Source 2 | Metrocuadrado 2025 — Diana Turbay y barrios aledaños | URL: https://www.metrocuadrado.com/ | Date: 2025 |

### San Cristóbal
| Aspect | Detail |
|--------|--------|
| Central value | 3,800,000 COP/m² |
| Range | 2,500,000 – 5,200,000 |
| Source 1 | Catastro Bogotá 2025: promedio $4,000,000/m² | URL: https://www.catastrobogota.gov.co/ | Date: 2025 |
| Source 2 | Catastro Bogotá 2026: catastral variation +10.1% (highest in city, contextual) | URL: https://www.catastrobogota.gov.co/ | Date: 2026 |

### Tunjuelito
| Aspect | Detail |
|--------|--------|
| Central value | 3,600,000 COP/m² |
| Range | 2,500,000 – 4,800,000 |
| Source 1 | Metrocuadrado 2025: promedio $3,616,667/m² | URL: https://www.metrocuadrado.com/ | Date: 2025 |

### Ciudad Bolívar
| Aspect | Detail |
|--------|--------|
| Central value | 3,200,000 COP/m² |
| Range | 1,800,000 – 4,500,000 |
| Source 1 | Catastro Bogotá 2025: promedio $3,578,623/m² | URL: https://www.catastrobogota.gov.co/ | Date: 2025 |

### Usme
| Aspect | Detail |
|--------|--------|
| Central value | **null** |
| Reason | No citable data for new residential project sale prices in Usme. Predominantly VIS and self-built housing; catastral values reflect informal stock, not new development market. |

### Sumapaz
| Aspect | Detail |
|--------|--------|
| Central value | **null** |
| Reason | Páramo and protected rural land. No urban residential development applicable. |

### default_bogota (city-wide fallback)
| Aspect | Detail |
|--------|--------|
| Central value | 7,500,000 COP/m² |
| Range | 3,000,000 – 13,000,000 |
| Source 1 | Ciencuadras 2026: promedio general Bogotá $7,421,952/m² | URL: https://www.ciencuadras.com/ | Date: 2026 |
| Source 2 | Galería Inmobiliaria mar-2026: No-VIS nuevos $10,198,986/m², VIS nuevos $5,918,180/m² | URL: https://www.galeriainmobiliaria.com.co/informes-inmobiliarios | Date: 2026-03 |

---

## 3. Developer soft costs (`costos_blandos_pct`)

| Value | Range | Source | URL | Date |
|-------|-------|--------|-----|------|
| 0.18 (18%) | 12%–25% | Camacol industry practice; BBVA Situación Inmobiliaria Colombia 2025 (documents 12-25% range over direct construction cost for residential projects) | https://www.camacol.co/ ; https://www.bbvaresearch.com/ | 2025 |

No single publication provides a single definitive figure; the 12-25% range is the industry consensus documented across Camacol technical publications and BBVA's annual sector report.

---

## 4. Target developer margin (`margen_objetivo_pct`)

| Value | Range | Source | URL | Date |
|-------|-------|--------|-----|------|
| 0.20 (20%) | 12%–30% | BBVA Situación Inmobiliaria Colombia 2025: Non-VIS promoter margins 15-25% over revenues; Camacol industry practice | https://www.bbvaresearch.com/ ; https://www.camacol.co/ | 2025 |

---

## 5. Discount rate / IBR (`tasa_descuento_anual`)

| Value | Source | URL | Date |
|-------|--------|-----|------|
| 0.11183 (11.183% EA) | Banco de la República — IBR overnight nominal, 14-ago-2026 | https://www.banrep.gov.co/es/estadisticas/ibr | 2026-08-14 |
| Context | Monetary policy rate (tasa de intervención): 12.0% — Junta Directiva BanRep jul-2026 (+75 pb increase) | https://www.banrep.gov.co/es/noticias/comunicado-banco-republica | 2026-07 |
| Legacy ref | DTF weekly EA: 9.09% (4-week avg, dic-2025) — superseded by IBR as official reference rate from 2027 | https://www.actualicese.com/tasa-dtf/ | 2025-12 |

---

## 6. Default project duration (`plazo_meses_default`)

| Value | Source | URL | Date |
|-------|--------|-----|------|
| 30 months | Camacol: typical Bogotá residential project = pre-sale 6-12 months + construction 18-24 months ≈ 24-36 months; 30 months is the conservative midpoint | https://www.camacol.co/ | 2025 |

---

## Staleness policy

`fecha_actualizacion: "2026-08"` — threshold: 180 days.  
After **2027-02-27**, `is_stale()` returns `True` and the UI must display:

> ⚠ Precios de referencia desactualizados — verifique con fuentes de mercado recientes.

Priority sources to check on refresh:
1. **Galería Inmobiliaria** — monthly "Informe Inmobiliario" (nuevos + usados, by ciudad)
2. **DANE ICOCED** — quarterly construction cost index
3. **Banco de la República IBR** — weekly rate
4. **Habi.co / Metrocuadrado** — asking-price scrapers (monthly)
5. **Catastro Bogotá** — annual catastral update (typically March-April)
