'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {board: null, date: '', period: 'day', filter: 'all', search: '', editing: null, detail: null, transition: null, request: 0, end: '', panelRequest: 0, saving: false, role: 'admin', productionScope: ''};
const labels = {marcado_solicitado:'Marcado solicitado',pendiente:'Pendiente de marcado', solicitado:'Solicitado a producción (histórico)', marcado:'Marcado físico', entregado:'Entregado', cancelado:'Cancelado'};
const next = {pendiente:'marcado_solicitado', marcado_solicitado:'marcado', marcado:'entregado'};
const actions = {pendiente:'Solicitar marcado', marcado_solicitado:'Ya está marcado', marcado:'Registrar entrega'};
let toastTimer;

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
  if (state.period === 'custom' && (!state.date || !state.end || state.end < state.date)) {
    $('#load-error').textContent = 'Completa ambas fechas; la final debe ser igual o posterior a la inicial.'; $('#load-error').classList.remove('hidden'); return false;
  }
  try {
    const board = await api(`/api/board?period=${state.period}${state.date ? `&date=${encodeURIComponent(state.date)}` : ''}${state.period === 'custom' ? `&end=${encodeURIComponent(state.end)}` : ''}`);
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
    $('#count-label').textContent = `${board.orders.length} pedidos · ${board.start} → ${board.end}, inclusive · retiro/entrega en Chile`;
    $('#summary-total').textContent = `${board.summary.outstanding} u.`;
    $('#summary').innerHTML = board.summary.rows.map(row => `<div class="summary-row"><div><strong>${itemLabel(row)}</strong><small>${escapeHTML(row.size)}</small></div><span class="summary-qty">${row.quantity}</span></div>`).join('') || '<p class="hint">Sin encargadas pendientes de entrega.</p>';
    renderOrders(); renderOperations(); return true;
  } catch (error) { if (request !== state.request) return false; $('#load-error').textContent = 'No se pudo actualizar. ' + error.message; $('#load-error').classList.remove('hidden'); return false; }
}

