/* Additive figure UI. Normative quotations are loaded only from the local corpus. */
(function(root){
  'use strict';
  const labels={resuelto:'Resuelto',derivado:'Derivado',insuficiente:'Datos insuficientes',requiere_concepto:'Requiere concepto',no_aplica:'No aplica',error:'Error'};
  const plural={resuelto:'resueltas',derivado:'derivadas',insuficiente:'con datos insuficientes',requiere_concepto:'requieren concepto',no_aplica:'no aplican',error:'con error'};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=v=>Number.isFinite(v)?v.toLocaleString('es-CO',{maximumFractionDigits:1}):null;
  const plain=v=>String(v??'')
    .replace(/área_construible_max(?:_m2)?/gi,'área construible usada como base')
    .replace(/huella_x_pisos/gi,'huella edificable × pisos permitidos')
    .replace(/sin_fuente_configurada/gi,'sin fuente automatizada')
    .replace(/ancho_via_m/gi,'ancho total de la vía')
    .replace(/frente_m/gi,'frente del lote')
    .replace(/altura_m/gi,'altura en metros')
    .replace(/\b([a-záéíóúñ]+(?:_[a-záéíóúñ0-9]+)+)\b/gi,match=>match.replaceAll('_',' '));
  const sourceText=value=>value==='sin_dato'?'Fuente no disponible':value;
  const humanKey=key=>({
    area_lote:'Área del lote',huella_calculada:'Huella calculada',rango_huella_m2:'Rango de huella',
    pisos:'Pisos',aislamientos_aplicados:'Aislamientos aplicados',antejardin_m:'Antejardín',
    retroceso_m:'Retroceso de fachada',retroceso_aplicado_a_huella:'Retroceso aplicado a la huella',
    polygon_used:'Polígono catastral utilizado',snap_fallback_used:'Selección aproximada utilizada'
  }[key]||plain(key).replace(/^./,c=>c.toUpperCase()));
  let selected='todos',corpusPromise;
  const badge=s=>`<span class="figure-badge status-${esc(s)}">${esc(labels[s]||labels.insuficiente)}</span>`;
  const action=f=>f.que_se_necesita
    ? `<p class="figure-action"><strong>Se necesita:</strong> ${esc(plain(f.que_se_necesita))}${f.quien_lo_resuelve?` — ${esc(plain(f.quien_lo_resuelve))}`:''}</p>`
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
    if(f.estado==='no_aplica'){
      if(/resultante|no fija|no fijad|modelado/i.test(f.motivo||''))return 'Resultante';
      if(v===0||/no exig|sin exig|no aplica|inaplicable/i.test(f.motivo||''))return 'No exigido';
      return 'No aplica';
    }
    if(num(v)!==null)return num(v);
    if(typeof v==='boolean'&&f.estado==='resuelto')return v?'Permitida con condiciones':'No permitida';
    if(Array.isArray(f.rango_m2)&&f.rango_m2.length===2&&f.rango_m2.every(Number.isFinite))return f.rango_m2.map(num).join('–');
    if(f.estado==='requiere_concepto')return 'Concepto requerido';
    if(f.estado==='insuficiente')return 'Dato pendiente';
    if(f.estado==='error')return 'Cálculo fallido';
    if(f.estado==='derivado')return 'Estimación no disponible';
    if(f.formula)return esc(plain(f.formula));
    if(f.requerido===true)return 'Requerido';
    if(f.aplica===true)return 'Aplica con condiciones';
    if(f.nivel&&!f.nivel.startsWith('no_aplica'))return esc(plain(f.nivel));
    return 'Dato no disponible';
  }
  function numericFigure(f){
    if(f.estado==='no_aplica')return false;
    return num(f.valor??f.valor_m2??f.dimension_m)!==null
      ||(Array.isArray(f.rango_m2)&&f.rango_m2.length===2&&f.rango_m2.every(Number.isFinite));
  }
  function renderEntries(value){
    if(value===null||value===undefined)return '<span>Dato no disponible</span>';
    if(Array.isArray(value))return `<ul>${value.map(item=>`<li>${typeof item==='object'?renderEntries(item):esc(plain(item))}</li>`).join('')}</ul>`;
    if(typeof value==='object')return `<dl class="figure-inputs">${Object.entries(value).map(([key,item])=>`<div><dt>${esc(humanKey(key))}</dt><dd>${typeof item==='object'?renderEntries(item):esc(typeof item==='boolean'?(item?'Sí':'No'):plain(item))}</dd></div>`).join('')}</dl>`;
    return esc(plain(value));
  }
  function valueRows(f){
    if(!f.valores_resumen)return '';
    return `<dl class="figure-values">${f.valores_resumen.map(item=>{
      const itemNumeric=numericFigure(item);
      return `<div><dt>${esc(item.etiqueta)}</dt><dd class="${itemNumeric?'':'figure-text-value'}">${display(item)}${itemNumeric&&item.unidad?` <small>${esc(item.unidad)}</small>`:''}</dd></div>`;
    }).join('')}</dl>`;
  }
  function card(f,compact=false,headingLevel=3,primary=false){
    const numeric=numericFigure(f);
    const heading=`h${Math.min(6,Math.max(2,headingLevel))}`;
    const members=f.valores_resumen?.length||1;
    const applied=f.valores_resumen
      ? f.valores_resumen.map(item=>`${esc(item.etiqueta)}: ${display(item)}`).join(' · ')
      : `${display(f)}${numeric&&f.unidad?' '+esc(f.unidad):''}`;
    if(compact) return `<div class="kpi-cell figure-card figure-compact${primary?' is-primary':''}" data-figure-id="${esc(f.id)}" data-figure-members="${members}" data-estado="${esc(f.estado)}">
      <${heading} class="kpi-label">${esc(f.etiqueta)}</${heading}>${badge(f.estado)}${f.valores_resumen?valueRows(f):`<div class="kpi-value ${numeric?'':'figure-text-value'}">${display(f)}</div>`}
      ${!f.valores_resumen&&numeric&&f.unidad?`<div class="m-unit">${esc(f.unidad)}</div>`:''}
      <p class="m-note">${esc(plain(f.motivo||'Estado informado por el motor de cálculo.'))}</p>
      ${action(f)}
      <details class="figure-article" data-article-id="${esc(f.articulo_id||'')}"><summary>Ver el texto del artículo ▾</summary><div class="article-text">${f.articulo_id?'Cargando corpus local…':'Transcripción no disponible; consulte el instrumento específico.'}</div>
      <p><strong>Aplicado a este predio:</strong> ${applied}. ${esc(plain(f.motivo))}</p><p>${esc(plain(f.advertencia||''))}</p><p>${esc(plain(f.que_se_necesita||''))}</p><p>${esc(sourceText(f.fuente_dato))} · ${esc(f.fecha_consulta||'Fecha no disponible')}</p></details></div>`;
    return `<article class="${compact?'kpi-cell':'metric-card'} figure-card${primary?' is-primary':''}" data-figure-id="${esc(f.id)}" data-figure-members="${members}" data-estado="${esc(f.estado)}">
      <${heading} class="${compact?'kpi-label':'m-label'}">${esc(f.etiqueta)}</${heading}>${badge(f.estado)}
      ${f.valores_resumen?valueRows(f):`<div class="${compact?'kpi-value':'m-value'} ${numeric?'':'figure-text-value'}">${display(f)}</div>`}
      ${!f.valores_resumen&&numeric&&f.unidad?`<div class="m-unit">${esc(f.unidad)}</div>`:''}
      <p class="m-note">${esc(plain(f.motivo||'Estado informado por el motor de cálculo.'))}</p>
      ${action(f)}
      ${f.condicion?`<p class="m-note"><strong>Condición:</strong> ${esc(plain(f.condicion))}</p>`:''}
      ${f.advertencia?`<p class="derived-warning">${esc(plain(f.advertencia))}</p>`:''}
      ${f.metodo?`<p class="m-note">Método: ${esc(plain(f.metodo))} · confianza ${esc(plain(f.confianza))}</p>`:''}
      ${f.supuestos?.length?`<details><summary>Supuestos y entradas</summary><ul>${f.supuestos.map(x=>`<li>${esc(plain(x))}</li>`).join('')}</ul>${renderEntries(f.entradas??{})}</details>`:''}
      <p class="figure-source">${esc(f.fuente_verificada||f.articulo||f.fuente||f.articulo_id||'Fuente del dato')} · ${esc(sourceText(f.fuente_dato||'Fuente pendiente de individualizar'))} · consulta ${esc(f.fecha_consulta||'fecha no disponible')}</p>
      <details class="figure-article" data-article-id="${esc(f.articulo_id||'')}"><summary>Ver el texto del artículo ▾</summary>
        <div class="article-text">${f.articulo_id?'Abra para consultar el corpus local.':'No hay una transcripción normativa cotejada para esta figura. Los datos catastrales y las estimaciones no son cifras literales del decreto; las referencias pendientes deben verificarse en la fuente.'}</div>
        <p><strong>Aplicado a este predio:</strong> ${esc(f.etiqueta)}: ${applied}. ${esc(plain(f.motivo))}</p>
      </details></article>`;
  }
  function sameExplanation(a,b){
    return a.estado===b.estado&&plain(a.motivo)===plain(b.motivo)&&
      (a.articulo_id||'')===(b.articulo_id||'')&&(a.fuente_dato||'')===(b.fuente_dato||'');
  }
  function mergedFigure(figures,label,id){
    const first=figures[0];
    return Object.assign({},first,{id,etiqueta:label,valores_resumen:figures.map(item=>Object.assign({},item))});
  }
  function mergeVolumetricFigures(figures){
    const remaining=[...figures],merged=[];
    function take(ids,label,id){
      const found=ids.map(itemId=>remaining.find(item=>item.id===itemId)).filter(Boolean);
      if(found.length<2||!found.every(item=>sameExplanation(found[0],item)))return;
      found.forEach(item=>remaining.splice(remaining.indexOf(item),1));
      merged.push(mergedFigure(found,label,id));
    }
    take(['parking.min_pct','parking.max_pct','parking.adicional_pct'],'Régimen de estacionamientos','parking.porcentajes');
    take(['parking.min_area_m2','parking.max_area_m2','parking.adicional_area_m2'],'Áreas de estacionamiento','parking.areas');
    take(['metrics.area_construible_max_m2','metrics.planta_maxima_m2'],'Área construible y huella normativas','metrics.area_y_huella_normativas');
    const metrics=remaining.filter(item=>item.id?.startsWith('metrics.'));
    const seen=new Set();
    metrics.forEach(item=>{
      if(seen.has(item.id))return;
      const family=metrics.filter(other=>!seen.has(other.id)&&sameExplanation(item,other));
      if(family.length<2)return;
      family.forEach(other=>{seen.add(other.id);remaining.splice(remaining.indexOf(other),1);});
      merged.push(mergedFigure(family,'Valores volumétricos relacionados',`group.${family.map(other=>other.id.split('.').pop()).join('.')}`));
    });
    return remaining.concat(merged);
  }
  const groupDefinitions=[
    {id:'lote',title:'Lote y área',match:f=>f.id==='lote.area_m2'||f.id==='anu',priority:['lote.area_m2','anu']},
    {id:'edificabilidad',title:'Edificabilidad (ICe)',match:f=>!/^parking\./.test(f.id)&&!/antejardin|aislamiento|retroceso/.test(f.id)&&f.id!=='lote.area_m2'&&f.id!=='anu',priority:['metrics.area_construible_max_m2','metrics.area_y_huella_normativas','metrics.area_construible_estimada','edificabilidad.indice_construccion','metrics.altura_base_pisos']},
    {id:'aislamientos',title:'Aislamientos y retrocesos',match:f=>/antejardin|aislamiento|retroceso/.test(f.id),priority:['antejardin','metrics.aislamiento_posterior_m','metrics.retroceso_fachada_A_m']},
    {id:'estacionamientos',title:'Estacionamientos',match:f=>/^parking\./.test(f.id),priority:['parking.porcentajes','parking.min_pct','parking.areas','parking.min_area_m2']}
  ];
  function groupedVolumetry(d){
    const figures=mergeVolumetricFigures((d.figuras||[]).filter(f=>f.seccion==='volumetria'||!f.seccion));
    return `<div class="figure-groups">${groupDefinitions.map(group=>{
      const items=figures.filter(group.match);
      if(!items.length)return '';
      const rank=item=>{
        const ids=[item.id,...(item.valores_resumen||[]).map(value=>value.id)];
        const positions=ids.map(id=>group.priority.indexOf(id)).filter(index=>index>=0);
        const hasNumber=item.valores_resumen?.some(numericFigure)||numericFigure(item);
        return (hasNumber?0:100)+(positions.length?Math.min(...positions):99);
      };
      items.sort((a,b)=>rank(a)-rank(b));
      return `<section class="figure-group" aria-labelledby="figure-group-${group.id}"><h3 id="figure-group-${group.id}" class="figure-group-title">${group.title}</h3><div class="figure-group-grid">${items.map((item,index)=>card(item,false,4,index===0)).join('')}</div></section>`;
    }).join('')}</div>`;
  }
  function summary(d){
    const fallbackCounts=Object.fromEntries(Object.keys(labels).map(k=>[k,0]));
    const uniqueFigures=[...new Map((d.figuras||[]).map(f=>[f.id,f])).values()];
    uniqueFigures.forEach(f=>{if(f.estado in fallbackCounts)fallbackCounts[f.estado]++;});
    const supplied=d.resumen_estados||{};
    const counts=Object.fromEntries(Object.keys(labels).map(k=>[
      k,Number.isFinite(Number(supplied[k]))?Number(supplied[k]):fallbackCounts[k]
    ]));
    const total=Number.isFinite(Number(supplied.total))
      ? Number(supplied.total)
      : Object.values(counts).reduce((sum,value)=>sum+value,0);
    const statusOrder=['resuelto','derivado','insuficiente','requiere_concepto','no_aplica','error'];
    const countLabel=k=>counts[k]===1?({resuelto:'resuelta',derivado:'derivada',insuficiente:'con datos insuficientes',requiere_concepto:'requiere concepto',no_aplica:'no aplica',error:'con error'}[k]||plural[k]):plural[k];
    return `<div class="figure-summary" aria-label="Resumen y filtros de todas las figuras"><div class="figure-summary-line"><button type="button" data-status-filter="todos" aria-pressed="true">Todos · ${total}</button>${statusOrder.filter(k=>counts[k]).map(k=>`<span aria-hidden="true">·</span><button type="button" data-status-filter="${k}" aria-pressed="false">${counts[k]} ${countLabel(k)}</button>`).join('')}</div><p>Estado de las ${total} figuras del informe completo. Seleccione un estado para filtrar; pulse de nuevo el filtro activo o «Todos» para restablecer.</p></div><p id="figure-filter-feedback" class="figure-filter-feedback" role="status" hidden></p>`;
  }
  function applyFilter(){
    const results=document.getElementById('results');if(!results)return;
    results.dataset.figureFilter=selected;
    results.querySelectorAll('[data-estado]').forEach(el=>{el.hidden=selected!=='todos'&&el.dataset.estado!==selected;});
    results.querySelectorAll('.figure-group').forEach(group=>{
      group.hidden=selected!=='todos'&&![...group.querySelectorAll('[data-estado]')].some(card=>!card.hidden);
    });
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
    if(feedback){feedback.hidden=selected==='todos';feedback.textContent=`Filtro activo: ${labels[selected]||''}. Pulse de nuevo este filtro o «Todos» para volver al informe completo.`;}
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
    results.querySelectorAll('[data-status-filter]').forEach(b=>b.addEventListener('click',()=>{
      const requested=b.dataset.statusFilter;
      selected=requested!=='todos'&&selected===requested?'todos':requested;
      applyFilter();
    }));
    results.querySelectorAll('.figure-article').forEach(el=>el.addEventListener('toggle',()=>loadArticle(el)));
    applyFilter();
  }
  root.AinmoFigures={card,summary,badge,display,metricFigures,init,applyFilter,grid:(d,section='volumetria')=>section==='volumetria'?groupedVolumetry(d):`<div class="metrics-grid">${(d.figuras||[]).filter(f=>f.seccion===section).map(f=>card(f,false,3)).join('')}</div>`};
  if(typeof module!=='undefined')module.exports=root.AinmoFigures;
})(typeof window==='undefined'?globalThis:window);
