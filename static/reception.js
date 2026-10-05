'use strict';

const sourceAlertLabel = alert => ({cancelled:alert?.resolution==='cancel'?'Anulado':'Anulada en Toteat · Confirmar',partial_cancel:'Anulación parcial · revisar',refund_review:'Documento financiero anulado · revisar'}[alert?.state]||'');
function sourceAlertHTML(alert) {
  if(!alert)return '';
  return `<div class="source-alert ${alert.state==='cancelled'?'cancelled':''}"><strong>${escapeHTML(sourceAlertLabel(alert))}</strong><p>${alert.state==='cancelled'?(alert.resolution==='cancel'?'Anulación confirmada en el ERP. Las entregas realizadas se conservan en el historial.':'Toteat informa una anulación. Un socio debe confirmar Anular en ERP; los estados locales todavía se conservan.'):alert.resolution==='keep'?'Un socio revisó la alerta y mantuvo el encargo.':'Comprueba con el cliente si se mantiene, cambia o anula el encargo. Una nota de crédito no demuestra por sí sola la cancelación completa.'}</p></div>`;
}
function receptionCard(order,resolved=false) {
  const reception=order.reception||{},alert=reception.alert;
  const immediate=reception.decision?.decision==='immediate';
  const closed=Boolean(reception.closed_at||order.sales_comment_evidence?.length||order.status_label==='CLOSED');
  const incomplete=reception.can_classify_immediate===true;
  const title=alert?sourceAlertLabel(alert):immediate?'Entregada inmediata':reception.web_delivery_review?'Revisar programación web':incomplete?'Revisar tipo de entrega':closed&&!reception.closed_payload_received?'Esperando datos de venta cerrada':closed&&!reception.payment?'Mesa cerrada · revisar pago':closed?'Pendiente de revisión':'Esperando pago y cierre · Datos pendientes de recibir';
  const missing=reception.missing_fields||[...(reception.parsed?.issues||[]),...(reception.parsed?.warnings||[])].map(code=>({nombre_pendiente:'Nombre y apellido',fecha_pendiente:'Fecha de entrega',horario_pendiente:'Hora de entrega',telefono_pendiente:'Teléfono'}[code])).filter(Boolean);

  return `<article class="reception-card"><header><div><small>LECTURA DE TOTEAT</small><h3>Comanda ${escapeHTML(order.order_id)}</h3></div><span class="timing-label">${escapeHTML(title)}</span></header><p>${escapeHTML(reception.channel||'No informado')}${order.vendor?` · ${escapeHTML(order.vendor)}`:''}</p>${sourceAlertHTML(alert)}${reception.web_delivery_review?'<p class="hint">Modalidad web pendiente de verificar. Si el comentario pide otro día u hora, un socio debe confirmar la programación. El pago no acredita entrega.</p>':''}${order.items.map(item=>`<p><strong>${escapeHTML(item.quantity)} ×</strong> ${escapeHTML(item.productName||'Producto del catálogo')}</p>`).join('')}<details data-comment-key="${escapeHTML(order.key)}" ${state.commentDisclosure.get(order.key)!==false?'open':''}><summary>Comentario original</summary><p class="source-comment">${escapeHTML(reception.closed_payload_received?(reception.original_comment||'La venta cerrada no trae comentario.'):(reception.original_comment||order.comments.map(c=>c.text).join('\n')||'Datos pendientes de recibir desde Toteat.'))}</p></details>${incomplete&&!immediate?`<p class="hint">Falta revisar: ${missing.map(escapeHTML).join(' · ')}. Confirma si fue entrega inmediata o completa los datos de agendamiento.</p>`:''}${reception.payment&&!immediate&&!reception.order_id?'<p class="hint">Venta cerrada y saldada. Completa los datos para agendar o clasifica como venta inmediata de stock.</p>':''}<div class="actions">${state.partner?`<button class="button ${resolved?'secondary':'primary'}" data-action="reception" data-key="${escapeHTML(order.key)}">${resolved?'Ver revisión e historial':alert&&!alert.resolution?'Revisar anulación':reception.order_id?'Revisar alerta y pedido':reception.payment?'Completar o clasificar':'Forzar agendamiento'}</button>`:'<small>La revisión y autorización corresponden a un socio.</small>'}${state.partner&&!resolved&&reception.can_classify_immediate===true?`<button class="button secondary" data-action="reception-immediate-open" data-key="${escapeHTML(order.key)}">Pasar a venta inmediata</button>`:''}${reception.order_id?`<button class="button secondary" data-action="detail" data-id="${reception.order_id}">Ver pedido agendado</button>`:''}</div></article>`;
}
function renderToteat() {
  const board=state.board,reader=board.toteat||{state:'not_configured',orders:[]};
  $('#toteat-panel').classList.toggle('hidden',board.operating_mode!=='toteat-local'||state.view==='agenda');
  const fresh=reader.state==='receiving'&&reader.last_success&&Date.now()-Date.parse(reader.last_success)<90000;
  const sales=reader.sales_reader,salesFresh=sales?.state==='receiving'&&sales.last_success&&Date.now()-Date.parse(sales.last_success)<240000;
  $('#toteat-reader-state').textContent=(fresh?'Lectura activa · cada 30 segundos':'Lectura pendiente o sin actualización reciente')+(salesFresh?' · Ventas cada 90 segundos':' · Ventas por revisar');
  const pending=reader.orders||[],resolved=reader.reviewed_orders||[];
  const cancelled=resolved.filter(order=>order.reception?.alert?.resolution==='cancel'),delivered=resolved.filter(order=>order.reception?.alert?.resolution!=='cancel'&&order.reception?.decision?.decision==='immediate');
  $('#cancelled-tab-count').textContent=cancelled.length;$('#immediate-tab-count').textContent=delivered.length;
  const history=state.view==='cancelled'||state.view==='immediate';
  const shown=state.view==='cancelled'?cancelled:state.view==='immediate'?delivered:pending;
  $('#toteat-count').textContent=shown.length;
  $('#toteat-orders').innerHTML=(history?`<h3>${state.view==='cancelled'?'Historial de anuladas':'Historial de entregadas'}</h3><p class="hint">Mismos registros de origen y trazabilidad conservada.</p>`:'')+(shown.map(order=>receptionCard(order,history)).join('')||`<div class="empty compact"><strong>${history?'Sin registros en este historial.':'Sin comandas pendientes.'}</strong>${history?'Las decisiones confirmadas aparecerán aquí.':'Los encargos confirmados están en Agendadas.'}</div>`);
  if(board.operating_mode==='toteat-local'&&(state.scope==='operations'||state.view!=='agenda')) {
    $('.connection').textContent=fresh?'Toteat · lectura activa':'Toteat · lectura por revisar';
    $('#data-notice').innerHTML='<strong>Recepción y agendamiento.</strong> Una comanda entra a lectura; al cerrar y saldar, se agenda si trae datos suficientes. Las excepciones las revisa un socio.';
  }
}
async function showReception(key) {
  try {
    const context=await api('/api/toteat/reception?key='+encodeURIComponent(key));
    state.reception=context;state.receptionRequest=crypto.randomUUID();
    const parsed=context.parsed,alert=context.alert;
    const blocked=alert?.state==='cancelled'||(alert&&!alert.resolution);
    const immediate=context.decision?.decision==='immediate';
    const canSchedule=!context.order_id&&!blocked;
    const body=`${sourceAlertHTML(alert)}<div class="detail-field source-comment"><small>Comentario original · conservado</small>${escapeHTML(context.original_comment||'Sin comentario del cliente')}</div><p>${context.payment?'Cierre y saldo recibidos de Toteat.':context.closed_at?'Mesa cerrada. El detalle del pago debe revisarse; no se registra dinero al clasificar.':'Sin cierre y saldo acreditados. Forzar agendamiento requiere autorización del socio y no registra pago.'}</p>${canSchedule?`<form id="reception-form"><div id="form-error" class="error hidden" role="alert"></div><div class="form-grid">${field('Nombre y apellido','customer',parsed.customer,'text','required maxlength="160"')}${field('Teléfono','customer_phone',parsed.customer_phone,'tel','required maxlength="25"')}${field('Fecha y hora de entrega · Chile','pickup_at',parsed.pickup_at||'','datetime-local','required')}${selectField('Modalidad','fulfillment',[['retiro','Retiro en local'],['despacho','Delivery']],'retiro')}${field('Dirección para delivery','delivery_address','','text','maxlength="500"')}${selectField('Canal de origen','channel',[['Presencial','Presencial'],['Instagram','Instagram'],['Web/Mercat','Web/Mercat'],['','Seleccionar canal']],context.channel==='No informado'?'':context.channel)}<label class="field full">Motivo de autorización o clasificación<input name="reason" required maxlength="500"></label><label class="check-field full"><input name="is_test" type="checkbox" ${parsed.test_marker?'checked':''}>Prueba ficticia</label></div><h3 class="section-label">Productos recibidos</h3>${context.received.items.map(i=>`<p>${escapeHTML(i.quantity)} × ${escapeHTML(i.productName)}</p>`).join('')}<p class="hint">El cierre posterior se vinculará a este mismo pedido, preservando las modificaciones del cliente.</p></form>`:`<form id="reception-decision-form"><div id="form-error" class="error hidden" role="alert"></div>${alert&&!alert.resolution?`${alert.state==='cancelled'?'<input type="hidden" name="decision" value="cancel"><p>Al confirmar <strong>Anular en ERP</strong> se cancelan los compromisos pendientes. Se conservan pagos, entregas realizadas e historial. No se modifica Toteat.</p>':selectField('Resolución','decision',[['keep','Mantener el encargo'],['cancel','Confirmar anulación del encargo']],'keep')}${field('Motivo y verificación realizada','reason','','text','required maxlength="500"')}`:''}</form>`}<details><summary>Trazabilidad · ${context.history.length} movimientos</summary>${context.history.map(e=>`<article class="history-entry"><strong>${escapeHTML(e.action)} · ${escapeHTML(e.actor)}</strong><p>${escapeHTML(e.reason)}</p><time>${escapeHTML(new Date(e.occurred_at).toLocaleString('es-CL',{timeZone:'America/Santiago'}))}</time><details><summary>Evidencia y cambios</summary><pre>${escapeHTML(JSON.stringify({antes:e.before,después:e.after},null,2))}</pre></details></article>`).join('')||'<p>Recepción conservada; sin decisiones de socios.</p>'}</details>`;
    let footer='<button class="button secondary" data-action="close">Cerrar</button>';
    if(canSchedule)footer+=`${context.can_classify_immediate===true?'<button class="button secondary" data-action="reception-immediate">Pasar a venta inmediata</button>':''}<button class="button primary" type="submit" form="reception-form">${context.payment?'Agendar pedido':'Forzar agendamiento'}</button>`;
    if(alert&&!alert.resolution)footer+=`<button class="button ${alert.state==='cancelled'?'danger':'primary'}" type="submit" form="reception-decision-form">${alert.state==='cancelled'?'Anular en ERP':'Guardar resolución'}</button>`;
    if(context.order_id)footer+=`<button class="button secondary" data-action="detail" data-id="${context.order_id}">Ver pedido</button>`;
    modal('Revisar recepción',`Comanda ${context.received.order_id}`,body,footer,'SOLO SOCIOS');
  } catch(error){toast(error.message);}
}
async function showImmediateConfirmation(key){
  try{
    if(key)state.reception=await api('/api/toteat/reception?key='+encodeURIComponent(key));
    const context=state.reception;
    if(!state.partner||!context?.can_classify_immediate)return;
    const previousReason=$('#reception-form')?.elements.reason.value||'';
    modal('Pasar a venta inmediata',`Comanda ${context.received.order_id}`,`<form id="reception-immediate-form"><div id="form-error" class="error hidden" role="alert"></div><strong>${escapeHTML(context.parsed.customer||'Cliente sin informar')}</strong>${context.received.items.map(item=>`<p>${escapeHTML(item.quantity)} × ${escapeHTML(item.productName)}</p>`).join('')}<p>Confirmas que estos productos ya fueron entregados en el momento. Saldrán de pendientes y quedarán en <strong>Historial de entregadas</strong>, con la glosa <strong>Entregada inmediata</strong>.</p><p class="hint">${context.payment?.state==='discount_settled'?'Mesa cerrada y saldada con descuento autorizado; no equivale a dinero recibido.':'Pago y cierre recibidos de Toteat.'}</p><p class="hint">No modifica Toteat ni inventario. La decisión y su responsable quedarán registrados; un socio podrá revisarla posteriormente.</p><label class="field">Motivo de la clasificación<input name="reason" required maxlength="500" value="${escapeHTML(previousReason)}"></label></form>`,'<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="reception-immediate-form">Confirmar entrega inmediata</button>','CONFIRMACIÓN DE SOCIO');
  }catch(error){toast(error.message);}
}

