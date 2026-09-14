# Estados de figuras — implementación 14.09.2026

## Contrato y compatibilidad

`figure_status.annotate_result` añade metadatos sin modificar cifras, nulos,
condiciones ni claves existentes. Copia las entradas para no contaminar el caché GIS.
Los objetos de métricas, área, ANU y antejardín llevan `estado`, `motivo`,
`que_se_necesita` y `quien_lo_resuelve`. Los índices del lookup están en el bloque
aditivo `edificabilidad`. Los escalares originales (por ejemplo porcentajes de
estacionamientos) conservan sus tipos: sus objetos de presentación están en `figuras`.

`resumen_estados` cuenta IDs únicos de `figuras`. Las copias del resumen ejecutivo,
los pasos de trazabilidad, las entradas auxiliares del lookup, el escenario financiero
y la maqueta no se cuentan otra vez. El filtro se aplica a todas las secciones.
La referencia de obligaciones tenía un `estado` heredado con otro significado:
se conserva; su figura paralela usa el enum cerrado nuevo.

Consolidación conserva su área normativa nula. La estimación conserva método,
intervalo, confianza, entradas y supuestos. No se introduce ninguna cifra nueva
en el motor financiero. Se conserva y hace visible la bandera de área estimada.

## Corpus local verificado

`static/article-corpus.json` contiene 13 entradas: artículos 281, 303, 304, 310,
338 y 389 del D.555/2021; secciones 1.7, 1.9.b, 1.11.a, 3.1.b, 3.2.a, 4.2.b
y 5.4.b del Anexo 5 sustituido por D.466/2024. Cada entrada identifica la página,
URL oficial, fecha de cotejo y si es artículo completo, extracto o tabla.
Las tablas se transcriben en texto accesible; los saltos de línea se normalizan.
El cliente carga este archivo estático, nunca consulta decretos remotos por predio.

Se usaron las publicaciones oficiales del archivo de la Secretaría Jurídica:

- D.555: NORMFIL_ID 28934, 494 páginas.
- Anexo 5 D.466: NORMFIL_ID 67754, 108 páginas; cotejo visual de páginas escaneadas.

Esto **no es un corpus exhaustivo ni una certificación de vigencia**. No se afirma
haber revisado integralmente todas las modificaciones e instrumentos especiales.
Donde falta una transcripción o mapeo seguro, el disclosure lo dice expresamente.
Para áreas catastrales o escenarios financieros no existe una cifra literal del decreto.

## Pendientes de revisión normativa, no corregidos aritméticamente en este cambio

- Completar corpus de D.253/2026 / Anexo 36.1, Res.954/2026, incentivos D.676,
  Art.390 y demás apartados del Anexo 5 que aparecen como pendientes.
- Art.307 no sustenta la ausencia general de antejardín en Renovación: el nuevo
  visor muestra Sección 1.7.a, incluyendo excepción de empates; el campo heredado
  se conserva para compatibilidad.
- `A = 2,5 D` es altura límite de fachada, no retiro horizontal. El visor distingue
  A de R = AD/5. Capa 38 no aporta el perfil completo.
- Art.310 parágrafo 4 contiene una excepción numérica para San José de Bavaria.
  No se añadió detección geográfica de esa excepción. Requiere una revisión aparte
  antes de afirmar que la regla general cubre todos los predios de Consolidación.
- Art.338 incluye «igual o mayor a 2000 m²» para cesión; el motor heredado usa
  `> 2000` en esa rama. Pendiente corregir con pruebas de frontera en una revisión
  de cálculos, no ocultarlo mediante etiquetas.
- La antigua referencia al Art.339 para estacionamientos PEMP requiere corrección:
  el artículo consultado trata de bordes urbanos. No se incorporó una cita falsa.
- La subdivisión en tipología continua no se autoriza únicamente por Art.310.4:
  el nuevo visor señala la verificación pendiente de frente, área e instrumento.
- La huella de la maqueta 3D usa otro método aproximado y puede diferir del área
  derivada del resumen. Se informa en la vista; no se cambió su geometría.

## Verificación

`python3 -m pytest -q` y `node static/test_figure_status.cjs`.
Pruebas de valores heredados, las cinco fixtures solicitadas, enum, cero,
ausencia de estimación ficticia, no mutación, conteo, fórmulas sin entradas,
umbral condicionado, cita de decreto especial, escapado y sintaxis JS.
Prueba en navegador local: Kennedy, 204 m² derivados y 5 pisos resueltos;
filtro de derivados, apertura de Art.310, sin celdas vacías/dash y sin desborde
horizontal en vista normal y 390 px. No constituye una auditoría exhaustiva.

No se realizó push ni despliegue en Railway en este cambio.
