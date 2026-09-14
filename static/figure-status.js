/* Additive figure UI. Normative quotations are loaded only from the local corpus. */
(function(root){
  'use strict';
  const labels={resuelto:'Resuelto',derivado:'Derivado',insuficiente:'Datos insuficientes',requiere_concepto:'Requiere concepto',no_aplica:'No aplica',error:'Error'};
  const plural={resuelto:'resueltas',derivado:'derivadas',insuficiente:'con datos insuficientes',requiere_concepto:'requieren concepto',no_aplica:'no aplican',error:'con error'};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=v=>Number.isFinite(v)?v.toLocaleString('es-CO',{maximumFractionDigits:1}):null;
  let selected='todos',corpusPromise;
  const badge=s=>`<span class="figure-badge status-${esc(s)}">${esc(labels[s]||labels.insuficiente)}</span>`;
  const action=f=>f.que_se_necesita
    ? `<p class="figure-action"><strong>Se necesita:</strong> ${esc(f.que_se_necesita)}${f.quien_lo_resuelve?` — ${esc(f.quien_lo_resuelve)}`:''}</p>`
    : '';
  function metricFigures(d){
    const figuresById=new Map((d.figuras||[]).map(f=>[f.id,f]));
    const entries=Object.entries(d.metrics||{});
    if(!entries.length)return [...new Map((d.figuras||[]).map(f=>[f.id,f])).values()];
    return entries.map(([key,metric])=>{
      const id=`metrics.${key}`;
      const figure=figuresById.get(id)||{};
      return Object.assign({id,etiqueta:key.replaceAll('_',' '),seccion:'volumetria'},metric,figure);
    });
  }
  function display(f){
    const v=f.valor??f.valor_m2??f.dimension_m;
    if(num(v)!==null)return num(v);
    if(typeof v==='boolean'&&f.estado==='resuelto')return v?'Permitida con condiciones':'No permitida';
    if(Array.isArray(f.rango_m2)&&f.rango_m2.length===2&&f.rango_m2.every(Number.isFinite))return f.rango_m2.map(num).join('–');
    if(f.formula)return esc(f.formula);
    if(f.requerido===true)return 'Requerido';
    if(f.aplica===true)return 'Aplica con condiciones';
    if(f.nivel&&!f.nivel.startsWith('no_aplica'))return esc(f.nivel.replaceAll('_',' '));
    return f.estado==='no_aplica'&&/resultante|no fija|no fijad|modelado/i.test(f.motivo||'')?'Resultante':esc(labels[f.estado]||labels.insuficiente);
  }
  function card(f,compact=false){
    const numeric=num(f.valor??f.valor_m2??f.dimension_m)!==null||Array.isArray(f.rango_m2);
    if(compact) return `<div class="kpi-cell figure-card figure-compact" data-figure-id="${esc(f.id)}" data-estado="${esc(f.estado)}">
      <div class="kpi-label">${esc(f.etiqueta)}</div>${badge(f.estado)}<div class="kpi-value ${numeric?'':'figure-text-value'}">${display(f)}</div>
      ${numeric&&f.unidad?`<div class="m-unit">${esc(f.unidad)}</div>`:''}
      <p class="m-note">${esc(f.motivo||'Estado informado por el motor de cálculo.')}</p>
      ${action(f)}
      <details class="figure-article" data-article-id="${esc(f.articulo_id||'')}"><summary>Ver el texto del artículo ▾</summary><div class="article-text">${f.articulo_id?'Cargando corpus local…':'Transcripción no disponible; consulte el instrumento específico.'}</div>
      <p><strong>Aplicado a este predio:</strong> ${display(f)}${numeric?' '+esc(f.unidad):''}. ${esc(f.motivo)}</p><p>${esc(f.advertencia||'')}</p><p>${esc(f.que_se_necesita||'')}</p><p>${esc(f.fuente_dato)} · ${esc(f.fecha_consulta||'Fecha no disponible')}</p></details></div>`;
    return `<div class="${compact?'kpi-cell':'metric-card'} figure-card" data-figure-id="${esc(f.id)}" data-estado="${esc(f.estado)}">
      <div class="${compact?'kpi-label':'m-label'}">${esc(f.etiqueta)}</div>${badge(f.estado)}
      <div class="${compact?'kpi-value':'m-value'} ${numeric?'':'figure-text-value'}">${display(f)}</div>
      ${numeric&&f.unidad?`<div class="m-unit">${esc(f.unidad)}</div>`:''}
      <p class="m-note">${esc(f.motivo||'Estado informado por el motor de cálculo.')}</p>
      ${action(f)}
      ${f.condicion?`<p class="m-note"><strong>Condición:</strong> ${esc(f.condicion)}</p>`:''}
      ${f.advertencia?`<p class="derived-warning">${esc(f.advertencia)}</p>`:''}
      ${f.metodo?`<p class="m-note">Método: ${esc(f.metodo)} · confianza ${esc(f.confianza)}</p>`:''}
      ${f.supuestos?.length?`<details><summary>Supuestos y entradas</summary><ul>${f.supuestos.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><pre>${esc(JSON.stringify(f.entradas??{},(k,v)=>v===null?'Dato no disponible':v,2))}</pre></details>`:''}
      <p class="figure-source">${esc(f.fuente_verificada||f.articulo||f.fuente||f.articulo_id||'Fuente del dato')} · ${esc(f.fuente_dato||'Fuente pendiente de individualizar')} · consulta ${esc(f.fecha_consulta||'fecha no disponible')}</p>
      <details class="figure-article" data-article-id="${esc(f.articulo_id||'')}"><summary>Ver el texto del artículo ▾</summary>
        <div class="article-text">${f.articulo_id?'Abra para consultar el corpus local.':'No hay una transcripción normativa cotejada para esta figura. Los datos catastrales y las estimaciones no son cifras literales del decreto; las referencias pendientes deben verificarse en la fuente.'}</div>
        <p><strong>Aplicado a este predio:</strong> ${esc(f.etiqueta)}: ${display(f)}${numeric&&f.unidad?' '+esc(f.unidad):''}. ${esc(f.motivo)}</p>
      </details></div>`;
  }
  function summary(d){
    const counts=Object.fromEntries(Object.keys(labels).map(k=>[k,0]));
    const metrics=metricFigures(d);
    metrics.forEach(f=>{if(f.estado in counts)counts[f.estado]++;});
    const statusOrder=['resuelto','derivado','insuficiente','requiere_concepto','no_aplica','error'];
    const countLabel=k=>counts[k]===1?({derivado:'derivada',insuficiente:'con datos insuficientes',requiere_concepto:'requiere concepto',no_aplica:'no aplica',error:'con error'}[k]||plural[k]):plural[k];
    return `<div class="figure-summary" aria-label="Resumen y filtros de las métricas"><div class="figure-summary-line"><button type="button" data-status-filter="todos" aria-pressed="true">${counts.resuelto} de ${metrics.length} ${metrics.length===1?'resuelta':'resueltas'}</button>${statusOrder.filter(k=>k!=='resuelto'&&counts[k]).map(k=>`<span aria-hidden="true">·</span><button type="button" data-status-filter="${k}" aria-pressed="false">${counts[k]} ${countLabel(k)}</button>`).join('')}</div><p>Estado de las ${metrics.length} métricas del cálculo. Seleccione un estado para filtrar el informe; “resuelto” no significa licencia.</p></div><p id="figure-filter-feedback" class="figure-filter-feedback" role="status" hidden></p>`;
  }
  function applyFilter(){
    const results=document.getElementById('results');if(!results)return;
    results.dataset.figureFilter=selected;
    results.querySelectorAll('[data-estado]').forEach(el=>{el.hidden=selected!=='todos'&&el.dataset.estado!==selected;});
    results.querySelectorAll('[data-status-filter]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.statusFilter===selected)));
    results.querySelectorAll('.result-anchor-section').forEach(section=>{
      section.querySelectorAll(':scope > :not([data-estado]):not(.result-section-label):not(.figure-section-empty)').forEach(el=>{
        const children=[...el.querySelectorAll('[data-estado]')];
        el.classList.toggle('figure-context-hidden',selected!=='todos'&&!children.some(child=>!child.hidden));
      });
      let msg=section.querySelector(':scope > .figure-section-empty');
      const empty=selected!=='todos'&&![...section.querySelectorAll('[data-estado]')].some(el=>!el.hidden);
      if(!msg){msg=document.createElement('p');msg.className='figure-section-empty';section.append(msg);}
      msg.textContent='Esta sección no contiene variables del estado seleccionado.';msg.hidden=!empty;
    });
    const feedback=document.getElementById('figure-filter-feedback');
    if(feedback){feedback.hidden=selected==='todos';feedback.textContent=`Filtro activo: ${labels[selected]||''}. Pulse «Todas» para volver al informe completo.`;}
  }
  async function loadArticle(details){
    const id=details.dataset.articleId;if(!id||!details.open)return;
    try{
      corpusPromise ||= fetch('/static/article-corpus.json?v=20260914').then(r=>{if(!r.ok)throw Error('corpus');return r.json();}).catch(e=>{corpusPromise=null;throw e;});
      const a=(await corpusPromise).articulos[id];
      details.querySelector('.article-text').innerHTML=a?`<strong>${esc(a.titulo)}</strong><p class="m-note">${esc(a.alcance)} · ${esc(a.version)} · cotejo ${esc(a.fecha_cotejo)}</p><blockquote>${esc(a.texto)}</blockquote><a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">Abrir fuente oficial ↗</a>`:'Transcripción pendiente de cotejo; consulte la fuente indicada. No se presenta un texto inventado.';
    }catch(_){details.querySelector('.article-text').textContent='No se pudo cargar el corpus local. Cierre y vuelva a abrir para reintentar.';}
  }
  function init(){
    selected='todos';const results=document.getElementById('results');
    results.querySelectorAll('[data-status-filter]').forEach(b=>b.addEventListener('click',()=>{selected=b.dataset.statusFilter;applyFilter();}));
    results.querySelectorAll('.figure-article').forEach(el=>el.addEventListener('toggle',()=>loadArticle(el)));
    applyFilter();
  }
  root.AinmoFigures={card,summary,badge,display,metricFigures,init,applyFilter,grid:(d,section='volumetria')=>`<div class="metrics-grid">${(d.figuras||[]).filter(f=>f.seccion===section||(!f.seccion&&section==='volumetria')).map(f=>card(f)).join('')}</div>`};
  if(typeof module!=='undefined')module.exports=root.AinmoFigures;
})(typeof window==='undefined'?globalThis:window);
