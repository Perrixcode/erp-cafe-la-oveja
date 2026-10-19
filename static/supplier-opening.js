'use strict';
window.OvejaSupplierOpening=(()=>{
  let active=false,section='',sequence=0,snapshot=null,busy=false,page=0,draft=null;
  const filters={condition:'all',supplier:'all',view:'pending',search:''};
  const root=()=>$('#supplier-opening-workspace');
  const visible=()=>active&&state.partner&&state.module==='suppliers'&&['Pagos','Proveedores'].includes(state.moduleSection);
  const money=v=>v===null||v===undefined?'Sin dato':clp(v);
  const label=v=>snapshot.conditions[v];
  const literal=v=>v===null||v===undefined||v===''?'Sin dato':escapeHTML(v);
  const when=v=>escapeHTML(new Date(v).toLocaleString('es-CL',{timeZone:'America/Santiago'}));
  const sourceLink=(row,form=false)=>`https://docs.google.com/spreadsheets/d/${snapshot.batch.source_id}/edit#gid=${form?'1773786094':'2146906641'}&range=A${row}:${form?'F':'J'}${row}`;
  function reset(){sequence++;active=false;busy=false;snapshot=null;section='';draft=null;page=0;Object.assign(filters,{condition:'all',supplier:'all',view:'pending',search:''});root().replaceChildren();}
  function leave(){if(active){sequence++;active=false;busy=false;draft=null;}}
  function enter(){
    if(!state.partner){root().innerHTML='<p class="empty compact">La revisión de proveedores está disponible para socios.</p>';return;}
    if(active&&section===state.moduleSection)return;
    active=true;section=state.moduleSection;load();
  }
  async function load(){
    if(!visible())return;
    const request=++sequence;busy=true;
    try{const data=await api('/api/suppliers/opening');if(request!==sequence||!visible())return;snapshot=data;render();}
    catch(error){if(request===sequence&&visible()){if(snapshot)toast(error.message);else root().innerHTML=`<p class="error">${escapeHTML(error.message)}</p><button class="button secondary" data-supplier-refresh>Reintentar</button>`;}}
    finally{if(request===sequence)busy=false;}
  }
  function selected(){return snapshot.rows.filter(r=>(filters.condition==='all'||r.condition===filters.condition)&&(filters.supplier==='all'||r.name===filters.supplier)&&(!filters.search||`${r.name} ${r.folio??''}`.toLocaleLowerCase().includes(filters.search.toLocaleLowerCase()))&&(filters.view==='all'||filters.view==='pending'&&(r.balance??0)>0||filters.view==='negative'&&(r.balance??0)<0||filters.view==='issues'&&r.issues.length));}
  function options(value,inherit=false){return Object.entries(snapshot.conditions).filter(([k])=>inherit||k!=='inherit').map(([k,v])=>`<option value="${k}" ${k===value?'selected':''}>${escapeHTML(v)}</option>`).join('');}
  function render(){
    if(!snapshot||!visible())return;
    if(!snapshot.available){root().innerHTML='<div class="empty compact"><h2>Saldos iniciales</h2><p>Todavía no hay una planilla auditada importada.</p><button class="button secondary" data-supplier-refresh>Actualizar</button></div>';return;}
    const t=snapshot.totals;
    root().innerHTML=`<div class="supplier-heading"><div><span class="eyebrow">PREPARACIÓN DE SALDOS</span><h2>${section==='Proveedores'?'Condiciones por proveedor':'Pagos · saldos iniciales'}</h2><p class="hint">Copia de la planilla al ${when(snapshot.batch.source_modified)}. Actualizar consulta los cambios del ERP.</p></div><button class="button secondary" data-supplier-refresh>Actualizar vista</button></div>
      <div class="review-stats"><article class="review-stat"><span>Saldos pendientes de la planilla</span><strong>${money(t.positive)}</strong><small>${t.pending_rows} registros con saldo positivo · por validar</small></article><article class="review-stat"><span>Saldos negativos por aclarar</span><strong>${money(t.negative)}</strong><small>${t.negative_rows} registros · se revisan por separado</small></article><article class="review-stat"><span>Calidad de los antecedentes</span><strong>${snapshot.issues.length} incidencias</strong><small>${t.missing_folio} sin folio · ${t.short_folio} folios cortos por validar · ${t.missing_due} sin vencimiento · ${t.blank_balances} saldo(s) vacío(s)</small></article></div>
      <p class="source-alert">Saldo inicial provisional. Los abonos y estados provienen de la planilla y requieren respaldo. La recepción de mercadería y la condición de compra no confirman un pago.</p>
      ${section==='Proveedores'?supplierList():`<form id="supplier-filter" class="delivery-filter"><label class="field">Condición de compra<select name="condition"><option value="all">Todas las condiciones</option>${options(filters.condition)}</select></label><label class="field">Proveedor<select name="supplier"><option value="all">Todos los proveedores</option>${snapshot.suppliers.map(s=>`<option ${s.name===filters.supplier?'selected':''} value="${escapeHTML(s.name)}">${escapeHTML(s.name)}</option>`).join('')}</select></label><label class="field">Registros<select name="view">${[['pending','Con saldo pendiente'],['all','Todos los antecedentes'],['negative','Saldos negativos'],['issues','Con incidencias']].map(([k,v])=>`<option value="${k}" ${k===filters.view?'selected':''}>${v}</option>`).join('')}</select></label><label class="field">Nombre o folio<input name="search" type="search" value="${escapeHTML(filters.search)}" placeholder="Buscar en la planilla"></label><button class="button secondary">Aplicar filtros</button></form><div id="supplier-rows"></div>`}
      <details class="review-daily"><summary>Incidencias de la importación · ${snapshot.issues.length}</summary><div class="supplier-issues">${snapshot.issues.map(issueCard).join('')}</div></details>
      <details class="review-daily"><summary>Trazabilidad y alcance</summary><div class="supplier-issues"><p>${t.rows} filas del libro y ${snapshot.form.length} respuestas del formulario conservadas. Las respuestas sin coincidencia y los posibles duplicados no se añaden ni se eliminan del saldo.</p><p>Saldo neto registrado: ${money(t.net_recorded)}. Excluye ${t.blank_balances} celda(s) vacía(s); no se utiliza para compensar saldos negativos contra pendientes.</p><p>Los nombres se mantienen separados. Una variante de nombre no acredita que sea la misma razón social. La condición inicial es «Sin definir» hasta registrarla con un antecedente.</p><p>Importado por ${escapeHTML(snapshot.batch.actor)} · ${when(snapshot.batch.imported_at)}</p><small class="review-hash">Huella del original: ${escapeHTML(snapshot.batch.id)}</small></div></details>`;
    if(section==='Pagos')renderRows();
  }
  function supplierList(){return `<p class="hint">La condición predeterminada se aplica a los registros de este nombre exacto que no tengan una excepción por factura. Incluye los antecedentes importados.</p><div class="chat-table-wrap"><table class="chat-table"><thead><tr><th>Proveedor según planilla</th><th>Condición predeterminada</th><th>Registros</th><th>Acción</th></tr></thead><tbody>${snapshot.suppliers.map((s,i)=>`<tr><th>${escapeHTML(s.name)}</th><td>${escapeHTML(label(s.condition))}</td><td>${snapshot.rows.filter(r=>r.name===s.name).length}</td><td><button class="text-button" data-supplier-default="${i}">Definir condición</button></td></tr>`).join('')}</tbody></table></div>`;}
  function renderRows(){
    const rows=selected(),pages=Math.max(1,Math.ceil(rows.length/25));page=Math.min(page,pages-1);
    const positive=rows.reduce((sum,r)=>sum+Math.max(0,r.balance??0),0);
    $('#supplier-rows').innerHTML=`<div class="delivery-caption"><div><h3>${rows.length} registros en esta vista</h3><p>Saldos positivos de la selección: <strong>${money(positive)}</strong></p></div><span class="hint">Página ${page+1} de ${pages}</span></div><div class="chat-table-wrap"><table class="chat-table"><thead><tr><th>Proveedor / documento</th><th>Condición</th><th>Monto factura</th><th>Abono declarado</th><th>Saldo registrado</th><th>Revisión</th></tr></thead><tbody>${rows.slice(page*25,page*25+25).map(r=>`<tr><th>${escapeHTML(r.name)}<small>Folio ${literal(r.folio)} · fila ${r.row}</small>${r.folio_review?'<small>Folio por validar</small>':''}<small>Vence: ${literal(r.due_text)}</small></th><td>${escapeHTML(label(r.condition))}<small>${r.condition_source==='row'?'Excepción por factura':'Condición del proveedor'}</small></td><td>${money(r.amount)}</td><td>${money(r.paid)}${r.paid===null||r.paid===undefined?'<small>No registrado</small>':''}</td><td><strong>${money(r.balance)}</strong><small>${({paid:'Saldado según planilla',pending:'Pendiente según saldo',partial:'Abono parcial declarado',review:'Por revisar'})[r.payment_state]}</small></td><td><button class="text-button" data-supplier-row="${r.row}">Ver y clasificar</button><small>${r.issues.length?r.issues.length+' incidencia(s)':'Sin diferencias detectadas'}${r.annotation?' · con anotación':''}</small></td></tr>`).join('')||'<tr><td colspan="6">No hay registros para estos filtros.</td></tr>'}</tbody></table></div><div class="review-actions"><button class="button secondary" data-supplier-page="-1" ${page===0?'disabled':''}>Anterior</button><button class="button secondary" data-supplier-page="1" ${page>=pages-1?'disabled':''}>Siguiente</button></div>`;
  }
  function issueCard(issue){
    const refs=issue.rows.map(n=>{const r=snapshot.rows.find(r=>r.row===n);return `<p><button class="text-button" data-supplier-row="${n}">Libro · fila ${n}</button> · ${escapeHTML(r.name)} · folio ${literal(r.folio)} · factura ${money(r.amount)} · saldo ${money(r.balance)}</p>`;}).join('');
    const forms=issue.form_rows.map(n=>{const r=snapshot.form.find(r=>r.row===n);return `<p><a href="${sourceLink(n,true)}" target="_blank" rel="noopener noreferrer">Formulario · fila ${n}</a> · ${escapeHTML(r.name)} · folio ${literal(r.folio)} · ${money(r.amount)}</p>`;}).join('');
    return `<article class="review-report"><h3>${escapeHTML(issue.title)}</h3>${refs}${forms}<p class="hint">${escapeHTML(issue.detail)}</p></article>`;
  }
  function detail(kind,target){
    const r=kind==='row'?snapshot.rows.find(r=>String(r.row)===target):snapshot.suppliers.find(r=>r.name===target);if(!r)return;
    const current=r.annotation;
    draft={request_id:crypto.randomUUID(),batch_id:snapshot.batch.id,target_kind:kind,target,version:current?.version||0};
    const value=current?.condition||(kind==='row'?'inherit':'unknown');
    const history=snapshot.history.filter(h=>h.target_kind===kind&&h.target===target).sort((a,b)=>b.version-a.version);
    const source=kind==='row'?`<div class="review-report"><p><strong>Factura: ${money(r.amount)} · abono declarado: ${money(r.paid)} · saldo original: ${money(r.balance)}</strong></p><p>Diferencia calculada: ${money(r.computed_balance)}. Un abono vacío significa que no hay importe registrado.</p><p>Estado literal: ${literal(r.status)} · vencimiento: ${literal(r.due_text)} · folio: ${literal(r.folio)}</p><p>Condición efectiva: ${escapeHTML(label(r.condition))}.</p><a href="${sourceLink(r.row)}" target="_blank" rel="noopener noreferrer">Ver fila ${r.row} en la planilla</a></div>${r.issues.map(i=>issueCard(snapshot.issues[i])).join('')}`:`<p class="source-alert">Se aplicará a ${snapshot.rows.filter(x=>x.name===target).length} registros del nombre exacto «${escapeHTML(target)}», salvo sus excepciones por factura. No se unen otros nombres ni se modifican los importes.</p>`;
    modal(kind==='row'?r.name+' · folio '+(r.folio??'sin dato'):r.name,kind==='row'?'Clasificación y antecedentes del saldo inicial':'Condición predeterminada del proveedor',`${source}<form id="supplier-annotation"><div id="form-error" class="error hidden" role="alert"></div><label class="field">Condición de compra<select name="condition">${options(value,kind==='row')}</select></label><label class="field">Antecedente o nota de revisión<textarea name="note" required minlength="5" maxlength="1000" rows="3" placeholder="Indica el acuerdo o documento que respalda esta clasificación."></textarea></label><p class="hint">Guardar registra una condición y una nota. Las incidencias y los importes originales permanecen para su conciliación.</p><button class="button primary" type="submit">Guardar clasificación</button></form><details class="review-daily"><summary>Historial de este ${kind==='row'?'registro':'proveedor'} · ${history.length}</summary><div class="supplier-issues">${history.map(h=>`<article class="history-entry"><strong>${escapeHTML(label(h.condition))}</strong><p>${escapeHTML(h.note)}</p><small>${escapeHTML(h.actor)} · ${when(h.created_at)}</small></article>`).join('')||'<p>Sin cambios registrados.</p>'}</div></details>`,'','PROVEEDORES · SALDOS INICIALES');
  }
  document.addEventListener('submit',async event=>{
    if(!visible())return;
    if(event.target.id==='supplier-filter'){event.preventDefault();Object.assign(filters,Object.fromEntries(new FormData(event.target)));page=0;renderRows();return;}
    if(event.target.id!=='supplier-annotation')return;
    event.preventDefault();if(busy||!draft)return;
    const form=event.target,button=$('button[type="submit"]',form),request=sequence,context=draft;
    busy=true;button.disabled=true;
    try{await api('/api/suppliers/opening/annotations','POST',{...context,...Object.fromEntries(new FormData(form))});if(request!==sequence||!visible())return;$('#modal').close();draft=null;toast('Clasificación guardada. Importes originales conservados.');await load();}
    catch(error){if(request===sequence&&visible())showFormError(error.message);}
    finally{if(request===sequence){busy=false;button.disabled=false;}}
  });
  document.addEventListener('click',event=>{
    if(!visible())return;
    const b=event.target.closest('[data-supplier-refresh],[data-supplier-row],[data-supplier-default],[data-supplier-page]');if(!b)return;
    if(b.hasAttribute('data-supplier-refresh')){if(!busy)load();}
    else if(b.dataset.supplierRow!==undefined){if(!busy)detail('row',b.dataset.supplierRow);}
    else if(b.dataset.supplierDefault!==undefined){if(!busy)detail('supplier',snapshot.suppliers[Number(b.dataset.supplierDefault)].name);}
    else{page+=Number(b.dataset.supplierPage);renderRows();}
  });
  return {enter,leave,reset};
})();
