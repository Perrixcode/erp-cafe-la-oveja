'use strict';
// Deterministic memory transport: no network, accounts, credentials or DB writes.
const assert=require('node:assert/strict');
const {setup,flush,sha}=require('./test_cashier_photo_ui.cjs');
const fields=['identity','cash','retained','terminals','bagNumber','note','photos','missing_evidence_reasons'];
const declaration=v=>Object.fromEntries(fields.map(k=>[k,structuredClone(v[k])]));
function fixture(){
 let t,state,saveCommits=0,attachmentCommits=0;const receipts=new Map(),attachments=new Map(),faults=[];
 t=setup({http:async(url,init)=>{
  state||=structuredClone(t.model.current);const action=url.split('/').at(-1),body=JSON.parse(init.body);
  const fault=faults[0]?.action===action?faults.shift():null;
  if(fault?.status)return {ok:false,status:fault.status,json:async()=>({error:'Synthetic status '+fault.status})};
  if(fault?.when==='before')return new Promise(()=>{});
  let result;
  // Server replays exact receipts before checking the optimistic version.
  if(receipts.has(body.request_id)){
   const receipt=receipts.get(body.request_id);assert.equal(JSON.stringify(body),receipt.body);assert.equal(action,receipt.action);result=structuredClone(receipt.result);
  }else{
   if(body.version!==state.version)return {ok:false,status:409,json:async()=>({error:'El cierre cambió en otra sesión.'})};
   if(action==='save'){
    if(JSON.stringify(body.declaration)!==JSON.stringify(declaration(state))){state={...state,...structuredClone(body.declaration),version:state.version+1};saveCommits++;}
    result=structuredClone(state);
   }else{
    assert.equal(action,'attach');const hash=await sha(t.blob);result=attachments.get(hash);
    if(!result){result={id:'photo-1',kind:body.kind,name:body.file_name,type:body.file_type,size:t.blob.size,sha256:hash,legible:false};attachments.set(hash,result);attachmentCommits++;}
   }
   receipts.set(body.request_id,{body:JSON.stringify(body),action,result:structuredClone(result)});
  }
  if(fault?.when==='after')return new Promise(()=>{});
  return {ok:true,json:async()=>structuredClone(result)};
 }});
 state=structuredClone(t.model.current);
 const edit=text=>{const el=t.host.querySelector('[data-missing-reason="mp"]');el.value=text;el.oninput();};
 return {t,edit,faults,get state(){return state;},get saves(){return saveCommits;},get attachments(){return attachmentCommits;},
  otherEdit(text){state={...state,note:text,version:state.version+1};},
  bodies:action=>t.requests.filter(r=>r.url.endsWith('/'+action)).map(r=>r.body)};
}
async function timeout(work,t){const checked=Promise.resolve(work).then(()=>null,e=>e);await flush();await t.expire();return checked;}
async function run(){
 const cases=[],test=async(name,fn)=>{await fn();cases.push(name);};
 await test('Guardar ahora replays committed save before later edits, without duplicate versions',async()=>{
  const f=fixture();f.edit('First');f.faults.push({action:'save',when:'after'});assert(await timeout(f.t.ui.save(),f.t));
  f.edit('Latest');await f.t.ui.save();const saves=f.bodies('save');assert.deepEqual(saves[0],saves[1]);assert.notEqual(saves[1].request_id,saves[2].request_id);
  assert.equal(f.saves,2);assert.equal(f.state.version,3);assert.equal(f.t.model.current.version,3);assert.equal(f.state.missing_evidence_reasons.mp,'Latest');assert.equal(f.t.ui.current.missing_evidence_reasons.mp,'Latest');assert(!f.t.ui.dirty);assert.equal(f.t.model.savedSequence,f.t.model.sequence);
  await f.t.ui.save();assert.equal(f.bodies('save').length,3);
 });
 await test('photo retry reconciles pre-upload save then preserves exact uncertain attachment across another edit',async()=>{
  const f=fixture();f.edit('Before photo');f.faults.push({action:'save',when:'after'});await timeout(f.t.select(),f.t);assert(f.t.ui.pending);
  f.edit('After save timeout');f.faults.push({action:'attach',when:'after'});await timeout(f.t.click('retry-photo'),f.t);assert(f.t.ui.pending);assert.equal(f.attachments,1);
  f.edit('After attachment timeout');await f.t.ui.save();await f.t.click('retry-photo');await f.t.ui.save();
  assert.deepEqual(f.bodies('save')[0],f.bodies('save')[1]);assert.deepEqual(f.bodies('attach')[0],f.bodies('attach')[1]);
  assert.equal(f.attachments,1);assert.equal(f.saves,4);assert.equal(f.state.version,5);assert.equal(f.t.ui.current.photos.length,1);assert.equal(f.state.photos.length,1);assert.equal(f.state.photos[0].legible,false);assert.equal(f.state.missing_evidence_reasons.mp,'After attachment timeout');assert(!f.t.ui.pending);
 });
 await test('unchanged retry creates no additional version',async()=>{
  const f=fixture();f.edit('One');f.faults.push({action:'save',when:'after'});await timeout(f.t.ui.save(),f.t);await f.t.ui.save();assert.deepEqual(...f.bodies('save'));assert.equal(f.saves,1);assert.equal(f.state.version,2);
 });
 await test('edits while the first request hangs survive both reconciliation and queued saves',async()=>{
  const f=fixture();f.edit('First');f.faults.push({action:'save',when:'after'});const first=f.t.ui.save();await flush();f.edit('During request');const later=f.t.ui.save();await timeout(first,f.t);await later;assert.equal(f.state.missing_evidence_reasons.mp,'During request');assert.equal(f.saves,2);assert.equal(f.bodies('save').length,3);
 });
 await test('repeated lost replay responses retain one original identity',async()=>{
  const f=fixture();f.edit('One');f.faults.push({action:'save',when:'after'},{action:'save',when:'after'});await timeout(f.t.ui.save(),f.t);f.edit('Two');await timeout(f.t.ui.save(),f.t);f.edit('Three');await f.t.ui.save();
  const b=f.bodies('save');assert.deepEqual(b[0],b[1]);assert.deepEqual(b[0],b[2]);assert.equal(f.saves,2);assert.equal(f.state.missing_evidence_reasons.mp,'Three');
 });
 await test('uncommitted lost request is replayed before newer edits',async()=>{
  const f=fixture();f.edit('First');f.faults.push({action:'save',when:'before'});await timeout(f.t.ui.save(),f.t);assert.equal(f.saves,0);f.edit('Second');await f.t.ui.save();assert.deepEqual(f.bodies('save')[0],f.bodies('save')[1]);assert.equal(f.saves,2);assert.equal(f.state.missing_evidence_reasons.mp,'Second');
 });
 for(const committed of [true,false])await test('another actor is never overwritten after '+(committed?'committed':'uncommitted')+' uncertain save',async()=>{
  const f=fixture();f.edit('First');f.faults.push({action:'save',when:committed?'after':'before'});await timeout(f.t.ui.save(),f.t);f.otherEdit('Other actor');f.edit('Local latest');
  await assert.rejects(()=>f.t.ui.save(),e=>e.status===409);await assert.rejects(()=>f.t.ui.save(),e=>e.status===409);
  assert.equal(f.state.note,'Other actor');assert.equal(f.state.missing_evidence_reasons.mp,committed?'First':'');assert.equal(f.t.ui.current.missing_evidence_reasons.mp,'Local latest');assert(f.t.ui.dirty);assert.equal(f.saves,committed?1:0);assert.equal(f.t.model.current.version,committed?2:1);
 });
 await test('photo retry surfaces actual conflict and never attaches over another actor',async()=>{
  const f=fixture();f.edit('Before');f.faults.push({action:'save',when:'after'});await timeout(f.t.select(),f.t);f.otherEdit('Other actor');f.edit('After');await f.t.click('retry-photo');assert(f.t.ui.pending);assert.equal(f.bodies('attach').length,0);assert.match(f.t.host.innerHTML,/otra sesión/);assert.equal(f.state.note,'Other actor');
 });
 for(const status of [401,403,408,429])await test(status+' during recovery keeps uncertain identity for later authorized retry',async()=>{
  const f=fixture();f.edit('First');f.faults.push({action:'save',when:'after'});await timeout(f.t.ui.save(),f.t);f.edit('Second');f.faults.push({action:'save',status});await assert.rejects(()=>f.t.ui.save(),e=>e.status===status);await f.t.ui.save();const b=f.bodies('save');assert.deepEqual(b[0],b[1]);assert.deepEqual(b[0],b[2]);assert.equal(f.saves,2);assert.equal(f.state.missing_evidence_reasons.mp,'Second');
 });
 await test('definite validation failure permits corrected data without reporting false success',async()=>{
  const f=fixture();f.edit('Invalid');f.faults.push({action:'save',status:422});await assert.rejects(()=>f.t.ui.save(),e=>e.status===422);assert.equal(f.saves,0);assert(f.t.ui.dirty);f.edit('Corrected');await f.t.ui.save();assert.notEqual(f.bodies('save')[0].request_id,f.bodies('save')[1].request_id);assert.equal(f.saves,1);assert.equal(f.state.missing_evidence_reasons.mp,'Corrected');
 });
 console.log(JSON.stringify({passed:cases.length,checks:cases,limits:['Synthetic DOM and deterministic memory transport; no physical camera/browser.']}));
}
if(require.main===module)run().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={fixture,timeout};
