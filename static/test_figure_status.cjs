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
const html=fs.readFileSync(require('node:path').join(__dirname,'../index.html'),'utf8');
for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)){
  if(!/src=|type="(?:module|importmap)"/.test(match[1])) new vm.Script(match[2]);
}
console.log('Figure renderer: six statuses, zero, ranges, escaping, full-report counts and inline JS syntax passed.');
