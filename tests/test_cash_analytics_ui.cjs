'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{execFileSync}=require('node:child_process'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const fixtures=JSON.parse(execFileSync(process.env.ERP_TEST_PYTHON||'python3',['-B',path.join(__dirname,'cash_analytics_ui_fixture.py')],{cwd:root,env:{...process.env,PYTHONPATH:__dirname+path.delimiter+root},encoding:'utf8'}));
const flush=()=>new Promise(setImmediate);
function runtime(file,payload,role='partner',post=async()=>({ok:true})){
 const handlers={},calls=[],error={hidden:true,textContent:'',scrollIntoView(){}};
 const host={innerHTML:'',setAttribute(){},classList:{add(){},remove(){}},addEventListener(k,v){handlers[k]=v;},replaceChildren(){this.innerHTML='';},querySelector:s=>s==='[data-cc-error]'?error:null};
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const env={window:{},Intl,Date,URLSearchParams,console,Map,state:{user:{role}},authGeneration:1,location:{search:'?cash_start=2026-10-16&cash_end=2026-10-16'},history:{replaceState(){}},escapeHTML:esc,$:()=>({append(){}}),document:{createElement:()=>host,querySelector:()=>({append(){}})},FormData:class{constructor(form){return new Map(form.values);}},crypto:{randomUUID:()=> '11111111-2222-3333-4444-555555555555'},fetch:async(url,options)=>{calls.push({url,options});const body=options.method==='POST'?await post(JSON.parse(options.body)):payload;return {ok:true,status:200,json:async()=>body};}};
 vm.runInNewContext(fs.readFileSync(path.join(root,'static',file),'utf8'),env);
 return {host,handlers,calls,error,module:env.window[file==='cash-control.js'?'OvejaCashControl':'OvejaCashInsights'],async open(section){this.module.enter(section);await flush();}};
}
test('unknown starting point renders empty explicit configuration and no automatic write',async()=>{
 const r=runtime('cash-control.js',fixtures.unknown);await r.open('Resumen');
 assert.match(r.host.innerHTML,/No hay saldo inicial confirmado/);assert.match(r.host.innerHTML,/data-cc-command="balance-origin"/);
 assert.doesNotMatch(r.host.innerHTML,/name="amount"[^>]*value="0"|undefined|NaN/);assert.equal(r.calls.length,1);assert.equal(r.calls[0].options.method,'GET');
});
test('provisional balance renders actual backend conservation and unknown coverage',async()=>{
 const r=runtime('cash-control.js',fixtures.configured);await r.open('Movimientos');
 assert.match(r.host.innerHTML,/Saldo provisional · cobertura incompleta/);assert.match(r.host.innerHTML,/\$130/);assert.match(r.host.innerHTML,/Variación conocida del rango: \$30/);
 assert.match(r.host.innerHTML,/No acredita dinero disponible/);assert.doesNotMatch(r.host.innerHTML,/undefined|NaN/);
});
test('lost configuration response reuses the exact request and keeps explicit evidence',async()=>{
 const sent=[];let first=true;
 const r=runtime('cash-control.js',fixtures.unknown, 'partner',async body=>{sent.push(body);if(first){first=false;throw Error('Respuesta perdida');}return {ok:true};});await r.open('Resumen');
 const form={values:Object.entries({amount:'100',cutoff_day:'2026-10-16',evidence:'Apertura FICTICIA revisada',confirmed:'on'}),dataset:{ccCommand:'balance-origin'},matches:()=>true,hasAttribute:()=>false,querySelectorAll:()=>[]};
 await r.handlers.submit({target:form,preventDefault(){}});assert.match(r.error.textContent,/Respuesta perdida/);
 await r.handlers.submit({target:form,preventDefault(){}});assert.deepEqual(sent[0],sent[1]);assert.equal(sent[0].amount,100);assert.equal(sent[0].confirmed,true);assert.equal(sent[0].version,fixtures.unknown.version);
});
test('cashier cannot load administrative balance or analytics',async()=>{
 for(const file of ['cash-control.js','cash-insights.js']){const r=runtime(file,fixtures.unknown,'cashier');await r.open('analysis');assert.equal(r.calls.length,0);assert.doesNotMatch(r.host.innerHTML,/data-cc-command/);}
});
test('full analytics renders backend population and independent cash and MP metrics',async()=>{
 const r=runtime('cash-insights.js',fixtures.analysis);await r.open('analysis');
 assert.match(r.host.innerHTML,/Población conocida y muestra comparable/);assert.match(r.host.innerHTML,/1 de 2 registros revisados/);assert.match(r.host.innerHTML,/Mercado Pago por terminal y período/);assert.match(r.host.innerHTML,/cobertura parcial o pendiente/);assert.doesNotMatch(r.host.innerHTML,/undefined|NaN/);
 r.handlers.change({target:{dataset:{},name:'metric',value:'mp'}});assert.match(r.host.innerHTML,/Diferencias de Mercado Pago por terminal y período/);assert.match(r.host.innerHTML,/Bruto declarado de los terminales incluidos/);assert.match(r.host.innerHTML,/1 de 1 registros revisados/);
});
