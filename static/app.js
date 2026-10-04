'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {board: null, date: '', period: 'day', filter: 'all', deliveryFilter: 'all', search: '', editing: null, detail: null, transition: null, request: 0, end: '', panelRequest: 0, saving: false, role: 'admin', productionScope: '', scope:'operations'};
const labels = {marcado_solicitado:'Marcado solicitado',pendiente:'Pendiente de marcado', solicitado:'Solicitado a producción (histórico)', marcado:'Marcado físico', entregado:'Entregado', cancelado:'Cancelado'};
const timingLabels = {scheduled:'Programada',immediate:'Inmediata · entrega confirmada',unclassified:'Tipo de entrega por confirmar'};
const next = {pendiente:'marcado_solicitado', marcado_solicitado:'marcado', marcado:'entregado'};
const actions = {pendiente:'Solicitar marcado', marcado_solicitado:'Ya está marcado', marcado:'Registrar entrega'};
let toastTimer;
let noticeSequence=0, acceptedNoticeSequence=0, noticeScope='', noticeTracker=null;
let pendingNotices=[], noticePolling=false, notificationAudio=null, soundEnabled=false;


async function api(path, method = 'GET', body) {
  const response = await fetch(path, {method, headers: method === 'GET' ? {} : {'Content-Type':'application/json','X-ERP-Local':'1'}, body: body ? JSON.stringify(body) : undefined});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'No se pudo completar la operación.');
  return data;
}
function actor() {
  const value = $('#actor').value.trim();
  if (!value) throw new Error('Indica el responsable demo en la barra superior.');
  return value;
}
function toast(text) {
  clearTimeout(toastTimer); $('#toast').textContent = text; $('#toast').classList.remove('hidden');
  toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), 4500);
}
function dateLabel(value, options = {}) { return new Intl.DateTimeFormat('es-CL', {weekday:'long', day:'numeric', month:'long', ...options}).format(new Date(value + 'T12:00:00')); }
function badge(status) { return `<span class="badge ${escapeHTML(status)}">${escapeHTML(labels[status])}</span>`; }
function itemLabel(item) { return escapeHTML(item.flavor); }
function modal(title, subtitle, content, footer = '', eyebrow = 'PEDIDO FICTICIO') {
  state.panelRequest++;
  $('#modal-content').innerHTML = `<div class="modal-header"><div><div class="eyebrow">${escapeHTML(eyebrow)}</div><h2 id="modal-title">${escapeHTML(title)}</h2><p>${escapeHTML(subtitle)}</p></div><button class="close-button" data-action="close" aria-label="Cerrar panel">×</button></div><div class="modal-body">${content}</div>${footer ? `<div class="modal-footer">${footer}</div>` : ''}`;
  if (!$('#modal').open) $('#modal').showModal();
  $('#modal').scrollTop = 0;
}
function showFormError(message) { const box = $('#form-error'); if (box) { box.textContent = message; box.classList.remove('hidden'); box.scrollIntoView({block:'nearest'}); } else toast(message); }

async function loadBoard() {
  const request = ++state.request;
  const noticeRequest = ++noticeSequence;
  if (state.period === 'custom' && (!state.date || !state.end || state.end < state.date)) {
    $('#load-error').textContent = 'Completa ambas fechas; la final debe ser igual o posterior a la inicial.'; $('#load-error').classList.remove('hidden'); return false;
  }
  try {
    const board = await api(`/api/board?scope=${state.scope}&period=${state.period}${state.date ? `&date=${encodeURIComponent(state.date)}` : ''}${state.period === 'custom' ? `&end=${encodeURIComponent(state.end)}` : ''}`);
    if (request !== state.request) return;
    state.board = board; state.date ||= board.today; $('#date').value = state.date;
    $('#load-error').classList.add('hidden');
    $('#metric-stock').textContent = board.stock.available === null ? 'Desconocido' : `${board.stock.available} enteras`;
    $('#stock-rows').innerHTML = stockRows(board.stock);
    const metrics = board.summary.metrics;
    $('#metric-pending').textContent = metrics.pending + metrics.mark_requested;
    $('#metric-pending-detail').textContent = `${metrics.pending} pendientes · ${metrics.mark_requested} por confirmar`;
    $('#metric-marked').textContent = metrics.marked;
    $('#metric-total').textContent = board.summary.outstanding;
    $('#date-title').textContent = state.period === 'day' ? dateLabel(board.start) : `${dateLabel(board.start, {weekday:undefined, month:'short'})} — ${dateLabel(board.end, {weekday:undefined, month:'short'})}`;
    $('#count-label').textContent = `${board.scope !== 'operations' ? 'PRUEBAS · ' : ''}${board.orders.length} pedidos · ${board.start} → ${board.end}, inclusive · retiro/entrega en Chile`;
    $('#summary-total').textContent = `${board.summary.outstanding} u.`;
    $('#summary').innerHTML = board.summary.rows.map(row => `<div class="summary-row"><div><strong>${itemLabel(row)}</strong><small>${escapeHTML(row.size)}</small></div><span class="summary-qty">${row.quantity}</span></div>`).join('') || '<p class="hint">Sin encargadas pendientes de entrega.</p>';
    renderOrders(); renderOperations(); await applyNotifications(board.notifications,board.notification_scope,noticeRequest); return true;
  } catch (error) { if (request !== state.request) return false; $('#load-error').textContent = 'No se pudo actualizar. ' + error.message; $('#load-error').classList.remove('hidden'); return false; }
}

