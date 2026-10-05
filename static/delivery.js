'use strict';
window.OvejaFees=(()=>{
  let active=false,busy=false,sequence=0,month='',snapshot=null,lastLoad=0,lastError='',opened=new Set();
  const initialMonth=new URLSearchParams(location.search).get('month');
  const amount=value=>value===null?'Por consultar':clp(value);
  const localStamp=value=>value?new Intl.DateTimeFormat('es-CL',{dateStyle:'medium',timeStyle:'short',timeZone:'America/Santiago'}).format(new Date(value)):'Todavía sin lectura';
  const visible=()=>active&&state.partner&&state.module==='delivery'&&['Montos diarios','Tarifas de reparto'].includes(state.moduleSection);
  function reset(){sequence++;active=false;busy=false;snapshot=null;month='';lastLoad=0;lastError='';opened.clear();}
  function leave(){if(active){sequence++;active=false;busy=false;}}
  function enter(){
    if(!state.partner){$('#delivery-filter').classList.add('hidden');$('#delivery-results').innerHTML='<p class="empty compact">Los importes de reparto están disponibles para socios.</p>';return;}
    $('#delivery-filter').classList.remove('hidden');
    const fresh=!active;active=true;
    if(!month)month=/^\d{4}-\d{2}$/.test(initialMonth||'')?initialMonth:state.board.today.slice(0,7);
    $('#delivery-filter').elements.month.value=month;$('#delivery-filter').elements.month.max=state.board.today.slice(0,7);
    if(fresh)load(true);else if(Date.now()-lastLoad>10000)load(false);
  }
  async function load(refresh=false){
    if(busy||!visible())return;
    const request=++sequence;busy=true;
    const button=$('#delivery-filter button');button.disabled=true;
    $('#delivery-sync-note').textContent=refresh?'Solicitando actualización…':'Consultando última lectura…';
    if(!snapshot)$('#delivery-results').innerHTML='<div class="empty compact">Preparando el resumen de reparto…</div>';
    try{
      if(refresh)await api('/api/delivery/refresh','POST',{});
      const data=await api('/api/delivery/fees?'+new URLSearchParams({month}));
      if(request!==sequence||!visible())return;
      snapshot=data;lastError='';lastLoad=Date.now();render();
      $('#delivery-sync-note').textContent=data.progress.pending?`Actualización en curso · ${data.progress.pending} fechas pendientes`:'Última consulta completada';
    }catch(error){
      if(request!==sequence||!visible())return;
      lastError=error.message;
      $('#delivery-sync-note').textContent='No se pudo actualizar. Se conserva la lectura anterior.';
      if(snapshot)render();else $('#delivery-results').innerHTML=`<p class="error" role="alert">${escapeHTML(lastError)}</p>`;
    }finally{if(request===sequence){busy=false;button.disabled=false;}}
  }
  function dayStatus(day){
    if(day.state==='error')return 'Revisar consulta · lectura anterior conservada';
    if(day.fee===null)return day.state==='pending'?'Pendiente de consulta':'Sin consultar';
    if(day.state==='pending')return 'Actualizando · lectura anterior';
    return day.records?'Consultado':'Consultado · sin registros';
  }
  function render(){
    if(!snapshot||!visible())return;
    const data=snapshot,t=data.totals,complete=t.known_days===t.total_days;
    const monthTitle=new Intl.DateTimeFormat('es-CL',{month:'long',year:'numeric',timeZone:'America/Santiago'}).format(new Date(month+'-15T12:00:00-03:00'));
    const reader=data.reader||{};
    const readerWarning=reader.http_status===429?'Toteat pidió una pausa. La actualización se reanudará automáticamente.':['not_configured','access_required','review_required'].includes(reader.state)?'La lectura automática requiere revisión de su configuración. Se conserva el histórico disponible.':'';
    $('#delivery-results').innerHTML=`<div class="delivery-overview"><article class="delivery-total"><span class="eyebrow">TARIFAS DE REPARTO · ${escapeHTML(monthTitle)}</span><strong>${t.known_days?clp(t.fee):'Por consultar'}</strong><p>${complete?'Acumulado del mes consultado':'Acumulado parcial · faltan fechas por consultar'}</p><span class="timing-label">${t.known_days} de ${t.total_days} días consultados</span></article><article class="delivery-context"><h2>Control diario del reparto</h2><p>Importes del concepto <strong>Costo Delivery</strong> registrado en Toteat, destinados al pago de repartidores.</p><div class="delivery-facts"><div><strong>${t.records}</strong><span>Registros con tarifa</span></div><div><strong>${t.review_count}</strong><span>Registros por revisar</span></div></div><small>Última lectura: ${escapeHTML(localStamp(data.last_sync))}</small></article></div>${lastError?`<p class="source-alert" role="alert">${escapeHTML(lastError)}. Últimos importes conservados.</p>`:''}${readerWarning?`<p class="source-alert">${escapeHTML(readerWarning)}</p>`:''}${data.progress.errors?`<p class="source-alert">${data.progress.errors} fechas requieren revisión. Un error no elimina los importes guardados.</p>`:''}<div class="delivery-caption"><div><h2>Detalle por día</h2><p>Día operativo del turno en Toteat · hora de Chile.</p></div><span class="timing-label">${data.progress.pending?'↻ Actualización en curso':'✓ Lectura disponible'}</span></div><div class="delivery-days">${data.days.map(day=>`<details class="delivery-day" data-fee-date="${day.day}" ${opened.has(day.day)?'open':''}><summary><div class="delivery-day-name"><strong>${escapeHTML(dateLabel(day.day,{weekday:'long',month:'short'}))}</strong><small>${day.records} registros · ${escapeHTML(dayStatus(day))}</small></div><strong class="delivery-day-amount">${amount(day.fee)}</strong><span class="delivery-chevron" aria-hidden="true">⌄</span></summary><div class="delivery-day-detail">${day.error?`<p class="source-alert">${escapeHTML(day.error)}</p>`:''}${day.details.length?day.details.map(row=>`<article><div><strong>Comanda ${escapeHTML(row.order_id)}</strong><small>Pago ${escapeHTML(row.payment_id)}</small><small>Apertura: ${escapeHTML(sourceStamp(row.opened_at))}<br>Cierre: ${escapeHTML(sourceStamp(row.closed_at))}</small>${row.warnings.length?'<span class="badge pendiente">Requiere revisión</span>':''}</div><div><strong>${clp(row.fee)}</strong><small>Tarifa de reparto</small><small>Pago Toteat asociado: ${clp(row.sale_paid)}</small></div></article>`).join(''):`<p class="hint">${day.fee===null?'Esta fecha todavía no tiene una consulta válida.':'Toteat no devolvió conceptos Costo Delivery para este día operativo.'}</p>`}<small>Consulta: ${escapeHTML(localStamp(day.last_success))}</small></div></details>`).join('')}</div><div class="delivery-notes"><p><strong>Cómo se actualiza:</strong> el servidor revisa las tarifas diariamente y solicita una nueva lectura al abrir este apartado. Puedes seguir trabajando mientras completa las consultas.</p><p><strong>Para la liquidación:</strong> ${clp(t.unassigned_amount)} todavía no tienen repartidor conciliado. El total no acredita un pago al repartidor ni dinero recaudado por él.</p><p class="hint">La fecha corresponde al turno de Toteat; no confirma cuándo se realizó la entrega física. El total de una venta y su tarifa de reparto se conservan por separado.</p></div>`;
  }
  function sourceStamp(value){return localStamp(/[zZ]$|[+-]\d\d:\d\d$/.test(value)?value:value+'Z');}
  document.addEventListener('toggle',event=>{const day=event.target;if(!day.matches?.('[data-fee-date]'))return;if(day.open)opened.add(day.dataset.feeDate);else opened.delete(day.dataset.feeDate);},true);
  document.addEventListener('submit',event=>{if(event.target.id!=='delivery-filter')return;event.preventDefault();if(busy)return;const value=event.target.elements.month.value;if(value!==month){month=value;snapshot=null;opened.clear();}load(true);rememberView();});
  return {enter,leave,reset,get month(){return month;}};
})();
