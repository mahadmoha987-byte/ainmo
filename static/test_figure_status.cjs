const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ui=require('./figure-status.js');
for(const estado of ['resuelto','derivado','insuficiente','requiere_concepto','no_aplica','error']){
  const html=ui.card({id:'x',etiqueta:'Prueba',valor:null,estado,motivo:'Falta confirmar la entrada.'});
  assert(!/>\s*(?:null|undefined|NaN|—)\s*</.test(html));
  assert(html.includes('data-estado="'+estado+'"'));
}
assert.equal(ui.display({valor:0,estado:'resuelto'}),'0');
assert.equal(ui.display({valor:0,estado:'no_aplica',motivo:'No se exige antejardín.'}),'No exigido');
assert.equal(ui.display({valor:null,estado:'requiere_concepto',motivo:'Debe definirlo la curaduría.'}),'Concepto requerido');
assert.notEqual(ui.display({valor:null,estado:'requiere_concepto',motivo:'Debe definirlo la curaduría.'}),'Requiere concepto');
assert.equal(ui.display({valor_m2:null,rango_m2:[1671,1911.6],estado:'derivado'}),'1.671–1.911,6');
assert(!ui.card({id:'<img>',etiqueta:'<script>alert(1)</script>',estado:'error',motivo:'<img>'}).includes('<script>'));
const summary=ui.summary({figuras:[{id:'a',estado:'resuelto'},{id:'a',estado:'resuelto'},{id:'b',estado:'derivado'}]});
assert(summary.includes('Todos · 2'));
assert(summary.includes('1 resuelta'));
assert(summary.includes('1 derivada'));
const metricSummary=ui.summary({
  metrics:{a:{estado:'resuelto'},b:{estado:'derivado'},c:{estado:'insuficiente'}},
  figuras:[
    {id:'metrics.a',etiqueta:'A',estado:'resuelto'},
    {id:'metrics.b',etiqueta:'B',estado:'derivado'},
    {id:'metrics.c',etiqueta:'C',estado:'insuficiente'},
    {id:'parking.min_pct',etiqueta:'Parking',estado:'resuelto'}
  ]
});
assert(metricSummary.includes('Todos · 4'));
assert(metricSummary.includes('2 resueltas'));
assert(metricSummary.includes('1 derivada'));
assert(metricSummary.includes('1 con datos insuficientes'));
const payloadSummary=ui.summary({
  resumen_estados:{total:27,resuelto:7,derivado:3,insuficiente:10,requiere_concepto:0,no_aplica:7,error:0},
  figuras:[{id:'metrics.a',estado:'resuelto'}]
});
assert(payloadSummary.includes('Todos · 27'));
assert(payloadSummary.includes('7 resueltas'));
assert(payloadSummary.includes('3 derivadas'));
assert(payloadSummary.includes('10 con datos insuficientes'));
assert(payloadSummary.includes('7 no aplican'));
const action=ui.card({id:'metrics.x',etiqueta:'X',valor:null,estado:'insuficiente',motivo:'Falta perfil.',que_se_necesita:'Confirmar perfil vial.',quien_lo_resuelve:'topógrafo'});
assert(action.includes('Se necesita:'));
assert(action.includes('Confirmar perfil vial. — topógrafo'));
const cited=ui.card({id:'metrics.altura',etiqueta:'Altura',valor:5,estado:'resuelto',motivo:'Resuelta.',fuente:'SDP · Capa 15 · ALTURA_MAXIMA',fuente_dato:'SDP · Capa 15 · ALTURA_MAXIMA',articulo_id:'555:310',fecha_consulta:'2026-09-15'});
assert(cited.includes('ALTURA MAXIMA'));
assert(!cited.includes('metrosAXIMA'));
assert.equal((cited.match(/SDP · Capa 15 · ALTURA MAXIMA/g)||[]).length,1);
assert(!cited.includes('555:310 ·'));
const grouped=ui.grid({figuras:[
  {id:'lote.area_m2',seccion:'volumetria',etiqueta:'Área del lote',valor:7771.7,unidad:'m²',estado:'derivado',motivo:'Área catastral.'},
  {id:'metrics.area_construible_max_m2',seccion:'volumetria',etiqueta:'Área máxima',valor:null,unidad:'m²',estado:'no_aplica',motivo:'El IC/IO es resultante.',articulo_id:'555:310'},
  {id:'metrics.planta_maxima_m2',seccion:'volumetria',etiqueta:'Huella máxima',valor:null,unidad:'m²',estado:'no_aplica',motivo:'El IC/IO es resultante.',articulo_id:'555:310'},
  {id:'metrics.aislamiento_posterior_m',seccion:'volumetria',etiqueta:'Aislamiento posterior',valor:5,unidad:'m',estado:'resuelto',motivo:'Norma volumétrica.'},
  {id:'parking.min_pct',seccion:'volumetria',etiqueta:'Mínimo',valor:8,unidad:'%',estado:'resuelto',motivo:'Misma explicación.',articulo_id:'555:389'},
  {id:'parking.max_pct',seccion:'volumetria',etiqueta:'Máximo',valor:20,unidad:'%',estado:'resuelto',motivo:'Misma explicación.',articulo_id:'555:389'},
  {id:'parking.adicional_pct',seccion:'volumetria',etiqueta:'Adicional',valor:15,unidad:'%',estado:'resuelto',motivo:'Misma explicación.',articulo_id:'555:389'},
  {id:'parking.min_area_m2',seccion:'volumetria',etiqueta:'Área mínima',valor:null,unidad:'m²',estado:'insuficiente',motivo:'Falta área_construible_max.',articulo_id:'555:389'},
  {id:'parking.max_area_m2',seccion:'volumetria',etiqueta:'Área máxima',valor:null,unidad:'m²',estado:'insuficiente',motivo:'Falta área_construible_max.',articulo_id:'555:389'},
  {id:'parking.adicional_area_m2',seccion:'volumetria',etiqueta:'Área adicional',valor:null,unidad:'m²',estado:'insuficiente',motivo:'Falta área_construible_max.',articulo_id:'555:389'}
]});
for(const title of ['Lote y área','Edificabilidad (ICe)','Aislamientos y retrocesos','Estacionamientos'])assert(grouped.includes(title));
assert(grouped.includes('data-figure-members="3"'));
assert.equal((grouped.match(/Misma explicación\./g)||[]).length,2); // visible note + collapsed application, once for the merged family
assert(!grouped.includes('area_construible_max'));
assert(grouped.includes('<h3 id="figure-group-lote"'));
assert(grouped.includes('<h4 class="m-label"'));
const html=fs.readFileSync(require('node:path').join(__dirname,'../index.html'),'utf8');
for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)){
  if(!/src=|type="(?:module|importmap)"/.test(match[1])) new vm.Script(match[2]);
}
console.log('Figure renderer: statuses, grouped hierarchy, deduplication, plain-language output and inline JS syntax passed.');
