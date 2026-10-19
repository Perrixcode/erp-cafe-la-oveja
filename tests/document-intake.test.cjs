'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../static/document-intake.js'),'utf8');
const clone=x=>JSON.parse(JSON.stringify(x));
const base=()=>({id:1,state:'pending',name:'ficticio.pdf',mime:'application/pdf',extraction_note:'Lectura ficticia',reading:{text:'Ficticio'},same_files:[],lines:[],suggested:{invoice:{folio:'000800',issued_on:'2026-10-01',amount:11900,document_type:'invoice',issuer_name:'Proveedor QA'},net:10000,tax:1900,lines:[{code:'QA100',description:'Producto sugerido',quantity:'2',unit_price:'5000',amount_clp:10000}]},review:{}});
function harness(documents){
 const h={window:{},document:{addEventListener(){}},state:{user:{role:'partner'}},authGeneration:1,WeakMap,
  escapeHTML:x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),clp:x=>'$'+x,$:()=>null,
  api:async path=>path==='/api/suppliers/payables'?{suppliers:[{id:7,name:'Proveedor QA',tax_id:''}],invoices:[],types:{invoice:'Factura'}}:path==='/api/suppliers/procurement'?{items:[],units:[]}:clone(documents[Number(path.split('/').at(-1))]),
  modal:(title,subtitle,html)=>{h.title=title;h.html=html;}};
 vm.createContext(h);vm.runInContext(source,h);return h;
}
function value(h,name){const found=h.html.match(new RegExp('name="'+name+'" value="([^"]*)"'));assert.ok(found,'Rendered input '+name);return found[1];}
test('empty review uses structured invoice and lines in the actual rendered form',async()=>{
 const h=harness({1:base()});await h.window.OvejaDocumentIntake.review(1);
 assert.equal(value(h,'folio'),'000800');assert.equal(value(h,'amount'),'11900');assert.equal(value(h,'issued_on'),'2026-10-01');assert.equal(value(h,'quantity'),'2');assert.equal(value(h,'unit_price'),'5000');assert.match(h.html,/data-draft-id="1"/);
});
test('partial human invoice retains changed fields and fills only missing fields',async()=>{
 const d=base();d.review={invoice:{folio:'000999',description:'Edición humana'}};const h=harness({1:d});await h.window.OvejaDocumentIntake.review(1);
 assert.equal(value(h,'folio'),'000999');assert.equal(value(h,'amount'),'11900');assert.equal(value(h,'description'),'Edición humana');assert.equal(value(h,'quantity'),'2');
});
test('explicit blanks, zero tax and removal of all lines remain human choices',async()=>{
 const d=base();d.review={invoice:{folio:'',due_on:''},tax:0,lines:[]};const h=harness({1:d});await h.window.OvejaDocumentIntake.review(1);
 assert.equal(value(h,'folio'),'');assert.equal(value(h,'tax'),'0');assert.doesNotMatch(h.html,/<fieldset class="di-line">/);
});
test('human product rows are retained without combining rows by position',async()=>{
 const d=base();d.review={lines:[{description:'Producto humano',quantity:'3'}]};const h=harness({1:d});await h.window.OvejaDocumentIntake.review(1);
 assert.equal(value(h,'quantity'),'3');assert.equal(value(h,'unit_price'),'');assert.match(h.html,/Producto humano/);assert.doesNotMatch(h.html,/Producto sugerido/);
});
test('flat suggestion contract from the current parser also populates the form',async()=>{
 const d=base();d.suggested={...d.suggested,...d.suggested.invoice};delete d.suggested.invoice;d.review={invoice:{}};
 const h=harness({1:d});await h.window.OvejaDocumentIntake.review(1);assert.equal(value(h,'amount'),'11900');assert.equal(value(h,'quantity'),'2');
});
test('overrides and saved snapshots cannot cross document identities',async()=>{
 const d=base(),h=harness({1:d});await assert.rejects(h.window.OvejaDocumentIntake.review(1,{draft_id:2,invoice:{folio:'wrong'}}),/otro documento/);
 d.review={draft_id:2};const second=harness({1:d});await assert.rejects(second.window.OvejaDocumentIntake.review(1),/otro documento/);
});
test('a late response cannot replace another document or survive reset',async()=>{
 const a=base(),b={...base(),id:2,name:'segundo.pdf'},h=harness({1:a,2:b}),api=h.api;let release;
 h.api=path=>path.endsWith('/documents/1')?new Promise(resolve=>release=()=>resolve(a)):api(path);
 const first=h.window.OvejaDocumentIntake.review(1);await h.window.OvejaDocumentIntake.review(2);release();await first;assert.equal(h.title,'Revisar segundo.pdf');
 const pending=h.window.OvejaDocumentIntake.review(1);h.window.OvejaDocumentIntake.reset();release();await pending;assert.equal(h.title,'Revisar segundo.pdf');
});

