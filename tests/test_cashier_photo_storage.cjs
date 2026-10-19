'use strict';
// Exact client upload adapter + real Python image validation/storage, via stdio.
// No network, browser, credentials or production records.
const path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process'),readline=require('node:readline');
const {setup,sha}=require('./test_cashier_photo_ui.cjs');
(async()=>{
 const backend=spawn(process.env.ERP_TEST_PYTHON||'python3',['-B',path.join(__dirname,'cashier_photo_fixture.py')],{cwd:path.join(__dirname,'..'),env:{...process.env,PYTHONPATH:'.:tests',PYTHONDONTWRITEBYTECODE:'1'},stdio:['pipe','pipe','pipe']});
 let errors='';backend.stderr.on('data',b=>errors+=b);const rl=readline.createInterface({input:backend.stdout});const responses=[],waiting=[];
 rl.on('line',line=>{const data=JSON.parse(line);if(waiting.length)waiting.shift()(data);else responses.push(data);});
 const receive=()=>responses.length?Promise.resolve(responses.shift()):new Promise(resolve=>waiting.push(resolve));
 const rpc=async request=>{backend.stdin.write(JSON.stringify(request)+'\n');return receive();};
 const startup=setTimeout(()=>backend.kill(),15000);
 try{
  const config=await Promise.race([receive(),new Promise((_,reject)=>backend.once('exit',code=>reject(Error('Fixture exit '+code+': '+errors))))]);clearTimeout(startup);
  const blob=new Blob([Buffer.from(config.image,'base64')],{type:'image/jpeg'});let loseReply=true;
  const t=setup({value:config.value,blob,denominations:Object.keys(config.value.cash).map(Number),http:async(url,init)=>{
   const action=url.split('/').at(-1),result=await rpc({action,body:JSON.parse(init.body)});
   if(action==='attach'&&loseReply){loseReply=false;assert(result.ok);return new Promise(()=>{});}
   return {ok:result.ok,status:result.status,json:async()=>result.data};
  }});
  let finished=false;const upload=t.select({name:'synthetic-receipt.jpg',type:'image/jpeg',size:config.original_bytes}).then(()=>finished=true);
  // Wait for the actual storage commit; its successful response is deliberately lost.
  for(let i=0;i<100&&loseReply;i++)await new Promise(r=>setTimeout(r,20));assert(!loseReply);
  assert.equal((await rpc({action:'stats'})).data.cl_attachments,1);assert.equal(t.ui.current.photos.length,0);assert(!finished);
  await t.expire();await upload;assert(t.ui.pending);assert.equal(t.ui.current.photos.length,0);
  const input=t.host.querySelector('[data-missing-reason="mp"]');input.value='Comentario conservado durante recuperación';input.oninput();await t.ui.save();
  const advanced=t.model.current.version;assert(advanced>config.value.version);
  await t.click('retry-photo');assert.equal(t.ui.current.photos.length,1);assert.equal(t.ui.current.photos[0].sha256,await sha(blob));assert.equal(t.ui.current.photos[0].legible,false);
  const attaches=t.requests.filter(x=>x.url.endsWith('/attach'));assert.deepEqual(attaches[0].body,attaches[1].body);assert(attaches[1].body.version<advanced,'Retry recovers original committed operation despite later draft version');
  const legible=t.host.querySelector('[data-legible]');legible.checked=true;legible.onchange();await t.ui.save();
  const reopened=(await rpc({action:'view'})).data;assert.equal(reopened.photos.length,1);assert.equal(reopened.photos[0].sha256,await sha(blob));assert.equal(reopened.photos[0].legible,true);assert.deepEqual(reopened.cash,config.value.cash);assert.deepEqual(reopened.retained,config.value.retained);assert.equal(reopened.bagNumber,config.value.bagNumber);assert.equal(reopened.missing_evidence_reasons.mp,'Comentario conservado durante recuperación');
  const stats=(await rpc({action:'stats'})).data;assert.deepEqual(stats,{cl_attachments:1,cv2_images:1,cc_cash_events:0});
  console.log(JSON.stringify({passed:8,checks:['real synthetic 12 MP receipt raster','real server image decoding and SHA-256','committed attachment with lost response remains unconfirmed in UI','explicit retry reuses exact id/version/body after newer autosave','one durable attachment after retry','legibility requires human confirmation','reopened draft preserves cash, retained amount, bag and note','no cash events or credentials created'],image:{originalBytes:config.original_bytes,compressedBytes:config.compressed_bytes,dimensions:config.dimensions},limits:['Image/canvas callbacks simulated; real generated JPEG bytes validated by Pillow, no browser or physical camera.']}));
 }finally{clearTimeout(startup);rl.close();backend.stdin.end();await new Promise(resolve=>{backend.once('exit',resolve);setTimeout(()=>{backend.kill();resolve();},3000).unref();});}
})().catch(e=>{console.error(e);process.exitCode=1;});