function renderOrders() {
  if (!state.board) return;
  const orders = state.board.orders.filter(order => {
    const matchStatus = state.filter === 'all' || order.items.some(item => state.filter === 'to-mark' ? ['pendiente','marcado_solicitado'].includes(item.status) : state.filter === 'marked' ? item.status === 'marcado' : ['entregado','cancelado'].includes(item.status));
    const searchable = [order.customer, order.source_id, order.channel, ...order.items.flatMap(item => [item.flavor,item.size,item.sku])].join(' ').toLocaleLowerCase('es');
    return (state.deliveryFilter === 'all' || order.delivery_timing === state.deliveryFilter) && matchStatus && searchable.includes(state.search.toLocaleLowerCase('es'));
  });
  $('#orders').innerHTML = orders.map(order => {
    const [day, hour] = order.pickup_at.split('T');
    const initial = order.customer.replace(/^Cliente demo\s*/, '').slice(0,2).toUpperCase();
    return `<article class="order-card" data-order-id="${order.id}"><div class="order-header"><div class="avatar" aria-hidden="true">${escapeHTML(initial)}</div><div class="order-identity"><strong>${escapeHTML(order.customer)}</strong><div class="order-meta"><span>${escapeHTML(order.source_id)}</span><span>·</span><span class="channel">${escapeHTML(order.channel)}</span><span class="timing-label">${escapeHTML(timingLabels[order.delivery_timing])}</span>${order.is_simulation ? '<span class="test-badge">PRUEBA</span>' : ''}</div></div><div class="order-time"><strong>${hour}</strong><small>${state.period === 'week' ? `${escapeHTML(dateLabel(day,{weekday:'short',month:undefined}))} · ` : ''}${order.fulfillment === 'retiro' ? 'Retiro en local' : 'Despacho'}</small></div></div><div>${order.items.map(item => `<div class="item-row"><span class="quantity">${item.quantity}×</span><div class="item-info"><strong>${itemLabel(item)}</strong><small>${escapeHTML(item.size)} · ${escapeHTML(item.sku)}</small></div>${badge(item.status)}</div>`).join('')}</div><div class="order-footer">${order.is_simulation ? `<button class="text-button" data-action="${order.simulation_archived ? 'restore-test' : 'archive-test'}" data-id="${order.id}">${order.simulation_archived ? 'Restaurar prueba' : 'Retirar prueba'}</button>` : ''}<span class="payment-pill">✓ Pago simulado confirmado</span><button class="detail-button" data-action="detail" data-id="${order.id}" aria-label="Ver detalle de ${escapeHTML(order.customer)}">Ver pedido <span aria-hidden="true">↗</span></button></div></article>`;
  }).join('') || `<div class="empty"><strong>Todo despejado por aquí.</strong>No hay pedidos para esta fecha o filtro.${state.board.operating_mode !== 'toteat-local' && state.board.seed_date && state.board.seed_date !== state.date ? `<br><button class="button secondary" data-action="seed-date">Ver día de los ejemplos</button>` : ''}</div>`;
}

async function showDetail(id) {
  const order = state.board.orders.find(row => row.id === Number(id));
  if (!order) return;
  state.detail = order;
  modal(order.customer, `${order.source_id} · ${order.status_label}`, '<p class="hint">Cargando historial…</p>');
  try {
    const panelRequest = state.panelRequest;
    const history = await api(`/api/orders/${order.id}/history`);
    if (!$('#modal').open || panelRequest !== state.panelRequest) return;
    const content = `<div class="detail-grid"><div class="detail-field"><small>Retiro / entrega</small>${escapeHTML(dateLabel(order.pickup_at.split('T')[0]))} · ${order.pickup_at.split('T')[1]}</div><div class="detail-field"><small>Tipo de entrega</small>${escapeHTML(timingLabels[order.delivery_timing])}</div><div class="detail-field"><small>Teléfono ficticio</small>${escapeHTML(order.customer_phone || 'No registrado')}</div><div class="detail-field"><small>Canal y modalidad</small>${escapeHTML(order.channel)} · ${order.fulfillment === 'retiro' ? 'Retiro en local' : 'Despacho'}</div><div class="detail-field"><small>Fuente + ID</small>${escapeHTML(order.source)} / ${escapeHTML(order.source_id)}</div><div class="detail-field"><small>Pago</small>Confirmado (simulado)</div><div class="detail-field full"><small>Boleta</small>No disponible · documento real pendiente de integración. Fuente: Toteat, sin consultar.</div><div class="detail-field full"><small>Comentarios del pedido</small>${escapeHTML(order.comments || 'Sin comentarios')}</div></div><h3 class="section-label">Productos y estados</h3><p class="hint">Pendiente de marcado → marcado solicitado → marcado físico → entregado. Los cambios no afectan Toteat ni el stock.</p>${order.items.map(item => `<div class="detail-item"><div class="detail-item-header"><strong>${item.quantity} × ${itemLabel(item)} · ${escapeHTML(item.size)}</strong>${badge(item.status)}</div><p>SKU ${escapeHTML(item.sku)} · ID ítem ${escapeHTML(item.source_item_id)}${item.comments ? `<br>${escapeHTML(item.comments)}` : ''}</p><div class="actions">${next[item.status] && (state.role !== 'production' || ['pendiente'].includes(item.status)) ? `<button class="button primary" data-action="transition" data-item="${item.id}" data-status="${next[item.status]}">${actions[item.status]}</button>` : ''}${state.role !== 'production' && ['pendiente','marcado_solicitado','marcado'].includes(item.status) ? `<button class="button danger" data-action="transition" data-item="${item.id}" data-status="cancelado">Cancelar ítem</button>` : ''}${state.role !== 'production' && item.status !== 'pendiente' ? `<button class="button secondary" data-action="reverse" data-item="${item.id}" data-status="pendiente">Revertir a pendiente</button>` : ''}</div></div>`).join('')}<h3 class="section-label">Historial · ${history.length} registros</h3><p class="hint">Responsable declarado, sin identidad autenticada. Fechas mostradas en hora de Chile.</p>${history.map(event => `<article class="history-entry"><strong>${escapeHTML(event.action === 'estado' || event.action === 'reversion' ? `${labels[event.before.status]} → ${labels[event.after.status]}` : event.action === 'creado' ? 'Pedido creado' : 'Pedido corregido')} · ${escapeHTML(event.actor)}</strong><p>${escapeHTML(event.reason)}</p><time>${escapeHTML(new Intl.DateTimeFormat('es-CL',{dateStyle:'medium',timeStyle:'short',timeZone:state.board.timezone}).format(new Date(event.occurred_at)))}</time><details><summary>Ver detalle del cambio</summary><pre>${escapeHTML(JSON.stringify({antes:event.before,después:event.after},null,2))}</pre></details></article>`).join('')}`;
    modal(order.customer, `${order.source_id} · ${order.status_label}`, content, `<button class="button secondary" data-action="close">Cerrar</button><button class="button primary admin-only" data-action="edit" data-id="${order.id}">Corregir pedido</button>`);
  } catch(error) { toast(error.message); }
}

