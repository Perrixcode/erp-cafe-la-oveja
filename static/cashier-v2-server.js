/* Authenticated ERP adapter for the approved cashier V2 interface. No fixture or browser persistence. */
'use strict';
window.OvejaCashierV2Server=(()=>{
 const prefix='/api/cash-closures/v2',clone=x=>structuredClone(x),uid=()=>crypto.randomUUID().replaceAll('-','');
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const controllers=new Set();
 async function api(path,body){
  const r=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:body?{'Content-Type':'application/json','X-ERP-Local':'1'}:{},body:body?JSON.stringify(body):undefined});
  let data;try{data=await r.json();}catch{throw Error('Respuesta incompleta. Reintenta: la operación conserva su identificador.');}
  if(!r.ok){const e=Error(data.error||'No se pudo completar.');e.status=r.status;throw e;}return data;
 }
 function enrich(v){
  v=clone(v);v.safe_deposits=v.events.filter(e=>e.kind==='deposit_safe').map(e=>({...e.detail,at:e.created_at,signed_snapshot_hash:e.detail.snapshot_hash}));
  v.history=v.events;v.submitted_history=v.submitted_history.map(e=>({...e.snapshot,signature:e.signature,summary:{...e.snapshot.summary,totalMP:e.snapshot.summary.mp_total},declaration:e.snapshot.declaration,folio:e.folio,submitted_at:e.created_at}));
  v.late_comparisons=v.events.filter(e=>e.kind==='late_comparison').map(e=>({at:e.created_at,signed_snapshot_hash:e.detail.submission_hash,provenance:{reference:e.detail.comparison.cash_reference?.evidence||'Evidencia del servidor por medio',verified:!!e.detail.comparison.cash_reference},cash:{state:e.detail.comparison.difference==null?'pending':'compared',difference:e.detail.comparison.difference},terminals:e.detail.comparison.terminals.map(t=>({...t,state:t.comparison_state}))}));
  return v;
 }
 function declaration(v){return Object.fromEntries(['identity','cash','retained','terminals','bagNumber','note','photos','missing_evidence_reasons'].map(k=>[k,clone(v[k])]));}
 function localSummary(v){
  const total=x=>Object.values(x).some(n=>n==null)?null:Object.entries(x).reduce((a,[d,n])=>a+Number(d)*n,0);
  const A=total(v.cash),B=total(v.retained),s=clone(v.summary);s.A=A;s.B=B;s.C=A==null||B==null?null:A-B;s.difference=A==null||s.expected==null?null:A-s.expected;
  s.terminals=v.terminals.map(t=>{const old=s.terminals.find(x=>x.terminal_id.toUpperCase()===t.terminal_id.normalize('NFKC').trim().toUpperCase());const n=old&&t.scope_confirmed?{...old}: {terminal_id:t.terminal_id,expected:null,comparison_state:'pending'};n.difference=n.expected!=null&&t.total!=null?t.total-n.expected:null;return n;});
  s.requires_comment=s.difference!=null&&s.difference!==0||s.terminals.some(t=>t.difference!=null&&t.difference!==0);return s;
 }
 class Model {
  constructor(value,controller){this.current=enrich(value);this.controller=controller;this.sequence=0;this.savedSequence=0;this.queue=Promise.resolve();this.attempts=new Map();this.signature=null;this.denominations=controller.data.denominations;}
  snapshot(){return clone(this.current);}
  exportState(){return {drafts:[this.snapshot()]};}
  summary(){return localSummary(this.current);}
  update(value){
   const before=JSON.stringify(declaration(this.current));
   if(value.identity.business_date!==this.current.identity.business_date||value.identity.register_id!==this.current.identity.register_id){this.current.summary={...this.current.summary,expected:null,difference:null,terminals:[],selected_session:null,source_state:'pending'};}
   this.current={...this.current,...clone(value)};
   if(before!==JSON.stringify(declaration(this.current))){this.sequence++;this.signature=null;}
   this.current.summary=localSummary(this.current);return this.snapshot();
  }
  run(fn){const next=this.queue.then(fn);this.queue=next.catch(()=>{});return next;}
  async request(action,extra={},base=prefix){
   const body={version:this.current.version,confirmed:true,...extra};const key=JSON.stringify([base,action,body]);let attempt=this.attempts.get(key);
   if(!attempt){attempt={request_id:uid(),...body};this.attempts.set(key,attempt);}
   try{const result=await api(base+'/'+this.current.id+'/'+action,attempt);this.attempts.delete(key);return result;}catch(e){if(e.status&&e.status<500)this.attempts.delete(key);throw e;}
  }
  async save(){return this.run(async()=>{
   if(this.current.status!=='draft')return this.snapshot();
   while(this.savedSequence!==this.sequence){
    const seq=this.sequence,proposed=declaration(this.current);this.controller.status('Guardando en el servidor…');
    const saved=await this.request('save',{declaration:proposed});
    if(seq===this.sequence)this.current=enrich(saved);else{this.current.version=saved.version;this.current.revision=saved.revision;this.current.identity.counted_at=saved.identity.counted_at;}
    this.savedSequence=seq;
   }
   this.controller.status('Guardado en el servidor · versión '+this.current.version);return this.snapshot();
  });}
  async prepareReview(){await this.save();return this.run(()=>this.request('review'));}
  async sign(value){this.signature=clone(value);return {signature:null};}
  async submit(token){return this.run(async()=>{if(!this.signature)throw Error('Firma nuevamente esta versión.');const v=await this.request('submit',{review_token:token,strokes:this.signature.strokes,consent:this.signature.consent});this.current=enrich(v);this.signature=null;this.controller.status('Envío confirmado por el servidor · '+v.folio);return this.snapshot();});}
  async depositSafe(value){return this.run(async()=>{this.current=enrich(await this.request('deposit',{amount:value.amount,bag_number:value.bag_number,deposit_confirmed:value.confirmed}));this.controller.status('Depósito confirmado por el servidor. Recepción y conteo pendientes.');return this.snapshot();});}
  async revise(){
   const reason=await this.controller.reason();
   return this.run(async()=>{this.current=enrich(await this.request('revise',{reason}));this.signature=null;this.sequence=this.savedSequence=0;this.controller.status('Corrección abierta. Se conserva la presentación anterior; debes revisar y firmar de nuevo.');return this.snapshot();});
  }
  async uploadPhoto(blob,kind,name,reference=false){
   await this.save();
   const data=await blob.arrayBuffer();if(data.byteLength>3*1024*1024)throw Error('La foto comprimida supera 3 MiB. Usa otra foto más pequeña.');
   const bytes=new Uint8Array(data);let raw='';for(let i=0;i<bytes.length;i+=8192)raw+=String.fromCharCode(...bytes.subarray(i,i+8192));
   return this.run(()=>this.request(reference?'reference-attach':'attach',{kind,file_name:name.slice(0,120),file_type:blob.type,file_data:btoa(raw)}));
  }
  preview(){return this.controller.preview(this.current.id);}
  santiagoLocal(value){if(!value)return '';return new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Santiago',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value)).replace(' ','T');}
 }
 class Controller {
  constructor(config){this.config=config;this.host=config.host;this.closed=false;this.ui=null;this.model=null;this.legacy=null;this.pending=false;this.timer=null;this.generation=0;this.dialogs=new Set();this.data=null;this.message='';controllers.add(this);this.start();}
  get dirty(){return !!this.ui?.dirty||!!this.model&&this.model.sequence!==this.model.savedSequence;}
  status(message,error=false){this.message=message;const el=this.host.querySelector('[data-v2-status]');if(el){el.textContent=message;el.setAttribute('role',error?'alert':'status');}}
  fail(e){this.status(e.message,true);if(e.status===401)this.config.onUnauthorized?.();}
  async start(){try{this.host.innerHTML='<p role="status">Cargando cierres del servidor…</p>';this.data=await api(prefix);if(this.closed)return;if(this.config.closureId)await this.open(this.config.closureId);else this.list();}catch(e){this.host.textContent=e.message;this.fail(e);}}
  list(){
   this.ui?.destroy();this.ui=null;this.model=null;this.legacy?.destroy();this.legacy=null;clearTimeout(this.timer);
   this.host.innerHTML=`<section class="cl-workspace"><h2>Cierre digital · V2 de prueba semanal</h2><p data-v2-status role="status">${esc(this.message||'Elige un cierre guardado o inicia una nueva entrega.')}</p><p>El día operativo se conserva aunque el cierre administrativo termine después, incluso durante el fin de semana.</p>${this.data.can_admin?'<button type="button" class="button secondary" data-admin>Revisión administrativa</button>':''}${this.data.can_write&&!this.data.user.shared?`<form data-new class="cl-panel cl-form"><h3>Nuevo cierre</h3><label>Cajera / responsable<input readonly value="${esc(this.data.user.name)}"></label><label>Caja<select name="register_id" required>${this.data.registers.length!==1?'<option value="">Selecciona una caja</option>':''}${this.data.registers.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('')}</select></label><label>Día operativo<input type="date" name="business_date" value="${esc(this.data.today)}" min="2026-10-16" max="${esc(this.data.calendar_today)}" required></label><button class="button primary" ${this.data.registers.length?'':'disabled'}>Comenzar cierre V2</button></form>`:'<p>La creación V2 no está habilitada para este acceso. Los registros guardados permanecen disponibles.</p>'}<details><summary>Flujo anterior y configuración existente</summary><button type="button" class="button secondary" data-legacy>Consultar flujo anterior</button></details><div class="cl-list">${this.data.closures.map(c=>`<article class="cl-panel"><strong>${esc(c.business_date)} · ${esc(c.register_name)}</strong><p>${esc(c.responsible.name)} · ${esc(c.cashier_v2?'V2 · '+(c.cashier_v2.folio||'Borrador'):'Flujo anterior')} · ${esc(c.status==='draft'?'Borrador':c.cashier_v2?.deposit_status||c.status)}</p><button type="button" class="button secondary" data-open="${c.id}">Abrir cierre</button></article>`).join('')||'<p>Aún no hay cierres guardados para este acceso.</p>'}</div></section>`;
   this.host.querySelector('[data-admin]')?.addEventListener('click',()=>this.admin());
   this.host.querySelector('[data-legacy]')?.addEventListener('click',()=>this.openLegacy());
   this.host.querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>this.open(b.dataset.open).catch(e=>this.fail(e)));
   const form=this.host.querySelector('[data-new]');if(form)form.onsubmit=async e=>{e.preventDefault();if(this.pending)return;this.pending=true;const b=form.querySelector('button');b.disabled=true;const f=new FormData(form);const body={confirmed:true,identity:{business_date:f.get('business_date'),register_id:f.get('register_id')},...(this.config.bagId?{bag_id:this.config.bagId}:{})};
    if(!this.createAttempt||JSON.stringify(this.createAttempt.body)!==JSON.stringify(body))this.createAttempt={body,request_id:uid()};
    try{const v=await api(prefix+'/create',{...body,request_id:this.createAttempt.request_id});this.createAttempt=null;await this.open(v.id,v);}catch(err){this.fail(err);}finally{this.pending=false;b.disabled=false;}};
  }
  async refreshList(){if(this.dirty){await this.ui.save();}this.data=await api(prefix);this.list();}
  openLegacy(id){this.ui?.destroy();this.ui=null;this.model=null;this.host.innerHTML='<button type="button" class="button secondary" data-back>Volver a cierres V2</button><section data-legacy-host></section>';this.host.querySelector('[data-back]').onclick=()=>this.refreshList().catch(e=>this.fail(e));this.legacy=this.config.legacyMount({...this.config,host:this.host.querySelector('[data-legacy-host]'),closureId:id,legacy:true});}
  async open(id,preloaded){
   if(this.closed)return;
   const known=this.data.closures.find(c=>c.id===id);if(known&&!known.cashier_v2&&!preloaded)return this.openLegacy(id);
   clearTimeout(this.timer);const generation=++this.generation;this.ui?.destroy();this.ui=null;this.legacy?.destroy();this.legacy=null;
   const value=preloaded||await api(prefix+'/'+id);if(this.closed||generation!==this.generation)return;
   this.model=new Model(value,this);const photos=[];
   for(const meta of value.photos){const r=await fetch('/api/cash-closures/'+id+'/attachments/'+meta.id,{credentials:'same-origin',cache:'no-store'});if(!r.ok)throw Error('No se pudo recuperar el respaldo guardado. Reabre el cierre.');photos.push({...meta,blob:await r.blob()});}
   const warnings=await window.CashierV2.restorePhotos(this.model,[{closureId:id,photos}]);
   if(warnings.length)throw Error(warnings.join(' '));
   if(this.closed||generation!==this.generation)return;
   this.host.innerHTML='<div class="cl-top"><button type="button" class="button secondary" data-back>Volver a mis cierres</button><button type="button" class="button secondary" data-save>Guardar ahora</button></div><p data-v2-status role="status"></p><section data-v2-host></section><section data-v2-ops></section>';
   this.host.querySelector('[data-back]').onclick=()=>this.refreshList().catch(e=>this.fail(e));
   this.host.querySelector('[data-save]').onclick=()=>this.ui.save().catch(e=>this.fail(e));
   this.ui=window.CashierV2.mount({host:this.host.querySelector('[data-v2-host]'),user:{name:value.responsible.name},registers:this.data.registers,model:this.model,onChange:()=>{clearTimeout(this.timer);if(this.model.current.status==='draft')this.timer=setTimeout(()=>this.ui?.save().then(()=>this.operations()).catch(e=>this.fail(e)),700);else this.operations();}});
   this.status('Recuperado del servidor · versión '+value.version);this.operations();
  }
  operations(){
   const host=this.host.querySelector('[data-v2-ops]');if(!host||!this.model)return;const c=this.model.current;
   host.innerHTML=`<div class="cl-panel"><h3>Custodia y revisión</h3><p>Depósito en caja fuerte, entrega física, recepción y conteo son hechos distintos. Registra solo lo que ocurrió.</p>${c.status==='submitted'&&c.delivery?.state==='prepared'?'<button type="button" class="button secondary" data-deliver>Registrar entrega física a administración</button>':''}${this.data.can_admin?'<button type="button" class="button secondary" data-review>Revisión y conteo administrativo</button><button type="button" class="button secondary" data-compare>Registrar comparación posterior</button>':''}${this.data.can_admin&&c.identity.counted_at?`<details><summary>Verificar referencia independiente de efectivo</summary><p>Uso administrativo. Nunca transcribas collection.cash como esperado. Revisa un reporte independiente del mismo corte. Esta referencia no modifica la declaración firmada.</p><form data-reference class="cl-form"><label>Apertura verificada<input name="opening" type="number" min="0" step="1" required></label><label>Ventas netas antes de movimientos<input name="net_sales" type="number" min="0" step="1" required></label><label>Movimiento neto incluido en el corte<input name="movement_net" type="number" step="1" required></label><label>Reporte, caja, día, sesión y corte verificados<textarea name="evidence" required maxlength="1500"></textarea></label><label>Respaldo guardado<select name="attachment_id"><option value="">Selecciona el reporte independiente</option>${(c.reference_attachments||c.photos).map(p=>`<option value="${p.id}">${esc(p.kind+' · '+p.name)}</option>`).join('')}</select></label><label>O adjunta un nuevo reporte independiente (JPG, PNG o WebP, hasta 3 MiB)<input type="file" name="reference_file" accept="image/jpeg,image/png,image/webp"></label>${['opening','sales','movements'].map(k=>`<label><input type="checkbox" name="${k}_verified" required> Verifiqué ${k==='opening'?'la apertura':k==='sales'?'las ventas netas y su base':'los movimientos, sin duplicarlos'} para el mismo corte de este conteo.</label>`).join('')}<button class="button secondary">Guardar referencia revisada</button></form></details>`:''}</div>`;
   host.querySelector('[data-deliver]')?.addEventListener('click',async()=>{if(!confirm('¿Confirmas que entregaste físicamente esta bolsa a administración? Esto es distinto del depósito en caja fuerte.'))return;try{await this.model.run(()=>this.model.request('deliver',{},'/api/cash-closures'));await this.open(c.id);}catch(e){this.fail(e);}});
   host.querySelector('[data-review]')?.addEventListener('click',()=>this.admin(c.id));
   host.querySelector('[data-compare]')?.addEventListener('click',async()=>{try{await this.ui.save();await this.model.run(()=>this.model.request('compare'));await this.open(c.id);}catch(e){this.fail(e);}});
   const form=host.querySelector('[data-reference]');if(form)form.onsubmit=async e=>{e.preventDefault();if(this.pending)return;this.pending=true;const f=new FormData(form),extra={basis:'independent_net_sales_before_movements',evidence:f.get('evidence'),attachment_id:f.get('attachment_id')};for(const k of ['opening','net_sales','movement_net'])extra[k]=Number(f.get(k));for(const k of ['opening_verified','sales_verified','movements_verified'])extra[k]=f.has(k);
    try{await this.ui.save();const file=f.get('reference_file');if(file?.size){const uploaded=await this.model.uploadPhoto(file,'toteat',file.name,true);extra.attachment_id=uploaded.id;}await this.model.run(()=>this.model.request('cash-reference',extra));await this.open(c.id);}catch(err){this.fail(err);}finally{this.pending=false;}};
  }
  async admin(id){try{if(this.ui)await this.ui.save();window.OvejaReview?.open({closureId:id,onUnauthorized:this.config.onUnauthorized,openClosure:id=>this.open(id),onClose:()=>{if(this.model)this.open(this.model.current.id).catch(e=>this.fail(e));}});}catch(e){this.fail(e);}}
  reason(){return new Promise((resolve,reject)=>{const d=document.createElement('dialog');d.className='cl-pdf';d.innerHTML='<form><h3>Nueva revisión</h3><p>La versión firmada se conserva. Debes explicar el cambio y volver a firmar.</p><label>Motivo<textarea name="reason" maxlength="1500" required></textarea></label><button type="submit">Abrir revisión</button><button type="button" data-cancel>Cancelar</button></form>';this.dialogs.add(d);document.body.append(d);let accepted=false;d.querySelector('form').onsubmit=e=>{e.preventDefault();const reason=new FormData(e.target).get('reason').trim();if(!reason)return;accepted=true;d.close();resolve(reason);};d.querySelector('[data-cancel]').onclick=()=>d.close();d.onclose=()=>{d.remove();this.dialogs.delete(d);if(!accepted)reject(Error('No se abrió una corrección.'));};d.showModal();});}
  async preview(id){
   const v=this.model.current,sub=v.submitted_history.at(-1)?.submission_id;const url=prefix+'/'+id+'/preview.pdf'+(sub?'?submission='+encodeURIComponent(sub):'');const response=await fetch(url,{credentials:'same-origin',cache:'no-store'});
   if(!response.ok)throw Error('No se pudo verificar y generar la vista previa del cierre.');const blob=await response.blob();if(!blob.type.includes('pdf'))throw Error('Vista previa no válida.');const object=URL.createObjectURL(blob),d=document.createElement('dialog');d.className='cl-pdf';this.dialogs.add(d);
   d.innerHTML=`<div class="cl-top"><h3>Vista previa · cierre firmado</h3><button type="button" data-close>Cerrar</button></div><iframe title="Vista previa del cierre PDF" src="${esc(url)}"></iframe><a class="button primary" href="${object}" download="Cierre_V2_${id.slice(0,8)}.pdf">Descargar este PDF</a><p>Para imprimir, usa la opción de impresión de la vista PDF.</p>`;
   document.body.append(d);d.querySelector('[data-close]').onclick=()=>d.close();d.onclose=()=>{URL.revokeObjectURL(object);d.remove();this.dialogs.delete(d);};d.showModal();
  }
  mountToteat(){} closeSubmission(){for(const d of this.dialogs)d.close();}
  destroy(){this.closed=true;this.generation++;clearTimeout(this.timer);this.ui?.destroy();this.legacy?.destroy();this.closeSubmission();controllers.delete(this);this.host.replaceChildren();}
 }
 window.addEventListener('beforeunload',e=>{if([...controllers].some(c=>c.dirty||c.pending)){e.preventDefault();e.returnValue='';}});
 return {mount:config=>new Controller(config),Model,enrich,localSummary};
})();
