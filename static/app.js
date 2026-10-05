'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {board: null, date: '', period: 'day', filter: 'all', deliveryFilter: 'all', search: '', editing: null, detail: null, transition: null, request: 0, end: '', panelRequest: 0, saving: false, role: '', productionScope: '', scope:'operations', view:'agenda', partner:null, user:null, module:'home', moduleSection:'',commentDisclosure:new Map()};
const modules={
  home:{name:'Inicio',icon:'⌂',sections:[]},
  people:{name:'Gestión de personas',icon:'♧',sections:['Colaboradores','Contratos y anexos','Permisos','Vacaciones','Licencias médicas','Liquidaciones','Incorporaciones y desvinculaciones','Capacitaciones','Vencimientos']},
  documents:{name:'Documentos y procedimientos',icon:'▤',sections:['Formatos','Checklists','Reglamento interno','Protocolos','Manuales','Control de versiones','Constancias de lectura']},
  suppliers:{name:'Gestión de proveedores',icon:'▧',sections:['Proveedores','Facturas','Pagos','Programación semanal de pagos']},
  cakes:{name:'Pedidos de tortas',icon:'▦',sections:[]},
  delivery:{name:'Despacho y reparto',icon:'↗',sections:['Montos diarios','Asignación de pedidos','Seguimiento GPS','Tarifas de reparto','Recaudación por cliente','Liquidación de repartos','Repartidores y zonas']},
  shifts:{name:'Planificación de turnos',icon:'◷',sections:['Calendario de turnos','Asignación de equipo','Horas planificadas','Cambios y ausencias']},
  attendance:{name:'Control de asistencia',icon:'◴',sections:['Integración GeoVictoria','Marcaciones','Horas trabajadas','Diferencias y revisión']},
  transfers:{name:'Conciliación de transferencias',icon:'⇄',sections:['Transferencias del local','Cierres diarios','Estado del bot','Conciliación','Comprobantes','Historial']},
  inventory:{name:'Inventario y abastecimiento',icon:'▤',sections:['Existencias','Movimientos','Pedidos sugeridos','Compras','Mermas','Reserva para vitrina']},
  production:{name:'Producción',icon:'◷',sections:['Planificación','Recetas y bases']},
  catalog:{name:'Catálogo y proveedores',icon:'◇',sections:['Productos','Formatos','Proveedores']},
  reports:{name:'Analítica y reportes',icon:'▥',sections:['Panel ejecutivo','Rentabilidad por producto','Venta conjunta','Demanda por día y horario','Precios y promociones','Canales y reparto','Inventario y mermas','Clientes y recompra','Productividad operativa','Proyección de demanda','Calidad de datos','Power BI','Reportes']},
  costs:{name:'Costos y rentabilidad',icon:'◈',sections:['Costos de recetas','Márgenes por producto','Evolución de costos']},
  customers:{name:'Gestión de clientes',icon:'○',sections:['Directorio','Historial de pedidos','Preferencias de entrega']},
  maintenance:{name:'Activos y mantenimiento',icon:'◇',sections:['Equipos','Mantenciones preventivas','Incidencias']}
};
const initialView = new URLSearchParams(location.search);
const localThemePreview=['127.0.0.1','localhost'].includes(location.hostname)&&['b','c'].includes(initialView.get('theme'))?initialView.get('theme'):'';
if(localThemePreview)document.documentElement.dataset.theme=localThemePreview;
for (const [key, allowed] of [['scope',['operations','tests','archived']],['view',['agenda','inbox','cancelled','immediate']],['period',['day','week','biweekly','month','custom']]]) {
  if (allowed.includes(initialView.get(key))) state[key]=initialView.get(key);
}
if(modules[initialView.get('module')])state.module=initialView.get('module');
if(modules[state.module].sections.includes(initialView.get('section')))state.moduleSection=initialView.get('section');
for (const key of ['date','end']) if (/^\d{4}-\d{2}-\d{2}$/.test(initialView.get(key)||'')) state[key]=initialView.get(key);
$('#record-scope').value=state.scope; $('#period').value=state.period; $('#end-date').value=state.end;
$('#custom-range').classList.toggle('hidden',state.period!=='custom');
$('#previous').disabled=state.period==='custom'; $('#next').disabled=state.period==='custom';
const paymentLabel = order => order.toteat_schedule ? (order.toteat_schedule.payment.state === 'discount_settled' ? 'Saldado con descuento' : 'Pago registrado en Toteat') : order.manual_scheduling ? (order.manual_scheduling.payment_status==='unpaid' ? (order.scheduling_status==='draft'?'Sin pago · pendiente de autorización':'Agendado sin pago') : 'Pago verificado por socio · registro manual') : 'Pago simulado confirmado';
const scheduleWarnings = {telefono_pendiente:'Teléfono pendiente',telefono_por_revisar:'Revisar teléfono',fecha_pasada:'La fecha indicada ya pasó',anio_inferido:'Año inferido de la fecha de la venta'};
const receiptLinks = order => !can('receipt') ? 'Disponible para socios y caja' : order.receipt?.status === 'verified' ? `<button class="button secondary" data-action="receipt-preview" data-id="${order.id}">Ver boleta PDF</button> <a class="button secondary" href="/api/orders/${order.id}/receipt?download=1" download>Descargar para enviar</a>` : 'Boleta aún no recibida';
const labels = {marcado_solicitado:'Marcado solicitado',pendiente:'Pendiente de marcado', solicitado:'Solicitado a producción (histórico)', marcado:'Marcado físico', entregado:'Entregado', cancelado:'Cancelado'};
const timingLabels = {scheduled:'Programada',immediate:'Inmediata · entrega confirmada',unclassified:'Tipo de entrega por confirmar'};
const next = {pendiente:'marcado_solicitado', marcado_solicitado:'marcado', marcado:'entregado'};
const actions = {pendiente:'Solicitar marcado', marcado_solicitado:'Ya está marcado', marcado:'Registrar entrega'};
let authGeneration=0;
const can = permission => Boolean(state.user?.permissions.includes(permission));
let toastTimer;
let noticeSequence=0, acceptedNoticeSequence=0, noticeScope='', noticeTracker=null;
let pendingNotices=[], noticePolling=false, notificationAudio=null, soundEnabled=false;


