'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../static/app.js'),'utf8');
function harness(fetch){const context={fetch,authGeneration:1,lockAccess:()=>{},Number,Error,JSON};vm.createContext(context);vm.runInContext(source.slice(source.indexOf('async function responsePayload('),source.indexOf('async function api(')),context);return context;}
const response=(status,body)=>({status,ok:status>=200&&status<300,text:async()=>body});
const file={name:'ficticio.png',size:697794,type:'image/png'};
test('empty proxy failure and HTML response give actionable errors, not JSON exceptions',async()=>{
  const h=harness();for(const body of ['', '<html>gateway</html>'])await assert.rejects(h.responsePayload(response(502,body),true),/No se pudo confirmar la carga.*no se duplica/);
});
test('oversize and expired session remain meaningful without JSON',async()=>{
  const h=harness();await assert.rejects(h.responsePayload(response(413,''),true),/máximo es 8 MB/);
  await assert.rejects(h.responsePayload(response(401,'')),/sesión terminó/);
  await assert.rejects(h.responsePayload(response(401,'{"error":"Usuario o contraseña incorrectos."}')),/contraseña incorrectos/);
});
test('valid domain errors are retained and success requires a confirmed identifier',async()=>{
  const h=harness(async()=>response(201,'{}'));await assert.rejects(h.responsePayload(response(409,'{"error":"El registro cambió."}')),/registro cambió/);
  await assert.rejects(h.supplierFileUpload('/api/suppliers/invoices/1/files',file),/no confirmó el archivo/);
});
test('interrupted upload does not retry automatically or discard the selected file',async()=>{
  let calls=0,sent;const h=harness(async(path,options)=>{calls++;sent=options.body;throw Error('network');});
  await assert.rejects(h.supplierFileUpload('/api/suppliers/invoices/1/files',file),/Conserva el mismo archivo/);
  assert.equal(calls,1);assert.equal(sent,file);assert.equal(file.size,697794);
});
test('manual retry sends identical bytes and accepts server deduplication',async()=>{
  const sent=[];let attempts=0;const h=harness(async(path,options)=>{sent.push(options.body);return ++attempts===1?response(502,''):response(201,'{"id":7,"already_attached":true}');});
  await assert.rejects(h.supplierFileUpload('/api/suppliers/invoices/1/files',file),/no se duplica/);
  const result=await h.supplierFileUpload('/api/suppliers/invoices/1/files',file);assert.equal(result.id,7);assert.equal(result.already_attached,true);assert.equal(sent[0],sent[1]);
});
test('session change during response reading rejects a late success',async()=>{
  const h=harness(async()=>({status:201,ok:true,text:async()=>{h.authGeneration=2;return '{"id":7}';}}));
  await assert.rejects(h.supplierFileUpload('/api/suppliers/invoices/1/files',file),/sesión cambió/);
});
