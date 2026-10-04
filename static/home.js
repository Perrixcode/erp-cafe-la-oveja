'use strict';
let homeRequest=0;
async function renderHome(){
  const request=++homeRequest;
  $('#module-title').textContent='Inicio';$('#module-placeholder .module-subtitle').textContent='Una mirada a la operación de Oveja.';
  $('#module-subtabs').replaceChildren();$('#module-placeholder .module-empty').classList.add('hidden');
  const target=$('#home-workspace');target.classList.remove('hidden');target.innerHTML='<p class="hint">Actualizando resumen…</p>';
  try{
    const data=await api('/api/home');if(request!==homeRequest||state.module!=='home'||!state.user)return;
    const counts=data.attention.counts,orders=data.attention.orders.filter(o=>o.issues.includes('upcoming')).slice(0,5);
    target.innerHTML=`<div class="home-welcome"><div><span class="eyebrow">OVEJA · COCINA Y CAFÉ</span><h2>Hola, ${escapeHTML(state.user.name.split(' ')[0])}.</h2><p>${escapeHTML(dateLabel(state.board.today,{weekday:'long',year:'numeric'}))}</p></div><button class="button primary" data-module="cakes">Ir a pedidos de tortas ↗</button></div><div class="analytics-grid home-metrics"><article><strong>${counts.upcoming}</strong><span>Pedidos próximos · 7 días</span></article><article><strong>${counts.unmarked}</strong><span>Sin marcar · hasta 7 días</span></article><article><strong>${counts.overdue}</strong><span>Entregas de días anteriores</span></article><article><strong>${counts.unpaid}</strong><span>Agendados sin pago</span></article></div><div class="home-columns"><section><h3>Próximas entregas</h3>${orders.map(o=>`<button class="home-order" data-action="detail" data-id="${o.id}"><strong>${escapeHTML(o.customer)}</strong><span>${escapeHTML(dateLabel(o.pickup_at.split('T')[0]))} · ${escapeHTML(o.pickup_at.split('T')[1])}</span><span>Ver pedido ↗</span></button>`).join('')||'<p class="hint">Sin entregas programadas para los próximos siete días.</p>'}</section><section><h3>Conexiones y revisión</h3><p class="hint">${data.attention.reader_alerts.length?data.attention.reader_alerts.map(escapeHTML).join('<br>'):'✓ Lectura de comandas y ventas actualizada.'}</p><p>Datos de cliente por completar: <strong>${counts.incomplete}</strong></p><button class="button secondary" data-home-inbox>Revisar lectura de Toteat</button>${state.partner?'<p><button class="button secondary" data-module="transfers">Transferencias del local ↗</button></p>':''}<p class="hint">Resumen de operación. Las pruebas están separadas.</p></section></div>`;
  }catch(error){if(request===homeRequest&&state.user)target.innerHTML=`<p class="error">${escapeHTML(error.message)}</p>`;}
}
document.addEventListener('click',event=>{if(event.target.closest('[data-home-inbox]')){state.module='cakes';state.view='inbox';renderModules();renderWorkflow();renderToteat();rememberView();}});
