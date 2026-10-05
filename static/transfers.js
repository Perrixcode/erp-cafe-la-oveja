'use strict';
let transferRequest=0,transferRows=[],transferSnapshot=null;
const clp=value=>new Intl.NumberFormat('es-CL',{style:'currency',currency:'CLP',maximumFractionDigits:0}).format(value||0);
async function loadTransfers(page=1){
  const request=++transferRequest,target=$('#transfer-results');
  if(!state.partner){target.innerHTML='<p class="empty compact">Esta información está disponible para socios.</p>';return;}
  const form=$('#transfer-filter'),query=new URLSearchParams({page});
  for(const key of ['start','end','status','cashier'])if(form.elements[key].value)query.set(key,form.elements[key].value);
  try{
    const data=await api('/api/transfers?'+query);
    if(request!==transferRequest||!state.user)return;
    $('#transfer-filter').classList.toggle('hidden',state.moduleSection!=='Transferencias del local');
    if(!data.configured){target.innerHTML=`<p class="empty compact">${escapeHTML(data.message)}</p>`;return;}
    transferSnapshot=data;
    const selectedCashier=form.elements.cashier.value;
    form.elements.cashier.innerHTML='<option value="">Todas las cajeras</option>'+data.cashiers.map(c=>`<option value="${escapeHTML(c.id)}">${escapeHTML(c.name)}</option>`).join('');
    form.elements.cashier.value=selectedCashier;
    if(state.moduleSection==='Cierres diarios'){renderClosures();return;}
    if(state.moduleSection==='Estado del bot'){renderBotHealth();return;}
    form.elements.start.value=data.start;form.elements.end.value=data.end;transferRows=data.rows;
    target.innerHTML=`<p class="hint">${data.stale?'⚠ Última lectura conservada; la actualización requiere revisión.':'Lectura actualizada'} · ${escapeHTML(new Date(data.updated_at).toLocaleString('es-CL',{timeZone:'America/Santiago'}))}</p><div class="analytics-grid"><article><strong>${data.total}</strong><span>Revisiones del período</span></article><article><strong>${data.summary.pending}</strong><span>Pendientes de revisión</span></article><article><strong>${data.summary.compatible}</strong><span>Datos visibles compatibles</span></article><article><strong>${clp(data.summary.expected_amount)}</strong><span>Monto esperado · ${data.summary.sales} ventas únicas</span></article></div><p class="hint">Un comprobante compatible no confirma un abono bancario. El seguimiento conserva el resultado original y se comparte con los cierres y alertas de Ovejita.</p>${!data.followup_available?'<p class="source-alert">Seguimiento temporalmente no disponible. Puedes consultar la última copia conservada.</p>':''}${renderTransferBreakdown(data.breakdown)}<div class="transfer-list">${data.rows.map((r,index)=>`<article class="transfer-row"><div><small>${escapeHTML(new Date(r.fecha).toLocaleString('es-CL',{dateStyle:'medium',timeStyle:'short',timeZone:'America/Santiago'}))}</small><h3>${escapeHTML(r.cajera)} · Mesa ${escapeHTML(r.mesa||'sin informar')}</h3><p>Venta ${escapeHTML(r.orden_id||'sin vincular')} · Revisión #${r.id}</p><span class="timing-label">${r.compatible?'Datos compatibles':r.pending?'Requiere revisión':'Revisada por socio'}</span></div><div class="transfer-amount"><strong>${clp(r.monto_esperado)}</strong><small>Monto esperado</small><button class="button secondary" data-transfer="${index}">Ver revisión</button></div></article>`).join('')||'<div class="empty compact">Sin revisiones en este rango.</div>'}</div><div class="actions">${page>1?`<button class="button secondary" data-transfer-page="${page-1}">Anterior</button>`:''}<span>Página ${page} de ${Math.max(1,Math.ceil(data.total/data.page_size))}</span>${page*data.page_size<data.total?`<button class="button secondary" data-transfer-page="${page+1}">Siguiente</button>`:''}</div>`;
  }catch(error){if(request===transferRequest&&state.user)target.innerHTML=`<p class="error">${escapeHTML(error.message)}</p>`;}
}
document.addEventListener('submit',event=>{if(event.target.id==='transfer-filter'){event.preventDefault();loadTransfers();}});
document.addEventListener('click',event=>{
  const page=event.target.closest('[data-transfer-page]');if(page)loadTransfers(Number(page.dataset.transferPage));
  const button=event.target.closest('[data-transfer]');if(!button||!state.partner)return;
  const row=transferRows[Number(button.dataset.transfer)];if(!row)return;
  showTransfer(row);
});

