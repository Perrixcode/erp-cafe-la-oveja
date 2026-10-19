'use strict';
window.OvejaCashBags=(()=>{
  const host=document.createElement('section');
  host.id='cash-bags-workspace';host.className='hidden';host.setAttribute('aria-label','Entregas de efectivo');
  $('#module-placeholder').append(host);
  const dialog=document.createElement('dialog');dialog.className='bags-preview';dialog.setAttribute('aria-label','Vista previa de etiquetas');
  document.body.append(dialog);
  const e=escapeHTML, fmt=n=>n===null||n===undefined?'Sin completar':new Intl.NumberFormat('es-CL',{style:'currency',currency:'CLP',maximumFractionDigits:0}).format(n);
  let epoch=0,active=false,current=null,attempts=new Map(),busy=false;
  const allowed=()=>state.user&&['partner','cashier'].includes(state.user.role);
  const input=(label,name,value='',extra='')=>`<label class="field">${label}<input name="${name}" value="${e(value??'')}" ${extra}></label>`;
  function leave(){active=false;epoch++;host.classList.add('hidden');dialog.close();dialog.replaceChildren();}
  function reset(){leave();host.replaceChildren();current=null;attempts.clear();busy=false;}
  function message(value){const el=host.querySelector('[data-bag-error]');if(el){el.textContent=value;el.hidden=!value;}}
  function base(content){host.innerHTML=`<div data-bag-error class="error" role="alert" hidden></div>${content}`;}
  async function request(path,method='GET',body){
    const generation=authGeneration;
    const response=await fetch(path,{method,headers:method==='GET'?{}:{'Content-Type':'application/json','X-ERP-Local':'1'},body:body?JSON.stringify(body):undefined});
    if(generation!==authGeneration)throw new Error('La sesión cambió.');
    if(response.status===401){lockAccess('Tu sesión terminó.');throw new Error('Inicia sesión nuevamente.');}
    let data;try{data=await response.json();}catch{throw new Error('No se pudo confirmar la respuesta. Reintenta la misma operación.');}
    if(!response.ok){const error=new Error(data.error||'No se pudo completar la operación.');error.status=response.status;throw error;}
    return data;
  }
  async function write(path,body){
    const signature=JSON.stringify(body);
    let attempt=attempts.get(path);
    if(attempt&&attempt.signature!==signature)throw new Error('Hay un intento sin confirmar. Reintenta con los mismos datos antes de cambiarlos.');
    if(!attempt){attempt={signature,id:crypto.randomUUID()};attempts.set(path,attempt);}
    try{const result=await request(path,'POST',{...body,request_id:attempt.id});attempts.delete(path);return result;}
    catch(error){if(error.status>=400&&error.status<500&&error.status!==408)attempts.delete(path);throw error;}
  }
  async function listing(before=''){
    const token=++epoch;current=null;
    base('<div class="load-placeholder" role="status"><span class="load-coffee" aria-hidden="true"></span><span>Cargando bolsas…</span></div>');
    try{
      const data=await request('/api/cash-bags'+(before?'?before='+before:''));
      if(token!==epoch||!active)return;
      base(`<div class="bags-intro"><div><h2>Preparar bolsas para completar después</h2><p>Imprime las etiquetas, identifica cada bolsa y completa su ficha al cierre.</p></div><strong>${data.total} bolsas registradas</strong></div>
        ${data.cashier_management?'<p><a class="button secondary" href="/bag-cashiers">Administrar personas y accesos</a></p>':''}
        ${data.can_create?`<form data-bag-form="create" class="bags-card"><h3>Nuevo lote de etiquetas</h3><p>Siguiente número: <strong>BOL-${String(data.next_number).padStart(6,'0')}</strong></p><div class="bags-toolbar">${input('Cantidad de bolsas','quantity',4,'type="number" min="1" max="100" step="1" required')}<p>Etiquetas de 7 × 12 cm · 4 por hoja Carta<br>Los números se reservan al crear el lote y no se reutilizan.</p></div><label class="bags-confirm"><input type="checkbox" name="confirmed" required> Confirmo crear estas fichas vacías y reservar sus números.</label><button class="button primary" type="submit">Crear lote y ver etiquetas</button></form>`:data.import_pending?'<p class="bags-card">Las primeras 40 etiquetas están reservadas para los lotes preimpresos. La creación de lotes se habilitará cuando se importen ambos manifiestos definitivos.</p>':'<p class="bags-card">Las personas autorizadas preparan los lotes. Puedes abrir y completar las fichas existentes.</p>'}
        <div class="bags-toolbar"><h3>Fichas de bolsas</h3><button class="button secondary" data-bag-refresh>Actualizar</button></div>
        <div class="bags-table-wrap"><table class="bags-table"><thead><tr><th>N.º / ID</th><th>Estado</th><th>Fecha y caja</th><th>C · Bolsa declarada</th><th></th></tr></thead><tbody>${data.bags.map(b=>`<tr><td><strong>${e(b.label)}</strong></td><td><span class="bags-status">${e(b.status_label)}</span></td><td>${e(b.business_date||'Sin completar')}<br>${e(b.register_name||'')}</td><td>${fmt(b.bag_amount)}</td><td><button class="button secondary" data-bag-open="${b.id}">Abrir ficha</button></td></tr>`).join('')||'<tr><td colspan="5">Todavía no hay fichas disponibles.</td></tr>'}</tbody></table></div>
        <div class="bags-toolbar">${before?'<button class="button secondary" data-bag-refresh>Volver a las recientes</button>':''}${data.next_before?`<button class="button secondary" data-bag-page="${data.next_before}">Ver anteriores</button>`:''}</div>
        <h3>Lotes recientes</h3><div class="bags-batches">${data.batches.map(b=>`<article class="bags-card"><strong>BOL-${String(b.first_number).padStart(6,'0')} — BOL-${String(b.first_number+b.quantity-1).padStart(6,'0')}</strong><p>${b.quantity} etiquetas · ${e(b.actor)}${b.is_test?' · Prueba local':''}</p><p>${b.blank_count} sin completar · ${b.pending_count} pendientes · ${b.verified_count} verificadas · ${b.difference_count} con diferencia</p><button class="button secondary" data-bag-preview="${b.id}">Ver / reimprimir etiquetas</button></article>`).join('')||'<p>Sin lotes creados.</p>'}</div>`);
    }catch(error){if(token===epoch&&active)base(`<p class="error" role="alert">${e(error.message)}</p><button class="button secondary" data-bag-refresh>Reintentar</button>`);}
  }
  function bagPath(id){const p=new URLSearchParams(location.search);p.set('module','cash');p.set('section','Entregas');p.set('bag',id);history.replaceState(null,'','/?'+p);}
  const when=value=>value?new Date(value).toLocaleString('es-CL',{timeZone:'America/Santiago'}):'Sin información';
  async function detail(id){
    const token=++epoch;base('<div class="load-placeholder" role="status"><span class="load-coffee" aria-hidden="true"></span><span>Abriendo ficha…</span></div>');
    try{
      const b=await request('/api/cash-bags/'+id);
      if(token!==epoch||!active)return;
      current=b;bagPath(id);
      base(`<div class="bags-toolbar"><button class="button secondary" data-bag-list>← Todas las bolsas</button><button class="button secondary" data-bag-preview="${b.batch_id}">Ver etiquetas del lote</button></div>
        <section class="bags-card"><div class="bags-intro"><div><span class="eyebrow">N.º DE BOLSA / ID</span><h2>${e(b.label)}</h2></div><span class="bags-status">${e(b.status_label)}</span></div>
        ${b.original_declaration?`<div class="bags-trace"><p>Responsable original de la entrega: <strong>${e(b.original_declaration.responsible||'Sin información')}</strong><br>${e(when(b.original_declaration.created_at))} · hora Chile</p><p>Último guardado: <strong>${e(b.last_editor?.actor||'Sin información')}</strong><br>${e(when(b.last_editor?.created_at))} · hora Chile</p></div>`:''}
        ${b.declaration_identity?`<p>Cuenta autenticada: <strong>${e(b.declaration_identity.account_name)}</strong>${b.declaration_identity.email?` (${e(b.declaration_identity.email)})`:''}${b.declaration_identity.shared?` · Compartida<br>Persona declarada: <strong>${e(b.declaration_identity.declared_person)}</strong> · identidad personal no verificada`:``}</p>`:''}<p>Datos de ingreso manual · sin precarga de cierres Toteat.</p><p>Abrir o escanear esta ficha no confirma entrega ni verificación de efectivo.</p>
        ${b.closure_id?`<p>Esta bolsa pertenece a un cierre digital y conserva su número y QR.</p><a class="button primary" href="/?module=cash&section=Cierres&closure=${e(b.closure_id)}">Abrir cierre digital</a><p>A original: <strong>${fmt(b.physical_count_A)}</strong> · Ajuste posterior: <strong>${fmt(b.post_count_net)}</strong> · B: <strong>${fmt(b.retained)}</strong> · C: <strong>${fmt(b.bag_amount)}</strong></p>`:b.status==='void'?`<p class="error">Bolsa anulada. ${e(b.void_reason)}</p>`:(['verified','difference'].includes(b.status)||(b.status==='pending'&&!b.can_correct))?`<p>${e(b.business_date)} · ${e(b.register_name)} · ${e(b.shift_name)} · ${e(b.responsible)}</p><p>A contado: <strong>${fmt(b.counted)}</strong> · B fondo: <strong>${fmt(b.retained)}</strong> · C declarado: <strong>${fmt(b.bag_amount)}</strong></p>`:`<form data-bag-form="complete"><div class="bags-grid">
        ${input('Fecha operativa','business_date',b.business_date,'type="date" required')}
        ${input('Caja','register_name',b.register_name,'maxlength="100" required')}
        ${input('Turno','shift_name',b.shift_name,'maxlength="100" required')}
        ${input('Responsable de la bolsa','responsible',b.responsible,'maxlength="100" required')}
        ${input('A · Total contado, incluido el fondo y propinas físicas','counted',b.counted,'type="number" min="0" max="10000000000" step="1" required')}
        ${input('B · Fondo que queda para la próxima apertura','retained',b.retained,'type="number" min="0" max="10000000000" step="1" required')}
        <div class="bags-amount"><span>C · Bolsa = A − B</span><output data-bag-amount>${fmt(b.bag_amount)}</output></div></div>
        ${b.status==='pending'?input('Motivo de la corrección (si cambias algún dato)','reason','','maxlength="500"'):''}
        <label class="bags-confirm"><input type="checkbox" name="confirmed" required>${b.status==='pending'?' Confirmo guardar la revisión; se conserva la entrega original.':' Confirmo estos datos manuales y la entrega de la bolsa. Quedará pendiente del contabilización de efectivo.'}</label>
        <button class="button primary" type="submit">${b.status==='pending'?'Guardar revisión':'Confirmar datos y entrega'}</button></form>`}
        ${b.verified_at?`<section class="bags-amount"><h3>Contabilización de efectivo</h3><p>Real: <strong>${fmt(b.verified_amount)}</strong> · Diferencia frente a C: <strong>${fmt(b.verified_difference)}</strong></p><p>${e(b.verification_note||'Sin diferencia')} · Contabilizado por ${e(b.verified_by_name||b.verified_by)}</p></section>`:''}
        ${b.status==='pending'&&b.can_verify?`<section class="bags-card"><h3>Contabilización de efectivo</h3><p>C declarado: <strong>${fmt(b.bag_amount)}</strong>. Ingresa el dinero que contaste, sin modificar lo declarado.</p><form data-bag-form="verify">${input('Efectivo contabilizado en la bolsa','verified_amount','','type="number" min="0" max="10000000000" step="1" required')}${input('Observación (obligatoria si hay diferencia)','note','','maxlength="1000"')}<label class="bags-confirm"><input type="checkbox" name="confirmed" required> Confirmo que conté físicamente esta bolsa y que este es el importe real.</label><button class="button primary" type="submit">Confirmar contabilización</button></form></section>`:''}
        ${b.status!=='void'&&b.can_void?`<details class="bags-void"><summary>Anular ficha</summary><form data-bag-form="void">${input('Motivo','reason','','maxlength="500" required')}<label class="bags-confirm"><input type="checkbox" name="confirmed" required> Confirmo anular la ficha y conservar su número.</label><button class="button secondary" type="submit">Anular ficha</button></form></details>`:''}</section>
        <section class="bags-card"><h3>Historial</h3>${b.history.map(h=>`<p>${e({created:'Ficha creada',imported:'Etiqueta preimpresa importada',complete:'Entrega y datos declarados confirmados',correct:'Corrección de datos; entrega original conservada',verify:'Contabilización confirmada',void:'Ficha anulada'}[h.action]||h.action)} · ${e(h.declaration_identity?.account_name||h.actor)} · ${e(when(h.created_at))}${h.reason?`<br>Motivo: ${e(h.reason)}`:''}${h.declaration_identity?`<br>Cuenta autenticada: ${e(h.declaration_identity.account_name)}${h.declaration_identity.email?` (${e(h.declaration_identity.email)})`:''}${h.declaration_identity.shared?` · Compartida<br>Persona declarada: ${e(h.declaration_identity.declared_person)} · identidad personal no verificada`:''}`:''}</p>`).join('')}</section>`);
    }catch(error){if(token===epoch&&active)base(`<p class="error" role="alert">${e(error.message)}</p><button class="button secondary" data-bag-list>Volver a bolsas</button>`);}
  }
  async function preview(batchId){
    const token=epoch;
    const url='/api/cash-bags/batches/'+batchId+'/labels.pdf';
    dialog.innerHTML='<div class="bags-toolbar"><h2>Vista previa de etiquetas</h2><button class="button secondary" data-bag-close>Cerrar</button></div><p role="status">Preparando PDF…</p>';dialog.showModal();
    try{
      const response=await fetch(url);
      if(token!==epoch||!active){await response.body?.cancel();return;}
      if(!response.ok||!response.headers.get('content-type')?.startsWith('application/pdf')){
        const data=await response.json().catch(()=>({}));throw new Error(data.error||'No se pudo generar el PDF. El lote ya existe; reintenta desde sus etiquetas.');
      }
      await response.arrayBuffer();
      if(token!==epoch||!active)return;
      dialog.innerHTML=`<div class="bags-toolbar"><h2>Vista previa de etiquetas</h2><button class="button secondary" data-bag-close>Cerrar</button></div><p>Carta · 4 etiquetas por hoja · 7 × 12 cm. <strong>Imprimir al 100 %, sin ajustar a página.</strong></p><p>Reimprimir conserva los mismos números y QR. Úsalo solo para reemplazar una impresión fallida; no etiquetes dos bolsas con el mismo número. Los campos quedan en blanco para completar después.</p><iframe title="Etiquetas de bolsas en PDF" src="${url}"></iframe><div class="bags-toolbar"><a class="button primary" href="${url}?download=1" download>Descargar PDF</a><a class="button secondary" href="${url}" target="_blank" rel="noopener">Abrir PDF para imprimir</a></div>`;
    }catch(error){if(token===epoch&&active)dialog.innerHTML=`<h2>No se pudo abrir la vista previa</h2><p role="alert">${e(error.message)}</p><button class="button secondary" data-bag-close>Cerrar</button>`;}
  }
  function enter(){
    if(!state.user){reset();return;}
    if(active)return;
    active=true;host.classList.remove('hidden');
    if(!allowed()){base('<p>Las fichas de bolsas están disponibles para cuentas autorizadas y caja.</p>');return;}
    const ids=new URLSearchParams(location.search).getAll('bag');
    if(ids.length&&(ids.length!==1||!/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(ids[0]))){base('<p class="error" role="alert">El enlace de bolsa no es válido.</p>');return;}
    if(ids.length)detail(ids[0]);else listing();
  }
  host.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b||busy)return;
    if(b.hasAttribute('data-bag-list')){const p=new URLSearchParams(location.search);p.set('module','cash');p.set('section','Entregas');p.delete('bag');history.replaceState(null,'','/?'+p);listing();}
    if(b.hasAttribute('data-bag-refresh'))listing();
    if(b.dataset.bagOpen)detail(b.dataset.bagOpen);
    if(b.dataset.bagPage)listing(b.dataset.bagPage);
    if(b.dataset.bagPreview)preview(b.dataset.bagPreview);
  });
  dialog.addEventListener('click',event=>{if(event.target.closest('[data-bag-close]'))dialog.close();});
  host.addEventListener('input',event=>{
    if(!['counted','retained'].includes(event.target.name))return;
    const form=event.target.form,a=form.elements.counted,b=form.elements.retained,out=form.querySelector('[data-bag-amount]');
    const valid=a.value!==''&&b.value!==''&&a.validity.valid&&b.validity.valid&&Number(b.value)<=Number(a.value);
    out.textContent=valid?fmt(Number(a.value)-Number(b.value)):'Completa A y B; B no puede superar A';
  });
  host.addEventListener('submit',async event=>{
    const form=event.target;if(!form.dataset.bagForm)return;event.preventDefault();if(busy||!allowed())return;
    const action=form.dataset.bagForm,values=new FormData(form),token=epoch;
    let path,body={confirmed:values.get('confirmed')==='on'};
    if(action==='create'){path='/api/cash-bags/batches';body.quantity=Number(values.get('quantity'));}
    else{
      path='/api/cash-bags/'+current.id+'/'+action;body.version=current.version;
      if(action==='void')body.reason=values.get('reason');
      else if(action==='verify'){body.verified_amount=values.get('verified_amount')===''?null:Number(values.get('verified_amount'));body.note=values.get('note');}
      else{if(values.has('reason'))body.reason=values.get('reason');for(const key of ['business_date','register_name','shift_name','responsible'])body[key]=values.get(key);for(const key of ['counted','retained'])body[key]=values.get(key)===''?null:Number(values.get(key));}
    }
    busy=true;message('');form.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{
      const result=await write(path,body);
      if(token!==epoch||!active)return;
      if(action==='create'){await listing();await preview(result.batch_id);}
      else await detail(result.id);
    }catch(error){if(token===epoch&&active)message(error.message);}
    finally{busy=false;form.querySelectorAll('button').forEach(b=>b.disabled=false);}
  });
  return {enter,leave,reset};
})();
