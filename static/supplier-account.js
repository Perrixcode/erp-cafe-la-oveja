'use strict';
window.OvejaSupplierAccount=(()=>{
  const e=escapeHTML,money=clp,requests=new WeakMap();
  let view=null,supplierId=null,draft=null,busy=false,epoch=0;
  const filters={from:'',to:'',kind:'all'},kinds={all:'Todos los movimientos',invoices:'Facturas y anulaciones',payments:'Pagos y anulaciones',credit_notes:'Notas de crédito y anulaciones',applications:'Aplicaciones y reversos'};
  const query=()=>new URLSearchParams(filters).toString();
  const allowed=()=>state.user?.role==='partner';
  const button=(label,action,id='',primary=false)=>`<button type="button" class="button ${primary?'primary':'secondary'}" data-ac="${action}" data-id="${e(id)}">${label}</button>`;
  const input=(label,name,value='',extra='')=>`<label class="field">${label}<input name="${name}" value="${e(value)}" ${extra}></label>`;
  const entry=id=>view.entries.find(row=>row.id===Number(id));
  const eligible=()=>view.invoices.filter(row=>!row.voided_at&&!row.payment_review&&row.balance>0);
  const title=row=>(row.kind==='payment'?'Pago / anticipo':'Nota de crédito '+row.folio)+' · CC-'+row.id;
  function reset(){epoch++;view=null;supplierId=null;draft=null;busy=false;Object.assign(filters,{from:'',to:'',kind:'all'});}
  async function open(id){
    if(!allowed())return;
    if(draft?.uncertain){pendingNote();return;}
    if(supplierId!==Number(id))Object.assign(filters,{from:'',to:'',kind:'all'});
    const token=++epoch,auth=authGeneration;
    const result=await api('/api/suppliers/accounts/'+Number(id)+'?'+query());
    if(token!==epoch||auth!==authGeneration||!allowed())return;
    view=result;supplierId=result.supplier.id;draft=null;render();
  }
  function fileLinks(files){return files.map(f=>`<div class="ap-file"><a href="/api/suppliers/account-files/${f.id}" target="_blank" rel="noopener noreferrer">${e(f.name)}</a><a href="/api/suppliers/account-files/${f.id}?download=1">Descargar</a></div>`).join('');}
  function entryCard(row){
    return `<article class="ap-card ${row.voided_at?'ap-void':''}"><div class="ap-heading"><div><h3>${e(title(row))} · ${money(row.amount)}</h3><p>${e(row.occurred_on)}${row.method?' · '+e(view.methods[row.method]):''} · ${e(row.reference||'Sin referencia')}</p><p class="hint">Registrado por ${e(row.actor)}${row.origin_invoice_id?' · Vinculada a factura '+e(view.invoices.find(i=>i.id===row.origin_invoice_id)?.folio||''):''}</p></div><strong>${row.voided_at?'Anulado':'Saldo a favor disponible: '+money(row.available)}</strong></div>${row.voided_at?`<p>Anulado por ${e(row.voided_by)} · ${e(row.void_reason)}</p>`:`<p>Aplicado a facturas: ${money(row.applied)}</p>`}${row.initial_available_credit?`<p>Excedente al registrar: ${money(row.initial_available_credit)}. El saldo disponible descuenta sus aplicaciones posteriores.</p>`:''}${row.notes?`<p>${e(row.notes)}</p>`:''}${row.applications.map(a=>`<p>${a.voided_at?'Aplicación anulada':'Aplicación'}: ${money(a.amount)} · factura ${e(a.folio)}${a.voided_at?' · '+e(a.void_reason)+' · '+e(a.voided_by):''} ${!row.voided_at&&!a.voided_at?button('Revertir aplicación','unapply',a.id):''}</p>`).join('')}${fileLinks(row.files)||'<p class="hint">Sin comprobante o documento adjunto.</p>'}<div class="ap-actions">${!row.voided_at&&row.available?button('Aplicar a facturas','apply',row.id,true):''}${button('Adjuntar documento','attach',row.id)}${!row.voided_at?button('Anular registro','void',row.id):''}</div></article>`;
  }
  function historicalPayments(){
    return view.invoices.flatMap(invoice=>(invoice.payments||[]).map(payment=>({invoice,payment})))
      .sort((a,b)=>b.payment.created_at.localeCompare(a.payment.created_at));
  }
  function historicalPaymentCard({invoice,payment:p}){
    const files=(invoice.files||[]).filter(f=>f.payment_id===p.id);
    return `<article class="ap-card ${p.voided_at?'ap-void':''}"><div class="ap-heading"><div><h3>Pago histórico PH-${p.id} · ${money(p.amount)}</h3><p>${e(p.paid_on||'Fecha de pago no registrada')} · factura ${e(invoice.folio)}</p><p class="hint">Registrado por ${e(p.actor)} con el flujo anterior.</p></div>${button('Ver factura','invoice',invoice.id)}</div>${p.voided_at?`<p>Anulado · ${e(p.void_reason)}</p>`:`<p>Aplicado a esta factura: ${money(p.amount)} · Saldo pendiente de la factura: ${money(invoice.balance)}.</p><p class="hint">Este pago ya está aplicado; no constituye saldo a nuestro favor disponible.</p>`}${files.map(f=>`<div class="ap-file"><a href="/api/suppliers/files/${f.id}" target="_blank" rel="noopener noreferrer">${e(f.name)}</a></div>`).join('')||'<p class="hint">Sin comprobante adjunto.</p>'}</article>`;
  }
  function render(){
    const t=view.totals,historical=historicalPayments();
    modal('Cuenta corriente · '+view.supplier.name,'Importes en CLP · '+(view.supplier.tax_id?'RUT '+view.supplier.tax_id:'RUT por completar'),
      `<div id="account-workspace"><div class="ap-stats"><article><span>Deuda pendiente de facturas</span><strong>${money(t.outstanding)}</strong></article><article><span>Saldo a nuestro favor disponible</span><strong>${money(t.available_credit)}</strong></article><article><span>${t.net_balance<0?'Saldo a favor':'Saldo neto por pagar'}</span><strong>${money(Math.abs(t.net_balance))}</strong></article></div><p class="hint">Saldo neto = facturas pendientes − pagos y notas de crédito disponibles. Aplica los créditos a las facturas para actualizar sus vencimientos y programación.</p>${!t.consistent?'<p class="error">La cartola presenta una diferencia. Revisa los antecedentes antes de registrar otra operación.</p>':''}<div class="ap-actions">${button('Registrar pago / anticipo','new-payment','',true)}${button('Registrar nota de crédito','new-note')}${button('Actualizar','refresh')}<a class="button secondary" href="/api/suppliers/accounts/${supplierId}/export.pdf?${query()}" download title="Con los filtros aplicados de fecha y movimiento">Descargar cartola PDF</a></div><h3>Pagos, anticipos y notas de crédito</h3>${view.entries.slice().reverse().map(entryCard).join('')}${historical.length?'<p class="hint">Pagos registrados con el flujo anterior. Se muestran junto con los pagos nuevos y se contabilizan una sola vez en la cartola.</p>'+historical.map(historicalPaymentCard).join(''):''}${!view.entries.length&&!historical.length?'<p class="hint">Aún no hay pagos, anticipos ni notas de crédito registrados.</p>':''}<h3>Cartola cronológica</h3>${filterForm()}<p class="hint">Cargos y abonos con los valores vigentes de cada documento. Los antecedentes sin fecha se muestran primero, sin asignarles una fecha inventada. Aplicar un crédito solo lo distribuye: no vuelve a rebajar el saldo neto. Las correcciones de factura se consultan en su historial. Registrar un pago deja constancia de que fue realizado; no acredita conciliación bancaria.</p><div class="ap-table-wrap"><table class="ap-table"><thead><tr><th>Fecha</th><th>Movimiento</th><th>Cargo</th><th>Abono</th><th>Saldo acumulado</th></tr></thead><tbody>${view.journal.map(row=>`<tr><td>${e(row.date||'Sin fecha registrada')}</td><th>${e(row.label)}${row.invoice_id?button('Ver factura','invoice',row.invoice_id):''}</th><td>${row.debit?money(row.debit):'—'}</td><td>${row.credit?money(row.credit):'—'}</td><td>${money(row.balance)}${row.balance<0?' a favor':''}</td></tr>`).join('')||'<tr><td colspan="5">Sin movimientos.</td></tr>'}</tbody></table></div>${view.invoices.some(r=>r.payment_review)?'<p class="ap-notice">Hay facturas con saldo por validar: no se incluyen en esta cartola hasta revisar sus pagos anteriores.</p>':''}</div>`,'','COMPRAS Y PROVEEDORES');
  }
  function filterForm(){
    const p=view.period;
    return `<form id="account-filter" class="ap-toolbar">${input('Desde','from',filters.from,'type="date"')}${input('Hasta','to',filters.to,'type="date"')}<label class="field">Tipo de movimiento<select name="kind">${Object.entries(kinds).map(([k,label])=>`<option value="${k}" ${filters.kind===k?'selected':''}>${label}</option>`).join('')}</select></label><button class="button secondary">Filtrar cartola</button></form><p class="hint">${p.shown_count} movimiento(s) visibles · Cargos: ${money(p.shown_debit)} · Abonos: ${money(p.shown_credit)}. Saldo antes del intervalo: ${money(p.opening_balance)} · Saldo al final del intervalo: ${money(p.closing_balance)}. Estos saldos incluyen todos los tipos de movimiento, aunque el filtro oculte algunos.${p.undated_count?' Los antecedentes sin fecha se consideran al inicio; no acreditan un cierre histórico fechado.':''}</p><a class="button secondary" href="/api/suppliers/accounts/${supplierId}/export.csv?${query()}">Descargar cartola filtrada CSV</a>`;
  }
  function allocations(){
    return `<section class="ap-card"><h3>Aplicar a facturas</h3><p class="hint">Indica cuánto aplicar a cada factura. Deja 0 para conservar el importe como crédito disponible.</p>${eligible().map(r=>input(`Folio ${e(r.folio)} · saldo ${money(r.balance)}`,'allocation_'+r.id,0,`type="number" min="0" max="${r.balance}" step="1" data-ac-allocation="${r.id}"`)).join('')||'<p class="hint">No hay facturas con saldo validado pendiente. El importe quedará disponible.</p>'}<p id="account-amount-summary" class="hint" aria-live="polite"></p></section>`;
  }
  function form(kind,id){
    const row=kind==='invoice_credit_note'?view.invoices.find(r=>r.id===Number(id)):id?entry(id):null;
    draft={kind,row};
    let body='',heading='';
    if(kind==='invoice_credit_note'){
      if(!row||row.voided_at||row.payment_review){toast('La factura cambió o necesita validar su saldo. Actualiza antes de registrar la nota.');return;}
      const issued=view.entries.filter(e=>e.kind==='credit_note'&&e.origin_invoice_id===row.id&&!e.voided_at).reduce((n,e)=>n+e.amount,0),remaining=row.amount-issued;
      if(remaining<=0){toast('Las notas vigentes ya cubren el importe original de esta factura.');return;}
      heading='Agregar nota de crédito recibida';
      body=`<p><strong>${e(view.supplier.name)}</strong> · Factura <strong>${e(row.folio)}</strong></p><p>Importe original: ${money(row.amount)} · Pagado: ${money(row.paid)} · NC aplicadas: ${money(row.credited)} · Saldo pendiente: ${money(row.balance)}.</p><p class="hint">Se aplicará a esta factura hasta su saldo pendiente. El resto quedará a favor con este proveedor, separado de cualquier sobrepago existente. No emite una nota en el SII ni modifica el importe original o los pagos.</p><div class="ap-form">${input('Folio de la nota recibida','folio','','required maxlength="80"')}${input('Fecha de la nota','occurred_on',view.today,`type="date" required min="${e(row.issued_on)}" max="${view.today}"`)}${input('Monto total de la nota · CLP','amount','',`type="number" min="1" max="${remaining}" step="1" required`)}${input('Motivo de la nota recibida','notes','','required maxlength="1000"')}</div><p class="hint">NC vigentes asociadas: ${money(issued)}. Máximo restante sobre el original: ${money(remaining)}.</p><p id="account-amount-summary" class="hint" aria-live="polite"></p><label class="field">Nota recibida · PDF, JPG o PNG · hasta 8 MB<input name="file" type="file" accept="application/pdf,image/png,image/jpeg"></label><p class="hint">Si falta el documento puedes adjuntarlo luego en la cuenta corriente; el registro mostrará que no tiene respaldo.</p><label class="ap-check"><input name="confirmed" type="checkbox" required>Confirmo la nota recibida, su factura de origen y el saldo a favor indicado.</label>`;
    }else if(kind==='payment'||kind==='credit_note'){
      heading=kind==='payment'?'Registrar pago / anticipo':'Registrar nota de crédito';
      const origins=view.invoices.filter(r=>!r.voided_at&&!r.payment_review);
      if(kind==='credit_note'&&!origins.length){toast('Primero registra y valida la factura de origen.');return;}
      body=`<p class="hint">${kind==='payment'?'Registra dinero que ya fue pagado al proveedor. El importe sin aplicar queda como anticipo.':'Registra una nota de crédito monetaria recibida. No registra devolución física ni modifica stock.'}</p><div class="ap-form">${input('Monto total · CLP','amount','','type="number" min="1" max="999999999999" step="1" required')}${input(kind==='payment'?'Fecha del pago':'Fecha de la nota','occurred_on',view.today,`type="date" required max="${view.today}"`)}${kind==='payment'?`<label class="field">Medio de pago<select name="method">${Object.entries(view.methods).map(([k,v])=>`<option value="${e(k)}">${e(v)}</option>`).join('')}</select></label>`:input('Folio de nota de crédito','folio','','required maxlength="80"')+`<label class="field">Factura de origen<select name="origin_invoice_id">${origins.map(r=>`<option value="${r.id}">Folio ${e(r.folio)} · ${money(r.amount)}</option>`).join('')}</select></label>`}${input('Referencia','reference','','maxlength="160"')}${input('Notas','notes','','maxlength="1000"')}</div>${allocations()}<label class="field">Documento opcional · PDF, JPG o PNG<input name="file" type="file" accept="application/pdf,image/png,image/jpeg"></label><label class="ap-check"><input name="confirmed" type="checkbox" required>${kind==='payment'?'Confirmo que este pago ya fue realizado.':'Confirmo que recibí y revisé esta nota de crédito.'}</label>`;
    }else if(kind==='apply'){
      heading='Aplicar '+title(row).toLowerCase();body=`<p>Disponible: <strong>${money(row.available)}</strong></p>${allocations()}<label class="ap-check"><input name="confirmed" type="checkbox" required>Confirmo las facturas y los importes a aplicar. Se usará el crédito y el comprobante existentes, sin otro desembolso.</label>`;
    }else if(kind==='attach'){
      heading='Adjuntar documento';body='<label class="field">PDF, JPG o PNG · hasta 8 MB<input name="file" type="file" accept="application/pdf,image/png,image/jpeg" required></label>';
    }else{
      heading=kind==='void'?'Anular registro':'Revertir aplicación';
      body=`${kind==='void'&&row.reversal_blockers?.length?`<p class="ap-notice">Antes de anular, revierte estas aplicaciones posteriores: ${row.reversal_blockers.map(a=>'#'+a.id+' · factura '+e(a.folio)+' · '+money(a.amount)).join(', ')}.</p>`:''}<p>${kind==='void'?'Se anularán el registro y sus aplicaciones, conservando la auditoría. No revierte un pago bancario ni una nota tributaria en el SII.':'La factura recuperará el saldo y el importe volverá a quedar disponible. No registra dinero nuevo.'}</p>${input('Motivo','reason','','required maxlength="500"')}`;
    }
    modal(heading,view.supplier.name,`<form id="account-form"><div id="form-error" class="error hidden" role="alert"></div>${body}<div class="ap-actions"><button type="submit" class="button primary">Guardar</button>${button('Volver a cartola','back')}</div></form>`,'','CUENTA CORRIENTE');
    updateAmounts();
  }
  function updateAmounts(){
    const f=$('#account-form'),summary=$('#account-amount-summary');if(!f||!summary)return;
    if(draft.kind==='invoice_credit_note'){
      const amount=Number(f.elements.amount?.value||0),applied=Math.min(amount,draft.row.balance);
      summary.textContent=`Aplicar a factura ${draft.row.folio}: ${money(applied)} · Saldo de factura después: ${money(draft.row.balance-applied)} · Nuevo saldo a favor por esta NC: ${money(amount-applied)}`;
      return;
    }
    const applied=[...f.querySelectorAll('[data-ac-allocation]')].reduce((sum,input)=>sum+Number(input.value||0),0);
    const total=draft.kind==='apply'?draft.row.available:Number(f.elements.amount?.value||0);
    summary.textContent=`Aplicado: ${money(applied)} · Queda disponible: ${money(total-applied)}`;
    const constraint=f.elements.amount||f.querySelector('[data-ac-allocation]');
    if(constraint)constraint.setCustomValidity(applied>total?'Las aplicaciones superan el importe disponible.':'');
  }
  async function upload(entryId,file){
    return supplierFileUpload(`/api/suppliers/account-entries/${entryId}/files`,file);
  }
  const encode=file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('No se pudo leer la nota adjunta.'));reader.readAsDataURL(file);});
  function pendingNote(){
    const p=draft.pending,r=draft.row;
    modal('Confirmar resultado de la nota',view.supplier.name,`<form id="account-form"><div id="form-error" class="error hidden" role="alert"></div><p>El servidor todavía no confirmó el resultado. Se conserva la misma solicitud para evitar duplicados.</p><p>Factura ${e(r.folio)} · Nota ${e(p.folio)} · ${money(p.amount)} · ${e(p.occurred_on)}</p><p>${e(p.notes)}</p><p>${p.proof?'Documento conservado: '+e(p.proof.name):'Sin documento adjunto.'}</p><div class="ap-actions"><button type="submit" class="button primary">Reintentar la misma nota</button>${button('Volver a cartola','back')}</div></form>`,'','CUENTA CORRIENTE');
  }
  async function submit(f){
    if(busy||!allowed())return;busy=true;state.saving=true;const auth=authGeneration,current=draft;
    const buttons=[...f.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
    try{
      const values=Object.fromEntries(new FormData(f)),{kind,row}=current;
      let path,body,result,message='Guardado en la cuenta corriente.';
      const applications=[...f.querySelectorAll('[data-ac-allocation]')].filter(el=>Number(el.value)>0).map(el=>{const invoice=view.invoices.find(r=>r.id===Number(el.dataset.acAllocation));return {invoice_id:invoice.id,version:invoice.version,amount:Number(el.value)};});
      if(kind==='invoice_credit_note'){
        path='/api/suppliers/account-invoice-credit-notes';
        body=current.pending||{supplier_id:supplierId,origin_invoice_id:row.id,origin_version:row.version,currency:'CLP',folio:values.folio,amount:Number(values.amount),occurred_on:values.occurred_on,notes:values.notes,confirmed:f.elements.confirmed.checked};
        const file=f.elements.file?.files[0];
        if(!current.pending&&file){if(!file.size||file.size>8*1024*1024)throw Error('Selecciona un documento de hasta 8 MB.');body.proof={name:file.name,base64:await encode(file)};}
        if(auth!==authGeneration||!allowed())return;
        current.pending=body;
      }else if(kind==='attach'){await upload(row.id,f.elements.file.files[0]);}
      else if(kind==='payment'||kind==='credit_note'){
        path='/api/suppliers/'+(kind==='payment'?'account-payments':'account-credit-notes');
        body={supplier_id:supplierId,currency:'CLP',amount:Number(values.amount),occurred_on:values.occurred_on,reference:values.reference,notes:values.notes,confirmed:f.elements.confirmed.checked,applications};
        if(kind==='payment')body.method=values.method;else{body.folio=values.folio;body.origin_invoice_id=Number(values.origin_invoice_id);body.origin_version=view.invoices.find(r=>r.id===body.origin_invoice_id).version;}
      }else if(kind==='apply'){path=`/api/suppliers/account-entries/${row.id}/apply`;body={version:row.version,applications,confirmed:f.elements.confirmed.checked};}
      else if(kind==='void'){
        path=`/api/suppliers/account-entries/${row.id}/void`;
        const invoices=new Map(row.applications.filter(a=>!a.voided_at).map(a=>[a.invoice_id,a.invoice_version]));
        body={version:row.version,reason:values.reason,invoices:[...invoices].map(([id,version])=>({id,version}))};
      }else{path=`/api/suppliers/account-applications/${row.application.id}/void`;body={version:row.version,invoice_version:row.application.invoice_version,reason:values.reason};}
      if(path){
        const signature=JSON.stringify([path,body]);let request=kind==='invoice_credit_note'?current.request:requests.get(f);
        if(request?.signature!==signature){request={signature,id:crypto.randomUUID()};if(kind==='invoice_credit_note')current.request=request;else requests.set(f,request);}
        if(kind==='invoice_credit_note')current.sending=true;
        result=await api(path,'POST',{...body,request_id:request.id});
        if(kind==='invoice_credit_note'){
          if(!Number.isInteger(result.id)||(body.proof&&!Number.isInteger(result.file_id)))throw Error('No se confirmó el registro completo. Reintenta la misma nota.');
          current.sending=false;current.uncertain=false;
          message=`Nota ${result.folio} registrada. Aplicada: ${money(result.applied)} · Nuevo saldo a favor: ${money(result.available)}.`;
        }
      }
      if(result?.id&&(kind==='payment'||kind==='credit_note')&&f.elements.file?.files[0]){
        try{await upload(result.id,f.elements.file.files[0]);}catch(err){message='Registro guardado. Falta adjuntar el documento: '+err.message;}
      }
      if(auth!==authGeneration||!allowed())return;
      await window.OvejaPayables.refresh();await open(supplierId);toast(message);
    }catch(err){
      if(current.kind==='invoice_credit_note'){
        const definite=err.status>=400&&err.status<500&&err.status!==408;
        current.uncertain=!!current.sending&&!definite;
        if(!current.uncertain){current.pending=null;current.sending=false;if(auth===authGeneration){for(const el of f.elements)el.disabled=false;const submit=f.querySelector('[type="submit"]');if(submit)submit.textContent='Guardar';}}
        if(auth===authGeneration&&current.uncertain){for(const el of f.elements)if(el.tagName!=='BUTTON')el.disabled=true;const submit=f.querySelector('[type="submit"]');if(submit)submit.textContent='Reintentar la misma nota';}
      }
      if(auth===authGeneration)showFormError(err.message);
    }
    finally{busy=false;state.saving=false;buttons.forEach(b=>b.disabled=false);}
  }
  document.addEventListener('submit',event=>{
    if(event.target.id==='account-filter'){
      event.preventDefault();if(busy||!allowed())return;
      const f=event.target,values=Object.fromEntries(new FormData(f));
      Object.assign(filters,{from:values.from,to:values.to,kind:values.kind});
      const button=f.querySelector('button');button.disabled=true;button.textContent='Cargando…';
      open(supplierId).catch(err=>{toast(err.message);button.disabled=false;button.textContent='Filtrar cartola';});return;
    }
    if(event.target.id!=='account-form')return;event.preventDefault();submit(event.target);
  });
  document.addEventListener('input',event=>{if(event.target.closest('#account-form'))updateAmounts();});
  document.addEventListener('click',async event=>{
    const b=event.target.closest('[data-ac]');if(!b||!allowed()||busy)return;
    const action=b.dataset.ac,id=b.dataset.id;
    try{
      if(draft?.uncertain){pendingNote();return;}
      if(action==='open')await open(id);
      else if(action==='invoice-note'){await open(b.dataset.supplier);form('invoice_credit_note',id);}
      else if(action==='refresh'||action==='back')await open(supplierId);
      else if(action==='new-payment')form('payment');
      else if(action==='new-note')form('credit_note');
      else if(action==='invoice')await window.OvejaPayables.showInvoice(Number(id));
      else if(action==='unapply'){
        const row=view.entries.find(e=>e.applications.some(a=>a.id===Number(id)));
        form('unapply',row.id);draft.row={...row,application:row.applications.find(a=>a.id===Number(id))};
      }else form(action,id);
    }catch(err){toast(err.message);}
  });
  return {open,reset};
})();