function showTransfer(row){
  modal('Revisión de transferencia',`Ovejita · #${row.id} · Mesa ${row.mesa||'sin informar'}`,`<div class="detail-grid"><div class="detail-field"><small>Cajera</small>${escapeHTML(row.cajera)}</div><div class="detail-field"><small>Monto esperado</small>${clp(row.monto_esperado)}</div><div class="detail-field"><small>Código de transacción</small>${escapeHTML(row.codigo||'No detectado')}</div><div class="detail-field"><small>Registro</small>${escapeHTML(row.registro)}</div></div><h3 class="section-label">Comprobante de transferencia</h3>${row.photo?`<figure class="transfer-preview"><img src="/api/transfers/${row.id}/photo" alt="Comprobante asociado a esta revisión"><figcaption><a class="button secondary" href="/api/transfers/${row.id}/photo?download=1" download>Descargar comprobante</a></figcaption></figure>`:'<p class="hint">Foto no archivada. El bot anterior no conservaba las imágenes históricas.</p>'}<h3 class="section-label">Venta seleccionada por la cajera</h3><p><strong>Comanda ${escapeHTML(row.orden_id||'sin identificar')}</strong> · Mesa ${escapeHTML(row.mesa||'sin informar')} · ${clp(row.monto_esperado)}</p>${row.selected_sale?`<p>Opción seleccionada: ${escapeHTML(row.selected_sale.selected_option)}</p><p class="source-comment">${escapeHTML(row.selected_sale.sale.comentario||row.selected_sale.sale.cliente||'Sin referencia adicional')}</p>${row.selected_sale.products.map(p=>`<p>${escapeHTML(p.quantity)} × ${escapeHTML(p.productName||'Producto')} · ${clp(p.amountAfterTax)}${p.cancelled?' · Anulado':''}</p>`).join('')}`:'<p class="hint">Identidad y monto conservados por Ovejita. El detalle de productos no se archivó en esta revisión.</p>'}<h3 class="section-label">Verificaciones del bot</h3>${Object.entries(row.estados).map(([k,v])=>`<p><strong>${escapeHTML(k)}</strong> · ${escapeHTML(v)}</p>`).join('')}<h3 class="section-label">Observaciones</h3>${row.alertas.map(a=>`<p>${escapeHTML(typeof a==='string'?a:JSON.stringify(a))}</p>`).join('')||'<p>Sin observaciones adicionales.</p>'}<h3 class="section-label">Seguimiento</h3>${row.history.map(h=>`<article class="history-entry"><strong>${escapeHTML(h.accion)} · ${escapeHTML(h.usuario)}</strong><p>${escapeHTML(h.motivo)}</p><time>${escapeHTML(new Date(h.fecha).toLocaleString('es-CL',{timeZone:'America/Santiago'}))}</time></article>`).join('')||'<p>Sin seguimiento registrado.</p>'}${transferFollowupForm(row)}<p class="hint">El seguimiento deja registro del socio, fecha y motivo. No cambia la evaluación original ni confirma abonos bancarios.</p>`,'<button class="button secondary" data-action="close">Cerrar</button>','TRANSFERENCIAS DEL LOCAL');
}

function renderClosures(selected){
  const rows=transferSnapshot?.closures||[],entry=rows.find(c=>c.date===selected)||rows[0],target=$('#transfer-results');
  if(!entry){target.innerHTML='<div class="empty compact">Ovejita todavía no tiene cierres archivados.</div>';return;}
  const r=entry.report;
  target.innerHTML=`<label class="field">Fecha operativa<select id="closure-date">${rows.map(c=>`<option ${c===entry?'selected':''}>${escapeHTML(c.date)}</option>`).join('')}</select></label><h2>${escapeHTML(dateLabel(entry.date))}</h2><p class="hint">Envío del cierre: ${escapeHTML(entry.send_status||'Sin envío')}</p><span class="timing-label">${r.completo?'Cierres disponibles':'Incompleto · requiere revisión'}</span>${(r.avisos||[]).map(a=>`<p class="source-alert">${escapeHTML(a)}</p>`).join('')}<div class="analytics-grid"><article><strong>${clp(r.total)}</strong><span>Ventas · ${r.ventas} ventas</span></article><article><strong>${clp(r.propinas)}</strong><span>Propinas</span></article><article><strong>${r.pendientes||0}</strong><span>Comprobantes pendientes</span></article><article><strong>${r.alertas_boleta||0}</strong><span>Alertas de boleta</span></article></div><h3>Ventas por caja</h3>${Object.entries(r.cajas||{}).map(([id,c])=>`<article class="transfer-row"><strong>${escapeHTML(id)} · ${escapeHTML(c.nombre)}</strong><span>${c.pagos} pagos · ${clp(c.total)} · Propinas ${clp(c.propinas)}</span></article>`).join('')}<h3>Medios de pago registrados</h3>${Object.entries(r.medios||{}).map(([k,v])=>`<p>${escapeHTML(k)} · ${clp(v)}</p>`).join('')}<h3>Aperturas y cierres</h3>${(r.cierres||[]).map(c=>`<article class="history-entry"><strong>Caja ${escapeHTML(c.caja)} · Turno ${escapeHTML(c.turno)} · ${c.cerrado?'Cerrada':'Abierta'}</strong><p>${escapeHTML(c.apertura)} → ${escapeHTML(c.cierre||'Pendiente')}</p></article>`).join('')}<p class="hint">${escapeHTML(r.base||'Fecha operativa informada por Toteat')}. Cajas excluidas: ${escapeHTML((r.excluidas||[]).join(', '))}. No confirma abonos bancarios.</p><details><summary>Detalle íntegro del informe conservado</summary><pre>${escapeHTML(JSON.stringify(r,null,2))}</pre></details>`;
}
function renderBotHealth(){
  const data=transferSnapshot;
  $('#transfer-results').innerHTML=`<p class="hint">Mensajes con envío incierto: ${Number(data.uncertain_messages||0)} · Última copia ${escapeHTML(data.updated_at)}${data.stale?' · Lectura atrasada':''}</p>${data.health.map(h=>`<article class="transfer-row"><div><h3>${escapeHTML(h.componente)}</h3><span class="timing-label">${h.delayed?'⚠ Actualización atrasada · ':''}${escapeHTML(h.estado)}</span><p>${escapeHTML(h.detalle)}</p><small>${escapeHTML(h.fecha)}</small></div></article>`).join('')}<h3>Alertas operativas</h3>${(data.alerts||[]).map(a=>`<article class="history-entry"><strong>${escapeHTML(a.tipo)}</strong><p>${escapeHTML(a.detalle)}</p><small>${escapeHTML(a.fecha)}</small></article>`).join('')||'<p>Sin alertas operativas registradas.</p>'}`;
}
document.addEventListener('change',event=>{if(event.target.id==='closure-date')renderClosures(event.target.value);});