async function api(path, method = 'GET', body) {
  const generation=authGeneration;
  const response = await fetch(path, {method, headers: method === 'GET' ? {} : {'Content-Type':'application/json','X-ERP-Local':'1'}, body: body ? JSON.stringify(body) : undefined});
  const data = await response.json();
  if(!path.startsWith('/api/partner/') && generation!==authGeneration) throw new Error('La sesión cambió.');
  if(response.status===401 && !path.endsWith('/login')) lockAccess('Tu sesión terminó. Ingresa nuevamente.');
  if (!response.ok) throw new Error(data.error || 'No se pudo completar la operación.');
  return data;
}
function actor() {
  if(!state.user) throw new Error('Inicia sesión para continuar.');
  return state.user.username;
}
function lockAccess(message='Ingresa con tu cuenta para continuar.') {
  window.OvejaFees?.reset();
  authGeneration++;state.request++;state.panelRequest++;
  state.user=null;state.partner=null;state.board=null;state.detail=null;state.editing=null;
  state.transition=null;state.sosContext=null;state.sosReview=null;state.commentDisclosure.clear();
  pendingNotices=[];noticeTracker=null;noticeScope='';
  document.body.classList.add('auth-locked');
  $('#access-message').textContent=message;
  $('#modal').close();$('#modal-content').replaceChildren();
  for(const id of ['orders','summary','alerts','recipes','analytics','catalog-products','toteat-orders','stock-rows','production-totals','notification-list','attention-list','attention-counts','backup-state','reader-warning','account-name','account-role','partner-session-state','tests-in-period','transfer-results','home-workspace','delivery-results','delivery-sync-note']) $('#'+id)?.replaceChildren();
  $('#toast').classList.add('hidden');
  $('#access-form').reset();
}
async function startAccess() {
  await loadPartnerSession();
  if(state.user) await loadBoard();
}
async function loginAccess(event) {
  event.preventDefault();const form=event.target,button=$('#access-submit');
  if(button.disabled)return;
  button.disabled=true;$('#access-error').classList.add('hidden');
  try {
    await api('/api/partner/login','POST',{username:form.elements.username.value,password:form.elements.password.value});
    form.reset();await startAccess();
  } catch(error){$('#access-error').textContent=error.message;$('#access-error').classList.remove('hidden');}
  finally{button.disabled=false;}
}
function toast(text) {
  clearTimeout(toastTimer); $('#toast').textContent = text; $('#toast').classList.remove('hidden');
  toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), 4500);
}
function dateLabel(value, options = {}) { return new Intl.DateTimeFormat('es-CL', {weekday:'long', day:'numeric', month:'long', ...options}).format(new Date(value + 'T12:00:00')); }
function badge(status) { const icon={pendiente:'◷',solicitado:'◎',marcado_solicitado:'◎',marcado:'✓',entregado:'✓',cancelado:'⊘'}[status]||'•';return `<span class="badge ${escapeHTML(status)}"><span aria-hidden="true">${icon}</span>${escapeHTML(labels[status])}</span>`; }
function itemLabel(item) { return escapeHTML(item.flavor); }
function modal(title, subtitle, content, footer = '', eyebrow = 'PEDIDO FICTICIO') {
  state.panelRequest++;
  $('#modal-content').innerHTML = `<div class="modal-header"><div><div class="eyebrow">${escapeHTML(eyebrow)}</div><h2 id="modal-title">${escapeHTML(title)}</h2><p>${escapeHTML(subtitle)}</p></div><button class="close-button" data-action="close" aria-label="Cerrar panel">×</button></div><div class="modal-body">${content}</div>${footer ? `<div class="modal-footer">${footer}</div>` : ''}`;
  if (!$('#modal').open) $('#modal').showModal();
  $('#modal').scrollTop = 0;
}
function showFormError(message) { const box = $('#form-error'); if (box) { box.textContent = message; box.classList.remove('hidden'); box.scrollIntoView({block:'nearest'}); } else toast(message); }

async function loadBoard() {
  if(!state.user)return false;
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
    rememberView(); renderOrders(); renderOperations(); await applyNotifications(board.notifications,board.notification_scope,noticeRequest); return true;
  } catch (error) { if (request !== state.request) return false; $('#load-error').textContent = 'No se pudo actualizar. ' + error.message; $('#load-error').classList.remove('hidden'); return false; }
}

function renderModules() {
  const showFees=state.module==='delivery'&&['','Montos diarios','Tarifas de reparto'].includes(state.moduleSection);
  if(!showFees)window.OvejaFees?.leave();
  const module=modules[state.module];
  const moduleButton=key=>{const value=modules[key];return `<button type="button" class="module-tab ${state.module===key?'selected':''}" data-module="${key}" aria-current="${state.module===key?'page':'false'}"><span aria-hidden="true">${value.icon}</span><span>${escapeHTML(value.name)}</span>${!['home','cakes','transfers','delivery'].includes(key)?'<small>En preparación</small>':''}</button>`;};
  const groups=[['Operación',['home','cakes','delivery','production','inventory']],['Administración',['transfers','suppliers','catalog','costs','reports']],['Equipo y gestión',['people','shifts','attendance','documents','customers','maintenance']]];
  $('#module-sidebar').innerHTML=groups.map(([label,keys])=>`<div class="module-group-label">${label}</div>${keys.map(moduleButton).join('')}`).join('');
  $('#module-mobile').innerHTML=`<details><summary>${escapeHTML(module.name)} <small>Cambiar módulo</small></summary><div class="module-mobile-options">${groups.flatMap(([,keys])=>keys).map(moduleButton).join('')}</div></details>`;
  $('#tortas-workspace').classList.toggle('hidden',state.module!=='cakes');
  $('#module-placeholder').classList.toggle('hidden',state.module==='cakes');
  $('#breadcrumb-module').textContent=module.name;
  $('#home-workspace').classList.add('hidden');
  $('#transfer-workspace').classList.add('hidden');
  $('#delivery-workspace').classList.add('hidden');
  $('#module-placeholder .module-empty').classList.remove('hidden');
  if(state.module==='cakes')return;
  if(state.module==='home'){renderHome();return;}
  if(!module.sections.includes(state.moduleSection))state.moduleSection=module.sections[0]||module.name;
  $('#module-title').textContent=module.name;
  $('#module-placeholder .module-subtitle').textContent=module.proposed?'Módulo sugerido · alcance por definir.':'Estructura preparada · datos e integración pendientes.';
  $('#module-section-title').textContent=state.moduleSection;
  $('#module-placeholder .module-empty p').textContent=state.module==='suppliers'&&state.moduleSection==='Programación semanal de pagos'?'Próximamente: seleccionar facturas de la semana y copiar un resumen para WhatsApp con proveedor, folio, vencimiento, monto y total.':'Este espacio todavía no tiene datos ni operaciones habilitadas.';
  if(state.module==='transfers' && ['Transferencias del local','Cierres diarios','Estado del bot'].includes(state.moduleSection)){
    $('#module-placeholder .module-empty').classList.add('hidden');$('#transfer-workspace').classList.remove('hidden');
    $('#module-placeholder .module-subtitle').textContent='Transferencias enviadas por caja al bot Ovejita · lectura y seguimiento.';
    loadTransfers();
  }
  if(showFees){
    $('#module-placeholder .module-empty').classList.add('hidden');$('#delivery-workspace').classList.remove('hidden');
    $('#module-placeholder .module-subtitle').textContent='Tarifas de reparto por día y acumulado mensual.';
    window.OvejaFees?.enter();
  }
  $('#module-subtabs').innerHTML=module.sections.map(section=>`<button class="${section===state.moduleSection?'selected':''}" data-module-section="${escapeHTML(section)}" aria-pressed="${section===state.moduleSection}">${escapeHTML(section)}</button>`).join('');
}
document.addEventListener('click',event=>{
  const moduleButton=event.target.closest('[data-module]');
  const sectionButton=event.target.closest('[data-module-section]');
  if(!state.user || (!moduleButton&&!sectionButton))return;
  if(moduleButton){state.module=moduleButton.dataset.module;state.moduleSection='';}
  else state.moduleSection=sectionButton.dataset.moduleSection;
  renderModules();rememberView();
});
function rememberView() {
  const params=new URLSearchParams({module:state.module,view:state.view,scope:state.scope,period:state.period,date:state.date});
  if(state.period==='custom')params.set('end',state.end);
  if(state.module!=='cakes' && state.moduleSection)params.set('section',state.moduleSection);
  if(state.module==='delivery'&&window.OvejaFees?.month)params.set('month',window.OvejaFees.month);
  if(localThemePreview)params.set('theme',localThemePreview);
  history.replaceState(null,'',`${location.pathname}?${params}`);
}
function renderWorkflow() {
  document.body.classList.toggle('inbox-view',state.view!=='agenda');
  for(const button of document.querySelectorAll('[data-view]')) {
    const active=button.dataset.view===state.view;
    button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));
  }
  $('#inbox-tab-count').textContent=state.board.toteat?.pending_orders||0;
  const tests=state.board.test_order_count||0;
  $('#tests-in-period').classList.toggle('hidden',state.scope!=='operations'||!tests||state.board.operating_mode!=='toteat-local');
  $('#tests-in-period').innerHTML=`Hay ${tests} ${tests===1?'pedido de prueba agendado':'pedidos de prueba agendados'} en este período. Están separados de la operación. <button class="text-button" data-action="view-tests">Ver simulaciones del período →</button>`;
  $('#record-scope-wrap').classList.toggle('hidden',state.view!=='agenda'||state.board.operating_mode!=='toteat-local');
}
function renderOrders() {
  if (!state.board) return;
  const orders = state.board.orders.filter(order => {
    if(order.source_alert?.resolution==='cancel')return false;
    const matchStatus = state.filter === 'all' || order.items.some(item => state.filter === 'to-mark' ? ['pendiente','marcado_solicitado'].includes(item.status) : state.filter === 'marked' ? item.status === 'marcado' : ['entregado','cancelado'].includes(item.status));
    const searchable = [order.customer, order.source_id, order.channel, ...order.items.flatMap(item => [item.flavor,item.size,item.sku])].join(' ').toLocaleLowerCase('es');
    return (state.deliveryFilter === 'all' || order.delivery_timing === state.deliveryFilter) && matchStatus && searchable.includes(state.search.toLocaleLowerCase('es'));
  });
  const cards = orders.map(order => {
    const [day, hour] = order.pickup_at.split('T');
    const initial = order.customer.replace(/^Cliente demo\s*/, '').slice(0,2).toUpperCase();
    return `<article class="order-card" data-order-id="${order.id}"><div class="order-header"><div class="avatar" aria-hidden="true">${escapeHTML(initial)}</div><div class="order-identity"><strong>${escapeHTML(order.customer)}</strong><div class="order-meta"><span>${escapeHTML(order.toteat_schedule ? 'Comanda '+order.toteat_schedule.order_id : order.source_id)}</span><span>·</span><span class="channel">${escapeHTML(order.channel)}</span><span class="timing-label">${escapeHTML(timingLabels[order.delivery_timing])}</span>${order.is_simulation ? '<span class="test-badge">PRUEBA</span>' : ''}</div></div><div class="order-time"><strong>${escapeHTML(dateLabel(day,{weekday:'long',month:'short'}))} · ${hour}</strong><small>${order.fulfillment === 'retiro' ? 'Retiro en local' : 'Despacho'}</small></div></div>${sourceAlertHTML(order.source_alert)}<div>${order.items.map(item => `<div class="item-row"><span class="quantity">${item.quantity}×</span><div class="item-info"><strong>${itemLabel(item)}</strong><small>${escapeHTML(item.size)} · ${escapeHTML(item.sku)}</small></div>${badge(item.status)}</div>`).join('')}</div><div class="order-footer">${order.is_simulation && can('simulate') ? `<button class="text-button" data-action="${order.simulation_archived ? 'restore-test' : 'archive-test'}" data-id="${order.id}">${order.simulation_archived ? 'Restaurar prueba' : 'Retirar prueba'}</button>` : ''}<span class="payment-pill">${order.manual_scheduling?.payment_status==='unpaid'?'◷':'✓'} ${escapeHTML(paymentLabel(order))}</span><button class="detail-button" data-action="detail" data-id="${order.id}" aria-label="Ver detalle de ${escapeHTML(order.customer)}">Ver pedido <span aria-hidden="true">↗</span></button></div></article>`;
  }).join('') || `<div class="empty"><strong>Todo despejado por aquí.</strong>No hay pedidos para esta fecha o filtro.${state.board.operating_mode !== 'toteat-local' && state.board.seed_date && state.board.seed_date !== state.date ? `<br><button class="button secondary" data-action="seed-date">Ver día de los ejemplos</button>` : ''}</div>`;
  $('#orders').innerHTML=orders.length?`<div class="orders-desktop-table"><table class="agenda-table"><thead><tr><th>Fecha y hora</th><th>Cliente / pedido</th><th>Torta / formato</th><th>Entrega</th><th>Estado</th><th><span class="sr-only">Detalle</span></th></tr></thead><tbody>${orders.map(order=>`<tr data-order-id="${order.id}"><td><strong>${escapeHTML(dateLabel(order.pickup_at.split('T')[0],{weekday:'short',month:'short'}))}</strong><small>${escapeHTML(order.pickup_at.split('T')[1])} · Chile</small></td><td><strong>${escapeHTML(order.customer)}</strong><small>${escapeHTML(order.toteat_schedule?'Comanda '+order.toteat_schedule.order_id:'Pedido #'+order.id)}</small><small>${escapeHTML(order.channel)}</small>${order.is_simulation?'<span class="test-badge">PRUEBA</span>':''}${order.is_simulation&&can('simulate')?`<button class="text-button" data-action="${order.simulation_archived?'restore-test':'archive-test'}" data-id="${order.id}">${order.simulation_archived?'Restaurar':'Retirar prueba'}</button>`:''}</td><td>${order.items.map(item=>`<div class="table-product"><strong>${item.quantity} × ${itemLabel(item)}</strong><small>${escapeHTML(item.size)}</small></div>`).join('')}</td><td>${order.fulfillment==='retiro'?'Retiro en local':'Delivery'}<small>${escapeHTML(timingLabels[order.delivery_timing])}</small><small>${escapeHTML(paymentLabel(order))}</small></td><td>${order.source_alert?`<strong>${escapeHTML(sourceAlertLabel(order.source_alert))}</strong>`:''}${order.items.map(item=>`<div class="table-status">${badge(item.status)}</div>`).join('')}</td><td><button class="detail-button" data-action="detail" data-id="${order.id}" aria-label="Ver pedido de ${escapeHTML(order.customer)}">↗</button></td></tr>`).join('')}</tbody></table></div><div class="orders-mobile-cards">${cards}</div>`:cards;

}

