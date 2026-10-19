'use strict';
window.OvejaSupplierDirectory=(()=>{
  const e=escapeHTML;let data=null,finance=null,generation=0,detailGeneration=0,query='',status='all',selectedId=null;
  const days=v=>v?.length?v.map(d=>['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'][d]).join(', '):'Por confirmar';
  const b=(label,action,id='')=>`<button type="button" class="button secondary" data-sd="${action}" data-id="${id}">${e(label)}</button>`;
  const fields=rows=>`<dl class="sd-fields">${rows.map(([label,value])=>`<dt>${e(label)}</dt><dd>${e(value??'Por confirmar')||'Por confirmar'}</dd>`).join('')}</dl>`;
  const table=(headers,rows)=>`<div class="ap-table-wrap"><table class="ap-table"><thead><tr>${headers.map(h=>`<th>${e(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${headers.length}" class="pc-empty-row">Sin registros.</td></tr>`}</tbody></table></div>`;
  async function render(root,financial){
    const n=++generation,auth=authGeneration;finance=financial;root.replaceChildren();
    const progress=window.OvejaLoading.begin(root,{label:'Cargando directorio…'});
    try{
      const result=await api('/api/suppliers/procurement');
      if(n!==generation||auth!==authGeneration||!root.isConnected||state.moduleSection!=='Proveedores')return;
      data=result;
      root.innerHTML=`<form id="sd-filter" class="ap-toolbar"><label class="field">Buscar proveedor, producto o vendedor<input type="search" name="query" value="${e(query)}" placeholder="Nombre, producto o contacto"></label><label class="field">Revisión operativa<select name="status"><option value="all">Todos</option><option value="pending">Por revisar</option><option value="reviewed">Revisados</option></select></label><button class="button secondary">Filtrar</button></form><div class="sd-layout" id="sd-layout"><div class="sd-list-panel" id="sd-list"></div><aside id="sd-side-panel" class="sd-side-panel hidden" aria-label="Ficha del proveedor"></aside></div>`;
      $('#sd-filter').elements.status.value=status;list();
    }catch(err){if(n===generation&&auth===authGeneration&&root.isConnected)progress.fail(err.message,()=>render(root,financial));}
    finally{progress.done();}
  }
  function list(){
    const rows=data.suppliers.filter(s=>(status==='all'||s.profile.reviewed===(status==='reviewed'))&&[s.name,s.contact,s.phone,s.profile.products_text,s.profile.order_channel].join(' ').toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    $('#sd-list').innerHTML=`<p class="sd-count">${rows.length} de ${data.suppliers.length} proveedores · Abre un nombre para consultar su ficha.</p><div class="ap-table-wrap"><table class="ap-table sd-table"><thead><tr><th>Proveedor / productos</th><th>Pedido / entrega</th><th>Vendedor / canal</th><th>Saldo neto</th><th>Ficha</th></tr></thead><tbody>${rows.map(s=>{const f=finance.suppliers.find(f=>f.id===s.id),p=s.profile;return `<tr aria-selected="${s.id===selectedId}"><th><button type="button" class="text-button sd-name" data-sd="detail" data-id="${s.id}"><span class="sd-avatar" aria-hidden="true">${e(s.name.split(/\s+/).slice(0,2).map(w=>w[0]).join('').toUpperCase())}</span>${e(s.name)}</button><small>${e(p.products_text||'Productos por confirmar')}</small></th><td>Pedido: ${e(days(p.order_days))}${p.cutoff_time?' · '+e(p.cutoff_time):''}<small>Entrega: ${e(days(p.delivery_days))}</small></td><td>${e(s.contact||'Vendedor por confirmar')}<small>${e(s.phone||'Teléfono por confirmar')} · ${e(p.order_channel||'Canal por confirmar')}</small></td><td class="num">${clp(f?.net_balance??0)}</td><td><span class="ap-badge ${p.reviewed?'paid':'review'}">${p.reviewed?'Revisada':'Por revisar'}</span></td></tr>`;}).join('')||'<tr><td colspan="5" class="pc-empty-row">No hay proveedores para estos filtros.</td></tr>'}</tbody></table></div>`;
  }
  function tab(key){
    for(const pane of document.querySelectorAll('#sd-detail [data-sd-pane]'))pane.classList.toggle('hidden',pane.dataset.sdPane!==key);
    for(const button of document.querySelectorAll('#sd-detail [data-sd-tab]'))button.setAttribute('aria-pressed',String(button.dataset.sdTab===key));
  }
  async function detail(id){
    const n=++detailGeneration,auth=authGeneration;
    const side=state.module==='suppliers'&&state.moduleSection==='Proveedores'?$('#sd-side-panel'):null;
    selectedId=Number(id);
    if(side){
      $('#sd-layout').classList.add('has-detail');side.classList.remove('hidden');
      side.innerHTML='<header class="sd-side-heading"><div><h2 id="sd-title" tabindex="-1">Ficha del proveedor</h2><p id="sd-subtitle">Consultando registros vinculados.</p></div><button type="button" class="close-button" data-sd="close" aria-label="Cerrar ficha">×</button></header><div id="sd-detail"></div>';
      list();$('#sd-title').focus({preventScroll:true});
      if(matchMedia('(max-width:1250px)').matches)side.scrollIntoView({block:'start',behavior:'auto'});
    }else modal(data?.suppliers.find(s=>s.id===Number(id))?.name||'Ficha del proveedor','Consultando ficha y registros vinculados.','<div id="sd-detail"></div>','','PROVEEDOR');
    const target=$('#sd-detail'),panel=state.panelRequest,valid=()=>n===detailGeneration&&auth===authGeneration&&target.isConnected&&(side?state.moduleSection==='Proveedores'&&selectedId===Number(id):panel===state.panelRequest&&$('#modal').open),progress=window.OvejaLoading.begin(target,{label:'Cargando ficha del proveedor…'});
    try{
      const [p,f]=await Promise.all([api('/api/suppliers/procurement'),api('/api/suppliers/payables')]);
      if(!valid())return;
      data=p;finance=f;
      const s=p.suppliers.find(s=>s.id===Number(id)),a=f.suppliers.find(s=>s.id===Number(id));if(!s||!a)throw new Error('El proveedor ya no está disponible.');
      const profile=s.profile,orders=p.orders.filter(o=>o.supplier_id===s.id),prices=p.prices.filter(r=>r.supplier_id===s.id&&r.valid),invoices=f.invoices.filter(i=>i.supplier_id===s.id);
      const panes=[['summary','Datos'],['invoices','Cuenta corriente'],['purchases','Compras']];
      $(side?'#sd-title':'#modal-title').textContent=s.name;$(side?'#sd-subtitle':'#modal .modal-header p').textContent='RUT '+(s.tax_id||'por confirmar');
      target.innerHTML=`<div class="ap-actions">${b('Editar ficha','edit',s.id)}<button type="button" class="button secondary" data-ag-provider="${s.id}">Planificar compra</button></div><nav class="sd-detail-tabs" aria-label="Secciones de la ficha">${panes.map(([key,label])=>`<button type="button" data-sd-tab="${key}" aria-pressed="${key==='summary'}" aria-controls="sd-pane-${key}">${label}</button>`).join('')}</nav>
      <section class="sd-pane" id="sd-pane-summary" data-sd-pane="summary"><div class="sd-detail-summary"><section><h3>Contacto y pedidos</h3>${fields([['Productos',profile.products_text],['Vendedor',s.contact],['Teléfono',s.phone],['Correo',s.email],['Canal',profile.order_channel],['Contacto del canal',profile.order_contact],['Enlace',profile.order_url],['Pedido',days(profile.order_days)+(profile.cutoff_time?' · hasta '+profile.cutoff_time:'')],['Entrega',days(profile.delivery_days)]])}</section><section><h3>Condiciones de compra y pago</h3>${fields([['Condiciones',s.terms],['Plazo de pago',profile.payment_term_days===null?null:profile.payment_term_days+' días'],['Plazo de entrega',profile.delivery_lead_days===null?null:profile.delivery_lead_days+' días'],['Mínimo monetario',profile.minimum_amount_clp===null?null:clp(profile.minimum_amount_clp)],['Mínimo en cantidad',profile.minimum_quantity===null?null:profile.minimum_quantity+' '+(p.units.find(u=>u.id===profile.minimum_unit_id)?.code||'')],['Sustitutos',(profile.substitute_ids||[]).map(id=>p.suppliers.find(s=>s.id===id)?.name).join(', ')||profile.substitute_text]])}</section><section><h3>Cuenta de pago</h3>${fields([['Banco',s.bank],['Tipo de cuenta',s.account_type],['Número de cuenta',s.account_number],['Titular',s.account_holder],['RUT titular',s.account_tax_id]])}<p class="hint sd-verification">${profile.reviewed?'Ficha operativa revisada.':'Datos por verificar.'} Los datos registrados no acreditan validación bancaria.</p></section></div><details class="sd-source"><summary>Fuente, notas y revisión</summary>${fields([['Fuente',profile.source_reference],['Aclaración',profile.schedule_note||'Sin relación confirmada entre días de pedido y entrega.'],['Notas',s.notes]])}</details></section>
      <section class="sd-pane hidden" id="sd-pane-purchases" data-sd-pane="purchases"><h3>Compras y recepción</h3>${table(['Pedido','Entrega esperada','Estado','Discrepancias'],orders.map(o=>`<tr><th>${b('OC-'+o.id,'order',o.id)}</th><td>${e(o.expected_on||'Por confirmar')}</td><td>${e(({open:'Pendiente',partial:'Parcial',complete:'Recibido',cancelled:'Cancelado'})[o.state])}</td><td>${o.open_issues}</td></tr>`))}<h3>Precios históricos revisados</h3>${table(['Insumo / documento','Fecha','CLP / unidad base'],prices.map(r=>`<tr><th>${e(r.item_name)}<small>Folio ${e(r.folio)} · ${r.basis==='net'?'neto':'con impuestos'}</small></th><td>${e(r.issued_on)}</td><td class="num">${Number(r.unit_price).toLocaleString('es-CL',{maximumFractionDigits:4})} / ${e(p.units.find(u=>u.id===r.base_unit_id)?.code||'')}</td></tr>`))}</section>
      <section class="sd-pane hidden" id="sd-pane-invoices" data-sd-pane="invoices"><div class="ap-stats"><article><span>Facturas pendientes</span><strong>${clp(a.balance)}</strong></article><article><span>Créditos disponibles</span><strong>${clp(a.available_credit||0)}</strong></article><article><span>Saldo neto</span><strong>${clp(a.net_balance??a.balance)}</strong></article></div><div class="ap-actions"><button type="button" class="button primary" data-ac="open" data-id="${s.id}">Ver movimientos de cuenta</button></div><h3>Facturas vinculadas</h3>${table(['Documento','Vencimiento','Monto','Saldo'],invoices.map(i=>`<tr><th>${b('Folio '+i.folio,'invoice',i.id)}${i.voided_at?'<small>Anulada</small>':''}</th><td>${e(i.due_on||'Por confirmar')}</td><td class="num">${clp(i.amount)}</td><td class="num">${i.payment_review?'Por validar':clp(i.balance)}</td></tr>`))}</section>`;
      if(side&&matchMedia('(max-width:1250px)').matches)side.scrollIntoView({block:'start',behavior:'auto'});
    }catch(err){if(valid())progress.fail(err.message,()=>detail(id));}
    finally{progress.done();}
  }
  document.addEventListener('submit',event=>{if(event.target.id==='sd-filter'){event.preventDefault();const v=Object.fromEntries(new FormData(event.target));query=v.query;status=v.status;list();}});
  document.addEventListener('click',async event=>{
    const paneButton=event.target.closest('[data-sd-tab]');if(paneButton){tab(paneButton.dataset.sdTab);return;}
    const el=event.target.closest('[data-sd]');if(!el||state.user?.role!=='partner'||state.saving)return;
    try{
      if(el.dataset.sd==='close'){detailGeneration++;const id=selectedId;selectedId=null;$('#sd-side-panel').replaceChildren();$('#sd-side-panel').classList.add('hidden');$('#sd-layout').classList.remove('has-detail');list();document.querySelector(`[data-sd=detail][data-id="${id}"]`)?.focus();return;}
      if(el.dataset.sd==='detail')await detail(el.dataset.id);
      if(el.dataset.sd==='edit')await window.OvejaProcurement.showDirectory(el.dataset.id);
      if(el.dataset.sd==='order'){state.moduleSection='Pedidos y recepción';renderModules();rememberView();await window.OvejaProcurement.showOrder(el.dataset.id);}
      if(el.dataset.sd==='invoice'){state.moduleSection='Facturas';renderModules();rememberView();await window.OvejaPayables.showInvoice(el.dataset.id);}
    }catch(err){toast(err.message);}
  });
  document.addEventListener('click',event=>{const el=event.target.closest('[data-ag-provider]');if(el&&state.user?.role==='partner')window.OvejaPurchaseAgenda.planSupplier(Number(el.dataset.agProvider)).catch(err=>toast(err.message));});
  return {render,detail};
})();
