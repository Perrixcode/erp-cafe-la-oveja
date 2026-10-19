'use strict';
// Read-only workspace. Financial calculations and exports come from the existing account API.
window.OvejaSupplierAccounts=(()=>{
  const e=escapeHTML,money=clp;
  const kinds={all:'Todos los movimientos',invoices:'Facturas',payments:'Pagos',credit_notes:'Notas de crédito',applications:'Aplicaciones'};
  let root=null,suppliers=[],selected='',applied={from:'',to:'',kind:'all'},view=null,page=1,epoch=0;
  const allowed=()=>state.user?.role==='partner'&&state.module==='suppliers'&&state.moduleSection==='Cuentas corrientes';
  const options=rows=>'<option value="">Selecciona un proveedor</option>'+rows.map(s=>`<option value="${s.id}" ${String(s.id)===selected?'selected':''}>${e(s.name)}${s.tax_id?' · '+e(s.tax_id):''}</option>`).join('');
  function reset(){epoch++;root=null;suppliers=[];selected='';view=null;page=1;applied={from:'',to:'',kind:'all'};}
  function leave(){epoch++;}
  function render(target,rows){
    epoch++;root=target;suppliers=rows;
    if(!suppliers.some(s=>String(s.id)===selected))selected='';
    root.innerHTML=`<section class="accounts-page"><div class="accounts-intro"><p>Consulta el saldo y la cartola de cada proveedor.</p><a class="button secondary" href="/api/suppliers/pending.pdf" download title="Todos los proveedores; no usa los filtros de esta cartola">PDF de todos los pendientes</a></div><form id="accounts-filter"><div class="accounts-provider"><label class="field">Buscar proveedor<input name="search" type="search" placeholder="Nombre o RUT" autocomplete="off"></label><label class="field">Proveedor<select name="supplier">${options(suppliers)}</select></label></div><div class="accounts-filters"><label class="field">Desde<input name="from" type="date" value="${e(applied.from)}"></label><label class="field">Hasta<input name="to" type="date" value="${e(applied.to)}"></label><label class="field">Movimiento<select name="kind">${Object.entries(kinds).map(([k,v])=>`<option value="${k}" ${applied.kind===k?'selected':''}>${v}</option>`).join('')}</select></label><button class="button primary">Consultar</button><button class="text-button" type="button" data-accounts-all>Todo el historial</button></div><p class="hint" data-accounts-search-status aria-live="polite">${suppliers.length} proveedores disponibles. Fechas inclusivas; deja un extremo vacío para un rango abierto.</p></form><div id="accounts-detail" aria-live="polite"></div></section>`;
    load();
  }
  async function load(){
    const token=++epoch,auth=authGeneration,target=root?.querySelector('#accounts-detail');
    if(!target||!allowed())return;
    view=null;page=1;
    if(!selected){target.innerHTML=`<div class="accounts-empty"><h2>${suppliers.length?'Elige un proveedor':'Aún no hay proveedores'}</h2><p>${suppliers.length?'Su saldo, movimientos y cartola aparecerán aquí.':'Agrega un proveedor en el directorio para consultar su cuenta corriente.'}</p></div>`;return;}
    target.replaceChildren();
    const snapshot={id:selected,filters:{...applied}},progress=window.OvejaLoading.begin(target,{label:'Consultando cuenta corriente…',blocking:true});
    try{
      const result=await api('/api/suppliers/accounts/'+snapshot.id+'?'+new URLSearchParams(snapshot.filters));
      if(token!==epoch||auth!==authGeneration||!allowed()||!target.isConnected)return;
      view={...result,query:new URLSearchParams(snapshot.filters).toString()};paint();
    }catch(err){if(token===epoch&&allowed())progress.fail(err.message,load);}
    finally{progress.done();}
  }
  function paint(){
    if(!view||!allowed())return;
    const t=view.totals,p=view.period,id=view.supplier.id,rows=view.journal.slice((page-1)*50,page*50),pages=Math.max(1,Math.ceil(view.journal.length/50));
    root.querySelector('#accounts-detail').innerHTML=`<section class="accounts-summary"><div><p class="accounts-eyebrow">CUENTA CORRIENTE · CLP</p><h2>${e(view.supplier.name)}</h2><p class="hint">${e(view.supplier.tax_id||'RUT por completar')}</p></div><div class="accounts-balance"><span>${t.net_balance<0?'Saldo actual a favor':'Saldo actual por pagar'}</span><strong>${money(Math.abs(t.net_balance))}</strong><small>Facturas pendientes ${money(t.outstanding)} · Créditos disponibles ${money(t.available_credit)}</small></div></section><div class="accounts-actions"><div><a class="button primary" href="/api/suppliers/accounts/${id}/export.pdf?${view.query}" download>Descargar cartola PDF</a><a class="button secondary" href="/api/suppliers/accounts/${id}/export.csv?${view.query}" download>Exportar CSV</a></div><button class="text-button" type="button" data-ac="open" data-id="${id}">Gestionar cuenta</button></div><p class="hint">El saldo actual considera toda la cuenta. Los filtros se aplican a la cartola y a sus descargas.</p>${!t.consistent?'<p class="error">Hay una diferencia en la cartola. Revisa los antecedentes antes de registrar operaciones.</p>':''}<div class="accounts-ledger-heading"><h3>Movimientos</h3><span>${e(p.start||'Inicio del historial')} → ${e(p.end||'Último movimiento')} · ${e(kinds[p.kind])}</span></div><dl class="accounts-period"><div><dt>Saldo al inicio</dt><dd>${money(p.opening_balance)}</dd></div><div><dt>Cargos visibles</dt><dd>${money(p.shown_debit)}</dd></div><div><dt>Abonos visibles</dt><dd>${money(p.shown_credit)}</dd></div><div><dt>Saldo al cierre</dt><dd>${money(p.closing_balance)}</dd></div></dl><div class="accounts-table-wrap" tabindex="0" role="region" aria-label="Movimientos de cuenta corriente"><table class="accounts-table"><thead><tr><th scope="col">Fecha</th><th scope="col">Movimiento</th><th scope="col">Cargo</th><th scope="col">Abono</th><th scope="col">Saldo</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${e(row.date||'Sin fecha')}</td><th scope="row">${e(row.label)}</th><td>${row.debit?money(row.debit):'—'}</td><td>${row.credit?money(row.credit):'—'}</td><td>${money(row.balance)}${row.balance<0?' a favor':''}</td></tr>`).join('')||'<tr><td colspan="5" class="accounts-empty-row">Sin movimientos para este proveedor y período.</td></tr>'}</tbody></table></div><div class="accounts-pagination"><span>${p.shown_count} movimientos · Página ${page} de ${pages}</span>${pages>1?`<div><button class="button secondary" data-accounts-page="${page-1}" ${page===1?'disabled':''}>Anterior</button><button class="button secondary" data-accounts-page="${page+1}" ${page===pages?'disabled':''}>Siguiente</button></div>`:''}</div><p class="hint">Los saldos inicial, final y acumulado incluyen todos los tipos de movimiento. PDF y CSV incluyen todos los movimientos del filtro, aunque ocupen varias páginas.${p.undated_count?' Hay antecedentes sin fecha considerados al inicio; no acreditan un cierre histórico fechado.':''}${view.invoices.some(i=>i.payment_review)?' Las facturas con saldo por validar quedan fuera de la cartola.':''}</p>`;
  }
  function apply(form){
    const values=Object.fromEntries(new FormData(form));
    try{
      if(values.from||values.to)window.OvejaDateRange.validate(values.from||'0001-01-01',values.to||'9999-12-31');
      selected=values.supplier;applied={from:values.from,to:values.to,kind:values.kind};load();
    }catch(err){toast(err.message);}
  }
  document.addEventListener('submit',event=>{if(event.target.id==='accounts-filter'){event.preventDefault();if(allowed())apply(event.target);}});
  document.addEventListener('input',event=>{
    if(!event.target.matches('#accounts-filter [name=search]')||!allowed())return;
    const f=event.target.form,q=event.target.value.trim().toLocaleLowerCase(),rows=suppliers.filter(s=>`${s.name} ${s.tax_id||''}`.toLocaleLowerCase().includes(q));
    const current=suppliers.find(s=>String(s.id)===selected);
    f.elements.supplier.innerHTML=options(current&&!rows.includes(current)?[current,...rows]:rows);
    f.querySelector('[data-accounts-search-status]').textContent=rows.length?`${rows.length} coincidencias. Selecciona un proveedor para consultar.`:'No hay coincidencias. La cuenta consultada se conserva.';
  });
  document.addEventListener('change',event=>{if(event.target.matches('#accounts-filter [name=supplier]')&&allowed())apply(event.target.form);});
  document.addEventListener('click',event=>{
    if(!allowed())return;
    if(event.target.closest('[data-accounts-all]')){const f=root.querySelector('#accounts-filter');f.elements.from.value='';f.elements.to.value='';f.elements.kind.value='all';apply(f);}
    const b=event.target.closest('[data-accounts-page]');if(b&&view){page=Math.min(Math.max(1,Number(b.dataset.accountsPage)),Math.max(1,Math.ceil(view.journal.length/50)));paint();}
  });
  return {render,reset,leave};
})();