async function showDetail(id) {
  await loadPartnerSession();
  if(!state.user || !state.board)return;
  let order = state.board.orders.find(row => row.id === Number(id));
  if (!order) { try { order = await api(`/api/orders/${Number(id)}`); } catch(error) { toast(error.message); return; } }
  state.detail = order;
  modal(order.customer, `${order.source_id} · ${order.status_label}`, '<p class="hint">Cargando historial…</p>');
  try {
    const panelRequest = state.panelRequest;
    const history = await api(`/api/orders/${order.id}/history`);
    if (!$('#modal').open || panelRequest !== state.panelRequest) return;
    const content = `${sourceAlertHTML(order.source_alert)}${state.partner && order.source_alert ? '<button class="button secondary" data-action="source-alert">Revisar alerta de origen</button>' : ''}<div class="detail-grid"><div class="detail-field"><small>Retiro / entrega</small>${escapeHTML(dateLabel(order.pickup_at.split('T')[0]))} · ${order.pickup_at.split('T')[1]}</div><div class="detail-field"><small>Tipo de entrega</small>${escapeHTML(timingLabels[order.delivery_timing])}</div><div class="detail-field"><small>${order.toteat_schedule || order.manual_scheduling ? 'Teléfono' : 'Teléfono ficticio'}</small>${escapeHTML(order.customer_phone || 'No registrado')}</div><div class="detail-field"><small>Canal y modalidad</small>${escapeHTML(order.channel)} · ${order.fulfillment === 'retiro' ? 'Retiro en local' : 'Despacho'}</div><div class="detail-field"><small>Fuente + ID</small>${escapeHTML(order.source)} / ${escapeHTML(order.source_id)}</div><div class="detail-field"><small>Pago / cierre</small>${escapeHTML(paymentLabel(order))}</div><div class="detail-field full"><small>Boleta</small>${receiptLinks(order)}</div>${order.toteat_schedule?.warnings.length ? `<div class="detail-field full"><small>Datos por revisar</small>${order.toteat_schedule.warnings.map(w=>escapeHTML(scheduleWarnings[w]||w)).join(' · ')}</div>` : ''}${order.fulfillment==='despacho' ? `<div class="detail-field full"><small>Dirección de delivery</small>${escapeHTML(order.delivery_address||'Pendiente')}</div>` : ''}${order.customer_edited ? '<p class="hint full">Datos de entrega actualizados por un socio. El comentario original se conserva abajo.</p>' : ''}<div class="detail-field full source-comment"><small>Comentario original de Toteat · conservado</small>${escapeHTML(order.source_comment || order.comments || 'Sin comentarios')}</div>${order.manual_scheduling ? `<div class="detail-field full"><small>Nota del pedido manual</small>${escapeHTML(order.comments||'Sin nota')}<br><small>Verificación manual del pago</small>${escapeHTML(order.manual_scheduling.payment_evidence||'Sin pago verificado')}</div>` : ''}</div><h3 class="section-label">Productos y estados</h3><p class="hint">Pendiente de marcado → marcado solicitado → marcado físico → entregado. Los cambios no afectan Toteat ni el stock.</p>${order.items.map(item => `<div class="detail-item"><div class="detail-item-header"><strong>${item.quantity} × ${itemLabel(item)} · ${escapeHTML(item.size)}</strong>${badge(item.status)}</div><p>SKU ${escapeHTML(item.sku)} · ID ítem ${escapeHTML(item.source_item_id)}${item.comments ? `<br>${escapeHTML(item.comments)}` : ''}</p><div class="actions">${order.source_alert?.state!=='cancelled' && order.scheduling_status!=='draft' && next[item.status] && can({pendiente:'request_mark',marcado_solicitado:'confirm_mark',marcado:'deliver'}[item.status]) ? `<button class="button primary" data-action="transition" data-item="${item.id}" data-status="${next[item.status]}">${actions[item.status]}</button>` : ''}${order.source_alert?.state!=='cancelled' && order.scheduling_status!=='draft' && can('correct') && ['pendiente','marcado_solicitado','marcado'].includes(item.status) ? `<button class="button danger" data-action="transition" data-item="${item.id}" data-status="cancelado">Cancelar ítem</button>` : ''}${order.source_alert?.state!=='cancelled' && order.scheduling_status!=='draft' && can('correct') && item.status !== 'pendiente' ? `<button class="button secondary" data-action="reverse" data-item="${item.id}" data-status="pendiente">Revertir a pendiente</button>` : ''}</div></div>`).join('')}<h3 class="section-label">Historial · ${history.length} registros</h3><p class="hint">Los cambios nuevos registran la cuenta autenticada; los registros anteriores conservan su responsable original. Hora de Chile.</p>${history.map(event => `<article class="history-entry"><strong>${escapeHTML(event.action === 'estado' || event.action === 'reversion' ? `${labels[event.before.status]} → ${labels[event.after.status]}` : event.action === 'creado' ? 'Pedido creado' : event.action === 'agendado' ? 'Pedido agendado' : event.action === 'boleta_adjunta' ? 'Boleta adjunta' : event.action === 'datos_cliente' ? 'Cliente y entrega modificados por socio' : ({sos_creado:'Ingreso manual',agendado_sin_pago:'Agendamiento sin pago autorizado',vinculo_toteat:'Comanda vinculada por socio',pago_toteat_recibido:'Pago de Toteat recibido',conciliacion_distinta:'Coincidencia revisada: pedidos distintos'}[event.action]||'Pedido corregido'))} · ${escapeHTML(event.actor)}</strong><p>${escapeHTML(event.reason)}</p><time>${escapeHTML(new Intl.DateTimeFormat('es-CL',{dateStyle:'medium',timeStyle:'short',timeZone:state.board.timezone}).format(new Date(event.occurred_at)))}</time><details><summary>Ver detalle del cambio</summary><pre>${escapeHTML(JSON.stringify({antes:event.before,después:event.after},null,2))}</pre></details></article>`).join('')}`;
    modal(order.customer, `${order.source_id} · ${order.status_label}`, content, `<button class="button secondary" data-action="close">Cerrar</button>${order.scheduling_status==='draft' && state.partner ? '<button class="button primary" data-action="sos-authorize">Forzar agendamiento</button>' : ''}${can('customer') && order.delivery_timing==='scheduled' && order.items.some(i=>!['entregado','cancelado'].includes(i.status)) ? '<button class="button primary" data-action="edit-customer">Editar cliente y entrega · Socios</button>' : ''}${can('correct') && ['demo','demo-toteat'].includes(order.source) ? `<button class="button primary admin-only" data-action="edit" data-id="${order.id}">Corregir pedido</button>` : ''}`, order.is_demo ? 'PRUEBA · TICKET DE AGENDAMIENTO' : 'TICKET DE AGENDAMIENTO');
  } catch(error) { toast(error.message); }
}