function renderOrders() {
  if (!state.board) return;
  const orders = state.board.orders.filter(order => {
    const matchStatus = state.filter === 'all' || order.items.some(item => state.filter === 'to-mark' ? ['pendiente','marcado_solicitado'].includes(item.status) : state.filter === 'marked' ? item.status === 'marcado' : ['entregado','cancelado'].includes(item.status));
    const searchable = [order.customer, order.source_id, order.channel, ...order.items.flatMap(item => [item.flavor,item.size,item.sku])].join(' ').toLocaleLowerCase('es');
    return matchStatus && searchable.includes(state.search.toLocaleLowerCase('es'));
  });
  $('#orders').innerHTML = orders.map(order => {
    const [day, hour] = order.pickup_at.split('T');
    const initial = order.customer.replace(/^Cliente demo\s*/, '').slice(0,2).toUpperCase();
    return `<article class="order-card" data-order-id="${order.id}"><div class="order-header"><div class="avatar" aria-hidden="true">${escapeHTML(initial)}</div><div class="order-identity"><strong>${escapeHTML(order.customer)}</strong><div class="order-meta"><span>${escapeHTML(order.source_id)}</span><span>·</span><span class="channel">${escapeHTML(order.channel)}</span></div></div><div class="order-time"><strong>${hour}</strong><small>${state.period === 'week' ? `${escapeHTML(dateLabel(day,{weekday:'short',month:undefined}))} · ` : ''}${order.fulfillment === 'retiro' ? 'Retiro en local' : 'Despacho'}</small></div></div><div>${order.items.map(item => `<div class="item-row"><span class="quantity">${item.quantity}×</span><div class="item-info"><strong>${itemLabel(item)}</strong><small>${escapeHTML(item.size)} · ${escapeHTML(item.sku)}</small></div>${badge(item.status)}</div>`).join('')}</div><div class="order-footer"><span class="payment-pill">✓ Pago simulado confirmado</span><button class="detail-button" data-action="detail" data-id="${order.id}" aria-label="Ver detalle de ${escapeHTML(order.customer)}">Ver pedido <span aria-hidden="true">↗</span></button></div></article>`;
  }).join('') || `<div class="empty"><strong>Todo despejado por aquí.</strong>No hay pedidos para esta fecha o filtro.${state.board.seed_date && state.board.seed_date !== state.date ? `<br><button class="button secondary" data-action="seed-date">Ver día de los ejemplos</button>` : ''}</div>`;
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
    const content = `<div class="detail-grid"><div class="detail-field"><small>Retiro / entrega</small>${escapeHTML(dateLabel(order.pickup_at.split('T')[0]))} · ${order.pickup_at.split('T')[1]}</div><div class="detail-field"><small>Canal y modalidad</small>${escapeHTML(order.channel)} · ${order.fulfillment === 'retiro' ? 'Retiro en local' : 'Despacho'}</div><div class="detail-field"><small>Fuente + ID</small>${escapeHTML(order.source)} / ${escapeHTML(order.source_id)}</div><div class="detail-field"><small>Pago</small>Confirmado (simulado)</div><div class="detail-field full"><small>Boleta</small>No disponible · documento real pendiente de integración. Fuente: Toteat, sin consultar.</div><div class="detail-field full"><small>Comentarios del pedido</small>${escapeHTML(order.comments || 'Sin comentarios')}</div></div><h3 class="section-label">Productos y estados</h3><p class="hint">Pendiente de marcado → marcado solicitado → marcado físico → entregado. Los cambios no afectan Toteat ni el stock.</p>${order.items.map(item => `<div class="detail-item"><div class="detail-item-header"><strong>${item.quantity} × ${itemLabel(item)} · ${escapeHTML(item.size)}</strong>${badge(item.status)}</div><p>SKU ${escapeHTML(item.sku)} · ID ítem ${escapeHTML(item.source_item_id)}${item.comments ? `<br>${escapeHTML(item.comments)}` : ''}</p><div class="actions">${next[item.status] && (state.role !== 'production' || ['pendiente'].includes(item.status)) ? `<button class="button primary" data-action="transition" data-item="${item.id}" data-status="${next[item.status]}">${actions[item.status]}</button>` : ''}${state.role !== 'production' && ['pendiente','marcado_solicitado','marcado'].includes(item.status) ? `<button class="button danger" data-action="transition" data-item="${item.id}" data-status="cancelado">Cancelar ítem</button>` : ''}${state.role !== 'production' && item.status !== 'pendiente' ? `<button class="button secondary" data-action="reverse" data-item="${item.id}" data-status="pendiente">Revertir a pendiente</button>` : ''}</div></div>`).join('')}<h3 class="section-label">Historial · ${history.length} registros</h3><p class="hint">Responsable declarado, sin identidad autenticada. Fechas mostradas en hora de Chile.</p>${history.map(event => `<article class="history-entry"><strong>${escapeHTML(event.action === 'estado' || event.action === 'reversion' ? `${labels[event.before.status]} → ${labels[event.after.status]}` : event.action === 'creado' ? 'Pedido creado' : 'Pedido corregido')} · ${escapeHTML(event.actor)}</strong><p>${escapeHTML(event.reason)}</p><time>${escapeHTML(new Intl.DateTimeFormat('es-CL',{dateStyle:'medium',timeStyle:'short',timeZone:state.board.timezone}).format(new Date(event.occurred_at)))}</time><details><summary>Ver detalle del cambio</summary><pre>${escapeHTML(JSON.stringify({antes:event.before,después:event.after},null,2))}</pre></details></article>`).join('')}`;
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
  const product = state.board.catalog.find(p => p.sku === item.sku) || state.board.catalog[0];
  item = {...product,...item};
  return `<fieldset class="form-item"><legend class="small-label">Ítem ${index}</legend><div class="form-item-heading"><span>${locked ? `Estado actual: ${escapeHTML(labels[item.status])}. Se conserva al guardar.` : 'Nuevo ítem · pendiente de marcado'}</span>${!locked ? '<button type="button" class="text-button" data-action="remove-item">Quitar borrador</button>' : ''}</div><div class="form-grid">${field('ID ítem','source_item_id',item.source_item_id || String(index),'text',`required maxlength="80" ${locked ? 'readonly' : ''}`)}${selectField('Producto entero demo','sku',state.board.catalog.map(p => [p.sku,`${p.flavor} · ${p.size}`]),item.sku)}${field('Cantidad','quantity',item.quantity || 1,'number','required min="1" max="999" step="1"')}${field('Producto','flavor',item.flavor || '','text','required readonly')}${field('Formato','size',item.size || '','text','required readonly')}<input type="hidden" name="kind" value="torta"><label class="field full">Comentario del ítem<input name="item_comments" value="${escapeHTML(item.comments || '')}" maxlength="500"></label></div></fieldset>`;
}
function showForm(order = null) {
  state.editing = order;
  const selectedDate = state.date || state.board.today;
  const body = `<form id="order-form"><div id="form-error" class="error hidden" role="alert"></div><p class="hint">Usa nombres inventados e IDs DEMO-. Esta versión es un laboratorio local. Los campos por ítem se estructuran aquí; el futuro mapeo Toteat aún debe verificarse.</p><div class="form-grid">${selectField('Fuente de ejemplo','source',[['demo','Demo manual'],['demo-toteat','Demo de origen Toteat (simulado)']],order?.source || 'demo')}${field('ID fuente único','source_id',order?.source_id || `DEMO-${Date.now()}`,'text',`required maxlength="80" ${order ? 'readonly' : ''}`)}${selectField('Canal','channel',[['Web/Mercat','Web / Mercat'],['Presencial','Presencial'],['Instagram','Instagram']],order?.channel || 'Web/Mercat')}${field('Cliente ficticio','customer',order?.customer || 'Cliente demo ','text','required maxlength="160"')}${field('Fecha y hora de retiro / entrega','pickup_at',order?.pickup_at || `${selectedDate}T12:00`,'datetime-local','required')}${selectField('Modalidad','fulfillment',[['retiro','Retiro en local'],['despacho','Despacho']],order?.fulfillment || 'retiro')}<label class="field full">Comentarios del pedido<input name="comments" value="${escapeHTML(order?.comments || '')}" maxlength="1000" placeholder="Indicaciones ficticias, despacho, mensaje de torta…"></label>${order ? '<label class="field full">Motivo de la corrección<input name="reason" required maxlength="500" placeholder="Qué cambió y por qué"></label>' : ''}<label class="check-field full"><input name="payment_confirmed" type="checkbox" required ${order?.payment_confirmed ? 'checked' : ''}><span>Confirmo un pago simulado verificado. En la operación real, un comprobante de transferencia no basta para acreditar el abono bancario.</span></label></div><h3 class="section-label">Productos del pedido</h3>${order ? '<p class="hint">Para cambiar un ítem que ya avanzó, primero reviértelo a Pendiente con motivo. Los ítems guardados se cancelan; no se borran.</p>' : ''}<div id="form-items">${(order?.items || [{}]).map((item,index) => itemForm(item,index+1)).join('')}</div><button class="button secondary" type="button" data-action="add-item">＋ Agregar ítem</button><p class="hint">Fecha local de retiro: America/Santiago. Responsable declarado: ${escapeHTML($('#actor').value)}.</p></form>`;
  modal(order ? 'Corregir pedido' : 'Nuevo pedido demo', 'Pago simulado confirmado · historial permanente · sin movimientos de stock', body, '<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="order-form">Guardar pedido ficticio</button>');
  if (order) $('#order-form [name=source]').disabled = true;
}

