'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),{execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const fixture=JSON.parse(execFileSync(process.env.ERP_TEST_PYTHON||'python3',['-B',path.join(__dirname,'invoice_credit_note_ui_fixture.py')],{cwd:root,env:{...process.env,PYTHONPATH:__dirname+path.delimiter+root},encoding:'utf8'}));
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const source=fs.readFileSync(path.join(root,'static/supplier-account.js'),'utf8');
const flush=()=>new Promise(setImmediate);
function runtime(data=fixture.before,post=async()=>fixture.result,role='partner'){
 const handlers={},calls=[],modals=[],errors=[],toasts=[],summary={textContent:''},submit={tagName:'BUTTON',disabled:false,textContent:'Guardar'};
 const values={folio:'00900',amount:'3000',occurred_on:'2026-01-03',notes:'Motivo FICTICIO',confirmed:'on'};
 const fields=Object.entries(values).map(([name,value])=>({name,value,tagName:'INPUT',disabled:false,checked:name==='confirmed'}));
 fields.push({name:'file',files:[],tagName:'INPUT',disabled:false},submit);for(const field of fields)if(field.name)fields[field.name]=field;
 const form={id:'account-form',elements:fields,querySelectorAll:s=>s==='button'?[submit]:[],querySelector:()=>submit};
 const env={window:{OvejaPayables:{refresh:async()=>{calls.push({refresh:true});}}},document:{addEventListener:(k,v)=>handlers[k]=v},state:{user:{role}},authGeneration:1,escapeHTML:escape,clp:n=>'$'+n,WeakMap,Map,URLSearchParams,JSON,Number,Error,console,crypto:{randomUUID:()=> '11111111-2222-3333-4444-555555555555'},FormData:class{constructor(f){return new Map(f.elements.filter(e=>e.name&&e.name!=='file'&&!e.disabled).map(e=>[e.name,e.value]));}},$:id=>id==='#account-form'?form:id==='#account-amount-summary'?summary:null,modal:(title,subtitle,html)=>modals.push({title,subtitle,html}),toast:s=>toasts.push(s),showFormError:s=>errors.push(s),api:async(url,method,body)=>{calls.push({url,method,body});return method==='POST'?post(body):data;}};
 vm.runInNewContext(source,env);
 const click=action=>handlers.click({target:{closest:()=>({dataset:{ac:action,id:String(data.invoices.find(i=>i.folio==='100').id),supplier:String(data.supplier.id)}})}});
 return {env,handlers,calls,modals,form,fields,submit,summary,errors,toasts,click,async open(){await click('invoice-note');},async save(){handlers.submit({target:form,preventDefault(){}});await flush();await flush();}};
}
test('invoice-linked form fixes origin and previews paid invoice credit without a new payment',async()=>{
 const r=runtime(fixture.paid);await r.open();const html=r.modals.at(-1).html;
 assert.match(html,/Factura <strong>100/);assert.match(html,/name="notes"[^>]*required/);assert.match(html,/name="file"/);assert.doesNotMatch(html,/select name="origin_invoice_id"|name="supplier_id"|name="method"/);
 assert.match(r.summary.textContent,/Aplicar a factura 100: \$0/);assert.match(r.summary.textContent,/Nuevo saldo a favor por esta NC: \$3000/);assert.equal(r.calls.filter(c=>c.method==='POST').length,0);
});
test('lost response retries frozen body and request ID instead of creating another note',async()=>{
 let count=0;const sent=[];const r=runtime(fixture.paid,async body=>{sent.push(body);if(!count++)throw Error('Respuesta perdida');return fixture.result;});await r.open();await r.save();
 assert.equal(r.submit.textContent,'Reintentar la misma nota');assert.ok(r.fields.amount.disabled);const calls=r.calls.length;await r.click('new-note');assert.equal(r.calls.length,calls);
 r.fields.amount.value='9999';await r.save();assert.deepEqual(sent[0],sent[1]);assert.equal(sent[1].amount,3000);assert.equal(sent[1].origin_invoice_id,fixture.paid.invoices.find(i=>i.folio==='100').id);assert.equal(sent[1].supplier_id,fixture.paid.supplier.id);assert.equal(sent[1].applications,undefined);
 assert.match(r.toasts.at(-1),/Nota 900 registrada/);
});
test('uncertain note is recoverable after another modal replaced the original form',async()=>{
 let attempts=0;const sent=[];const r=runtime(fixture.paid,async body=>{sent.push(body);if(!attempts++)throw Error('Respuesta perdida');return fixture.result;});await r.open();await r.save();
 await r.click('invoice-note');assert.equal(r.modals.at(-1).title,'Confirmar resultado de la nota');assert.match(r.modals.at(-1).html,/Motivo FICTICIO/);
 const retry={id:'account-form',elements:[r.submit],querySelectorAll:s=>s==='button'?[r.submit]:[],querySelector:()=>r.submit};
 r.env.FormData=class{constructor(){return new Map();}};r.env.crypto.randomUUID=()=> '99999999-9999-9999-9999-999999999999';
 // Submit through the registered listener using a newly rendered form object.
 // Existing handler is exposed by this harness, so the module state is retained.
 r.handlers.submit({target:retry,preventDefault(){}});await flush();await flush();assert.deepEqual(sent[0],sent[1]);
});
test('definitive validation error permits correction and does not freeze an uncertain result',async()=>{
 const r=runtime(fixture.before,async()=>{const e=Error('Folio duplicado');e.status=409;throw e;});await r.open();await r.save();
 assert.equal(r.errors.at(-1),'Folio duplicado');assert.equal(r.fields.amount.disabled,false);assert.equal(r.submit.textContent,'Guardar');await r.click('back');assert.ok(r.calls.length>=3);
});
test('cashier cannot open or submit invoice note',async()=>{const r=runtime(fixture.before,()=>assert.fail('Must not write'),'cashier');await r.open();await r.save();assert.equal(r.calls.length,0);});
test('invoice detail exposes original, applied credit, available credit and escaped proof links',()=>{
 const all=fs.readFileSync(path.join(root,'static/payables.js'),'utf8');const env={data:fixture.payables,e:escape,money:n=>'$'+n,invoice:fixture.payables.invoices.find(i=>i.folio==='100')};
 vm.createContext(env);vm.runInContext(all.slice(all.indexOf('  const creditButton='),all.indexOf('  function reset(){')),env);
 assert.match(vm.runInContext('creditButton(invoice)',env),/data-ac="invoice-note"/);
 const html=vm.runInContext('invoiceCreditNotes(invoice)',env);assert.match(html,/Original: \$10000/);assert.match(html,/NC 900 · \$3000/);assert.match(html,/Disponible a favor: \$3000/);assert.match(html,/\/api\/suppliers\/account-files\//);
 env.invoice={...env.invoice,voided_at:'2026-01-04'};assert.equal(vm.runInContext('creditButton(invoice)',env),'');
});
test('HTTP adapter preserves status for business rejection versus uncertain transport',async()=>{
 const source=fs.readFileSync(path.join(root,'static/app.js'),'utf8');const env={Error,JSON};vm.createContext(env);vm.runInContext(source.slice(source.indexOf('async function responsePayload('),source.indexOf('async function supplierFileUpload(')),env);
 for(const status of [400,403,409,413])await assert.rejects(env.responsePayload({status,ok:false,text:async()=>JSON.stringify({error:'Rechazado'})}),e=>e.status===status);
});