async function loadPartnerSession() {
  try {
    const session=await api('/api/partner/session');state.partnersConfigured=session.configured;
    if(!session.user) {
      lockAccess();
      $('#access-setup').classList.toggle('hidden',session.configured);
      $('#access-form').classList.toggle('hidden',!session.configured);
      $('#access-message').textContent=session.configured?'Ingresa con tu cuenta personal.':'Configura la primera cuenta de socio en este equipo para comenzar.';
      return;
    }
    state.user=session.user;state.partner=session.partner;state.role=state.user.role;
    document.body.classList.remove('auth-locked');
    document.body.classList.toggle('restricted-role',state.role!=='partner');
    $('#account-name').textContent=state.user.name;
    $('#account-role').textContent=state.user.role_label;
    $('#sos-access').classList.toggle('hidden',!can('sos'));
    $('#partner-session-state').textContent=state.user.name+' · '+state.user.role_label;
  } catch {lockAccess('No se pudo comprobar el acceso. Reintenta en unos momentos.');}
}
async function showPartnerAccess(returnToCustomer=false) {
  await loadPartnerSession();
  if(!state.user)return;
  if(returnToCustomer && can('customer')){showCustomerForm();return;}
  modal('Mi cuenta',state.user.name,`<p>${escapeHTML(state.user.role_label)} · ${escapeHTML(state.user.username)}</p><p>Los cambios que realices quedan asociados a esta cuenta.</p>`,'<button class="button secondary" data-action="close">Volver</button><button class="button primary" data-action="partner-logout">Cerrar sesión</button>','ACCESO PERSONAL');
}
function showCustomerForm() {
  const order=state.detail;
  modal('Cliente y entrega','Los cambios quedan en el historial con tu cuenta de socio.',`<form id="customer-form"><div id="form-error" class="error hidden" role="alert"></div><div class="form-grid">${field('Nombre del cliente','customer',order.customer,'text','required maxlength="160"')}${field('Teléfono','customer_phone',order.customer_phone,'tel','required maxlength="25"')}${selectField('Modalidad','fulfillment',[['retiro','Retiro en local'],['despacho','Delivery']],order.fulfillment)}${field('Fecha y hora de entrega · Chile','pickup_at',order.pickup_at,'datetime-local','required')}<label class="field full">Dirección de delivery<input name="delivery_address" value="${escapeHTML(order.delivery_address)}" maxlength="500" placeholder="Calle, número, comuna y departamento si corresponde"></label><label class="field full">Motivo del cambio<input name="reason" required maxlength="500" placeholder="Ej.: cliente solicitó delivery y nueva hora"></label></div><p class="hint">El pago, la boleta, los productos y el comentario de Toteat se conservan. No se envía el cambio a Toteat.</p></form>`,'<button class="button secondary" data-action="back-detail">Cancelar</button><button class="button primary" type="submit" form="customer-form">Guardar cambios</button>','EDICIÓN DE SOCIO');
  syncDeliveryAddress();
}
function syncDeliveryAddress(){const form=$('#customer-form');if(form)form.elements.delivery_address.required=form.elements.fulfillment.value==='despacho';}
async function savePartnerForm(form,button){
  if(state.saving)return;state.saving=true;button.disabled=true;
  try{await api('/api/partner/login','POST',{username:form.elements.username.value,password:form.elements.password.value});form.reset();await loadPartnerSession();if(state.returnToSos){state.returnToSos=false;await showSosPanel();}else if(state.returnToCustomer)showCustomerForm();else $('#modal').close();}
  catch(error){showFormError(error.message);}finally{state.saving=false;button.disabled=false;}
}
async function saveCustomer(form,button){
  if(state.saving)return;state.saving=true;button.disabled=true;
  try{
    const customer=Object.fromEntries(['customer','customer_phone','pickup_at','fulfillment','delivery_address'].map(k=>[k,form.elements.namedItem(k).value]));
    const updated=await api(`/api/orders/${state.detail.id}/customer`,'PUT',{customer,reason:form.elements.reason.value,version:state.detail.version});
    state.detail=updated;state.date=updated.pickup_at.split('T')[0];state.view='agenda';
    state.scope=updated.is_simulation?'tests':'operations';$('#record-scope').value=state.scope;
    if(state.period==='custom'){state.end=state.date;$('#end-date').value=state.end;}
    await loadBoard();await showDetail(updated.id);toast('Cliente y entrega actualizados. Cambio registrado en el historial.');
  }catch(error){showFormError(error.message);}finally{state.saving=false;button.disabled=false;}
}