function readOrder(form) {
  const value = name => form.elements.namedItem(name).value;
  return {source:value('source'),source_id:value('source_id'),channel:value('channel'),customer:value('customer'),pickup_at:value('pickup_at'),fulfillment:value('fulfillment'),comments:value('comments'),payment_confirmed:form.elements.namedItem('payment_confirmed').checked,is_demo:true,items:[...form.querySelectorAll('.form-item')].map(row => {const get = name => row.querySelector(`[name=${name}]`).value; return {source_item_id:get('source_item_id'),sku:get('sku'),quantity:Number(get('quantity')),flavor:get('flavor'),size:get('size'),kind:get('kind'),comments:get('item_comments')};})};
}
async function saveOrder(form, button) {
  if (state.saving) return;
  state.saving = true;
  try {
    button.disabled = true;
    const order = readOrder(form);
    const body = {actor:actor(), order, ...(state.editing ? {version:state.editing.version,reason:form.elements.namedItem('reason').value} : {})};
    await api(state.editing ? `/api/orders/${state.editing.id}` : '/api/orders', state.editing ? 'PUT' : 'POST', body);
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
  return stock.rows.map((row,index) => `<article class="stock-row"><div><strong>${escapeHTML(row.flavor)}</strong><small>${escapeHTML(row.size)}</small></div><div><small>Físico</small><strong>${row.physical ?? 'Desconocido'}</strong></div><div><small>** Reserva vitrina</small><strong>${row.reserved}</strong></div><div><small>Venta entera</small><strong>${row.available ?? 'Desconocido'}</strong></div><button class="text-button" data-action="stock-row" data-index="${index}">Editar conteo</button></article>`).join('') || '<p class="hint">Aún no hay conteos. Disponibilidad desconocida; registra solo cantidades ficticias.</p>';
}
function renderOperations() {
  const board = state.board;
  $('#alert-count').textContent = board.notifications.length;
  $('#alerts').innerHTML = board.notifications.map(n => `<article class="alert-row"><div><strong>${n.quantity} × ${escapeHTML(n.flavor)} · ${escapeHTML(n.size)}</strong><p>${escapeHTML(n.customer)} · retiro ${escapeHTML(n.pickup_at.replace('T',' '))}</p><small>Solicitó ${escapeHTML(n.actor)} · ${escapeHTML(new Intl.DateTimeFormat('es-CL',{dateStyle:'short',timeStyle:'short',timeZone:board.timezone}).format(new Date(n.occurred_at)))}</small></div><button class="button secondary" data-action="alert" data-item="${n.item_id}">Revisar solicitud</button></article>`).join('') || '<p class="hint">Sin solicitudes de marcado abiertas. Las nuevas solicitudes aparecen aquí al actualizar.</p>';
  $('#production-totals').innerHTML = state.productionScope ? `<div class="base-totals">${board.production.totals.map(b => `<article><strong>${escapeHTML(b.quantity)}</strong><span>${escapeHTML(b.name)}</span><small>${escapeHTML(b.size)}</small></article>`).join('') || '<p class="hint">No hay bases calculables en este período.</p>'}</div>${board.production.missing.length ? `<div class="recipe-warning">Receta/cantidad pendiente: ${board.production.missing.map(p => `${p.quantity} × ${escapeHTML(p.flavor)} (${escapeHTML(p.size)})`).join(' · ')}. No se incluyen en las bases calculadas.</div>` : ''}<p class="hint">Suma exacta por base y tamaño sobre todas las encargadas del rango. 0,5 permanece como media base, sin redondeo automático.</p>` : '<p class="empty compact">Elige un alcance para calcular. El registro aún no identifica qué bases ya están elaboradas.</p>';
  $('#recipes').innerHTML = board.catalog.map(p => `<article class="recipe-row"><div><strong>${escapeHTML(p.flavor)} · ${escapeHTML(p.size)}</strong><p>${p.bases ? p.bases.map(b => `${escapeHTML(b.quantity)} × ${escapeHTML(b.name)}`).join(' + ') : 'Receta/cantidad pendiente'}</p></div><button class="text-button" data-action="recipe" data-sku="${escapeHTML(p.sku)}">Editar bases</button></article>`).join('');
  const stats = board.analytics;
  if (stats) $('#analytics').innerHTML = `<div class="analytics-grid"><article><strong>${stats.orders}</strong><span>Pedidos del rango</span><small>Incluye finalizados y cancelados</small></article><article><strong>${stats.units}</strong><span>Enteros encargados</span><small>Unidades no canceladas, incluye entregadas</small></article><article><strong>${stats.mark_minutes === null ? 'Sin datos' : `${stats.mark_minutes} min`}</strong><span>Solicitud → marcado</span><small>Promedio de ${stats.mark_samples} confirmaciones demo</small></article><article><strong>${stats.on_time_total ? `${stats.on_time}/${stats.on_time_total}` : 'Sin datos'}</strong><span>Entregas demo a tiempo</span><small>Ítems entregados: hora registrada ≤ retiro</small></article></div><div class="channel-breakdown">${Object.entries(stats.channels).map(([channel,count]) => `<span>${escapeHTML(channel)} <strong>${count} pedidos</strong></span>`).join('')}</div><p class="hint">Agrupado por fecha programada de retiro/entrega. Tiempos medidos con registros de la demo; no son resultados reales. Productos: ver resumen por sabor/formato. Sin datos de ingresos ni márgenes.</p>`;
}
function baseForm(base = {}) {
  return `<div class="base-row">${field('Base','base_name',base.name || '','text','required maxlength="80"')}${field('Cantidad por entero','base_quantity',base.quantity || '1','number','required min="0.001" max="999" step="0.001"')}<button class="text-button" type="button" data-action="remove-base">Quitar</button></div>`;
}
function showRecipe(product) {
  state.recipeEditing = product;
  modal('Editar bases', `${product.flavor} · ${product.size} · valores por producto entero`, `<form id="recipe-form"><div id="form-error" class="error hidden" role="alert"></div><label class="check-field"><input type="checkbox" name="recipe_pending" ${product.bases === null ? 'checked' : ''}>Receta pendiente de confirmar</label><div id="recipe-bases">${(product.bases || []).map(baseForm).join('')}</div><button class="button secondary" type="button" data-action="add-base">Agregar base</button><label class="field">Motivo de edición<input name="reason" required maxlength="500"></label><p class="hint">Las cantidades se guardan como decimales exactos. El tamaño queda ligado al producto; bases de 10 y 20 personas permanecen separadas. Amor hojarasca: valor inicial provisional editable. Sin rellenos en esta etapa.</p></form>`,'<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="recipe-form">Guardar receta demo</button>','CONFIGURACIÓN DE PRODUCCIÓN');
}
async function saveRecipe(form,button) {
  if (state.saving) return;
  state.saving = true; button.disabled = true;
  try {
    const bases = form.elements.namedItem('recipe_pending').checked ? null : [...form.querySelectorAll('.base-row')].map(row => ({name:$('[name=base_name]',row).value,quantity:$('[name=base_quantity]',row).value}));
    await api('/api/recipes','PUT',{sku:state.recipeEditing.sku,bases,version:state.recipeEditing.version,actor:actor(),reason:form.elements.namedItem('reason').value});
    $('#modal').close(); await loadBoard(); toast('Receta demo actualizada con historial.');
  } catch(error) { showFormError(error.message); }
  finally { state.saving = false; button.disabled = false; }
}
function showStockForm(row = null) {
  state.stockEditing = row;
  const product = state.board.catalog.find(p => p.flavor === row?.flavor && p.size === row?.size) || state.board.catalog[0];
  modal('Stock y reserva para vitrina', 'Conteo ficticio actual · decisión manual · no ajusta Toteat', `<form id="stock-form"><div id="form-error" class="error hidden" role="alert"></div><div class="form-grid">${row ? '' : selectField('Producto del conteo','stock_product',state.board.catalog.map(p => [p.sku,`${p.flavor} · ${p.size}`]),product.sku)}${field('Sabor','flavor',product.flavor,'text','required readonly')}${field('Tamaño','size',product.size,'text','required readonly')}${field('Físico entero (vacío = desconocido)','physical',row?.physical ?? '','number','min="0" max="999" step="1"')}${field('** Enteras reservadas para vitrina','reserved',row?.reserved ?? 0,'number','required min="0" max="999" step="1"')}<label class="field full">Motivo<input name="reason" required maxlength="500" placeholder="Ej.: conteo ficticio, reservar 2 para vitrina"></label></div><p class="hint">Tú eliges 0, 1 o X enteras para trozar. No existe una regla automática de última torta. Disponibles = físico − reserva; nunca se restan encargos aquí. Sin físico conocido, la disponibilidad sigue desconocida.</p></form>`, '<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="stock-form">Guardar conteo demo</button>','CONTEO Y RESERVA MANUAL');
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
    $('#form-items').insertAdjacentHTML('beforeend', itemForm({},index));
  }
  if (action === 'remove-item') button.closest('.form-item').remove();
  if (action === 'stock-row') showStockForm(state.board.stock.rows[Number(button.dataset.index)]);
  if (action === 'recipe') showRecipe(state.board.catalog.find(p => p.sku === button.dataset.sku));
  if (action === 'add-base') $('#recipe-bases').insertAdjacentHTML('beforeend', baseForm());
  if (action === 'remove-base') button.closest('.base-row').remove();
  if (action === 'alert') {
    const notice = state.board.notifications.find(n => n.item_id === Number(button.dataset.item));
    state.date = notice.pickup_at.split('T')[0]; state.period = 'day'; $('#period').value = 'day'; $('#custom-range').classList.add('hidden'); $('#previous').disabled = false; $('#next').disabled = false;
    if (await loadBoard()) await showDetail(notice.order_id);
  }
  if (action === 'copy') await copyMessage();
  if (action === 'seed-date') {state.date = state.board.seed_date; await loadBoard();}
  if (button.dataset.filter) {selectFilter(button.dataset.filter); renderOrders();}
});
document.addEventListener('submit', async event => { if (!['order-form','transition-form','stock-form','recipe-form'].includes(event.target.id)) return; event.preventDefault(); const button = document.querySelector(`[type=submit][form=${event.target.id}]`); if (event.target.id === 'order-form') await saveOrder(event.target,button); else if (event.target.id === 'recipe-form') await saveRecipe(event.target,button); else if (event.target.id === 'stock-form') await saveStock(event.target,button); else await saveTransition(event.target,button); });
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
$('#role').addEventListener('change',event => {state.role = event.target.value; document.body.classList.toggle('production-role',state.role === 'production'); $('#actor').value = state.role === 'production' ? 'Jefa producción demo' : 'Esteban demo';});
$('#production-scope').addEventListener('change',event => {state.productionScope = event.target.value; if (state.board) renderOperations();});
document.addEventListener('change',event => {if (event.target.name === 'stock_product') { const p = state.board.catalog.find(p => p.sku === event.target.value); $('#stock-form [name=flavor]').value = p.flavor; $('#stock-form [name=size]').value = p.size; return; } if (event.target.name !== 'sku') return; const row = event.target.closest('.form-item'); if (!row) return; const product = state.board.catalog.find(p => p.sku === event.target.value); $('[name=flavor]',row).value = product.flavor; $('[name=size]',row).value = product.size;});
$('#search').addEventListener('input',event => {state.search = event.target.value; if (state.board) renderOrders();});
$('#whatsapp').addEventListener('click',() => { if (!state.board) return; modal('Texto para WhatsApp','Incluye todo el período elegido. Los filtros de búsqueda y estado no alteran este resumen.',`<textarea id="whatsapp-text" class="message-preview" readonly aria-label="Mensaje ficticio para copiar">${escapeHTML(state.board.whatsapp)}</textarea><p class="hint">PRODUCTOS POR MARCAR: pendientes y solicitados. RESUMEN ENCARGADAS: también incluye marcados. Stock actual separado de los encargos. ** = enteras reservadas para trozar/vitrina, decididas manualmente.</p>`,'<button class="button secondary" data-action="close">Cerrar</button><button class="button primary" data-action="copy">Copiar texto</button>','PREVIA · NO ENVÍA MENSAJES'); });
loadBoard();
