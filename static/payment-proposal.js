'use strict';
window.OvejaPaymentProposal=(()=>{
  const e=escapeHTML,money=clp,requests=new WeakMap();
  let value=null,params=null,heldInvoice=null,busy=false,epoch=0;
  const allowed=()=>state.user?.role==='partner';
  const button=(label,action,id='')=>`<button type="button" class="button secondary" data-pp="${action}" data-id="${e(id)}">${label}</button>`;
  const input=(label,name,val='',extra='')=>`<label class="field">${label}<input name="${name}" value="${e(val)}" ${extra}></label>`;
  const errors='<div id="form-error" class="error hidden" role="alert"></div>';
  function reset(){epoch++;value=null;params=null;heldInvoice=null;busy=false;}
  function shell(title,subtitle,body){modal(title,subtitle,`<div id="proposal-shell">${body}</div>`,'','PROGRAMACIÓN DE PAGOS');}
  async function open(today){
    if(!allowed())return;
    const token=++epoch,auth=authGeneration;
    params=params||{week_start:today,budget:'',allow_partial:true};value=null;
    shell('Propuesta semanal','Genera y revisa una programación por presupuesto y vencimiento.',
      `<p>Se priorizan los vencimientos más antiguos. El presupuesto incluye los pagos ya registrados y lo programado para la semana. Esta propuesta coordina transferencias; requiere factura original, saldo validado y ficha completa. Primero aplica los créditos disponibles de cada proveedor.</p><form id="proposal-generate">${errors}<div class="ap-form">${input('Semana · lunes a domingo','week_start',params.week_start,'type="date" required')}${input('Presupuesto total de la semana · CLP','budget',params.budget,'type="number" min="0" max="999999999999" step="1" required')}</div><label class="ap-check"><input type="checkbox" name="allow_partial" ${params.allow_partial?'checked':''}>Permitir pagos parciales cuando no alcance el presupuesto.</label><button class="button primary">Generar propuesta</button></form><h3>Programaciones guardadas</h3><div id="proposal-recent" role="status">Cargando…</div>`);
    try{
      const result=await api('/api/suppliers/payment-proposals');
      if(token!==epoch||auth!==authGeneration||!allowed()||!$('#proposal-recent'))return;
      $('#proposal-recent').innerHTML=result.proposals.map(r=>`<article class="ap-card"><strong>Propuesta #${r.id} · ${e(r.week_start)} a ${e(r.week_end)}</strong><p>${money(r.total)} programados · ${e(r.actor)} · ${e(r.scheduled_on)}</p>${button('Ver revisión guardada','saved',r.id)}</article>`).join('')||'<p class="hint">Todavía no hay propuestas guardadas.</p>';
    }catch(err){if(token===epoch&&$('#proposal-recent'))$('#proposal-recent').textContent=err.message;}
  }
  function reasons(row){return row.reasons.map(k=>e(value.reasons[k]||k)).join(' · ')+(row.hold_reason?' · '+e(row.hold_reason):'');}
  function reference(row){return `${e(row.supplier_name)} · RUT ${e(row.tax_id||'por completar')} · folio ${e(row.folio)}`;}
  function review(){
    const p=value.params;
    shell('Revisar propuesta semanal',`${p.week_start} a ${p.week_end} · importes CLP`,
      `<div class="ap-stats"><article><span>Presupuesto total</span><strong>${money(p.budget)}</strong></article><article><span>Pagos registrados esta semana</span><strong>${money(value.paid_amount)}</strong></article><article><span>Ya programado esta semana</span><strong>${money(value.reserved_amount)}</strong></article><article><span>Disponible para proponer</span><strong>${money(value.available_budget)}</strong></article></div>${value.over_budget?`<p class="error">Los pagos registrados y lo programado superan el presupuesto en ${money(value.over_budget)}. Revisa esas programaciones o el presupuesto.</p>`:''}<p class="hint">Orden: vencimiento, emisión, proveedor y factura. Puedes ajustar montos o dejar 0 para excluir. Se comprueban otra vez los saldos y las exclusiones al guardar. Máximo 100 facturas por confirmación.</p>${button('Cambiar semana o presupuesto','open')}
      ${value.candidates.length?`<form id="proposal-review">${errors}${input('Fecha propuesta de pago','scheduled_on',value.suggested_date,`type="date" min="${value.suggested_date}" max="${p.week_end}" required`)}${value.candidates.map(r=>`<article class="ap-card"><h3>${reference(r)}</h3><p>Vence ${e(r.due_on)} · ${e(r.priority)} · Saldo: ${money(r.balance)}</p>${input('Monto a programar · folio '+e(r.folio)+' · CLP','amount_'+r.id,r.proposed_amount,`type="number" min="0" max="${r.balance}" step="1" data-proposal-amount="${r.id}"`)}<p class="hint">${e(r.decision)}</p>${button('Ver factura','invoice',r.id)}</article>`).join('')}<p id="proposal-total" role="status" aria-live="polite"></p>${input('Nota de la revisión (opcional)','notes','','maxlength="1000"')}<label class="ap-check"><input type="checkbox" name="confirmed" required>Revisé documentos, vencimientos y montos. Guardar solo actualizará la programación.</label><button class="button primary">Guardar programación revisada</button></form>`:'<p class="ap-notice">No hay facturas elegibles para una propuesta nueva. Revisa los motivos de exclusión y las programaciones existentes.</p>'}
      <details class="ap-card"><summary>Programación existente · ${value.existing.length}</summary><p class="hint">Se conserva; esta propuesta no reemplaza esas fechas ni importes.</p>${value.existing.map(r=>`<p><strong>${reference(r)}</strong><br>${e(r.scheduled_on)} · ${money(r.planned_amount)} · ${r.in_week?'Reserva presupuesto de esta semana':'Fuera de esta semana'}<br>${reasons(r)}</p>${button('Ver factura','invoice',r.id)}`).join('')||'<p>Sin programación existente.</p>'}</details>
      <details class="ap-card"><summary>Facturas excluidas · ${value.excluded.length}</summary>${value.excluded.map(r=>`<article class="ap-card"><strong>${reference(r)}</strong><p>${reasons(r)}</p>${r.available_credit?`<p>Créditos por aplicar: ${money(r.available_credit)}</p>`:''}${button('Ver factura','invoice',r.id)}</article>`).join('')||'<p>Sin exclusiones.</p>'}</details><p class="hint">Una programación no registra dinero pagado ni conciliación bancaria. No se envían mensajes ni se ejecutan transferencias.</p>`);
    updateTotal();
  }
  function updateTotal(){
    const form=$('#proposal-review');if(!form||!value)return;
    const fields=[...form.querySelectorAll('[data-proposal-amount]')],total=fields.reduce((sum,field)=>sum+Number(field.value||0),0),count=fields.filter(f=>Number(f.value)>0).length;
    $('#proposal-total').textContent=`${count} factura(s) · Nueva programación: ${money(total)} · Total semanal con pagos y reservas: ${money(total+value.reserved_amount+value.paid_amount)} · Queda disponible: ${money(value.available_budget-total)}`;
    fields[0]?.setCustomValidity(total<=0?'Indica al menos un monto positivo.':count>100?'Selecciona hasta 100 facturas.':total>value.available_budget?'Se supera el presupuesto disponible.':'');
  }
  async function saveRequest(form,path,body){
    const signature=JSON.stringify([path,body]);let attempt=requests.get(form);
    if(attempt?.signature!==signature){attempt={signature,id:crypto.randomUUID()};requests.set(form,attempt);}
    return api(path,'POST',{...body,request_id:attempt.id});
  }
  async function saved(id){
    const token=++epoch,auth=authGeneration,result=await api('/api/suppliers/payment-proposals/'+Number(id));
    if(token!==epoch||auth!==authGeneration||!allowed())return;
    shell('Propuesta guardada #'+result.id,`${result.week_start} a ${result.week_end}`,
      `<p><strong>${money(result.total)} programados para ${e(result.scheduled_on)}</strong></p><p>Revisada por ${e(result.actor)} · ${e(new Date(result.created_at).toLocaleString('es-CL',{timeZone:'America/Santiago'}))}</p><p>Presupuesto: ${money(result.budget)} · Pagos registrados al confirmar: ${money(result.paid_amount)} · Reservas existentes al confirmar: ${money(result.reserved_amount)}</p>${result.notes?`<p>${e(result.notes)}</p>`:''}<p class="hint">Esta es la revisión original conservada. Las facturas pueden haber cambiado o recibido pagos después. Consulta su detalle para ver el saldo y la programación vigentes.</p>${result.selected.map(r=>`<article class="ap-card"><strong>${reference(r)}</strong><p>Importe revisado: ${money(r.amount)} · Saldo al confirmar: ${money(r.balance)}</p>${button('Ver factura actual','invoice',r.id)}</article>`).join('')}${button('Volver a propuestas','open')}`);
  }
  async function hold(id){
    const token=++epoch,auth=authGeneration,data=await api('/api/suppliers/payables');
    if(token!==epoch||auth!==authGeneration||!allowed())return;
    const row=data.invoices.find(r=>r.id===Number(id));if(!row)throw Error('Factura no encontrada.');
    heldInvoice=row;
    shell(row.payment_hold?'Resolver pausa de pago':'Pausar pago / marcar disputa',`${row.supplier_name} · folio ${row.folio}`,
      `<form id="proposal-hold">${errors}${row.payment_hold?`<p>Pausa vigente: ${e(row.payment_hold.reason)} · ${e(row.payment_hold.actor)}</p><p>Resolverla permitirá proponer pagos nuevamente. La programación anterior no se restaura.</p>`:'<p>La factura seguirá formando parte de la deuda. Se retirará su programación y se excluirá de propuestas/listados hasta resolver esta pausa.</p>'}${input('Motivo','reason','','required maxlength="500"')}<button class="button primary">${row.payment_hold?'Resolver pausa':'Guardar pausa'}</button></form>`);
  }
  async function submit(form){
    if(busy||!allowed())return;busy=true;const token=epoch,auth=authGeneration;
    const controls=[...form.querySelectorAll('button')];controls.forEach(c=>{c.disabled=true;});
    const submitButton=form.querySelector('.primary'),label=submitButton?.textContent,fields=[...form.querySelectorAll('input,select,textarea')].map(field=>[field,field.disabled]);
    const mutating=form.id!=='proposal-generate';if(mutating)state.saving=true;
    try{
      const values=Object.fromEntries(new FormData(form));
      fields.forEach(([field])=>field.disabled=true);
      if(submitButton)submitButton.textContent=mutating?'Guardando…':'Calculando…';
      if(form.id==='proposal-generate'){
        params={week_start:values.week_start,budget:Number(values.budget),allow_partial:form.elements.allow_partial.checked};
        const result=await api('/api/suppliers/payment-proposal','POST',params);
        if(token!==epoch||auth!==authGeneration||!allowed())return;value=result;params=result.params;review();
      }else if(form.id==='proposal-review'){
        const items=[...form.querySelectorAll('[data-proposal-amount]')].filter(f=>Number(f.value)>0).map(f=>{const row=value.candidates.find(r=>r.id===Number(f.dataset.proposalAmount));return {id:row.id,version:row.version,amount:Number(f.value)};});
        const result=await saveRequest(form,'/api/suppliers/payment-proposals',{...value.params,token:value.token,items,scheduled_on:values.scheduled_on,notes:values.notes,confirmed:form.elements.confirmed.checked});
        if(token!==epoch||auth!==authGeneration||!allowed())return;
        await window.OvejaPayables.refresh();
        try{await saved(result.id);}catch(err){shell('Programación guardada #'+result.id,`${money(result.total)} · ${result.scheduled_on}`,`<p>La programación quedó guardada. No se pudo cargar su detalle: ${e(err.message)}</p>${button('Cargar revisión guardada','saved',result.id)}`);}
        toast('Programación guardada. Los saldos y pagos no cambiaron.');
      }else{
        const row=heldInvoice;
        await saveRequest(form,`/api/suppliers/invoices/${row.id}/payment-hold`,{version:row.version,hold:!row.payment_hold,reason:values.reason});
        if(token!==epoch||auth!==authGeneration||!allowed())return;
        await window.OvejaPayables.showInvoice(row.id);toast(row.payment_hold?'Pausa resuelta.':'Pago en pausa.');
      }
    }catch(err){if(auth===authGeneration&&token===epoch)showFormError(err.message);}
    finally{busy=false;if(mutating)state.saving=false;controls.forEach(c=>c.disabled=false);fields.forEach(([field,disabled])=>field.disabled=disabled);if(submitButton)submitButton.textContent=label;}
  }
  document.addEventListener('submit',event=>{if(!['proposal-generate','proposal-review','proposal-hold'].includes(event.target.id))return;event.preventDefault();submit(event.target);});
  document.addEventListener('input',event=>{if(event.target.closest('#proposal-review'))updateTotal();});
  document.addEventListener('click',async event=>{
    const b=event.target.closest('[data-pp]');if(!b||busy||!allowed())return;
    try{
      if(b.dataset.pp==='open')await open(b.dataset.today||params?.week_start||value?.today);
      if(b.dataset.pp==='saved')await saved(b.dataset.id);
      if(b.dataset.pp==='invoice')await window.OvejaPayables.showInvoice(Number(b.dataset.id));
      if(b.dataset.pp==='hold')await hold(b.dataset.id);
    }catch(err){toast(err.message);}
  });
  document.addEventListener('click',event=>{if($('#proposal-shell')&&event.target.closest('[data-action=close]')&&!state.saving)reset();},true);
  $('#modal').addEventListener('cancel',()=>{if($('#proposal-shell')&&!state.saving)reset();});
  return {open,reset,hold};
})();