async function showSosPanel(){
  await loadPartnerSession();if(!state.partner){state.returnToSos=true;await showPartnerAccess(false);return;}
  try{
    const data=await api('/api/sos/orders');state.sosContext=data;
    const drafts=data.orders.filter(o=>o.scheduling_status==='draft');
    const body=`<p class="hint">Solo socios. Los ingresos sin pago esperan autorización y no se suman a producción hasta agendarlos.</p><h3 class="section-label">Por autorizar · ${drafts.length}</h3>${drafts.map(o=>`<article class="catalog-row"><div><strong>${escapeHTML(o.customer)}</strong><p>${escapeHTML(o.pickup_at.replace('T',' · '))} · Sin pago</p>${o.items.map(i=>`<p>${i.quantity} × ${itemLabel(i)} · ${escapeHTML(i.size)}</p>`).join('')}</div><button class="button secondary" data-action="detail" data-id="${o.id}">Revisar ingreso</button></article>`).join('')||'<p class="hint">Sin ingresos pendientes de autorización.</p>'}<h3 class="section-label">Posibles duplicados · ${data.reviews.length}</h3>${data.reviews.map((r,index)=>`<article class="catalog-row"><div><strong>Comanda ${escapeHTML(r.order_id)}</strong><p>${escapeHTML(r.customer)} · ${escapeHTML(r.pickup_at)}</p><p>No se agregó demanda: coincide con un pedido manual pendiente de vincular.</p><button class="button secondary" data-action="sos-review" data-index="${index}">Revisar vínculo</button></div></article>`).join('')||'<p class="hint">Sin coincidencias por revisar.</p>'}`;
    modal('Toma de pedido manual','Ingreso excepcional y conciliación de comandas.',body,'<button class="button secondary" data-action="close">Cerrar</button><button class="button primary" data-action="sos-new">Nuevo pedido manual</button>','SOLO SOCIOS');
  }catch(error){toast(error.message);}
}
function sosItem(item={}){
  return `<div class="sos-item form-grid">${selectField('Producto entero','sos_sku',state.board.catalog.map(p=>[p.sku,`${p.flavor} · ${p.size}`]),item.sku||state.board.catalog[0].sku)}${field('Cantidad','sos_quantity',item.quantity||1,'number','required min="1" max="999" step="1"')}<button type="button" class="text-button" data-action="sos-remove-item">Quitar producto</button></div>`;
}
function showSosForm(){
  state.sosRequestId=crypto.randomUUID();
  const sources=state.sosContext?.sources||[];
  modal('Nuevo pedido manual','Ingreso local de contingencia. No crea una venta en Toteat.',`<form id="sos-form"><div id="form-error" class="error hidden" role="alert"></div><div class="form-grid">${field('Cliente','customer','','text','required maxlength="160"')}${field('Teléfono','customer_phone','','tel','required maxlength="25"')}${field('Fecha y hora de entrega · Chile','pickup_at',`${state.date||state.board.today}T18:00`,'datetime-local','required')}${selectField('Modalidad','fulfillment',[['retiro','Retiro en local'],['despacho','Delivery']],'retiro')}<label class="field full">Dirección de delivery<input name="delivery_address" maxlength="500"></label>${selectField('Canal','channel',[['Presencial','Presencial'],['Web/Mercat','Web/Mercat'],['Instagram','Instagram']],'Presencial')}${selectField('Estado de pago','payment_status',[['unpaid','Sin pago · requiere autorización'],['paid_manual','Pago verificado por socio']],'unpaid')}<div id="sos-payment-proof" class="full hidden">${field('Referencia de verificación del pago','payment_evidence','','text','maxlength="500"')}<label class="check-field"><input name="payment_verified" type="checkbox">Verifiqué que el pago fue recibido, no solo el comprobante.</label></div><label class="field full">Comanda ya identificada<select name="source_key"><option value="">Sin comanda identificada</option>${sources.map(o=>`<option value="${escapeHTML(o.key)}">Comanda ${escapeHTML(o.order_id)}</option>`).join('')}</select></label><label class="field full">Nota de contingencia<input name="comments" maxlength="1000"></label><label class="field full">Motivo del ingreso manual<input name="reason" required maxlength="500"></label><label class="check-field full"><input name="is_test" type="checkbox" ${state.scope==='tests'?'checked':''}>Prueba ficticia · separada de la operación</label></div><h3 class="section-label">Productos</h3><div id="sos-items">${sosItem()}</div><button type="button" class="button secondary" data-action="sos-add-item">Agregar producto</button><p class="hint">Sin pago: se guarda para revisión del socio. Autorizar el agendamiento después no confirma pago ni entrega.</p></form>`,'<button class="button secondary" data-action="sos-panel">Cancelar</button><button class="button primary" type="submit" form="sos-form">Guardar pedido manual</button>','SOLO SOCIOS');
}
function syncSosForm(){
  const form=$('#sos-form');if(!form)return;
  const paid=form.elements.payment_status.value==='paid_manual';$('#sos-payment-proof').classList.toggle('hidden',!paid);
  form.elements.payment_evidence.required=paid;form.elements.payment_verified.required=paid;
  if(!paid){form.elements.payment_evidence.value='';form.elements.payment_verified.checked=false;}
  form.elements.delivery_address.required=form.elements.fulfillment.value==='despacho';
}
async function saveSos(form,button){
  if(state.saving)return;state.saving=true;button.disabled=true;
  try{
    const order=Object.fromEntries(['customer','customer_phone','pickup_at','fulfillment','delivery_address','channel','payment_status','payment_evidence','comments'].map(k=>[k,form.elements.namedItem(k).value]));
    order.is_test=form.elements.is_test.checked;order.payment_verified=form.elements.payment_verified.checked;
    order.items=[...form.querySelectorAll('.sos-item')].map(row=>({sku:row.querySelector('[name=sos_sku]').value,quantity:Number(row.querySelector('[name=sos_quantity]').value)}));
    const saved=await api('/api/sos/orders','POST',{order,request_id:state.sosRequestId,reason:form.elements.reason.value,source_key:form.elements.source_key.value});
    await loadBoard();await showDetail(saved.id);toast(saved.scheduling_status==='draft'?'Pedido manual guardado. Falta autorizar el agendamiento sin pago.':'Pedido manual agendado con pago verificado por socio.');
  }catch(error){showFormError(error.message);}finally{state.saving=false;button.disabled=false;}
}
function showSosAuthorization(){
  modal('Agendar sin pago',state.detail.customer,`<form id="sos-override-form"><div id="form-error" class="error hidden" role="alert"></div><p>El pedido contará como encargo de producción. Seguirá identificado como <strong>Agendado sin pago</strong>.</p><label class="field">Motivo de autorización<input name="reason" required maxlength="500"></label><p class="hint">Autoriza ${escapeHTML(state.partner.name)}. No se registra dinero, boleta ni entrega.</p></form>`,'<button class="button secondary" data-action="back-detail">Cancelar</button><button class="button primary" type="submit" form="sos-override-form">Forzar agendamiento</button>','EXCEPCIÓN DE SOCIO');
}
async function saveSosAuthorization(form,button){
  if(state.saving)return;state.saving=true;button.disabled=true;
  try{const saved=await api(`/api/orders/${state.detail.id}/schedule-without-payment`,'POST',{version:state.detail.version,reason:form.elements.reason.value});state.date=saved.pickup_at.split('T')[0];state.scope=saved.is_simulation?'tests':'operations';state.view='agenda';$('#record-scope').value=state.scope;if(state.period==='custom'){state.end=state.date;$('#end-date').value=state.end;}await loadBoard();await showDetail(saved.id);toast('Agendado sin pago. Autorización registrada.');}
  catch(error){showFormError(error.message);}finally{state.saving=false;button.disabled=false;}
}
function showSosReview(index){
  const review=state.sosContext.reviews[index];state.sosReview=review;
  modal('Revisar vínculo',`Comanda ${review.order_id}`,`<form id="sos-review-form"><div id="form-error" class="error hidden" role="alert"></div><p>${escapeHTML(review.customer)} · ${escapeHTML(review.pickup_at)}</p><details><summary>Comentario original</summary><p class="source-comment">${escapeHTML(review.original_comment)}</p></details><div class="form-grid">${selectField('Decisión del socio','decision',[['link','Es el mismo pedido: vincular'],['distinct','Son pedidos distintos']],'link')}${selectField('Pedido manual','order_id',review.candidates.map(o=>[o.order_id,`${o.customer} · ${o.pickup_at}`]),review.candidates[0]?.order_id)}<label class="field full">Motivo y comprobación realizada<input name="reason" required maxlength="500"></label></div><p class="hint">Vincular conserva un solo pedido y sus cambios locales. Los datos de pago llegarán en la próxima lectura de ventas.</p></form>`,'<button class="button secondary" data-action="sos-panel">Cancelar</button><button class="button primary" type="submit" form="sos-review-form">Guardar revisión</button>','SOLO SOCIOS');
}
async function saveSosReview(form,button){
  if(state.saving)return;state.saving=true;button.disabled=true;
  try{await api('/api/sos/reconcile','POST',{source_key:state.sosReview.source_key,version:state.sosReview.version,decision:form.elements.decision.value,order_id:Number(form.elements.order_id.value),reason:form.elements.reason.value});await loadBoard();await showSosPanel();toast('Revisión registrada. Se aplicará en la próxima lectura de ventas.');}
  catch(error){showFormError(error.message);}finally{state.saving=false;button.disabled=false;}
}

