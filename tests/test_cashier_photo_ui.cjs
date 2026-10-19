'use strict';
// Terminal-only DOM/camera/network fault injection. No browser, credentials or real photos.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
class Element {
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.innerHTML='';this.attrs={};this.dataset={};this.value='';}
 querySelectorAll(selector){
  if(this.parsed!==this.innerHTML){this.parsed=this.innerHTML;this.elements=[];for(const match of this.innerHTML.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*)>/gi)){const el=new Element(match[1]);for(const a of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)){el.attrs[a[1]]=a[2]??'';if(a[1].startsWith('data-'))el.dataset[a[1].slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=a[2]??'';}el.value=el.attrs.value??'';el.disabled='disabled'in el.attrs;this.elements.push(el);}}
  return this.elements.filter(el=>selector.split(',').some(sel=>{const tag=sel.trim().match(/^[a-z]+/i)?.[0];if(tag&&tag.toUpperCase()!==el.tagName)return false;for(const m of sel.matchAll(/\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\]/g)){if(!(m[1]in el.attrs)||m[2]!==undefined&&el.attrs[m[1]]!==m[2])return false;}return true;}));
 }
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
}
const flush=async()=>{for(let i=0;i<40;i++)await new Promise(r=>setImmediate(r));};
const sha=async blob=>crypto.createHash('sha256').update(Buffer.from(await blob.arrayBuffer())).digest('hex');
function setup(options={}){
 let timerId=0;const timers=new Map(),urls=new Map(),images=[],canvases=[],requests=[];
 const controls={decode:'ok',encode:'ok',network:'ok',digest:'ok',width:4032,height:3024,...options};
 const blob=options.blob||new Blob([Buffer.from('synthetic compressed JPEG bytes')],{type:'image/jpeg'});
 const value={id:'synthetic-closure',version:1,status:'draft',identity:{business_date:'2026-10-19',register_id:'qa',counted_at:null},cash:{1000:5},retained:{1000:1},terminals:[{terminal_id:'QA',total:9000}],photos:[],note:'Datos ya ingresados',bagNumber:'BOL-TEST',missing_evidence_reasons:{mp:'',toteat:''},summary:{expected:null,difference:null,terminals:[],source_state:'pending'},events:[],submitted_history:[]};
 const sandbox={console,structuredClone,AbortController,Blob,URLSearchParams,Intl,Date,TextEncoder,btoa,addEventListener(){},
  setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);},
  URL:{createObjectURL(b){const url='blob:synthetic/'+urls.size+'/'+Math.random();urls.set(url,b);return url;},revokeObjectURL(url){urls.delete(url);}},
  crypto:{randomUUID:crypto.randomUUID,subtle:{digest(...args){return controls.digest==='hang'?new Promise(()=>{}):crypto.webcrypto.subtle.digest(...args);}}},
  Image:class {constructor(){images.push(this);this.width=this.naturalWidth=controls.width;this.height=this.naturalHeight=controls.height;}set src(v){this.source=v;if(v&&controls.decode!=='hang')queueMicrotask(()=>controls.decode==='error'?this.onerror?.(new Error('decode')):this.onload?.());}},
  document:{createElement(tag){if(tag!=='canvas')return new Element(tag);const canvas={width:0,height:0,qualities:[],getContext(){return controls.encode==='no-context'?null:{fillRect(){},drawImage(){canvas.drawn=[canvas.width,canvas.height];if(controls.encode==='throw')throw Error('Canvas unavailable');}};},toBlob(callback,type,quality){canvas.qualities.push(quality);if(controls.encode==='hang')return;queueMicrotask(()=>callback(controls.encode==='null'?null:controls.encode==='oversize'||controls.encode==='reduce'&&quality===.85?new Blob([new Uint8Array(3*1024*1024+1)],{type:'image/jpeg'}):blob));}};canvases.push(canvas);return canvas;}},
  async fetch(url,init){requests.push({url,init,body:JSON.parse(init.body)});if(controls.network==='hang')return new Promise(resolve=>controls.lateResponse=resolve);if(controls.network==='body-hang')return {ok:true,json:()=>new Promise(()=>{})};if(controls.network==='error')throw Error('Synthetic offline');if(controls.network==='401')return {ok:false,status:401,json:async()=>({error:'La sesión venció.'})};const body=JSON.parse(init.body);if(options.http)return options.http(url,init);if(url.endsWith('/save'))return {ok:true,json:async()=>({...structuredClone(value),...body.declaration,version:++value.version})};const uploaded={id:'photo-1',kind:body.kind,name:body.file_name,type:body.file_type,size:blob.size,sha256:await sha(blob),legible:false};if(controls.network==='mismatch')uploaded.sha256='0'.repeat(64);return {ok:true,json:async()=>uploaded};}
 };
 sandbox.window=sandbox;vm.createContext(sandbox);
 const source=process.env.ERP_PHOTO_SOURCE||path.join(__dirname,'../static');
 for(const name of ['cashier-v2-server.js','cashier-v2.js'])vm.runInContext(fs.readFileSync(path.join(source,name),'utf8'),sandbox,{filename:name});
 const model=new sandbox.OvejaCashierV2Server.Model(options.value||value,{data:{denominations:options.denominations||[1000]},status(){}}),host=new Element();
 const ui=sandbox.CashierV2.mount({host,model,user:{name:'Synthetic QA'},registers:[{id:'qa',name:'QA'}]});ui.step=3;ui.render();
 const select=(file={name:'synthetic-camera.jpg',type:'image/jpeg',size:9000000},kind='mp')=>{const el=host.querySelector('[data-photo="'+kind+'"]');el.files=[file];return el.onchange();};
 const expire=async()=>{await flush();const current=[...timers.values()];timers.clear();for(const t of current)t.fn();await flush();};
 const click=action=>{const el=host.querySelector('[data-action="'+action+'"]');assert(el,action);return el.onclick();};
 return {controls,host,model,ui,select,expire,requests,urls,images,canvases,timers,blob,click};
}
async function run(){
 const results=[];const test=async(name,fn)=>{await fn();results.push(name);};
 await test('decoder that never fires releases busy state and preserves draft',async()=>{
  const t=setup({decode:'hang'});let finished=false;t.select().then(()=>finished=true);await t.expire();
  assert(finished,'Camera decode stays pending forever');assert(!t.host.querySelector('[data-action="next"]').disabled);assert.equal(t.ui.current.notes,'Datos ya ingresados');assert.equal(t.ui.current.countsA[1000],5);assert.equal(t.requests.length,0);assert.equal(t.urls.size,0);
 });
 await test('HTTP stall releases request queue without accepting a photo',async()=>{
  const t=setup({network:'hang'});let finished=false;t.select().then(()=>finished=true);await t.expire();
  assert(finished,'Photo request stays pending forever');assert.equal(t.ui.current.photos.length,0);assert.equal(t.ui.current.notes,'Datos ya ingresados');assert(t.requests[0].init.signal.aborted);
 });
 for(const fault of ['hang','null','no-context','throw','oversize'])await test('canvas '+fault+' exits safely and releases resources',async()=>{
  const t=setup({encode:fault});const work=t.select();await t.expire();await work;assert(!t.ui.pending);assert.equal(t.requests.length,0);assert.equal(t.urls.size,0);assert(t.canvases.every(c=>c.width===1&&c.height===1));assert.equal(t.ui.current.photos.length,0);
 });
 await test('digest timeout rejects evidence and keeps entered values',async()=>{
  const t=setup({digest:'hang'}),work=t.select();await t.expire();await work;assert.equal(t.ui.current.photos.length,0);assert(!t.ui.pending);assert.equal(t.ui.current.terminals[0].total,9000);
 });
 await test('large camera image is reduced to 2200 pixels and encoding stays under 3 MiB',async()=>{
  const t=setup({width:8000,height:6000,encode:'reduce'});await t.select();assert.deepEqual(t.canvases[0].drawn,[2200,1650]);assert.deepEqual(t.canvases[0].qualities,[.85,.75]);assert.equal(t.ui.current.photos.length,1);assert.equal(t.ui.current.photos[0].legible,false);assert.equal(t.urls.size,1);assert(!t.ui.pending);assert.equal(t.timers.size,0);
 });
 for(const file of [{name:'empty.jpg',type:'image/jpeg',size:0},{name:'too-large.jpg',type:'image/jpeg',size:31*1024*1024},{name:'document.pdf',type:'application/pdf',size:100}])await test(file.name+' rejected before decoding or upload',async()=>{
  const t=setup();await t.select(file);assert.equal(t.images.length,0);assert.equal(t.requests.length,0);assert(!t.ui.pending);
 });
 await test('over 50 megapixels rejected before canvas allocation',async()=>{
  const t=setup({width:10000,height:6000});await t.select();assert.equal(t.canvases.length,0);assert(!t.ui.pending);assert.equal(t.urls.size,0);
 });
 await test('empty MIME uses filename then decoder, unsupported HEIC gives actionable error',async()=>{
  const t=setup();await t.select({name:'camera.JPG',type:'',size:100});assert.equal(t.ui.current.photos.length,1);
  const heic=setup({decode:'error'});await heic.select({name:'camera.HEIC',type:'image/heic',size:100});assert.match(heic.host.innerHTML,/HEIC.*JPG/);assert.equal(heic.requests.length,0);assert(!heic.ui.pending);
 });
 await test('cancelled picker starts no work',async()=>{
  const t=setup(),el=t.host.querySelector('[data-photo]');el.files=[];await el.onchange();assert.equal(t.images.length,0);assert(!t.ui.pending);
 });
 await test('duplicate selections are serialized, duplicate server id is not appended twice',async()=>{
  const t=setup();await Promise.all([t.select(),t.select()]);assert.equal(t.requests.length,1);await t.select();assert.equal(t.ui.current.photos.length,1);
 });
 for(const fault of ['hang','body-hang','error','401','mismatch'])await test('upload '+fault+' remains unconfirmed and exact photo can be retried',async()=>{
  const t=setup({network:fault});const work=t.select();await t.expire();await work;
  assert.equal(t.ui.current.photos.length,0);assert(t.ui.pending);assert(t.host.querySelector('[data-action="retry-photo"]'));assert.equal(t.urls.size,0);const first=t.requests[0].body;
  // A distinct local edit/autosave can advance the version while a reply is lost.
  t.model.current.version=2;t.controls.network='ok';await t.click('retry-photo');assert.equal(t.ui.current.photos.length,1);assert(!t.ui.pending);
  if(['hang','body-hang','error'].includes(fault))assert.deepEqual(t.requests[1].body,first,'Uncertain retry must preserve request id AND original version/body');
 });
 await test('late HTTP success after timeout cannot modify evidence',async()=>{
  const t=setup({network:'hang'}),work=t.select();await t.expire();await work;t.controls.lateResponse({ok:true,json:async()=>({id:'too-late'})});await flush();assert.equal(t.ui.current.photos.length,0);assert(t.ui.pending);
 });
 await test('save timeout releases queue and preserves edits before attaching',async()=>{
  const t=setup({network:'hang'});const input=t.host.querySelector('[data-missing-reason="mp"]');input.value='Impreso pendiente';input.oninput();const work=t.select();await t.expire();await work;assert(t.requests[0].url.endsWith('/save'));assert.equal(t.ui.current.missing_evidence_reasons.mp,'Impreso pendiente');assert.equal(t.ui.current.photos.length,0);t.controls.network='ok';await t.click('retry-photo');assert.equal(t.ui.current.photos.length,1);assert.equal(t.requests[1].body.request_id,t.requests[0].body.request_id);
 });
 await test('discard of pending upload preserves financial fields and evidence validation',async()=>{
  const t=setup({network:'error'});await t.select();await t.click('discard-photo');assert(!t.ui.pending);assert.equal(t.ui.current.photos.length,0);assert.equal(t.ui.current.countsA[1000],5);assert.equal(t.ui.current.notes,'Datos ya ingresados');await t.click('next');assert.equal(t.ui.step,3);assert.match(t.host.innerHTML,/Confirma un respaldo legible/);
 });
 await test('batch errors remain visible after a later successful file',async()=>{
  const t=setup(),el=t.host.querySelector('[data-photo]');el.files=[{name:'bad.pdf',type:'application/pdf',size:100},{name:'good.jpg',type:'image/jpeg',size:100}];await el.onchange();assert.equal(t.ui.current.photos.length,1);assert.match(t.host.innerHTML,/bad.pdf/);
 });
 await test('destroy during decoding or upload never accepts late evidence',async()=>{
  const t=setup({decode:'hang'}),work=t.select();t.ui.destroy();await t.expire();await work;assert.equal(t.requests.length,0);assert.equal(t.urls.size,0);assert.equal(t.host.innerHTML,'');
  const u=setup({network:'hang'}),upload=u.select();await flush();u.ui.destroy();u.controls.lateResponse({ok:true,json:async()=>({id:'late'})});await upload;assert.equal(u.ui.current.photos.length,0);assert.equal(u.host.innerHTML,'');
 });
 console.log(JSON.stringify({passed:results.length,checks:results,limits:['Synthetic DOM and callback injection; no physical camera or browser engine.']}));
}
if(require.main===module)run().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={setup,flush,sha,Element};
