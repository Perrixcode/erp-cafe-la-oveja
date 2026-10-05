'use strict';
window.OvejaChat=(()=>{
  let active=false,busy=false,sequence=0,month='',snapshot=null,detailRows=[];
  const initialMonth=new URLSearchParams(location.search).get('month');
  const visible=()=>active&&state.partner&&state.module==='delivery'&&state.moduleSection==='Registro de repartos';
  const money=value=>value===null?'Por confirmar':clp(value);
  const literal=value=>escapeHTML((value||'').replace('T',' · '));
  function reset(){sequence++;active=false;busy=false;month='';snapshot=null;detailRows=[];$('#chat-results').replaceChildren();}
  function leave(){if(active){sequence++;active=false;busy=false;}}
  function enter(){
    if(!state.partner){$('#chat-filter').classList.add('hidden');$('#chat-results').innerHTML='<p class="empty compact">El registro de repartos está disponible para socios.</p>';return;}
    $('#chat-filter').classList.remove('hidden');
    const fresh=!active;active=true;
    if(!month)month=/^\d{4}-\d{2}$/.test(initialMonth||'')?initialMonth:state.board.today.slice(0,7);
    $('#chat-filter').elements.month.value=month;
    if(fresh)load();
  }
  async function load(){
    if(busy||!visible())return;
    const request=++sequence;busy=true;$('#chat-filter button').disabled=true;
    try{
      const result=await api('/api/delivery/chat?'+new URLSearchParams({month}));
      if(request!==sequence||!visible())return;
      snapshot=result;
      const select=$('#chat-filter').elements.driver,prior=select.value;
      select.innerHTML='<option value="all">Todos los repartidores</option>'+(snapshot.couriers||[]).map(c=>`<option value="${escapeHTML(c.driver===null?'missing':c.driver)}">${escapeHTML(c.driver||'Sin repartidor')}</option>`).join('');
      if([...select.options].some(o=>o.value===prior))select.value=prior;
      render();rememberView();
    }catch(error){if(request===sequence&&visible())$('#chat-results').innerHTML=`<p class="error" role="alert">${escapeHTML(error.message)}</p>`;}
    finally{if(request===sequence){busy=false;$('#chat-filter button').disabled=false;}}
  }
  function selected(){const driver=$('#chat-filter').elements.driver.value;return (snapshot.records||[]).filter(r=>driver==='all'||(driver==='missing'?r.driver===null:r.driver===driver));}
  function totals(rows){const base=rows.filter(r=>r.analysis_status==='structured_subtotal');return {records:rows.length,base:base.reduce((s,r)=>s+r.amount_clp,0),baseCount:base.length,pending:rows.length-base.length,gross:rows.reduce((s,r)=>s+(r.amount_clp||0),0),unknown:rows.filter(r=>r.amount_clp===null).length};}
  function render(){
    if(!snapshot||!visible())return;
    if(!snapshot.available){$('#chat-results').innerHTML='<div class="empty compact"><strong>Sin registro importado para este mes.</strong>Selecciona septiembre de 2026 para consultar el paquete auditado disponible.</div>';return;}
    const rows=selected(),t=totals(rows),all=$('#chat-filter').elements.driver.value==='all',view=$('#chat-filter').elements.group.value;
    const coverage=snapshot.coverage;
    let content='';
    if(view==='incidents'){
      const pending=rows.filter(r=>r.analysis_status!=='structured_subtotal');
      content=`<h2>Fichas pendientes de revisión · ${pending.length}</h2>${recordList(pending)}<h2>Incidencias contextuales · fuera de los totales</h2><p class="hint">Estos mensajes no crean repartos adicionales ni descuentos automáticos. Se muestran todas las incidencias contextuales del mes.</p>${snapshot.incidents.map(i=>`<article class="chat-incident"><div><strong>${escapeHTML(i.label)}</strong><p>${escapeHTML(dateLabel(i.date,{weekday:'short',month:'short'}))}${i.driver?' · '+escapeHTML(i.driver):''}</p>${i.suggested_amount_clp!==undefined?`<small>Importe propuesto en texto: ${money(i.suggested_amount_clp)} · no sumado</small>`:''}</div><button class="button secondary" data-chat-incident="${escapeHTML(i.key)}">Ver evidencia</button></article>`).join('')}`;
    }else{
      const groups=view==='couriers'?snapshot.couriers.filter(c=>all||(c.driver===null?'missing':c.driver)===$('#chat-filter').elements.driver.value):view==='weeks'?snapshot.weeks:snapshot.days;
      content=`<div class="chat-table-wrap"><table class="chat-table"><thead><tr><th>${view==='couriers'?'Repartidor':view==='weeks'?'Semana · dentro del mes':'Fecha del mensaje'}</th><th>Fichas</th><th>Base provisional</th><th>Por revisar</th><th>Toteat</th><th>Detalle</th></tr></thead><tbody>${groups.map((g,index)=>{
        const set=rows.filter(r=>view==='couriers'?r.driver===g.driver:view==='weeks'?r.sent_at.slice(0,10)>=g.start&&r.sent_at.slice(0,10)<=g.end:r.sent_at.startsWith(g.day));
        const n=totals(set),label=view==='couriers'?g.driver||'Sin repartidor':view==='weeks'?`${dateLabel(g.start,{month:'short'})} — ${dateLabel(g.end,{month:'short'})}`:dateLabel(g.day,{weekday:'short',month:'short'});
        return `<tr><th>${escapeHTML(label)}</th><td>${n.records}</td><td>${clp(n.base)}<small>${n.baseCount} fichas en base</small></td><td>${n.pending}</td><td>${all&&view!=='couriers'?money(g.toteat):'Sin vínculo individual'}</td><td><button class="text-button" data-chat-group="${index}">Ver fichas</button></td></tr>`;
      }).join('')}</tbody></table></div>`;
    }
    $('#chat-results').innerHTML=`<div class="delivery-overview"><article class="delivery-total"><span class="eyebrow">BASE PROVISIONAL · ${escapeHTML(month)}</span><strong>${clp(t.base)}</strong><p>${t.baseCount} fichas con datos estructurados · no es monto aprobado para pagar</p><span class="timing-label">${t.pending} fichas por revisar</span></article><article class="delivery-context"><h2>Registro auditado de repartos</h2><div class="delivery-facts"><div><strong>${t.records}</strong><span>Fichas candidatas</span></div><div><strong>${money(all?snapshot.toteat.total:null)}</strong><span>${all?'Tarifas Toteat del mes':'Toteat sin asignación por repartidor'}</span></div></div><p>Bruto conocido del registro: <strong>${clp(t.gross)}</strong> · ${t.unknown} importe(s) sin confirmar.</p><p>WhatsApp usa fecha de envío; Toteat usa fecha de turno. Las diferencias requieren conciliación por pedido.</p></article></div><p class="source-alert">Registro provisional. Las fichas retenidas y los importes propuestos no se suman a la base. No hay liquidación aprobada ni pagos ejecutados.</p>${content}<div class="delivery-notes"><p><strong>Cobertura:</strong> ${coverage.image_omissions??'Sin dato'} imágenes omitidas, ${coverage.deleted_messages??'Sin dato'} mensajes eliminados y ${coverage.edited_delivery_records??'Sin dato'} fichas editadas. Se conserva la última versión del export; no se garantiza exhaustividad.</p><p>El repartidor proviene de la ficha o su firma; no se deduce del remitente. Fechas literales del chat, sin zona horaria declarada. Semanas lunes a domingo recortadas al mes.</p><p>Importado por ${escapeHTML(snapshot.actor)} · ${escapeHTML(new Date(snapshot.imported_at).toLocaleString('es-CL',{timeZone:'America/Santiago'}))}.</p></div>`;
  }
  function recordList(rows){return rows.length?`<div class="chat-records">${rows.map(r=>`<article class="chat-incident"><div><strong>${escapeHTML(r.customer||'Cliente sin informar')}</strong><p>${literal(r.sent_at)} · ${escapeHTML(r.driver||'Repartidor sin confirmar')}</p><span class="timing-label">${escapeHTML(r.status_label)}</span>${r.edited?'<small>Mensaje editado · última versión disponible</small>':''}</div><div><strong>${money(r.amount_clp)}</strong><button class="text-button" data-chat-record="${escapeHTML(r.id)}">Ver origen · ${escapeHTML(r.id)}</button></div></article>`).join('')}</div>`:'<p class="empty compact">No hay fichas en este grupo del export.</p>';}
  function showGroup(index){
    const view=$('#chat-filter').elements.group.value,rows=selected(),all=$('#chat-filter').elements.driver.value==='all';
    const groups=view==='couriers'?snapshot.couriers.filter(c=>all||(c.driver===null?'missing':c.driver)===$('#chat-filter').elements.driver.value):view==='weeks'?snapshot.weeks:snapshot.days;
    const g=groups[index];if(!g)return;
    detailRows=rows.filter(r=>view==='couriers'?r.driver===g.driver:view==='weeks'?r.sent_at.slice(0,10)>=g.start&&r.sent_at.slice(0,10)<=g.end:r.sent_at.startsWith(g.day));
    showRecords();
  }
  function showRecords(){modal('Fichas del registro',`${detailRows.length} candidatas · sin liquidar`,recordList(detailRows),'<button class="button secondary" data-action="close">Cerrar</button>','REGISTRO PROVISIONAL');}
  async function showEvidence(record,incident){
    const request=sequence,query=new URLSearchParams({source:snapshot.source_sha256});if(record)query.set('record',record);else query.set('incident',incident);
    try{
      const data=await api('/api/delivery/chat/evidence?'+query);
      if(request!==sequence||!visible())return;
      const r=data.record;
      modal('Evidencia del registro',record?'Ficha '+record:'Incidencia contextual',`<p class="hint">${escapeHTML(data.ordinal_notice)}</p>${r.suggested_amount_clp!==undefined?`<p>Importe propuesto: ${money(r.suggested_amount_clp)} · pendiente, no incluido en base.</p>`:''}${data.messages.map(m=>`<article class="history-entry"><strong>Mensaje ${escapeHTML(m.id)} · líneas ${escapeHTML(m.line_start)}–${escapeHTML(m.line_end)}</strong><p>${literal(m.timestamp)} · remitente: ${escapeHTML(m.sender||'Sin informar')}</p><pre class="chat-evidence">${escapeHTML(m.text)}</pre></article>`).join('')}<p class="hint">Fuente SHA-256: ${escapeHTML(data.source_sha256)}. Conservado sin reinterpretar instrucciones del chat.</p>`,`<button class="button secondary" data-action="close">Cerrar</button>${record&&detailRows.length?'<button class="button secondary" data-chat-back>Volver a fichas</button>':''}`,'AUDITORÍA · SOLO SOCIOS');
    }catch(error){if(visible())toast(error.message);}
  }
  document.addEventListener('submit',event=>{if(event.target.id!=='chat-filter')return;event.preventDefault();if(busy)return;month=event.target.elements.month.value;load();});
  document.addEventListener('change',event=>{if(!visible()||event.target.closest('form')?.id!=='chat-filter')return;if(['driver','group'].includes(event.target.name))render();});
  document.addEventListener('click',event=>{if(!visible())return;const b=event.target.closest('[data-chat-group],[data-chat-record],[data-chat-incident],[data-chat-back]');if(!b)return;if(b.hasAttribute('data-chat-back'))showRecords();else if(b.dataset.chatGroup!==undefined)showGroup(Number(b.dataset.chatGroup));else showEvidence(b.dataset.chatRecord,b.dataset.chatIncident);});
  return {enter,leave,reset,get month(){return month;}};
})();