function showTransition(button) {
  const item = state.detail.items.find(row => row.id === Number(button.dataset.item));
  const status = button.dataset.status;
  const correction = button.dataset.action === 'reverse';
  state.transition = {order:state.detail,item,status,correction};
  modal(correction ? 'Revertir a pendiente' : labels[status], `${item.quantity} × ${item.flavor} · ${state.detail.customer}`, `<form id="transition-form"><div id="form-error" class="error hidden" role="alert"></div><p class="hint">Se conservará el estado anterior, responsable y fecha. La solicitud abarca las ${item.quantity} unidades de este ítem; no afecta a los otros ítems. ${status === 'marcado_solicitado' ? 'Esteban verá una alerta dentro del ERP. El ítem todavía NO está marcado físico.' : status === 'marcado' ? 'Marca solo cuando la torta física esté asignada. El ajuste manual de inventario se hace fuera de este prototipo.' : status === 'cancelado' ? 'El ítem dejará de contar en encargadas. No se borra ni se anula ninguna venta o pago.' : 'No se realizarán movimientos de stock ni ventas.'}</p><label class="field">Motivo del cambio<input name="reason" required maxlength="500" placeholder="Ej.: comanda física asignada en prueba demo"></label><p class="hint">Responsable: ${escapeHTML(state.user?.name)} · cuenta autenticada</p></form>`, '<button class="button secondary" data-action="back-detail">Volver</button><button class="button primary" type="submit" form="transition-form">Guardar estado</button>');
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
  return stock.rows.map((row,index) => `<article class="stock-row"><div><strong>${escapeHTML(row.flavor)}</strong><small>${escapeHTML(row.size)}</small><small>Manual provisional${row.updated_at ? ` · ${escapeHTML(new Date(row.updated_at).toLocaleString('es-CL',{dateStyle:'short',timeStyle:'short',timeZone:'America/Santiago'}))}` : ''}</small></div><div><small>Físico</small><strong>${row.physical ?? 'Desconocido'}</strong></div><div><small>** Reserva vitrina</small><strong>${row.reserved}</strong></div><div><small>Venta entera</small><strong>${row.available ?? 'Desconocido'}</strong></div><button class="text-button" data-action="stock-row" data-index="${index}">Editar conteo</button></article>`).join('') || '<p class="hint">Aún no hay conteos. Disponibilidad desconocida; no hay sincronización de inventario.</p>';
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
  $('#stock-edit').classList.toggle('hidden',!can('stock') || state.scope!=='operations');
  $('.demo-tag').textContent = state.scope !== 'operations' ? 'PRUEBA' : localMode ? 'LOCAL' : 'DEMO';
  $('#analytics-caption').textContent = state.scope !== 'operations' ? 'INDICADORES EXCLUSIVOS DE PRUEBA' : localMode ? 'LECTURA DE LA OPERACIÓN · REGISTROS LOCALES' : 'LECTURA DE LA OPERACIÓN · DATOS FICTICIOS';
  $('#catalog-help').textContent = localMode ? 'Solo productos incorporados de Toteat. Sin conteo, la disponibilidad es desconocida; no hay conexión de inventario.' : 'La disponibilidad requiere un conteo local. Sin conteo no significa cero. Los ejemplos conservan sus referencias para mantener el historial.';
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
const attentionLabels={upcoming:'Entregas próximas',overdue:'Fecha vencida',unmarked:'Por marcar',unpaid:'Sin pago',incomplete:'Datos incompletos'};
function renderAttention() {
  const info=state.board.attention;if(!info)return;
  $('#attention-summary').textContent=`${info.orders.length} pedidos con seguimiento`;
  $('#attention-range').textContent=`Próximos 7 días (${dateLabel(info.start,{weekday:'short',month:'short'})} a ${dateLabel(info.end,{weekday:'short',month:'short'})}) y pendientes de otras fechas. Un pedido puede aparecer en varias prioridades.`;
  $('#attention-counts').innerHTML=Object.entries(attentionLabels).map(([key,label])=>`<div><strong>${info.counts[key]}</strong><span>${label}</span><small>pedidos</small></div>`).join('');
  $('#attention-list').innerHTML=info.orders.map(order=>`<article class="attention-row"><div><strong>${escapeHTML(order.customer)}</strong><p>${escapeHTML(dateLabel(order.pickup_at.split('T')[0],{weekday:'long',month:'short'}))} · ${escapeHTML(order.pickup_at.split('T')[1])}</p><small>${order.issues.map(key=>attentionLabels[key]).join(' · ')}${order.is_simulation?' · PRUEBA':''}</small></div><button class="button secondary" data-action="detail" data-id="${order.id}">Revisar pedido</button></article>`).join('')||'<p class="hint">Sin pendientes para esta vista.</p>';
  $('#reader-warning').textContent=info.reader_alerts.join(' ');
  $('#reader-warning').classList.toggle('hidden',!info.reader_alerts.length);
  const backup=state.board.backup;
  $('#backup-state').classList.toggle('hidden',!backup);
  if(backup)$('#backup-state').textContent=backup.state==='ready'?`Respaldo local verificado: ${new Intl.DateTimeFormat('es-CL',{dateStyle:'medium',timeStyle:'short',timeZone:'America/Santiago'}).format(new Date(backup.last_success))}.`:backup.state==='error'?'Respaldo pendiente de revisión. La última copia no pudo verificarse.':'Respaldo automático: esperando primera copia verificada.';
}
function renderOperations() {
  renderModules();
  renderCatalog();
  renderAttention();
  renderToteat();
  renderWorkflow();
  const board = state.board;
  const localMode = board.operating_mode === 'toteat-local';
  $('#alert-count').textContent = board.notifications.length;
  $('#alerts').innerHTML = board.notifications.map(n => `<article class="alert-row"><div><strong>${n.quantity} × ${escapeHTML(n.flavor)} · ${escapeHTML(n.size)}</strong>${n.is_simulation ? '<span class="test-badge">PRUEBA</span>' : ''}<p>${escapeHTML(n.customer)} · retiro ${escapeHTML(n.pickup_at.replace('T',' '))}</p><small>Solicitó ${escapeHTML(n.actor)} · ${escapeHTML(new Intl.DateTimeFormat('es-CL',{dateStyle:'short',timeStyle:'short',timeZone:board.timezone}).format(new Date(n.occurred_at)))}</small></div><button class="button secondary" data-action="alert" data-item="${n.item_id}">Revisar solicitud</button></article>`).join('') || '<p class="hint">Sin solicitudes de marcado abiertas. Las nuevas solicitudes aparecen aquí al actualizar.</p>';
  const plan=board.production;
  $('#production-totals').innerHTML = state.productionScope ? `<div class="production-summary-heading"><h3>Tortas del período</h3><strong>${plan.cake_quantity} enteras</strong></div>${plan.cakes.length ? `<table class="production-table"><caption>Cantidades por sabor y tamaño · ${escapeHTML(board.start)} a ${escapeHTML(board.end)}</caption><thead><tr><th scope="col">Torta / formato</th><th scope="col">Cantidad</th><th scope="col">Receta</th></tr></thead><tbody>${plan.cakes.map(p=>`<tr><th scope="row">${escapeHTML(p.flavor)}<small>${escapeHTML(p.size)}</small></th><td>${p.quantity}</td><td>${p.recipe_defined?'Registrada':'Por definir'}</td></tr>`).join('')}</tbody></table>` : '<p class="hint">Sin tortas pendientes de entrega en este período.</p>'}<h3 class="section-label">Bases para estos encargos</h3><div class="base-totals">${plan.totals.map(b => `<article><strong>${escapeHTML(b.quantity)}</strong><span>${escapeHTML(b.name)}</span><small>${baseFormat(b)}</small>${!b.format_confirmed?`<small>Formato por confirmar · receta de ${escapeHTML(b.size)}</small>`:''}</article>`).join('') || '<p class="hint">Aún no hay bases calculables.</p>'}</div>${plan.missing.length ? `<div class="recipe-warning">Receta por definir: ${plan.missing.map(p => `${p.quantity} × ${escapeHTML(p.flavor)} (${escapeHTML(p.size)})`).join(' · ')}. Estas tortas figuran arriba, pero sus bases no están incluidas.</div>` : ''}<p class="hint">Demanda bruta: suma cada torta pendiente de entrega una vez, incluidas las marcadas. Fabricación pendiente: sin determinar, porque no hay registro de bases elaboradas ni stock verificado. Las fracciones se conservan sin redondear.</p>` : '<p class="empty compact">Elige Todas las encargadas para ver cantidades de tortas y bases del período.</p>';
  $('#recipes').innerHTML = board.catalog.map(p => `<article class="recipe-row"><div><strong>${escapeHTML(p.flavor)} · ${escapeHTML(p.size)}</strong><small>${p.is_demo ? 'Ejemplo ficticio' : 'Catálogo local · incorporación manual'}</small><p>${p.bases ? p.bases.map(b => `${escapeHTML(b.quantity)} × ${escapeHTML(b.name)} · ${baseFormat(b)}`).join(' + ') : 'Receta/cantidad pendiente'}</p>${p.recipe_note ? `<p>${escapeHTML(p.recipe_note)}</p>` : ''}</div><button class="text-button ${can('recipe') ? '' : 'hidden'}" data-action="recipe" data-sku="${escapeHTML(p.sku)}">Editar bases</button></article>`).join('');
  const stats = board.analytics;
  if (stats) $('#analytics').innerHTML = `<div class="analytics-grid"><article><strong>${stats.orders}</strong><span>Pedidos del rango</span><small>Incluye finalizados y cancelados</small></article><article><strong>${stats.units}</strong><span>Enteros encargados</span><small>Unidades no canceladas, incluye entregadas</small></article><article><strong>${stats.mark_minutes === null ? 'Sin datos' : `${stats.mark_minutes} min`}</strong><span>Solicitud → marcado</span><small>Promedio de ${stats.mark_samples} confirmaciones registradas</small></article><article><strong>${stats.on_time_total ? `${stats.on_time}/${stats.on_time_total}` : 'Sin datos'}</strong><span>Entregas programadas a tiempo</span><small>Solo entregas programadas clasificadas · hora registrada ≤ retiro</small></article></div><div class="channel-breakdown">${Object.entries(stats.channels).map(([channel,count]) => `<span>${escapeHTML(channel)} <strong>${count} pedidos</strong></span>`).join('')}</div><p class="hint">Agrupado por fecha programada de retiro/entrega. ${localMode ? 'Solo pedidos agendados del rango y la vista seleccionados.' : 'Tiempos medidos con registros de la demo; no son resultados reales.'} Productos: ver resumen por sabor/formato. Sin datos de ingresos ni márgenes.</p>`;
}
const baseUnits={whole_sponge:'Bizcocho entero',sponge_disc:'Disco de bizcocho',sheet:'Hojarasca',unit:'Unidad'};
function baseFormat(base) {
  return `${base.unit?escapeHTML(baseUnits[base.unit]||'Unidad por confirmar'):'Unidad por confirmar'} · ${base.diameter_cm?`${escapeHTML(base.diameter_cm)} cm`:'diámetro por confirmar'}`;
}
function baseForm(base = {}) {
  return `<div class="base-row">${field('Base','base_name',base.name || '','text','required maxlength="80"')}${field('Cantidad por torta','base_quantity',base.quantity || '1','number','required min="0.001" max="999" step="0.001"')}${selectField('Unidad de producción','base_unit',[['','Por confirmar'],...Object.entries(baseUnits)],base.unit||'')}${field('Diámetro · cm','base_diameter',base.diameter_cm||'','number','min="0.01" max="100" step="0.01" placeholder="Por confirmar"')}<button class="text-button" type="button" data-action="remove-base">Quitar</button></div>`;
}
function showRecipe(product) {
  state.recipeEditing = product;
  modal('Editar bases', `${product.flavor} · ${product.size} · valores por producto entero`, `<form id="recipe-form"><div id="form-error" class="error hidden" role="alert"></div><label class="check-field"><input type="checkbox" name="recipe_pending" ${product.bases === null ? 'checked' : ''}>Receta pendiente de confirmar</label><div id="recipe-bases">${(product.bases || []).map(baseForm).join('')}</div><button class="button secondary" type="button" data-action="add-base">Agregar base</button><label class="field">Motivo de edición<input name="reason" required maxlength="500"></label><p class="hint">Las cantidades se guardan como decimales exactos. Cada receta define su unidad y diámetro; no se convierten personas en centímetros ni bizcochos en discos automáticamente. Deja el formato por confirmar cuando no esté verificado. Sin rellenos en esta etapa.</p></form>`,'<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="recipe-form">Guardar receta local</button>','CONFIGURACIÓN DE PRODUCCIÓN');
}
async function saveRecipe(form,button) {
  if (state.saving) return;
  state.saving = true; button.disabled = true;
  try {
    const bases = form.elements.namedItem('recipe_pending').checked ? null : [...form.querySelectorAll('.base-row')].map(row => ({name:$('[name=base_name]',row).value,quantity:$('[name=base_quantity]',row).value,unit:$('[name=base_unit]',row).value||null,diameter_cm:$('[name=base_diameter]',row).value||null}));
    await api('/api/recipes','PUT',{sku:state.recipeEditing.sku,bases,version:state.recipeEditing.version,actor:actor(),reason:form.elements.namedItem('reason').value});
    $('#modal').close(); await loadBoard(); toast('Receta local actualizada con historial.');
  } catch(error) { showFormError(error.message); }
  finally { state.saving = false; button.disabled = false; }
}
function showStockForm(row = null) {
  if(!can('stock') || state.scope!=='operations')return;
  const product = state.board.catalog.find(p => p.flavor === row?.flavor && p.size === row?.size) || state.board.catalog[0];
  if(!product){toast('Primero incorpora un producto entero al catálogo.');return;}
  row ||= state.board.stock.rows.find(r=>r.flavor===product.flavor&&r.size===product.size) || null;
  state.stockEditing = row;
  modal('Ingresar stock físico', 'Manual provisional · no modifica el inventario de Toteat', `<form id="stock-form"><div id="form-error" class="error hidden" role="alert"></div><div class="form-grid">${selectField('Producto del catálogo','stock_product',catalogChoices(),product.sku)}${field('Sabor','flavor',product.flavor,'text','required readonly')}${field('Tamaño','size',product.size,'text','required readonly')}${field('Cantidad física entera (vacío = desconocido)','physical',row?.physical ?? '','number','min="0" max="999" step="1"')}${field('Enteras reservadas para trozar / vitrina','reserved',row?.reserved ?? 0,'number','required min="0" max="999" step="1"')}<div class="detail-field full"><small>Disponible para venta entera</small><output id="stock-available" aria-live="polite">Desconocido</output></div><label class="field full">Motivo del conteo<input name="reason" required maxlength="500" placeholder="Ej.: conteo físico de apertura"></label></div><p class="hint">Disponible = físico − reserva para trozar. No se descuentan pedidos nuevamente. Un campo físico vacío significa desconocido; 0 significa que no hay unidades. Tu cuenta, fecha y motivo quedan en el historial.</p></form>`, '<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="stock-form">Guardar conteo</button>','STOCK DE TORTAS · SOLO SOCIOS');
  updateStockAvailable();
}
function selectStockProduct(sku){
  const product=state.board.catalog.find(p=>p.sku===sku),form=$('#stock-form');if(!product||!form)return;
  const row=state.board.stock.rows.find(r=>r.flavor===product.flavor&&r.size===product.size)||null;
  state.stockEditing=row;
  for(const key of ['flavor','size'])form.elements[key].value=product[key];
  form.elements.physical.value=row?.physical??'';form.elements.reserved.value=row?.reserved??0;
  updateStockAvailable();
}
function updateStockAvailable(){
  const form=$('#stock-form');if(!form)return;
  const physical=form.elements.physical,reserved=form.elements.reserved;
  const amount=physical.value===''?null:Number(physical.value),held=Number(reserved.value);
  reserved.max=amount===null?'999':String(amount);
  reserved.setCustomValidity(amount!==null&&held>amount?'La reserva para trozar no puede superar el stock físico.':'');
  $('#stock-available').textContent=amount===null?'Desconocido':Number.isInteger(amount)&&amount>=0&&Number.isInteger(held)&&held>=0&&held<=amount?String(amount-held):'Revisar cantidades';
}
document.addEventListener('input',event=>{if(event.target.closest('#stock-form')&&['physical','reserved'].includes(event.target.name))updateStockAvailable();});
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
  try { await navigator.clipboard.writeText(text); toast('Texto copiado. No se envió ningún mensaje.'); }
  catch { $('#whatsapp-text').focus(); $('#whatsapp-text').select(); toast('Seleccionado. Usa Copiar en tu dispositivo; no se envió ningún mensaje.'); }
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button'); if (!button) return;
  const action = button.dataset.action;
  if (state.saving) return;
  if(button.dataset.view){state.view=button.dataset.view;rememberView();renderOperations();}
  if(action==='receipt-preview'&&can('receipt')){
    const id=Number(button.dataset.id);
    modal('Boleta adjunta','Vista previa del documento',`<iframe class="receipt-preview" src="/api/orders/${id}/receipt" title="Vista previa de boleta PDF"></iframe>`,`<button class="button secondary" data-action="detail" data-id="${id}">Volver al pedido</button><a class="button primary" href="/api/orders/${id}/receipt?download=1" download>Descargar boleta</a>`,'DOCUMENTO DEL PEDIDO');
  }
  if(action==='view-tests'){state.scope='tests';state.view='agenda';$('#record-scope').value='tests';await loadBoard();}
  if (action === 'close') { state.panelRequest++; $('#modal').close(); }
  if (action === 'detail') await showDetail(button.dataset.id);
  if(action==='sos-panel')await showSosPanel();
  if(action==='sos-new')showSosForm();
  if(action==='sos-authorize')showSosAuthorization();
  if(action==='sos-review')showSosReview(Number(button.dataset.index));
  if(action==='sos-add-item')$('#sos-items').insertAdjacentHTML('beforeend',sosItem());
  if(action==='sos-remove-item')button.closest('.sos-item').remove();
  if(action==='edit-customer')await showPartnerAccess(true);
  if(action==='partner-logout'){await api('/api/partner/logout','POST',{});await loadPartnerSession();$('#modal').close();}
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
    state.view='agenda';
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
document.addEventListener('submit', async event => { if (!['order-form','transition-form','stock-form','recipe-form','partner-form','customer-form','sos-form','sos-override-form','sos-review-form'].includes(event.target.id)) return; event.preventDefault(); const button = document.querySelector(`[type=submit][form=${event.target.id}]`); if(event.target.id==='sos-form')await saveSos(event.target,button);else if(event.target.id==='sos-override-form')await saveSosAuthorization(event.target,button);else if(event.target.id==='sos-review-form')await saveSosReview(event.target,button);else if(event.target.id==='partner-form')await savePartnerForm(event.target,button); else if(event.target.id==='customer-form')await saveCustomer(event.target,button); else if (event.target.id === 'order-form') await saveOrder(event.target,button); else if (event.target.id === 'recipe-form') await saveRecipe(event.target,button); else if (event.target.id === 'stock-form') await saveStock(event.target,button); else await saveTransition(event.target,button); });
$('#sos-access').addEventListener('click',showSosPanel);
document.addEventListener('change',event=>{if(['payment_status','fulfillment'].includes(event.target.name))syncSosForm();if(event.target.name==='source_key' && event.target.value){const source=state.sosContext.sources.find(o=>o.key===event.target.value);if(source)$('#sos-items').innerHTML=source.items.map(item=>{const product=state.board.catalog.find(p=>String(p.source_product?.idToteat)===String(item.productCodeToteat));return product?sosItem({sku:product.sku,quantity:item.quantity}):'';}).join('');}});
$('#account-button').addEventListener('click',()=>showPartnerAccess());
document.addEventListener('change',event=>{if(event.target.name==='fulfillment')syncDeliveryAddress();});
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
$('#production-scope').addEventListener('change',event => {state.productionScope = event.target.value; if (state.board) renderOperations();});
document.addEventListener('change',event => {if (event.target.name === 'delivery_timing') { syncTimingFields(); return; } if (event.target.name === 'stock_product') { selectStockProduct(event.target.value); return; } if (event.target.name !== 'sku') return; const row = event.target.closest('.form-item'); if (!row) return; const product = state.board.catalog.find(p => p.sku === event.target.value); $('[name=flavor]',row).value = product.flavor; $('[name=size]',row).value = product.size;});
$('#search').addEventListener('input',event => {state.search = event.target.value; if (state.board) renderOrders();});
$('#whatsapp').addEventListener('click',() => { if (!state.board) return; modal('Texto para WhatsApp','Incluye lunes a viernes del período elegido. Los pedidos de fin de semana permanecen en el ERP.',`<textarea id="whatsapp-text" class="message-preview" readonly aria-label="Mensaje para copiar">${escapeHTML(state.board.whatsapp)}</textarea><p class="hint">PRODUCTOS POR MARCAR: pendientes y solicitados. RESUMEN ENCARGADAS: también incluye marcados. Stock actual separado de los encargos. ** = enteras reservadas para trozar/vitrina, decididas manualmente.</p>`,'<button class="button secondary" data-action="close">Cerrar</button><button class="button primary" data-action="copy">Copiar texto</button>','PREVIA · NO ENVÍA MENSAJES'); });
$('#access-form').addEventListener('submit',loginAccess);
$('#access-retry').addEventListener('click',startAccess);
startAccess();

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
  if(noticePolling || !state.user) return;
  noticePolling=true;const sequence=++noticeSequence;
  try {
    const [data,reader]=await Promise.all([api('/api/notifications'),api('/api/toteat')]);
    if(state.board){state.board.toteat=reader;renderToteat();renderWorkflow();}
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
setInterval(()=>{if(!state.user)return;if(!document.hidden&&!state.saving&&!$('#modal').open)loadBoard();else refreshNotifications();},15000);
