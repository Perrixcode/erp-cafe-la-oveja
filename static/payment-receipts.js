'use strict';
window.OvejaPaymentReceipts=(()=>{
  let dialog=null, data=null, selected=new Set(), epoch=0, controller=null, previewURL=null, busy=false, choices={};
  const e=escapeHTML, money=clp;
  function clearPreview(){if(previewURL)URL.revokeObjectURL(previewURL);previewURL=null;dialog?.querySelector('[data-pr-preview]')?.replaceChildren();}
  function reset(){epoch++;controller?.abort();controller=null;clearPreview();data=null;selected.clear();choices={};busy=false;if(dialog?.open)dialog.close();}
  function ensure(){
    if(dialog)return;
    dialog=document.createElement('dialog');dialog.className='pr-dialog';dialog.setAttribute('aria-labelledby','pr-title');document.body.append(dialog);
    dialog.addEventListener('close',()=>{if(!dialog.open)reset();});
    dialog.addEventListener('click',async event=>{
      const action=event.target.closest('[data-pr-action]')?.dataset.prAction;if(!action)return;
      if(action==='close'){reset();return;}if(busy)return;
      if(action==='refresh')await load(dialog.querySelector('[name=payment-date]').value);
      if(action==='all'){selected=new Set(data.payments.slice(0,data.limits.payments).map(r=>r.key));clearPreview();renderRows();}
      if(action==='none'){selected.clear();clearPreview();renderRows();}
      if(action==='preview')await preview();
    });
    dialog.addEventListener('change',event=>{
      if(event.target.name==='payment-date'){load(event.target.value);return;}
      const fileKey=event.target.dataset.prFile;if(fileKey&&!busy){if(event.target.value)choices[fileKey]=Number(event.target.value);else delete choices[fileKey];clearPreview();return;}
      const key=event.target.dataset.prKey;if(!key||busy)return;
      if(event.target.checked)selected.add(key);else selected.delete(key);
      clearPreview();summary();
    });
  }
  function fail(error){if(error.name==='AbortError')return;const box=dialog?.querySelector('[data-pr-error]');if(box)box.textContent=error.message;}
  function summary(){
    const chosen=(data?.payments||[]).filter(r=>selected.has(r.key));
    dialog.querySelector('[data-pr-summary]').textContent=`${chosen.length} pagos seleccionados · ${money(chosen.reduce((s,r)=>s+r.amount,0))} · ${chosen.filter(r=>!r.files.length).length} sin comprobante.`;
    dialog.querySelector('[data-pr-action=preview]').disabled=busy||!chosen.length||chosen.length>data.limits.payments;
  }
  function renderRows(){
    dialog.querySelector('[data-pr-rows]').innerHTML=data.payments.map(row=>`<div class="pr-payment"><input aria-label="Seleccionar ${e(row.supplier_name)} ${e(row.key)}" type="checkbox" data-pr-key="${e(row.key)}" ${selected.has(row.key)?'checked':''}><span><strong>${e(row.supplier_name)}</strong><span>${e(row.key)} · ${money(row.amount)} · ${e(row.invoices.map(i=>i.folio).join(', ')||'Anticipo sin folio aplicado')}</span><small>${row.files.length?`${row.files.length} adjunto(s); se intentará incorporar el último, sin sustituirlo automáticamente.`:'Sin comprobante adjunto; el pago se incluirá en el resumen.'}</small>${row.files.length>1?`<label class="field">Versión a incorporar<select data-pr-file="${e(row.key)}"><option value="">Último adjunto · ${e(row.files[0].created_at)}</option>${row.files.map(f=>`<option value="${f.id}" ${choices[row.key]===f.id?'selected':''}>Elegir #${f.id} · ${e(f.created_at)} · ${e(f.name)}</option>`).join('')}</select></label>`:''}</span></div>`).join('')||'<p>No hay pagos registrados para esta fecha.</p>';
    summary();
  }
  async function request(url,signal,pdf=false){
    const response=await fetch(url,{credentials:'same-origin',signal,cache:'no-store'});
    if(!response.ok){let message='No se pudo preparar la consulta.';try{message=(await response.json()).error||message;}catch{}
      if(response.status===401){reset();lockAccess('Tu sesión terminó.');}
      throw Error(message);
    }
    if(pdf){if(!(response.headers.get('Content-Type')||'').startsWith('application/pdf'))throw Error('El servidor no entregó un PDF verificable.');return response.blob();}
    return response.json();
  }
  async function load(day=''){
    const token=++epoch;controller?.abort();controller=new AbortController();clearPreview();busy=true;data=null;selected.clear();choices={};
    dialog.innerHTML=`<div class="pr-header"><div><span class="eyebrow">FACTURAS Y PAGOS</span><h2 id="pr-title">Exportar comprobantes del día</h2></div><button class="button secondary" data-pr-action="close">Cerrar</button></div><p>Selecciona pagos por su fecha registrada. Los pagos conjuntos y sus respaldos se incluyen una sola vez. No cambia saldos ni estados de pago.</p><label class="field">Fecha de pago<input type="date" name="payment-date" value="${e(day)}" required></label><div class="ap-actions"><button class="button secondary" data-pr-action="refresh">Actualizar</button><button class="button secondary" data-pr-action="all" disabled>Seleccionar hasta 100</button><button class="button secondary" data-pr-action="none" disabled>Quitar selección</button></div><p data-pr-notice class="hint" role="status">Cargando pagos…</p><div data-pr-rows></div><p data-pr-summary role="status"></p><p data-pr-error role="alert" class="error"></p><button class="button primary" data-pr-action="preview" disabled>Vista previa del PDF</button><p class="hint">Hasta 100 pagos, 40 comprobantes únicos, 32 MB de archivos y 120 páginas de respaldo por exportación. Se conservan todas las páginas incorporadas. Si un archivo no puede abrirse, el PDF lo advertirá. Las versiones originales siguen disponibles en el detalle del pago.</p><div data-pr-preview></div>`;
    try{
      const value=await request('/api/suppliers/payment-receipts'+(day?'?date='+encodeURIComponent(day):''),controller.signal);
      if(token!==epoch||!dialog.open)return;data=value;busy=false;
      dialog.querySelector('[name=payment-date]').value=data.date;
      dialog.querySelector('[data-pr-notice]').textContent=`${data.payments.length} pagos en la fecha.${data.undated_payments?' Hay '+data.undated_payments+' pagos históricos sin fecha que no se asignan a este día.':''} Los folios son los vinculados actualmente.`;
      for(const action of ['all','none'])dialog.querySelector(`[data-pr-action=${action}]`).disabled=false;
      if(data.payments.length<=data.limits.payments)selected=new Set(data.payments.map(r=>r.key));
      renderRows();
    }catch(error){if(token===epoch)fail(error);}finally{if(token===epoch)busy=false;}
  }
  async function preview(){
    if(!data||busy)return;const token=epoch;busy=true;clearPreview();summary();dialog.querySelector('[data-pr-error]').textContent='';
    const controls=[...dialog.querySelectorAll('input,button,select')].filter(el=>el.dataset.prAction!=='close');controls.forEach(el=>el.disabled=true);
    dialog.querySelector('[data-pr-summary]').textContent='Preparando y verificando comprobantes…';
    try{
      const params=new URLSearchParams({date:data.date,ids:[...selected].join(','),token:data.token,files:JSON.stringify(Object.fromEntries(Object.entries(choices).filter(([k])=>selected.has(k))))});
      const check=await request('/api/suppliers/payment-receipts/preview?'+params,controller.signal);
      if(token!==epoch||!dialog.open)return;
      const warnings=check.payments.flatMap(row=>row.warnings.map(w=>row.key+': '+w));
      const blob=await request('/api/suppliers/payment-receipts.pdf?'+params,controller.signal,true);
      if(token!==epoch||!dialog.open)return;previewURL=URL.createObjectURL(blob);
      const area=dialog.querySelector('[data-pr-preview]');
      area.innerHTML=`${warnings.length?`<div class="ap-notice" role="alert"><strong>Respaldos pendientes</strong>${warnings.map(w=>`<p>${e(w)}</p>`).join('')}<p>Puedes elegir expresamente una versión anterior en la lista y volver a generar la vista previa.</p></div>`:''}<h3>Vista previa · ${e(data.date)}</h3><p>Revisa el resumen y las advertencias antes de descargar. Si tu dispositivo no muestra el visor, abre la vista previa en otra pestaña.</p><div class="ap-actions"><a class="button primary" data-pr-download download="comprobantes-pagos-${e(data.date)}.pdf">Descargar PDF revisado</a><a class="button secondary" data-pr-open target="_blank" rel="noopener noreferrer">Abrir vista previa</a></div><iframe class="pr-pdf" title="Vista previa de comprobantes del día"></iframe>`;
      area.querySelector('iframe').src=previewURL;area.querySelector('[data-pr-download]').href=previewURL;area.querySelector('[data-pr-open]').href=previewURL;
      area.scrollIntoView({block:'start',behavior:'smooth'});
    }catch(error){if(token===epoch)fail(error);}finally{if(token===epoch){busy=false;controls.forEach(el=>el.disabled=false);summary();}}
  }
  async function open(){if(state.user?.role!=='partner')return;ensure();reset();dialog.showModal();await load();}
  return {open,reset};
})();
