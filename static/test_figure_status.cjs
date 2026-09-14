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
assert(summary.includes('1 de 2 variables resueltas'));
const html=fs.readFileSync(require('node:path').join(__dirname,'../index.html'),'utf8');
for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)){
  if(!/src=|type="(?:module|importmap)"/.test(match[1])) new vm.Script(match[2]);
}
console.log('Figure renderer: six statuses, zero, ranges, escaping, counts and inline JS syntax passed.');
