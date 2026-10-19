'use strict';
// Actual Python domain + isolated temporary database over stdio. No HTTP/login.
const path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process'),readline=require('node:readline');
const {setup,sha}=require('./test_cashier_photo_ui.cjs');
(async()=>{
 const backend=spawn(process.env.ERP_TEST_PYTHON||'python3',['-B',path.join(__dirname,'cashier_photo_fixture.py')],{cwd:path.join(__dirname,'..'),env:{...process.env,PYTHONPATH:'.:tests',PYTHONDONTWRITEBYTECODE:'1'},stdio:['pipe','pipe','pipe']});
 let errors='';backend.stderr.on('data',b=>errors+=b);const rl=readline.createInterface({input:backend.stdout}),responses=[],waiting=[];
 rl.on('line',line=>{const data=JSON.parse(line);if(waiting.length)waiting.shift()(data);else responses.push(data);});
 const receive=()=>responses.length?Promise.resolve(responses.shift()):new Promise(resolve=>waiting.push(resolve));
 const rpc=async request=>{backend.stdin.write(JSON.stringify(request)+'\n');return receive();};
 const startup=setTimeout(()=>backend.kill(),15000);
 try{
  const config=await Promise.race([receive(),new Promise((_,reject)=>backend.once('exit',code=>reject(Error('Fixture exit '+code+': '+errors))))]);clearTimeout(startup);
  const initialVersion=config.value.version,blob=new Blob([Buffer.from(config.image,'base64')],{type:'image/jpeg'});let loseSave=true,loseAttach=true;
  const t=setup({value:config.value,blob,denominations:Object.keys(config.value.cash).map(Number),http:async(url,init)=>{
   const action=url.split('/').at(-1),result=await rpc({action,body:JSON.parse(init.body)});
   if(action==='save'&&loseSave){assert(result.ok);loseSave=false;return new Promise(()=>{});}
   if(action==='attach'&&loseAttach){assert(result.ok);loseAttach=false;return new Promise(()=>{});}
   return {ok:result.ok,status:result.status,json:async()=>result.data};
  }});
  const edit=text=>{const el=t.host.querySelector('[data-missing-reason="mp"]');el.value=text;el.oninput();};
  const until=async predicate=>{for(let i=0;i<200&&!predicate();i++)await new Promise(r=>setTimeout(r,10));assert(predicate(),'Expected actual domain commit');};
  edit('Before uncertain save');const first=t.select({name:'synthetic-receipt.jpg',type:'image/jpeg',size:config.original_bytes});await until(()=>!loseSave);await t.expire();await first;
  assert(t.ui.pending);assert.equal((await rpc({action:'view'})).data.version,initialVersion+1);assert.equal(t.model.current.version,initialVersion);
  edit('After uncertain save');await t.ui.save();assert.equal(t.model.current.version,initialVersion+2);assert.equal(t.ui.current.missing_evidence_reasons.mp,'After uncertain save');
  const second=t.click('retry-photo');await until(()=>!loseAttach);await t.expire();await second;assert(t.ui.pending);assert.equal((await rpc({action:'stats'})).data.cl_attachments,1);
  edit('Latest after uncertain attachment');await t.ui.save();await t.click('retry-photo');await t.ui.save();await t.ui.save();
  const saves=t.requests.filter(r=>r.url.endsWith('/save')),attaches=t.requests.filter(r=>r.url.endsWith('/attach'));
  assert.deepEqual(saves[0].body,saves[1].body);assert.deepEqual(attaches[0].body,attaches[1].body);assert.equal(attaches.length,2);
  const reopened=(await rpc({action:'view'})).data;assert.equal(reopened.version,initialVersion+4);assert.equal(reopened.photos.length,1);assert.equal(reopened.photos[0].sha256,await sha(blob));assert.equal(reopened.photos[0].legible,false);
  assert.equal(reopened.missing_evidence_reasons.mp,'Latest after uncertain attachment');assert.deepEqual(reopened.cash,config.value.cash);assert.deepEqual(reopened.retained,config.value.retained);assert.equal(reopened.bagNumber,config.value.bagNumber);assert(!t.ui.pending);
  const stats=(await rpc({action:'stats'})).data;assert.deepEqual(stats,{cl_attachments:1,cv2_images:1,cc_cash_events:0});
  console.log(JSON.stringify({passed:9,checks:['committed save response lost before photo upload','Guardar ahora replays original save before later edit','committed attachment response also lost','later autosave followed by exact attachment replay','four actual changed declarations advance exactly four versions','repeated unchanged save adds no version','one durable attachment with unconfirmed legibility','latest reason and original cash/retained/bag survive reopen','no cash events or login credentials created'],initialVersion,finalVersion:reopened.version,stats,limits:['Real domain and image storage in isolated temporary database; synthetic DOM/camera callbacks, no physical device or live service writes.']}));
 }finally{clearTimeout(startup);rl.close();backend.stdin.end();await new Promise(resolve=>{backend.once('exit',resolve);setTimeout(()=>{backend.kill();resolve();},3000).unref();});}
})().catch(e=>{console.error(e);process.exitCode=1;});
