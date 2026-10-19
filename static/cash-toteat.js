/* Provider data stays separate from the cashier declaration and physical counts. */
'use strict';
window.OvejaToteat=(()=>{
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const money=v=>v==null?'Pendiente':new Intl.NumberFormat('es-CL',{style:'currency',currency:'CLP',maximumFractionDigits:0}).format(v);
 const time=v=>v?new Intl.DateTimeFormat('es-CL',{timeZone:'America/Santiago',dateStyle:'short',timeStyle:'short'}).format(new Date(v))+' Chile':'Sin lectura';
 const key=q=>JSON.stringify([q.day,q.register_id,q.session_key||'',q.closure_id||'']);
 function fillBlanks(draft,source){
  // Imports require a versioned server command; temporal sales never certify a session.
  return {data:structuredClone(draft),changed:0};
 }

 function accept(requestKey,currentKey,sequence,currentSequence){return requestKey===currentKey&&sequence===currentSequence;}
 class Panel {
  constructor(config){this.config=config;this.seq=0;this.data=null;this.error='';this.timer=null;this.polls=0;this.pending=false;this.closed=false;}
  mount(host,context){
   const wasClosed=this.closed;this.host=host;this.closed=false;const contextKey=JSON.stringify([context.closure_id,context.day,context.register_id,context.session_key,context.frozen]);
   if(this.contextKey!==contextKey){clearTimeout(this.timer);this.seq++;this.contextKey=contextKey;this.context=context;this.query={day:context.day,register_id:context.register_id,session_key:context.session_key||'',...(context.closure_id?{closure_id:context.closure_id}:{})};this.data=null;this.error='';this.pending=false;this.polls=0;}
   this.context=context;this.draw();
   if((!this.data||wasClosed)&&!this.pending&&context.available&&this.query.register_id&&!context.frozen)this.load(!this.data);
  }
  async load(auto=false){
   if(this.closed)return;clearTimeout(this.timer);const q={...this.query},k=key(q),seq=++this.seq;this.pending=true;this.error='';this.draw();
   try{const v=await this.config.api('/toteat?'+new URLSearchParams(q));if(!accept(k,key(this.query),seq,this.seq)||this.closed)return;this.data=v;this.pending=false;this.draw();
    const stale=Object.values(v.sources).some(x=>x.state!=='observed');
    if(auto&&stale&&this.polls===0){await this.refresh();return;}
    if(['queued','running'].includes(v.sync?.state)&&this.polls<36){this.polls++;this.timer=setTimeout(()=>this.load(false),5000);}else this.timer=setTimeout(()=>this.load(false),60000);
   }catch(e){if(accept(k,key(this.query),seq,this.seq)&&!this.closed){this.pending=false;this.error=e.message;this.draw();}}
  }
  async refresh(){
   if(this.pending||this.closed)return;const q={...this.query},k=key(q),seq=++this.seq;this.pending=true;this.error='';this.draw();
   try{await this.config.api('/toteat/refresh',{day:q.day,...(q.closure_id?{closure_id:q.closure_id}:{})});if(!accept(k,key(this.query),seq,this.seq)||this.closed)return;this.pending=false;this.polls=1;await this.load(false);}
   catch(e){if(accept(k,key(this.query),seq,this.seq)&&!this.closed){this.pending=false;this.error=e.message;this.draw();}}
  }
  destroy(){this.closed=true;this.seq++;clearTimeout(this.timer);}
  select(name,value){this.query={...this.query,[name]:value,...(name!=='session_key'?{session_key:''}:{})};this.seq++;this.data=null;this.pending=false;this.polls=0;this.load(true);}
  table(data){
   const selected=data.selected,sales=data.sales;return `${selected?`<p><strong>${esc(selected.register_name||data.register_name)} · ${esc(selected.shift_name||'Turno '+selected.shift_id)}</strong><br>Día operativo ${esc(selected.business_date||data.business_date)} · apertura ${esc(time(selected.opened_at))} · ${selected.closed_at?'Cierre administrativo en Toteat: '+esc(time(selected.closed_at)):'Abierto en la última lectura · provisional'}</p><p>Fondo inicial reportado: ${money(selected.opening)}. Efectivo reportado por collection: ${money(selected.cash_reported)} · significado pendiente de validar.</p>`:'<p>Selecciona una sesión exacta para ver sus medios. Sin sesión no se asignan ventas diarias a un turno.</p>'}
    ${selected?`<div class="cl-source-table"><table><caption>Totales reportados por Toteat · no acreditan depósitos ni saldo disponible</caption><thead><tr><th>Medio</th><th>Collection reportado</th><th>Cobros observados por horario</th>${this.context.isAdmin?'<th>Ventas sin propina</th><th>Propina</th>':''}</tr></thead><tbody>${this.rows(data)}</tbody></table></div><p>${data.sources?.sales?.last_success?(sales?.payments??0):'Sin información de'} pagos dentro del horario de esta sesión (atribución provisional) en la lectura; ${data.sources?.sales?.last_success?(sales?.unassigned_payments??0):'sin información de'} pagos de esta caja sin atribución segura. Cobertura parcial: la ausencia de un medio o de pagos no significa cero.</p>${sales?.pending_payments?.length?`<details><summary>Pagos sin identidad de sesión comprobada (${sales.pending_payments.length})</summary><ul>${sales.pending_payments.map(p=>`<li>Pago ${esc(p.payment_id||p.payment_key)} · orden ${esc(p.order_id||'sin ID')} · ${esc({outside_session_windows:'Fuera de los horarios observados',overlapping_session_windows:'Horarios superpuestos',missing_order_close_time:'Sin hora de cierre',order_close_is_not_session_identity:'La hora de cierre de la orden no identifica el turno'}[p.reason])}</li>`).join('')}</ul></details>`:''}${(sales?.issues||[]).map(x=>`<p class="cl-alert">${esc(x)}</p>`).join('')}`:''}`;
  }
  rows(data){const methods=new Map(),sales=data.sales?.methods||[];for(const m of data.selected?.methods||[])methods.set(m.provider_id,{name:m.name||'Medio '+m.provider_id,reported:m.reported_total});for(const m of sales){const multiple=sales.filter(s=>s.provider_id===m.provider_id).length>1,k=multiple?'component:'+m.component_key:m.provider_id,r=methods.get(k)||{name:[m.provider_method,m.name||'Medio '+m.provider_id].filter(Boolean).join(' · ')};Object.assign(r,{total:m.total,sales:m.sales,tip:m.tip});methods.set(k,r);}return [...methods.values()].map(m=>`<tr><td>${esc(m.name)}</td><td>${money(m.reported)}</td><td>${money(m.total)}</td>${this.context.isAdmin?`<td>${money(m.sales)}</td><td>${money(m.tip)}</td>`:''}</tr>`).join('')||'<tr><td colspan="5">Sin importes observados para esta sesión.</td></tr>';}

  draw(){if(!this.host||this.closed)return;const c=this.context,d=this.data;
   if(!c.available){this.host.innerHTML='<p class="cl-muted">Guarda la identificación de tu cierre para cargar los datos de Toteat de esa caja y fecha.</p>';return;}
   if(c.frozen){this.host.innerHTML=`<details class="cl-panel"><summary>Fuente Toteat conservada al presentar</summary>${c.snapshot?this.table(c.snapshot):'<p>Este cierre se presentó antes de incorporar las instantáneas automáticas. No se reconstruye su fuente histórica.</p>'}<p>La fuente guardada se conserva; nuevas lecturas no reescriben este cierre.</p></details>`;return;}
   const query=this.query;this.host.innerHTML=`<section class="cl-panel cl-toteat"><h3>Datos de Toteat</h3><p>Consulta automática por fecha, caja y sesión. Actualizar conserva tu declaración y el conteo físico.</p>${!c.closure_id?`<div class="cl-grid"><label>Fecha comercial<input data-source-select="day" type="date" value="${esc(query.day)}" min="2026-10-16" max="${esc(c.today)}"></label><label>Caja<select data-source-select="register_id">${c.registers.map(r=>`<option value="${esc(r.id)}" ${r.id===query.register_id?'selected':''}>${esc(r.name)}</option>`).join('')}</select></label></div>`:''}
    <div class="cl-actions"><button type="button" class="button secondary" data-source-action="refresh" ${this.pending?'disabled':''}>${this.pending?'Consultando…':'Actualizar desde Toteat'}</button></div>${this.error?`<p role="alert" class="cl-error">${esc(this.error)}</p>`:''}
    ${d?`<div class="cl-source-status">${Object.entries(d.sources).map(([name,s])=>`<p><strong>${name==='sales'?'Ventas intradía':'Cajas y turnos'}</strong> · ${esc({missing:'Sin información',stale:'Lectura antigua',error:'Consulta fallida',observed:'Observado · provisional'}[s.state])}<br>Última lectura correcta: ${esc(time(s.last_success))}${s.error?`<br>${esc(s.error)} · intento ${esc(time(s.last_attempt))}`:''}</p>`).join('')}</div>${d.sync?.state==='queued'||d.sync?.state==='running'?'<p role="status">Actualización solicitada al colector existente. Puede tardar varios minutos por el límite de consultas.</p>':d.sync?.state==='error'?'<p class="cl-alert">La actualización requiere revisión. Se conserva la última lectura correcta.</p>':''}${d.missing_session?'<p class="cl-alert">La sesión vinculada no aparece en esta lectura. No se sustituye por otro turno.</p>':''}<label>Sesión exacta<select data-source-select="session_key"><option value="">${d.sessions.length===1?'Sesión única observada':'Selecciona un turno'}</option>${d.sessions.map(s=>`<option value="${esc(s.session_key)}" ${s.session_key===(query.session_key||d.selected?.session_key)?'selected':''}>${esc(s.shift_name||'Turno '+s.shift_id)} · ${esc(time(s.opened_at))} · ${s.closed_at?'cerrado':'abierto'}</option>`).join('')}</select></label>${this.table(d)}
    ${c.editing&&d.selected?`<div class="cl-actions"><button type="button" class="button secondary" data-source-action="link">Vincular este turno al borrador</button><button type="button" class="button secondary" data-source-action="copy" ${c.session_key!==d.selected.session_key||d.sources.collection.state!=='observed'?'disabled':''}>Importar totales vacíos como sugerencia</button></div><p class="cl-muted">Importar guarda el origen de cada total de esta sesión. La sugerencia permanece provisional y conserva los valores escritos. No completa medios ausentes, conteo, fondo retenido, horas reales ni movimientos.</p>`:''}<p class="cl-muted">${esc(d.notice)}</p>`:'<p role="status">Cargando información disponible…</p>'}</section>`;
   this.host.querySelectorAll('[data-source-select]').forEach(el=>el.onchange=()=>this.select(el.dataset.sourceSelect,el.value));
   this.host.querySelector('[data-source-action="refresh"]')?.addEventListener('click',()=>{this.polls=0;this.refresh();});
   this.host.querySelector('[data-source-action="link"]')?.addEventListener('click',()=>this.config.onLink(d.selected));
   this.host.querySelector('[data-source-action="copy"]')?.addEventListener('click',()=>this.config.onCopy(d));
  }
 }
 return {Panel,helpers:{fillBlanks,accept,key}};
})();