function showTransition(button) {
  const item = state.detail.items.find(row => row.id === Number(button.dataset.item));
  const status = button.dataset.status;
  const correction = button.dataset.action === 'reverse';
  state.transition = {order:state.detail,item,status,correction};
  modal(correction ? 'Revertir a pendiente' : labels[status], `${item.quantity} × ${item.flavor} · ${state.detail.customer}`, `<form id="transition-form"><div id="form-error" class="error hidden" role="alert"></div><p class="hint">Se conservará el estado anterior, responsable y fecha. La solicitud abarca las ${item.quantity} unidades de este ítem; no afecta a los otros ítems. ${status === 'marcado_solicitado' ? 'Esteban verá una alerta dentro del ERP. El ítem todavía NO está marcado físico.' : status === 'marcado' ? 'Marca solo cuando la torta física esté asignada. El ajuste manual de inventario se hace fuera de este prototipo.' : status === 'cancelado' ? 'El ítem dejará de contar en encargadas. No se borra ni se anula ninguna venta o pago.' : 'No se realizarán movimientos de stock ni ventas.'}</p><label class="field">Motivo del cambio<input name="reason" required maxlength="500" placeholder="Ej.: comanda física asignada en prueba demo"></label><p class="hint">Responsable: ${escapeHTML($('#actor').value)} · registro local ficticio</p></form>`, '<button class="button secondary" data-action="back-detail">Volver</button><button class="button primary" type="submit" form="transition-form">Guardar estado</button>');
}

function field(label, name, value = '', type = 'text', extra = '') { return `<label class="field">${label}<input type="${type}" name="${name}" value="${escapeHTML(value)}" ${extra}></label>`; }
function selectField(label, name, values, current) { return `<label class="field">${label}<select name="${name}">${values.map(([value,text]) => `<option value="${escapeHTML(value)}" ${current === value ? 'selected' : ''}>${escapeHTML(text)}</option>`).join('')}</select></label>`; }
function itemForm(item = {}, index = 1) {
  const locked = Boolean(item.id);
  const product = state.board.catalog.find(p => p.sku === item.sku) || state.board.catalog.find(p => !p.is_demo) || state.board.catalog[0];
  item = {...product,...item};
  return `<fieldset class="form-item"><legend class="small-label">Ítem ${index}</legend><div class="form-item-heading"><span ${locked ? '' : 'data-new-item-state'}>${locked ? `Estado actual: ${escapeHTML(labels[item.status])}. Se conserva al guardar.` : 'Nuevo ítem · pendiente de marcado'}</span>${!locked ? '<button type="button" class="text-button" data-action="remove-item">Quitar borrador</button>' : ''}</div><div class="form-grid">${field('ID ítem','source_item_id',item.source_item_id || String(index),'text',`required maxlength="80" ${locked ? 'readonly' : ''}`)}${selectField('Producto entero','sku',catalogChoices(),item.sku)}${field('Cantidad','quantity',item.quantity || 1,'number','required min="1" max="999" step="1"')}${field('Producto','flavor',item.flavor || '','text','required readonly')}${field('Formato','size',item.size || '','text','required readonly')}<input type="hidden" name="kind" value="torta"><label class="field full">Comentario del ítem<input name="item_comments" value="${escapeHTML(item.comments || '')}" maxlength="500"></label></div></fieldset>`;
}
function showForm(order = null) {
  state.editing = order;
  const selectedDate = state.date || state.board.today;
  const body = `<form id="order-form"><div id="form-error" class="error hidden" role="alert"></div><p class="hint">Usa nombres inventados e IDs DEMO-. Esta versión es un laboratorio local. Los campos por ítem se estructuran aquí; el futuro mapeo Toteat aún debe verificarse.</p><div class="form-grid">${selectField('Fuente de ejemplo','source',[['demo','Demo manual'],['demo-toteat','Demo de origen Toteat (simulado)']],order?.source || 'demo')}${field('ID fuente único','source_id',order?.source_id || `DEMO-${Date.now()}`,'text',`required maxlength="80" ${order ? 'readonly' : ''}`)}${selectField('Canal','channel',[['Web/Mercat','Web / Mercat'],['Presencial','Presencial'],['Instagram','Instagram']],order?.channel || 'Web/Mercat')}${selectField('Tipo de entrega','delivery_timing',[['','Elegir tipo'],['scheduled','Programada · pendiente de marcado'],['immediate','Inmediata · venta confirma entrega']],order?.delivery_timing || '')}${field('Teléfono ficticio','customer_phone',order?.customer_phone || (state.board.operating_mode === 'toteat-local' ? '000000000' : ''), 'tel','maxlength="40"')}<p class="hint full" id="timing-hint">El tipo de entrega pertenece al pedido; el producto y su inventario son los mismos. Los comentarios no se clasifican automáticamente.</p>${field('Cliente ficticio','customer',order?.customer || 'Cliente demo Prueba','text','required maxlength="160"')}${field('Fecha y hora · retiro programado o venta inmediata','pickup_at',order?.pickup_at || `${selectedDate}T12:00`,'datetime-local','required')}${selectField('Modalidad','fulfillment',[['retiro','Retiro en local'],['despacho','Despacho']],order?.fulfillment || 'retiro')}<label class="field full">Comentarios del pedido<input name="comments" value="${escapeHTML(order?.comments || '')}" maxlength="1000" placeholder="Indicaciones ficticias, despacho, mensaje de torta…"></label>${order ? '<label class="field full">Motivo de la corrección<input name="reason" required maxlength="500" placeholder="Qué cambió y por qué"></label>' : ''}<label class="check-field full"><input name="payment_confirmed" type="checkbox" required ${order?.payment_confirmed ? 'checked' : ''}><span>Confirmo un pago simulado verificado. En la operación real, un comprobante de transferencia no basta para acreditar el abono bancario.</span></label></div><h3 class="section-label">Productos del pedido</h3>${order ? '<p class="hint">Para cambiar un ítem que ya avanzó, primero reviértelo a Pendiente con motivo. Los ítems guardados se cancelan; no se borran.</p>' : ''}<div id="form-items">${(order?.items || [{}]).map((item,index) => itemForm(item,index+1)).join('')}</div><button class="button secondary" type="button" data-action="add-item">＋ Agregar ítem</button><p class="hint">Fecha local de retiro: America/Santiago. Responsable declarado: ${escapeHTML($('#actor').value)}.</p></form>`;
  modal(order ? 'Corregir pedido' : state.board.operating_mode === 'toteat-local' ? 'Simular pedido' : 'Nuevo pedido demo', 'Pago simulado confirmado · historial permanente · sin movimientos de stock', body, '<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="order-form">Guardar pedido ficticio</button>');
  syncTimingFields();
  if (order) $('#order-form [name=source]').disabled = true;
}