async function saveReception(form,button,immediate=false) {
  if(state.saving)return;
  const reason=form.elements.reason?.value.trim();
  if(!reason){showFormError('Indica el motivo para dejar trazabilidad.');return;}
  state.saving=true;button.disabled=true;
  try{
    const context=state.reception,common={source_key:context.received.key,reason,revision:context.revision};
    if(immediate)await api('/api/toteat/reception/decide','POST',{...common,decision:'immediate'});
    else if(form.id==='reception-decision-form')await api('/api/toteat/reception/resolve','POST',{...common,version:context.alert.version,decision:form.elements.decision.value});
    else{
      const order=Object.fromEntries(new FormData(form));order.is_test=form.elements.is_test.checked;
      const result=await api('/api/toteat/reception/schedule','POST',{...common,order,request_id:state.receptionRequest});
      state.date=result.pickup_at.split('T')[0];state.scope=result.is_simulation?'tests':'operations';state.view='agenda';
    }
    $('#modal').close();await loadBoard();toast(immediate?'Venta inmediata registrada, sin demanda programada.':'Decisión guardada con trazabilidad.');
  }catch(error){showFormError(error.message);}
  finally{state.saving=false;button.disabled=false;}
}
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-action]');if(!button)return;
  if(button.dataset.action==='reception')showReception(button.dataset.key);
  if(button.dataset.action==='reception-immediate')showImmediateConfirmation();
  if(button.dataset.action==='reception-immediate-open')showImmediateConfirmation(button.dataset.key);
  if(button.dataset.action==='source-alert'&&state.detail?.source_alert)showReception(state.detail.source_alert.source_id);
});
document.addEventListener('submit',event=>{
  if(!['reception-form','reception-decision-form','reception-immediate-form'].includes(event.target.id))return;
  event.preventDefault();saveReception(event.target,document.querySelector(`[type=submit][form=${event.target.id}]`),event.target.id==='reception-immediate-form');
});

document.addEventListener('toggle',event=>{const target=event.target;if(target.matches?.('details[data-comment-key]')&&target.isConnected&&state.user)state.commentDisclosure.set(target.dataset.commentKey,target.open);},true);
