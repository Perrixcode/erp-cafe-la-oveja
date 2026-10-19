'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../static/joint-payment.js'),'utf8');
function harness(){
 let html='',posts=[],fail=false,apiValue;const listeners={};const rows=[100,100,200].map((balance,i)=>({id:i+1,supplier_id:1,folio:String(i+801),balance,version:1}));
 apiValue={invoices:rows,suppliers:[{id:1,name:'Proveedor <QA>'}],today:'2026-10-15',methods:{transfer:'Transferencia'}};
 const h={window:{addEventListener(){}},state:{user:{role:'partner'}},authGeneration:1,escapeHTML:x=>String(x??'').replaceAll('<','&lt;'),clp:n=>'$'+n,modal:(_t,_s,body)=>html=body,api:async()=>apiValue,toast(){},showFormError:message=>{throw Error(message);},document:{addEventListener:(event,callback)=>listeners[event]=callback},crypto:{randomUUID:()=>String(Math.random())},FormData:class{constructor(form){return Object.entries(form.values);}},FileReader:class{readAsDataURL(){this.result='data:application/pdf;base64,UERG';this.onload();}},fetch:async(path,request)=>{posts.push({path,body:JSON.parse(request.body)});if(fail){fail=false;throw Error('network');}const p=JSON.parse(request.body);return {status:201,ok:true,text:async()=>JSON.stringify({id:7,file_id:8,applied:p.applications.reduce((n,a)=>n+a.amount,0),available:p.overpayment_confirmation?.excess||0})};},responsePayload:async response=>JSON.parse(await response.text())};
 vm.createContext(h);vm.runInContext(source.replace('return {open,reset};','return {open,reset,review,save,distribute,refreshBalances,inspect:()=>draft};'),h);const app=h.window.OvejaJointPayment;
 const edit=(total,amounts)=>({values:{paid_total:String(total),date:'2026-10-15',method:'transfer',reference:'QA',notes:'Nota conservada',...Object.fromEntries(rows.map((r,i)=>['amount_'+r.id,String(amounts[i]||0)]))},elements:Object.assign([],{proof:{files:[{name:'qa.pdf',size:3}]}})});
 const confirmation={elements:{confirmed:{checked:true}},querySelectorAll:()=>[]};
 return {h,app,rows,edit,confirmation,posts,listeners,html:()=>html,failNext:()=>fail=true,setData:v=>apiValue=v};
}
test('450 for three invoices applies 400 and explicitly confirms 50 with one payload',async()=>{
 const t=harness();await t.app.open([1,2,3],async()=>{});await t.app.review(t.edit(450,[100,100,200]));
 assert.match(t.html(),/Registrarás un pago de \$450 para facturas con saldo de \$400/);assert.match(t.html(),/Los \$50 restantes/);assert.match(t.html(),/Confirmar pago y saldo a favor/);assert.match(t.html(),/>Cancelar</);
 await t.app.save(t.confirmation);assert.equal(t.posts.length,1);assert.equal(t.posts[0].body.amount,450);assert.equal(t.posts[0].body.applications.reduce((n,a)=>n+a.amount,0),400);assert.equal(t.posts[0].body.overpayment_confirmation.excess,50);
});
test('individual excess uses same form and account endpoint',async()=>{
 const t=harness();await t.app.open([1],async()=>{});await t.app.review(t.edit(120,[100]));await t.app.save(t.confirmation);
 assert.equal(t.posts[0].path,'/api/suppliers/account-invoice-payments');assert.equal(t.posts[0].body.amount,120);assert.equal(t.posts[0].body.applications.length,1);
});
test('cancel retains entered total, reference, amounts and proof without posting',async()=>{
 const t=harness();await t.app.open([1,2,3],async()=>{});await t.app.review(t.edit(450,[100,100,200]));t.listeners.click({target:{closest:selector=>selector==='[data-joint-back]'?{}:null}});
 assert.equal(t.posts.length,0);assert.match(t.html(),/name="paid_total" value="450"/);assert.match(t.html(),/Nota conservada/);assert.match(t.html(),/Comprobante seleccionado: qa.pdf/);
});
test('double click is suppressed and uncertain retry uses identical operation and proof',async()=>{
 const t=harness();await t.app.open([1,2,3],async()=>{});await t.app.review(t.edit(450,[100,100,200]));t.failNext();await Promise.all([t.app.save(t.confirmation),t.app.save(t.confirmation)]);
 assert.equal(t.posts.length,1);assert.equal(t.app.inspect().uncertain,true);await t.app.save(t.confirmation);assert.equal(t.posts.length,2);assert.deepEqual(t.posts[0],t.posts[1]);
});
test('changed balances are reloaded then shown for a new confirmation',async()=>{
 const t=harness();await t.app.open([1,2,3],async()=>{});await t.app.review(t.edit(450,[100,100,200]));t.app.inspect().needsRefresh=true;await t.app.save(t.confirmation);assert.equal(t.posts.length,0);t.setData({invoices:t.rows.map((r,i)=>({...r,balance:i===0?75:r.balance,version:i===0?2:1}))});await t.app.refreshBalances();
 assert.equal(t.app.inspect().needsRefresh,false);assert.equal(t.app.inspect().payload,null);assert.equal(t.app.inspect().rows[0].version,2);await t.app.review(t.edit(450,[75,100,200]));assert.equal(t.app.inspect().payload.overpayment_confirmation.excess,75);assert.equal(t.posts.length,0);
});
test('distribution caps every invoice and handles partial amounts including zeros',()=>{
 const t=harness();assert.deepEqual(Array.from(t.app.distribute(450,t.rows)),[100,100,200]);assert.deepEqual(Array.from(t.app.distribute(50,t.rows)),[50,0,0]);
});