function renderTransferBreakdown(groups){
  return `<details class="transfer-breakdown"><summary>Distribución de revisiones</summary><div class="form-grid">${[['Motivos por revisar',groups.reasons],['Por cajera',groups.cashiers],['Por día',groups.days]].map(([title,rows])=>`<section><h3>${title}</h3>${rows.map(([key,count])=>`<p>${escapeHTML(title==='Por día'?dateLabel(key):key)} <strong>· ${count}</strong></p>`).join('')||'<p>Sin registros.</p>'}</section>`).join('')}</div></details>`;
}
function transferFollowupForm(row){
  if(row.compatible)return '<p class="hint">Este comprobante tiene datos compatibles; no requiere seguimiento manual.</p>';
  if(!transferSnapshot?.followup_available)return '<p class="source-alert">El seguimiento está desconectado. Actualiza la lectura antes de cambiar el estado.</p>';
  const action=row.seguimiento==='revisado'?'pendiente':'revisado';
  const label=action==='revisado'?'Marcar revisada':'Reabrir como pendiente';
  return `<details class="transfer-followup"><summary>${label}</summary><form id="transfer-followup-form" data-revision="${row.id}" data-action="${action}" data-version="${row.followup_version}" data-source-ref="${escapeHTML(row.source_ref)}" data-request-id="${crypto.randomUUID()}"><label class="field">Motivo del cambio<textarea name="reason" required minlength="5" maxlength="1000" rows="3" placeholder="Describe la revisión realizada o por qué debe revisarse nuevamente."></textarea></label><p class="hint">${action==='revisado'?'Se retirará de pendientes de seguimiento.':'Volverá a pendientes de seguimiento.'} La evaluación del bot permanece intacta.</p><p id="transfer-followup-error" class="error" role="alert"></p><button class="button primary" type="submit">${label}</button></form></details>`;
}
document.addEventListener('submit',async event=>{
  if(event.target.id!=='transfer-followup-form')return;
  event.preventDefault();if(state.saving||!state.partner)return;
  const form=event.target,button=form.querySelector('button[type="submit"]'),row=transferRows.find(r=>r.id===Number(form.dataset.revision));
  if(!row)return;
  state.saving=true;button.disabled=true;
  try{
    const result=await api(`/api/transfers/${row.id}/follow-up`,'POST',{action:form.dataset.action,reason:form.elements.reason.value,version:Number(form.dataset.version),source_ref:form.dataset.sourceRef,request_id:form.dataset.requestId});
    if(!state.user)return;
    Object.assign(row,result);showTransfer(row);toast('Seguimiento guardado.');await loadTransfers(transferSnapshot.page);
  }catch(error){if(state.user)$('#transfer-followup-error').textContent=error.message;}
  finally{state.saving=false;button.disabled=false;}
});
let transferExporting=false;
document.addEventListener('click',async event=>{
  const button=event.target.closest('#transfer-export');if(!button||transferExporting||!state.partner)return;
  transferExporting=true;button.disabled=true;
  try{
    const form=$('#transfer-filter'),filters={};
    for(const key of ['start','end','status','cashier'])if(form.elements[key].value)filters[key]=form.elements[key].value;
    const response=await fetch('/api/transfers/export',{method:'POST',headers:{'Content-Type':'application/json','X-ERP-Local':'1'},body:JSON.stringify({filters,request_id:crypto.randomUUID()})});
    if(!response.ok){const error=await response.json();throw new Error(error.error||'No se pudo exportar.');}
    if(!state.user)return;
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='oveja-transferencias.csv';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('Exportación preparada y registrada.');
  }catch(error){toast(error.message);}finally{transferExporting=false;button.disabled=false;}
});