test('RCV UI separates credits, retains tax evidence and warns about file coverage',async()=>{
 const full=fs.readFileSync(require.resolve('../static/payables.js'),'utf8'),siiSource=full.slice(full.indexOf('window.OvejaSiiReview='));
 const listeners={},elements=new Map(),node=()=>({innerHTML:'',textContent:'',checked:false,disabled:false,querySelectorAll:()=>[],addEventListener(){},classList:{add(){},remove(){},toggle(){}},scrollIntoView(){}});
 const row={row:2,name:'Proveedor ficticio',tax_id:'761111111',folio:'000123',issued_on:'2026-10-01',sii_type:'33',document_kind:'invoice',document_label:'Factura electrónica',status:'new',amount:11900,original:{'Codigo Otro Impuesto':'27','Valor Otro Impuesto':'50','Tasa Otro Impuesto':'10'},tax_details:[{row:3,original:{'Codigo Otro Impuesto':'271','Valor Otro Impuesto':'100','Tasa Otro Impuesto':'18'}}]};
 const preview={id:'demo',name:'ficticio.csv',rows:[row,{...row,row:4,sii_type:'61',document_kind:'credit_note',document_label:'Nota de crédito electrónica',status:'unsupported',amount:1985,tax_details:[],original:{}}],document_totals:{credit_note:{count:1,amount:1985}},review:{version:1,invalidated:[],draft:{selected:[],discarded:[],conditions:{},overrides:{},details:{},view:{page:1,filter:'pending',search:'',supplier:''}},follow_up:{'':''},reasons:{}}};
 const h={window:{addEventListener(){}},document:{addEventListener:(type,fn)=>(listeners[type]??=[]).push(fn)},state:{user:{role:'partner'}},authGeneration:1,structuredClone,clearTimeout,setTimeout,
  escapeHTML:x=>String(x??''),clp:x=>'$'+x,$:selector=>{if(!elements.has(selector))elements.set(selector,node());return elements.get(selector);},
  api:async()=>clone(preview),modal:(title,subtitle,html)=>h.html=html,toast:()=>{}};
 vm.createContext(h);vm.runInContext(siiSource,h);
 const button={dataset:{sii:'open',id:'demo'}},event={target:{closest:selector=>selector==='[data-sii]'?button:null}};
 for(const fn of listeners.click)fn(event);await new Promise(setImmediate);
 assert.match(h.html,/cubre solo los documentos contenidos en este CSV/);
 assert.match(h.html,/Una ausencia no elimina documentos del ERP/);
 const progress=elements.get('#sii-progress').innerHTML;
 assert.match(progress,/<span>Pendientes<\/span><strong>2<\/strong><small>\$11900<\/small>/);
 assert.match(progress,/Notas de crédito: 1 · \$1985 por revisar, sin compensar/);
 const rows=elements.get('#sii-rows').innerHTML;
 assert.match(rows,/folio 000123/);assert.match(rows,/código 27 · importe 50/);assert.match(rows,/código 271 · importe 100/);
 assert.match(rows,/type="checkbox" data-sii-pick="4"  disabled/);
});
