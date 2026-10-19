'use strict';
window.OvejaJointPayment=(()=>{
  const e=escapeHTML,money=clp;
  let draft=null,busy=false,epoch=0;
  const allowed=()=>state.user?.role==='partner';
  const input=(label,name,value,extra='')=>`<label class="field">${label}<input name="${name}" value="${e(value)}" ${extra}></label>`;
  function reset(){epoch++;draft=null;busy=false;}
  async function open(ids,onSaved){
    if(!allowed()||busy)return;
    if(draft?.uncertain){confirmation('El resultado anterior sigue pendiente de confirmar. Reintenta el mismo registro.');return;}
    const token=++epoch,auth=authGeneration;
    const data=await api('/api/suppliers/payables');
    if(token!==epoch||auth!==authGeneration||!allowed())return;
    const rows=ids.map(id=>data.invoices.find(r=>r.id===id));
    if(rows.length<1||rows.length>100)throw Error('Selecciona entre 1 y 100 facturas.');
    if(rows.some(r=>!r||r.voided_at||r.payment_review||r.payment_hold||r.balance<=0))throw Error('La selección cambió o contiene una factura no disponible. Actualiza las facturas.');
    if(new Set(rows.map(r=>r.supplier_id)).size!==1)throw Error('Selecciona facturas de un único proveedor para registrar un pago conjunto.');
    draft={supplier:data.suppliers.find(s=>s.id===rows[0].supplier_id),rows,methods:data.methods,today:data.today,amounts:rows.map(r=>r.balance),total:rows.reduce((n,r)=>n+r.balance,0),date:data.today,method:'transfer',reference:'',notes:'',proof:null,payload:null,uncertain:false,onSaved};
    edit();
  }
  function edit(){
    const d=draft;
    modal(d.rows.length===1?'Registrar pago / abono':'Registrar pago conjunto',`${d.supplier.name} · ${d.rows.length} ${d.rows.length===1?'factura':'facturas'}`,
      `<form id="joint-payment-edit"><div id="form-error" class="error hidden" role="alert"></div><p class="hint">Registra un pago ya realizado. Incluye todas las facturas seleccionadas, aunque alguna quede oculta por un filtro.</p>${input('Monto efectivamente pagado · CLP','paid_total',d.total,'type="number" min="1" max="999999999999" step="1" required data-joint-paid')}<p class="hint">${d.rows.length===1?'La distribución es automática: se aplica hasta cubrir el saldo de la factura.':'Al cambiar el total se distribuye en el orden mostrado; puedes ajustar las aplicaciones.'} El excedente quedará como saldo a nuestro favor con este proveedor.</p><div class="joint-payment-table"><table class="ap-table"><thead><tr><th>Folio</th><th>Saldo vigente</th><th>Aplicar · CLP</th></tr></thead><tbody>${d.rows.map((r,i)=>`<tr><th>${e(r.folio)}</th><td>${money(r.balance)}</td><td><input name="amount_${r.id}" data-joint-amount aria-label="Aplicar a folio ${e(r.folio)}" type="number" min="0" max="${r.balance}" step="1" required value="${d.amounts[i]}" ${d.rows.length===1?'readonly aria-readonly="true"':''}></td></tr>`).join('')}</tbody></table></div><p class="joint-payment-total" role="status">Total aplicado <strong data-joint-total>${money(d.amounts.reduce((a,b)=>a+b,0))}</strong> · Saldo a nuestro favor <strong data-joint-credit>${money(Math.max(0,d.total-d.amounts.reduce((a,b)=>a+b,0)))}</strong></p><div class="ap-form">${input('Fecha del pago','date',d.date,`type="date" required max="${d.today}"`)}<label class="field">Medio de pago<select name="method">${Object.entries(d.methods).map(([k,v])=>`<option value="${k}" ${k===d.method?'selected':''}>${e(v)}</option>`).join('')}</select></label>${input('Referencia / número de operación','reference',d.reference,'maxlength="160"')}${input('Notas','notes',d.notes,'maxlength="1000"')}</div><label class="field joint-payment-proof">Comprobante opcional · PDF, JPG o PNG · hasta 8 MB<input name="proof" type="file" accept="application/pdf,image/jpeg,image/png"></label><p class="hint">Puedes adjuntarlo después desde la cuenta corriente.</p>${d.proof?`<p class="hint">Comprobante seleccionado: ${e(d.proof.name)}. Se conservará si no eliges otro archivo.</p>`:''}<div class="ap-actions"><button class="button primary" type="submit">${d.rows.length===1?'Revisar pago':'Revisar pago conjunto'}</button></div></form>`,'','COMPRAS Y PROVEEDORES');
  }
  const encode=file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('No se pudo leer el comprobante.'));reader.readAsDataURL(file);});
  async function review(form){
    if(busy||!draft||!allowed())return;
    busy=true;state.saving=true;const token=epoch,d=draft,controls=[...form.elements];
    try{
      const values=Object.fromEntries(new FormData(form)),amounts=d.rows.length===1?distribute(Number(values.paid_total),d.rows):d.rows.map(r=>Number(values['amount_'+r.id]));
      if(amounts.some((v,i)=>!Number.isSafeInteger(v)||v<0||v>d.rows[i].balance))throw Error('Revisa los importes: cada aplicación debe ser cero o positiva y no superar el saldo.');
      const total=Number(values.paid_total),applied=amounts.reduce((a,b)=>a+b,0),balance=d.rows.reduce((n,r)=>n+r.balance,0);
      if(!Number.isSafeInteger(total)||total<=0)throw Error('Indica el monto efectivamente pagado.');
      if(applied!==Math.min(total,balance))throw Error('Las aplicaciones deben cubrir el pago o el saldo total seleccionado. Revisa los importes.');
      if(!Number.isSafeInteger(total)||total>999999999999)throw Error('El total supera el importe permitido.');
      const proof=form.elements.proof.files[0]||d.proof;
      if(proof&&(!proof.size||proof.size>8388608))throw Error('Adjunta un comprobante de hasta 8 MB.');
      controls.forEach(c=>c.disabled=true);
      const encoded=proof?await encode(proof):null;if(token!==epoch||!allowed())return;
      Object.assign(d,{amounts,total,date:values.date,method:values.method,reference:values.reference,notes:values.notes,proof});
      d.payload={request_id:crypto.randomUUID(),supplier_id:d.supplier.id,currency:'CLP',amount:total,occurred_on:d.date,method:d.method,reference:d.reference,notes:d.notes,confirmed:true,applications:d.rows.map((r,i)=>({invoice_id:r.id,version:r.version,amount:amounts[i]})).filter(a=>a.amount>0),invoices:d.rows.map(r=>({id:r.id,version:r.version})),...(total>balance?{overpayment_confirmation:{confirmed:true,selected_balance:balance,excess:total-balance}}:{}),...(proof?{proof:{name:proof.name,base64:encoded}}:{})};
      confirmation();
    }catch(err){if(token===epoch)showFormError(err.message);}
    finally{busy=false;state.saving=false;controls.forEach(c=>c.disabled=false);}
  }
  function confirmation(error=''){
    const d=draft,p=d.payload;
    modal(d.rows.length===1?'Confirmar pago':'Confirmar pago conjunto',d.supplier.name,
      `<form id="joint-payment-confirm"><div id="form-error" class="error ${error?'':'hidden'}" role="alert">${e(error)}</div><p><strong>${e(d.supplier.name)}</strong>${d.supplier.tax_id?' · '+e(d.supplier.tax_id):''} · ${d.rows.length} ${d.rows.length===1?'factura':'facturas'}</p><div class="joint-payment-table"><table class="ap-table"><thead><tr><th>Folio</th><th>Saldo vigente</th><th>Aplicar</th><th>Saldo restante</th></tr></thead><tbody>${d.rows.map((r,i)=>`<tr><th>${e(r.folio)}</th><td>${money(r.balance)}</td><td>${money(d.amounts[i])}</td><td>${money(r.balance-d.amounts[i])}</td></tr>`).join('')}</tbody></table></div><p class="joint-payment-total">Un único pago <strong>${money(p.amount)}</strong></p>${p.overpayment_confirmation?`<p class="ap-notice" role="alert">Registrarás un pago de ${money(p.amount)} para facturas con saldo de ${money(p.overpayment_confirmation.selected_balance)}. Los ${money(p.overpayment_confirmation.excess)} restantes quedarán como saldo a nuestro favor con este proveedor. ¿Confirmas?</p>`:''}<p>${e(d.date)} · ${e(d.methods[d.method])}${d.reference?' · '+e(d.reference):''}</p><p>${d.proof?'Comprobante compartido: <strong>'+e(d.proof.name)+'</strong>':'Sin comprobante. Puedes adjuntarlo después desde la cuenta corriente.'}</p><p class="hint">El pago y sus aplicaciones${d.proof?', junto con el comprobante,':''} se guardarán juntos. Los saldos se volverán a comprobar antes de guardar.</p>${!p.overpayment_confirmation?`<label class="ap-check"><input name="confirmed" type="checkbox" required ${d.uncertain?'checked':''}>Confirmo que este pago ya fue realizado y revisé el proveedor, todos los folios, los importes y el total.</label>`:''}<div class="ap-actions"><button class="button primary" type="submit" ${d.needsRefresh?'disabled':''}>${d.uncertain?'Reintentar el mismo registro':p.overpayment_confirmation?'Confirmar pago y saldo a favor':'Registrar un pago de '+money(p.amount)}</button><button class="button secondary" type="button" data-joint-back ${d.uncertain?'disabled':''}>Cancelar</button>${d.needsRefresh?'<button class="button secondary" type="button" data-joint-refresh>Actualizar saldos y revisar</button>':''}</div></form>`,'','COMPRAS Y PROVEEDORES');
  }
  async function save(form){
    if(busy||!draft||draft.needsRefresh||!allowed()||(!draft.payload.overpayment_confirmation&&!form.elements.confirmed.checked))return;
    busy=true;state.saving=true;const d=draft,token=epoch,auth=authGeneration;let response;
    form.querySelectorAll('button,input').forEach(c=>c.disabled=true);d.uncertain=true;
    try{
      response=await fetch(d.rows.length===1?'/api/suppliers/account-invoice-payments':'/api/suppliers/account-joint-payments',{method:'POST',headers:{'Content-Type':'application/json','X-ERP-Local':'1'},body:JSON.stringify(d.payload)});
      const result=await responsePayload(response);
      if(token!==epoch||auth!==authGeneration||!allowed())return;
      if(!Number.isInteger(result.id)||(d.payload.proof&&!Number.isInteger(result.file_id)))throw Error('No se recibió una confirmación completa. Reintenta el mismo registro.');
      d.uncertain=false;draft=null;
      const covered=d.rows.filter((r,i)=>d.amounts[i]>0);
      modal(d.rows.length===1?'Pago registrado':'Pago conjunto registrado',`${d.supplier.name} · CC-${result.id}`,`<p class="joint-payment-total">Un pago registrado <strong>${money(d.payload.amount)}</strong></p><p>${covered.length} ${covered.length===1?'factura vinculada':'facturas vinculadas'}: ${covered.map(r=>e(r.folio)).join(', ')}.</p><p>Aplicado a facturas: <strong>${money(result.applied)}</strong> · Saldo a nuestro favor disponible: <strong>${money(result.available)}</strong>.</p><p>${d.payload.proof?'El comprobante quedó vinculado a este único pago y sus facturas.':'Pago registrado sin comprobante. Puedes adjuntarlo después desde la cuenta corriente.'}</p><div class="ap-actions">${d.payload.proof?`<a class="button primary" href="/api/suppliers/account-files/${result.file_id}" target="_blank" rel="noopener noreferrer">Ver comprobante compartido</a>`:''}<button class="button secondary" type="button" data-ac="open" data-id="${d.supplier.id}">Ver cuenta corriente</button></div>`,'','COMPRAS Y PROVEEDORES');
      try{await d.onSaved(d.rows.map(r=>r.id));}catch{toast('Pago registrado. Actualiza la lista de facturas para ver los saldos.');}
    }catch(err){
      if(token===epoch&&auth===authGeneration&&allowed()){
        if(response&&[400,403,404,409,413,415].includes(response.status)){d.uncertain=false;d.needsRefresh=response.status===409;}
        confirmation(d.uncertain?'No se pudo confirmar el resultado. Reintenta este mismo registro; no se creará otro pago.':err.message);
      }
    }finally{busy=false;state.saving=false;}
  }
  function distribute(total,rows){let remaining=total;return rows.map(row=>{const amount=Math.max(0,Math.min(remaining,row.balance));remaining-=amount;return amount;});}
  async function refreshBalances(){
    if(!draft||busy||draft.uncertain)return;busy=true;const d=draft,token=epoch,auth=authGeneration;
    try{
      const data=await api('/api/suppliers/payables');if(draft!==d||token!==epoch||auth!==authGeneration||!allowed())return;
      const rows=d.rows.map(r=>data.invoices.find(i=>i.id===r.id));
      if(rows.some(r=>!r||r.voided_at||r.payment_review||r.payment_hold||r.balance<=0||r.supplier_id!==d.supplier.id))throw Error('La selección ya no está disponible. Cierra este panel y selecciona nuevamente las facturas pendientes.');
      d.rows=rows;d.amounts=distribute(d.total,rows);d.payload=null;d.needsRefresh=false;edit();
    }catch(err){if(token===epoch)showFormError(err.message);}finally{busy=false;}
  }
  document.addEventListener('submit',event=>{if(event.target.id==='joint-payment-edit'){event.preventDefault();review(event.target);}if(event.target.id==='joint-payment-confirm'){event.preventDefault();save(event.target);}});
  document.addEventListener('input',event=>{
    if(!draft||busy)return;const f=event.target.form;
    if(event.target.matches('[data-joint-paid]')){const values=distribute(Number(event.target.value)||0,draft.rows);[...f.querySelectorAll('[data-joint-amount]')].forEach((input,i)=>input.value=values[i]);}
    if(event.target.matches('[data-joint-amount],[data-joint-paid]')){
      const applied=[...f.querySelectorAll('[data-joint-amount]')].reduce((n,r)=>n+Number(r.value||0),0);
      f.querySelector('[data-joint-total]').textContent=money(applied);
      f.querySelector('[data-joint-credit]').textContent=money(Math.max(0,Number(f.elements.paid_total.value||0)-applied));
    }
  });
  document.addEventListener('click',event=>{if(event.target.closest('[data-joint-back]')&&draft&&!busy&&!draft.uncertain)edit();if(event.target.closest('[data-joint-refresh]'))refreshBalances();});
  window.addEventListener('beforeunload',event=>{if(draft?.uncertain){event.preventDefault();event.returnValue='';}});
  return {open,reset};
})();
