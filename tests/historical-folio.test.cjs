'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(require.resolve('../static/app.js'),'utf8');
const source=fs.readFileSync(require.resolve('../static/payables.js'),'utf8');
test('only structured 409 history warnings carry a confirmation payload',async()=>{
 const h={Error,JSON};vm.createContext(h);vm.runInContext(app.slice(app.indexOf('async function responsePayload('),app.indexOf('async function supplierFileUpload(')),h);
 for(const status of [409,403,500]){
  const warning={token:'state-bound',matches:[]};let result;
  try{await h.responsePayload({status,ok:false,text:async()=>JSON.stringify({error:'Revisar',historical_duplicate:warning})});}catch(error){result=error;}
  assert.equal(result.message,'Revisar');assert.equal(Boolean(result.historical_duplicate),status===409);
 }
});
test('real api wrapper preserves historical metadata and live duplicates have none',async()=>{
 let payload={error:'Histórico',historical_duplicate:{token:'actual',matches:[]}};
 const h={Error,JSON,authGeneration:1,clearTimeout,setTimeout,fetch:async()=>({status:409,ok:false,text:async()=>JSON.stringify(payload)})};vm.createContext(h);
 vm.runInContext(app.slice(app.indexOf('async function responsePayload('),app.indexOf('\nfunction actor()')),h);
 await assert.rejects(h.api('/api/suppliers/invoices','POST',{folio:'A'}),e=>e.historical_duplicate.token==='actual');
 payload={error:'Ya existe'};await assert.rejects(h.api('/api/suppliers/invoices','POST',{}),e=>e.message==='Ya existe'&&!e.historical_duplicate);
});
function node(){return {hidden:false,textContent:'Guardar',classList:{add(){},remove(){},toggle(){}},focus(){},scrollIntoView(){}};}
function harness(){
 const save=node(),panel=node(),cont=node(),reason={disabled:true,focus(){},value:''};let calls=0;
 const form={humanValues:{folio:'00042',amount:10000},elements:{history_reason:reason},querySelector(selector){if(selector==='button[type=submit]')return save;if(selector==='#ap-history-warning')return this.warning;if(selector==='#ap-history-confirmation')return panel;if(selector==='[data-ap=history-continue]')return cont;if(selector==='#form-error')return {insertAdjacentHTML:(_,html)=>{this.html=html;this.warning={remove:()=>{this.warning=null;},scrollIntoView(){}};}};}};
 const h={draft:{kind:'invoice'},form,$:()=>form,api:()=>calls++,e:x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),input:(label,name,value,extra)=>`<input name="${name}" ${extra}>`,button:(label,action)=>`<button type="button" data-ap="${action}">${label}</button>`};
 vm.createContext(h);vm.runInContext(source.slice(source.indexOf('  function clearHistoryWarning('),source.indexOf('  function sharedPaymentProofs(')),h);return {h,form,save,calls:()=>calls};
}
test('warning offers explicit continuation with safe evidence and two confirmation actions',()=>{
 const {h,form}=harness();h.historyWarning(form,{token:'fresh',matches:[{supplier_name:'<script>bad</script>',invoice_id:1,folio:'42',current_folio:'99'}]});
 assert.match(form.html,/Folio anterior: 42/);assert.match(form.html,/Folio actual: <strong>99/);assert.match(form.html,/Ingresar de todas formas/);assert.match(form.html,/Este folio aparece en el historial de una factura corregida. ¿Confirmas que corresponde a otra factura\?/);assert.match(form.html,/Confirmar e ingresar/);assert.match(form.html,/>Cancelar</);assert.match(form.html,/name="history_reason" disabled required/);assert.doesNotMatch(form.html,/<script>/);
});
test('opening and canceling confirmation preserves invoice values and sends no request',()=>{
 const {h,form,calls}=harness();h.historyWarning(form,{token:'fresh',matches:[]});h.historyConfirmation(true);
 assert.equal(form.elements.history_reason.disabled,false);h.historyConfirmation(false);
 assert.equal(form.elements.history_reason.disabled,true);assert.equal(h.draft.historyConfirmed,false);assert.equal(h.draft.history.token,'fresh');assert.deepEqual(form.humanValues,{folio:'00042',amount:10000});assert.equal(calls(),0);
});
test('editing invoice clears stale consent; editing confirmation reason preserves warning',()=>{
 const {h,form}=harness(),listeners={};h.document={addEventListener:(name,fn)=>listeners[name]=fn};h.visible=()=>true;h.busy=false;
 const start=source.indexOf("  for(const eventName of ['input','change'])");vm.runInContext(source.slice(start,source.indexOf("  document.addEventListener('change'",start)),h);
 h.historyWarning(form,{token:'fresh',matches:[]});listeners.input({target:{closest:s=>s==='#ap-edit'?form:s==='#ap-history-warning'?form.warning:null}});assert.equal(h.draft.history.token,'fresh');
 h.draft.historyConfirmed=true;listeners.change({target:{closest:s=>s==='#ap-edit'?form:null}});assert.equal(h.draft.history,null);assert.equal(h.draft.historyConfirmed,false);
});
test('both cooperating browser assets carry the same publication version',()=>{
 const html=fs.readFileSync(require.resolve('../static/index.html'),'utf8');
 assert.equal(html.match(/src="\/app.js\?v=([^"]+)/)[1],html.match(/src="\/payables.js\?v=([^"]+)/)[1]);
});
