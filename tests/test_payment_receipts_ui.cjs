// Terminal-only interaction tests; no browser, user session or operational records.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
  constructor(){this.nodes=new Map();this.events={};this.dataset={};this.value='';this.open=false;this.disabled=false;this._html='';}
  set innerHTML(v){this._html=v;this.nodes=new Map();}get innerHTML(){return this._html;}
  querySelector(s){if(!this.nodes.has(s)){const el=new Element();const action=s.match(/data-pr-action=([^\]]+)/);if(action)el.dataset.prAction=action[1];this.nodes.set(s,el);}return this.nodes.get(s);}
  querySelectorAll(){return [...this.nodes.values()];}setAttribute(){}append(){}replaceChildren(){this.innerHTML='';}scrollIntoView(){}
  addEventListener(n,fn){this.events[n]=fn;}showModal(){this.open=true;}close(){if(this.open){this.open=false;this.events.close?.();}}
}
const rows=[{key:'CC-7',supplier_name:'FICTICIO <QA>',amount:7000,invoices:[{folio:'100'},{folio:'101'}],files:[{id:3,created_at:'2026-01-03',name:'último.pdf'},{id:2,created_at:'2026-01-02',name:'anterior.pdf'}]},
{key:'PH-2',supplier_name:'SIN RESPALDO',amount:1000,invoices:[{folio:'102'}],files:[]}];
const listing={date:'2026-01-02',payments:rows,undated_payments:1,token:'test-token',limits:{payments:100}};
let dialog,requests=[],revoked=[],created=0,delay=null;
const context={window:{},state:{user:{role:'partner'}},document:{body:new Element(),createElement(){dialog=new Element();return dialog;}},
 escapeHTML:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),clp:n=>'$'+n,lockAccess(){context.state.user=null;},
 AbortController,URLSearchParams,URL:{createObjectURL(){return 'blob:test-'+(++created);},revokeObjectURL(u){revoked.push(u);}},
 fetch:async(url,options)=>{requests.push({url,options});if(delay)await delay;const pdf=url.includes('.pdf?');return {ok:true,headers:{get:()=>pdf?'application/pdf':'application/json'},blob:async()=>new Blob(['synthetic PDF']),json:async()=>url.includes('/preview?')?{payments:[{key:'CC-7',warnings:['Último comprobante no legible; respaldo pendiente.']}]}:structuredClone(listing)};}};
vm.createContext(context);vm.runInContext(fs.readFileSync('static/payment-receipts.js','utf8'),context);const app=context.window.OvejaPaymentReceipts;
const click=action=>dialog.events.click({target:{closest:()=>({dataset:{prAction:action}})}});
const change=target=>dialog.events.change({target});
(async()=>{
 await app.open();assert.equal(requests.length,1);assert(dialog.open);assert(!requests[0].url.includes('date='));assert(dialog.querySelector('[data-pr-rows]').innerHTML.includes('&lt;QA>'));
 assert(dialog.querySelector('[data-pr-summary]').textContent.includes('2 pagos'));assert(dialog.querySelector('[data-pr-summary]').textContent.includes('$8000'));assert.equal(created,0);
 await click('preview');assert.equal(requests.length,3);assert(requests[1].url.includes('/preview?'));assert(requests[2].url.includes('.pdf?'));assert.equal(new URLSearchParams(requests[2].url.split('?')[1]).get('files'),'{}');
 assert(dialog.querySelector('[data-pr-preview]').innerHTML.includes('Último comprobante no legible'));assert(dialog.querySelector('[data-pr-preview]').innerHTML.includes('Descargar PDF revisado'));
 change({dataset:{prFile:'CC-7'},value:'2'});assert.equal(revoked.length,1);assert.equal(dialog.querySelector('[data-pr-preview]').innerHTML,'');await click('preview');
 assert.equal(JSON.parse(new URLSearchParams(requests.at(-1).url.split('?')[1]).get('files'))['CC-7'],2);
 change({dataset:{prKey:'PH-2'},checked:false});await click('preview');assert.equal(new URLSearchParams(requests.at(-1).url.split('?')[1]).get('ids'),'CC-7');
 await click('none');assert(dialog.querySelector('[data-pr-action=preview]').disabled);await click('all');assert(dialog.querySelector('[data-pr-summary]').textContent.includes('2 pagos'));
 let release;delay=new Promise(resolve=>release=resolve);const pending=click('preview');app.reset();release();await pending;delay=null;assert(!dialog.open);assert.equal(dialog.querySelector('[data-pr-preview]').innerHTML,'');
 context.state.user={role:'cashier'};const count=requests.length;await app.open();assert.equal(requests.length,count);
 assert(requests.every(r=>!r.options.method||r.options.method==='GET'));assert(requests.every(r=>r.options.credentials==='same-origin'));
 console.log('PASS: date default, selection, preview before download, explicit previous version, missing proofs, stale/closed guard, revocation, permissions, read-only requests.');
})().catch(error=>{console.error(error);process.exit(1);});