function syncTimingFields() {
  const form = $('#order-form'); if (!form) return;
  const timing = form.elements.namedItem('delivery_timing').value;
  form.elements.namedItem('delivery_timing').required = true;
  form.elements.namedItem('customer_phone').required = timing === 'scheduled';
  for (const label of form.querySelectorAll('[data-new-item-state]')) label.textContent = timing === 'immediate' ? 'Nuevo ítem · entregado al guardar la venta' : 'Nuevo ítem · pendiente de marcado';
  $('#timing-hint').textContent = timing === 'immediate' ? 'Venta inmediata: al guardar, todos sus ítems quedan entregados y fuera de las encargadas pendientes. Confirma la fecha y hora de la venta simulada.' : timing === 'scheduled' ? 'Programada: nombre, teléfono, fecha, hora y pago simulado confirmado. Queda pendiente de marcado. No pagados requieren revisión y autorización; esta demo aún no los admite.' : 'Elige el tipo. Se conserva un solo producto e inventario. El comentario original no se interpreta automáticamente.';
}
function readOrder(form) {
  const value = name => form.elements.namedItem(name).value;
  return {delivery_timing:value('delivery_timing'),customer_phone:value('customer_phone'),source:value('source'),source_id:value('source_id'),channel:value('channel'),customer:value('customer'),pickup_at:value('pickup_at'),fulfillment:value('fulfillment'),comments:value('comments'),payment_confirmed:form.elements.namedItem('payment_confirmed').checked,is_demo:true,items:[...form.querySelectorAll('.form-item')].map(row => {const get = name => row.querySelector(`[name=${name}]`).value; return {source_item_id:get('source_item_id'),sku:get('sku'),quantity:Number(get('quantity')),flavor:get('flavor'),size:get('size'),kind:get('kind'),comments:get('item_comments')};})};
}
async function saveOrder(form, button) {
  if (state.saving) return;
  state.saving = true;
  try {
    button.disabled = true;
    const order = readOrder(form);
    const body = {actor:actor(), order, ...(state.editing ? {version:state.editing.version,reason:form.elements.namedItem('reason').value} : {})};
    const simulation = state.editing?.is_simulation || (!state.editing && state.board.operating_mode === 'toteat-local');
    await api(state.editing ? `/api/orders/${state.editing.id}` : simulation ? '/api/simulations' : '/api/orders', state.editing ? 'PUT' : 'POST', body);
    if (simulation) {state.scope='tests';$('#record-scope').value='tests';}
    state.date = order.pickup_at.split('T')[0]; state.filter = 'all'; state.search = ''; $('#search').value = ''; if (state.period === 'custom') { state.end = state.date; $('#end-date').value = state.end; } selectFilter('all');
    $('#modal').close(); await loadBoard(); toast('Pedido ficticio guardado. Historial conservado.');
  } catch(error) { showFormError(error.message); } finally { state.saving = false; button.disabled = false; }
}
async function saveTransition(form, button) {
  if (state.saving) return;
  state.saving = true;
  try {
    button.disabled = true;
    const {order,item,status,correction} = state.transition;
    await api(`/api/orders/${order.id}/items/${item.id}/status`, 'POST', {actor:actor(),reason:form.elements.namedItem('reason').value,status,correction,version:order.version});
    const fresh = await loadBoard(); if (fresh) await showDetail(order.id); else $('#modal').close(); toast('Estado actualizado. Sin ajustes de inventario ni ventas.');
  } catch(error) { showFormError(error.message); } finally { state.saving = false; button.disabled = false; }
}
function selectFilter(filter) { state.filter = filter; document.querySelectorAll('[data-filter]').forEach(button => {button.classList.toggle('selected',button.dataset.filter === filter);button.setAttribute('aria-pressed',String(button.dataset.filter === filter));}); }
function stockRows(stock) {
  return stock.rows.map((row,index) => `<article class="stock-row"><div><strong>${escapeHTML(row.flavor)}</strong><small>${escapeHTML(row.size)}</small></div><div><small>Físico</small><strong>${row.physical ?? 'Desconocido'}</strong></div><div><small>** Reserva vitrina</small><strong>${row.reserved}</strong></div><div><small>Venta entera</small><strong>${row.available ?? 'Desconocido'}</strong></div><button class="text-button" data-action="stock-row" data-index="${index}">Editar conteo</button></article>`).join('') || '<p class="hint">Aún no hay conteos. Disponibilidad desconocida; no hay sincronización de inventario.</p>';
}
function catalogChoices() {
  return [...state.board.catalog].sort((a,b) => Number(a.is_demo) - Number(b.is_demo)).map(p => [p.sku,`${p.is_demo ? 'Demo' : 'Catálogo local'} · ${p.flavor} · ${p.size}`]);
}
function renderCatalog() {
  const products = state.board.catalog;
  const localMode = state.board.operating_mode === 'toteat-local';
  $('#new-order').classList.remove('hidden');
  $('#new-order').textContent=localMode?'＋ Simular pedido':'＋ Nuevo pedido demo';
  $('#record-scope-wrap').classList.toggle('hidden',!localMode);
  document.body.classList.toggle('simulation-view',state.scope !== 'operations');
  $('#scope-notice').textContent=state.scope !== 'operations' ? 'VISTA DE PRUEBAS · estos pedidos e indicadores no pertenecen a la operación. Sin efecto en Toteat o stock.' : 'Las simulaciones no afectan los indicadores operativos ni el stock.';
  $('#stock-edit').classList.toggle('hidden',localMode);
  $('.demo-tag').textContent = state.scope !== 'operations' ? 'PRUEBA' : localMode ? 'LOCAL' : 'DEMO';
  $('#role-caption').textContent = localMode ? 'Vista' : 'Vista demo';
  $('#actor-caption').textContent = localMode ? 'Responsable' : 'Responsable demo';
  $('#analytics-caption').textContent = state.scope !== 'operations' ? 'INDICADORES EXCLUSIVOS DE PRUEBA' : localMode ? 'LECTURA DE LA OPERACIÓN · REGISTROS LOCALES' : 'LECTURA DE LA OPERACIÓN · DATOS FICTICIOS';
  if (localMode && $('#actor').value.endsWith(' demo')) $('#actor').value = $('#actor').value.replace(/ demo$/, '');
  $('#catalog-help').textContent = localMode ? 'Solo productos incorporados de Toteat. Sin conteo, la disponibilidad es desconocida; no hay conexión de inventario.' : 'La disponibilidad requiere un conteo local. Sin conteo no significa cero. Los ejemplos conservan sus referencias para mantener el historial.';
  $('.role-notice').textContent = localMode ? 'Instalación local sin autenticación real. Recepción de comandas Toteat pendiente de verificar.' : 'Perfiles de demostración, sin autenticación ni control de acceso real.';
  const local = products.filter(p => p.source === 'toteat-manual');
  $('#catalog-count').textContent = products.length;
  $('#catalog-context').textContent = localMode ? `${local.length} productos de Toteat incorporados manualmente. Sin productos de ejemplo.` : local.length ? `${local.length} productos incorporados manualmente desde la vista previa Toteat · ${products.length - local.length} ejemplos conservados.` : `${products.length} productos ficticios para probar el flujo.`;
  $('#data-notice').innerHTML = state.scope !== 'operations' ? '<strong>Simulaciones aisladas.</strong> Pedidos e indicadores de prueba. No modifican Toteat ni el stock. Puedes retirar cada prueba conservando su historial.' : localMode ? '<strong>Catálogo local de Toteat.</strong> La recepción automática de comandas y stock está pendiente; no hay conexión activa.' : local.length ? '<strong>Pedidos y conteos ficticios.</strong> Catálogo real incorporado manualmente; sin sincronización con Toteat.' : '<strong>Pedidos y conteos ficticios.</strong> Catálogo demo · sin conexión a Toteat.';
  $('#catalog-products').innerHTML = ['Tortas enteras','Dulces enteros'].map(group => `<h3 class="section-label">${escapeHTML(group)}</h3>${products.filter(p => p.catalog_group === group).sort((a,b) => Number(a.is_demo) - Number(b.is_demo)).map(p => {
    const count = state.board.stock.rows.find(r => r.flavor === p.flavor && r.size === p.size);
    const source = p.source_product;
    return `<article class="catalog-row"><div><strong>${escapeHTML(p.flavor)}</strong><p>${escapeHTML(p.size)} · ${p.is_demo ? 'Ejemplo ficticio' : 'Incorporación manual · Toteat'}</p><small>${p.bases ? (p.recipe_status === 'provisional' ? 'Receta provisional · editable' : 'Bases registradas') : 'Receta pendiente'}${p.recipe_note ? ` · ${escapeHTML(p.recipe_note)}` : ''}</small>${source ? `<details><summary>Ver identidad de origen</summary><dl><dt>ID</dt><dd>${escapeHTML(source.id)}</dd><dt>ID Toteat</dt><dd>${escapeHTML(String(source.idToteat))}</dd><dt>Código local</dt><dd>${escapeHTML(source.localCode)}</dd><dt>Categoría</dt><dd>${escapeHTML(source.category)} · ${escapeHTML(source.categoryId)}</dd><dt>Opciones de modificador</dt><dd>${source.modifier_options}</dd><dt>Procedencia</dt><dd>${escapeHTML(p.source_reference)}</dd></dl></details>` : ''}</div><div class="catalog-availability"><span>Venta entera</span><strong>${count?.available == null ? 'Desconocida' : `${count.available} enteras`}</strong><small>${count ? 'Conteo local' : 'Sin conteo registrado'}</small></div></article>`;
  }).join('')}`).join('');
}
function renderOperations() {
  renderCatalog();
  const board = state.board;
  const localMode = board.operating_mode === 'toteat-local';
  $('#alert-count').textContent = board.notifications.length;
  $('#alerts').innerHTML = board.notifications.map(n => `<article class="alert-row"><div><strong>${n.quantity} × ${escapeHTML(n.flavor)} · ${escapeHTML(n.size)}</strong>${n.is_simulation ? '<span class="test-badge">PRUEBA</span>' : ''}<p>${escapeHTML(n.customer)} · retiro ${escapeHTML(n.pickup_at.replace('T',' '))}</p><small>Solicitó ${escapeHTML(n.actor)} · ${escapeHTML(new Intl.DateTimeFormat('es-CL',{dateStyle:'short',timeStyle:'short',timeZone:board.timezone}).format(new Date(n.occurred_at)))}</small></div><button class="button secondary" data-action="alert" data-item="${n.item_id}">Revisar solicitud</button></article>`).join('') || '<p class="hint">Sin solicitudes de marcado abiertas. Las nuevas solicitudes aparecen aquí al actualizar.</p>';
  $('#production-totals').innerHTML = state.productionScope ? `<div class="base-totals">${board.production.totals.map(b => `<article><strong>${escapeHTML(b.quantity)}</strong><span>${escapeHTML(b.name)}</span><small>${escapeHTML(b.size)}</small></article>`).join('') || '<p class="hint">No hay bases calculables en este período.</p>'}</div>${board.production.missing.length ? `<div class="recipe-warning">Receta/cantidad pendiente: ${board.production.missing.map(p => `${p.quantity} × ${escapeHTML(p.flavor)} (${escapeHTML(p.size)})`).join(' · ')}. No se incluyen en las bases calculadas.</div>` : ''}<p class="hint">Suma exacta por base y tamaño sobre todas las encargadas del rango. 0,5 permanece como media base, sin redondeo automático.</p>` : '<p class="empty compact">Elige un alcance para calcular. El registro aún no identifica qué bases ya están elaboradas.</p>';
  $('#recipes').innerHTML = board.catalog.map(p => `<article class="recipe-row"><div><strong>${escapeHTML(p.flavor)} · ${escapeHTML(p.size)}</strong><small>${p.is_demo ? 'Ejemplo ficticio' : 'Catálogo local · incorporación manual'}</small><p>${p.bases ? p.bases.map(b => `${escapeHTML(b.quantity)} × ${escapeHTML(b.name)}`).join(' + ') : 'Receta/cantidad pendiente'}</p>${p.recipe_note ? `<p>${escapeHTML(p.recipe_note)}</p>` : ''}</div><button class="text-button" data-action="recipe" data-sku="${escapeHTML(p.sku)}">Editar bases</button></article>`).join('');
  const stats = board.analytics;
  if (stats) $('#analytics').innerHTML = `<div class="analytics-grid"><article><strong>${stats.orders}</strong><span>Pedidos del rango</span><small>Incluye finalizados y cancelados</small></article><article><strong>${stats.units}</strong><span>Enteros encargados</span><small>Unidades no canceladas, incluye entregadas</small></article><article><strong>${stats.mark_minutes === null ? 'Sin datos' : `${stats.mark_minutes} min`}</strong><span>Solicitud → marcado</span><small>Promedio de ${stats.mark_samples} confirmaciones registradas</small></article><article><strong>${stats.on_time_total ? `${stats.on_time}/${stats.on_time_total}` : 'Sin datos'}</strong><span>Entregas programadas a tiempo</span><small>Solo entregas programadas clasificadas · hora registrada ≤ retiro</small></article></div><div class="channel-breakdown">${Object.entries(stats.channels).map(([channel,count]) => `<span>${escapeHTML(channel)} <strong>${count} pedidos</strong></span>`).join('')}</div><p class="hint">Agrupado por fecha programada de retiro/entrega. ${localMode ? 'Sin recepción activa de comandas; las métricas esperan registros verificados.' : 'Tiempos medidos con registros de la demo; no son resultados reales.'} Productos: ver resumen por sabor/formato. Sin datos de ingresos ni márgenes.</p>`;
}
function baseForm(base = {}) {
  return `<div class="base-row">${field('Base','base_name',base.name || '','text','required maxlength="80"')}${field('Cantidad por entero','base_quantity',base.quantity || '1','number','required min="0.001" max="999" step="0.001"')}<button class="text-button" type="button" data-action="remove-base">Quitar</button></div>`;
}
function showRecipe(product) {
  state.recipeEditing = product;
  modal('Editar bases', `${product.flavor} · ${product.size} · valores por producto entero`, `<form id="recipe-form"><div id="form-error" class="error hidden" role="alert"></div><label class="check-field"><input type="checkbox" name="recipe_pending" ${product.bases === null ? 'checked' : ''}>Receta pendiente de confirmar</label><div id="recipe-bases">${(product.bases || []).map(baseForm).join('')}</div><button class="button secondary" type="button" data-action="add-base">Agregar base</button><label class="field">Motivo de edición<input name="reason" required maxlength="500"></label><p class="hint">Las cantidades se guardan como decimales exactos. El tamaño queda ligado al producto; bases de 10 y 20 personas permanecen separadas. Amor hojarasca: valor inicial provisional editable. Sin rellenos en esta etapa.</p></form>`,'<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="recipe-form">Guardar receta local</button>','CONFIGURACIÓN DE PRODUCCIÓN');
}
async function saveRecipe(form,button) {
  if (state.saving) return;
  state.saving = true; button.disabled = true;
  try {
    const bases = form.elements.namedItem('recipe_pending').checked ? null : [...form.querySelectorAll('.base-row')].map(row => ({name:$('[name=base_name]',row).value,quantity:$('[name=base_quantity]',row).value}));
    await api('/api/recipes','PUT',{sku:state.recipeEditing.sku,bases,version:state.recipeEditing.version,actor:actor(),reason:form.elements.namedItem('reason').value});
    $('#modal').close(); await loadBoard(); toast('Receta local actualizada con historial.');
  } catch(error) { showFormError(error.message); }
  finally { state.saving = false; button.disabled = false; }
}
function showStockForm(row = null) {
  state.stockEditing = row;
  const product = state.board.catalog.find(p => p.flavor === row?.flavor && p.size === row?.size) || state.board.catalog[0];
  modal('Stock y reserva para vitrina', 'Conteo ficticio actual · decisión manual · no ajusta Toteat', `<form id="stock-form"><div id="form-error" class="error hidden" role="alert"></div><div class="form-grid">${row ? '' : selectField('Producto del conteo','stock_product',catalogChoices(),product.sku)}${field('Sabor','flavor',product.flavor,'text','required readonly')}${field('Tamaño','size',product.size,'text','required readonly')}${field('Físico entero (vacío = desconocido)','physical',row?.physical ?? '','number','min="0" max="999" step="1"')}${field('** Enteras reservadas para vitrina','reserved',row?.reserved ?? 0,'number','required min="0" max="999" step="1"')}<label class="field full">Motivo<input name="reason" required maxlength="500" placeholder="Ej.: conteo ficticio, reservar 2 para vitrina"></label></div><p class="hint">Tú eliges 0, 1 o X enteras para trozar. No existe una regla automática de última torta. Disponibles = físico − reserva; nunca se restan encargos aquí. Sin físico conocido, la disponibilidad sigue desconocida.</p></form>`, '<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="stock-form">Guardar conteo demo</button>','CONTEO Y RESERVA MANUAL');
}
async function saveStock(form,button) {
  if (state.saving) return;
  state.saving = true; button.disabled = true;
  try {
    const value = name => form.elements.namedItem(name).value;
    await api('/api/stock','PUT',{stock:{flavor:value('flavor'),size:value('size'),physical:value('physical') === '' ? null : Number(value('physical')),reserved:Number(value('reserved'))},actor:actor(),reason:value('reason'),version:state.stockEditing?.version ?? 0});
    $('#modal').close(); await loadBoard(); toast('Conteo y reserva guardados con historial.');
  } catch(error) { showFormError(error.message); }
  finally { state.saving = false; button.disabled = false; }
}
async function copyMessage() {
  const text = $('#whatsapp-text').value;
  try { await navigator.clipboard.writeText(text); toast('Texto ficticio copiado. No se envió ningún mensaje.'); }
  catch { $('#whatsapp-text').focus(); $('#whatsapp-text').select(); toast('Seleccionado. Usa Copiar en tu dispositivo; no se envió ningún mensaje.'); }
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button'); if (!button) return;
  const action = button.dataset.action;
  if (state.saving) return;
  if (action === 'close') { state.panelRequest++; $('#modal').close(); }
  if (action === 'detail') await showDetail(button.dataset.id);
  if (action === 'edit') showForm(state.detail);
  if (action === 'transition' || action === 'reverse') showTransition(button);
  if (action === 'back-detail') await showDetail(state.detail.id);
  if (action === 'add-item') {
    const existing = [...document.querySelectorAll('#form-items [name=source_item_id]')].map(input => input.value);
    let index = existing.length + 1; while (existing.includes(String(index))) index++;
    $('#form-items').insertAdjacentHTML('beforeend', itemForm({},index)); syncTimingFields();
  }
  if (action === 'remove-item') button.closest('.form-item').remove();
  if (action === 'stock-row') showStockForm(state.board.stock.rows[Number(button.dataset.index)]);
  if (action === 'recipe') showRecipe(state.board.catalog.find(p => p.sku === button.dataset.sku));
  if (action === 'add-base') $('#recipe-bases').insertAdjacentHTML('beforeend', baseForm());
  if (action === 'remove-base') button.closest('.base-row').remove();
  if (action === 'alert') {
    const notice = pendingNotices.find(n => n.item_id === Number(button.dataset.item));
    if (!notice) { toast('La solicitud ya cambió. Actualiza los avisos.'); return; }
    closeNotifications(false);
    state.scope=notice.is_simulation?'tests':'operations';$('#record-scope').value=state.scope;
    state.date = notice.pickup_at.split('T')[0]; state.period = 'day'; $('#period').value = 'day'; $('#custom-range').classList.add('hidden'); $('#previous').disabled = false; $('#next').disabled = false;
    if (await loadBoard()) await showDetail(notice.order_id);
  }
  if (action === 'archive-test' || action === 'restore-test') {
    const order=state.board.orders.find(o=>o.id===Number(button.dataset.id));if(!order)return;
    state.saving=true;button.disabled=true;
    try {await api(`/api/simulations/${order.id}/${action==='archive-test'?'archive':'restore'}`,'POST',{actor:actor(),version:order.version});await loadBoard();toast(action==='archive-test'?'Prueba retirada. Puedes recuperarla en Pruebas retiradas.':'Prueba restaurada en Simulaciones.');}
    catch(error){toast(error.message);}finally{state.saving=false;button.disabled=false;}
  }
  if (action === 'copy') await copyMessage();
  if (action === 'seed-date') {state.date = state.board.seed_date; await loadBoard();}
  if (button.dataset.filter) {selectFilter(button.dataset.filter); renderOrders();}
});
document.addEventListener('submit', async event => { if (!['order-form','transition-form','stock-form','recipe-form'].includes(event.target.id)) return; event.preventDefault(); const button = document.querySelector(`[type=submit][form=${event.target.id}]`); if (event.target.id === 'order-form') await saveOrder(event.target,button); else if (event.target.id === 'recipe-form') await saveRecipe(event.target,button); else if (event.target.id === 'stock-form') await saveStock(event.target,button); else await saveTransition(event.target,button); });
$('#record-scope').addEventListener('change',event=>{state.scope=event.target.value;loadBoard();});
$('#delivery-filter').addEventListener('change', event => {state.deliveryFilter = event.target.value; renderOrders();});
$('#new-order').addEventListener('click', () => { if (state.board) showForm(); });
$('#refresh').addEventListener('click',loadBoard);
$('#date').addEventListener('change',event => {state.date = event.target.value; if (state.period !== 'custom') loadBoard();});
$('#today').addEventListener('click',() => {state.date = state.board?.today || ''; if (state.period === 'custom') { state.end = state.date; $('#end-date').value = state.end; } loadBoard();});
['previous','next'].forEach(id => $('#'+id).addEventListener('click',() => {
  if (!state.date || state.period === 'custom') return;
  const direction = id === 'previous' ? -1 : 1;
  const day = new Date(state.date + 'T12:00:00');
  if (state.period === 'month') { day.setDate(1); day.setMonth(day.getMonth() + direction); }
  else day.setDate(day.getDate() + direction * ({day:1,week:7,biweekly:14}[state.period] || 1));
  state.date = `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`; loadBoard();
}));
$('#period').addEventListener('change', event => {
  state.period = event.target.value; const custom = state.period === 'custom';
  $('#custom-range').classList.toggle('hidden', !custom); $('#previous').disabled = custom; $('#next').disabled = custom;
  if (custom) { state.end ||= state.date; $('#end-date').value = state.end; }
  loadBoard();
});
$('#end-date').addEventListener('change',event => { state.end = event.target.value; });
$('#apply-range').addEventListener('click',loadBoard);
$('#modal').addEventListener('cancel', event => { if (state.saving) event.preventDefault(); else state.panelRequest++; });
$('#stock-edit').addEventListener('click', () => showStockForm());
$('#role').addEventListener('change',event => {state.role = event.target.value; document.body.classList.toggle('production-role',state.role === 'production'); $('#actor').value = (state.role === 'production' ? 'Jefa producción' : 'Esteban') + (state.board?.operating_mode === 'toteat-local' ? '' : ' demo');});
$('#production-scope').addEventListener('change',event => {state.productionScope = event.target.value; if (state.board) renderOperations();});
document.addEventListener('change',event => {if (event.target.name === 'delivery_timing') { syncTimingFields(); return; } if (event.target.name === 'stock_product') { const p = state.board.catalog.find(p => p.sku === event.target.value); $('#stock-form [name=flavor]').value = p.flavor; $('#stock-form [name=size]').value = p.size; return; } if (event.target.name !== 'sku') return; const row = event.target.closest('.form-item'); if (!row) return; const product = state.board.catalog.find(p => p.sku === event.target.value); $('[name=flavor]',row).value = product.flavor; $('[name=size]',row).value = product.size;});
$('#search').addEventListener('input',event => {state.search = event.target.value; if (state.board) renderOrders();});
$('#whatsapp').addEventListener('click',() => { if (!state.board) return; modal('Texto para WhatsApp','Incluye todo el período elegido. Los filtros de búsqueda y estado no alteran este resumen.',`<textarea id="whatsapp-text" class="message-preview" readonly aria-label="Mensaje ficticio para copiar">${escapeHTML(state.board.whatsapp)}</textarea><p class="hint">PRODUCTOS POR MARCAR: pendientes y solicitados. RESUMEN ENCARGADAS: también incluye marcados. Stock actual separado de los encargos. ** = enteras reservadas para trozar/vitrina, decididas manualmente.</p>`,'<button class="button secondary" data-action="close">Cerrar</button><button class="button primary" data-action="copy">Copiar texto</button>','PREVIA · NO ENVÍA MENSAJES'); });
loadBoard();

function closeNotifications(returnFocus=true) {
  $('#notification-popup').classList.add('hidden');
  $('#notification-bell').setAttribute('aria-expanded','false');
  if(returnFocus) $('#notification-bell').focus();
}
function renderNotificationPopup() {
  const count=pendingNotices.length;
  $('#notification-badge').textContent=count;
  $('#notification-badge').classList.toggle('hidden',count===0);
  $('#notification-bell').setAttribute('aria-label',`Solicitudes de marcado: ${count} pendientes`);
  $('#notification-summary').textContent=count ? `${count} solicitudes pendientes` : 'Sin solicitudes pendientes';
  $('#notification-list').innerHTML=pendingNotices.map(n=>`<article class="notification-entry"><strong>${n.quantity} × ${escapeHTML(n.flavor)}</strong>${n.is_simulation ? '<span class="test-badge">PRUEBA</span>' : ''}<p>${escapeHTML(n.size)} · ${escapeHTML(n.pickup_at.replace('T',' '))}</p><small>Solicita ${escapeHTML(n.actor)} · pendiente de marcado</small><button class="button secondary" data-action="alert" data-item="${n.item_id}">Abrir pedido</button></article>`).join('') || '<p class="empty compact">No hay solicitudes de marcado pendientes.</p>';
}
function playNoticeSound() {
  if(!soundEnabled || notificationAudio?.state!=='running') return;
  const time=notificationAudio.currentTime;
  // Ataque suave y parciales metálicos: una campanita con caída natural.
  for(const [ratio,volume,decay] of [[1,0.05,1.95],[2.76,0.014,1.1],[5.4,0.006,0.55]]) {
    const oscillator=notificationAudio.createOscillator(),gain=notificationAudio.createGain();
    oscillator.type='sine';oscillator.frequency.setValueAtTime(1046.5*ratio,time);
    gain.gain.setValueAtTime(0,time);gain.gain.linearRampToValueAtTime(volume,time+0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001,time+decay);
    gain.gain.linearRampToValueAtTime(0,time+decay+0.025);
    oscillator.connect(gain);gain.connect(notificationAudio.destination);
    oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();};
    oscillator.start(time);oscillator.stop(time+decay+0.03);
  }
}
async function applyNotifications(items,scope,sequence) {
  if(sequence<acceptedNoticeSequence) return;
  acceptedNoticeSequence=sequence;pendingNotices=items;
  if(scope!==noticeScope || !noticeTracker) {
    noticeScope=scope;
    let storage;try{storage=window.localStorage;}catch{storage={getItem:()=>null,setItem:()=>{}};}
    noticeTracker=new OvejaNotifications.NoticeTracker(storage,'oveja-notices:'+scope);
  }
  const tracker=noticeTracker;
  const consume=()=>{if(tracker.consume(items).length)playNoticeSound();};
  if(navigator.locks) await navigator.locks.request('oveja-notices:'+scope,consume); else consume();
  if(sequence!==acceptedNoticeSequence)return;
  renderNotificationPopup();
  if(state.board) {state.board.notifications=items;renderOperations();}
}
async function refreshNotifications() {
  if(noticePolling) return;
  noticePolling=true;const sequence=++noticeSequence;
  try {
    const data=await api('/api/notifications');
    await applyNotifications(data.notifications,data.scope,sequence);
    $('#notification-update-status').textContent='Actualizado desde el ERP local · consulta cada 15 segundos.';
  } catch { $('#notification-update-status').textContent='No se pudo actualizar. Se conservan los últimos avisos; reintentaremos.'; }
  finally {noticePolling=false;}
}
$('#notification-bell').addEventListener('click',()=>{
  const popup=$('#notification-popup'),open=popup.classList.contains('hidden');
  popup.classList.toggle('hidden',!open);$('#notification-bell').setAttribute('aria-expanded',String(open));
  if(open){$('#notification-close').focus();refreshNotifications();}
});
$('#notification-close').addEventListener('click',()=>closeNotifications());
$('#notification-sound').addEventListener('click',async()=>{
  if(soundEnabled){soundEnabled=false;}
  else {
    try {
      const Audio=window.AudioContext||window.webkitAudioContext;
      if(!Audio)throw new Error('Unavailable');
      notificationAudio ||= new Audio();await notificationAudio.resume();
      soundEnabled=notificationAudio.state==='running';
    }catch{soundEnabled=false;}
  }
  $('#notification-sound').textContent=soundEnabled?'Silenciar':'Activar sonido';
  $('#notification-sound').setAttribute('aria-pressed',String(soundEnabled));
  $('#notification-audio-status').textContent=soundEnabled?'Campanita para solicitudes nuevas':'Sonido desactivado · los avisos visuales siguen activos';
});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('#notification-popup').classList.contains('hidden')){event.preventDefault();closeNotifications();}});
document.addEventListener('click',event=>{if(!event.target.closest('.notification-anchor')&&!$('#notification-popup').classList.contains('hidden'))closeNotifications(false);});
setInterval(refreshNotifications,15000);
