'use strict';
const assert=require('node:assert/strict');
const {setup,Element}=require('./test_cashier_photo_ui.cjs');
Element.prototype.focus=function(){this.focused=true;};
Element.prototype.scrollIntoView=function(){this.scrolled=true;};
(async()=>{
 const checks=[];
 for(const status of [502,413,401]){
  const t=setup({http:async()=>({ok:false,status,json:async()=>{throw new SyntaxError('empty proxy response');}})});
  await t.select();assert(t.ui.pending);assert.equal(t.ui.current.photos.length,0);
  const alert=t.host.querySelector('[data-photo-error]');assert(alert.focused&&alert.scrolled);assert(!('hidden' in alert.attrs));
  assert.match(t.host.innerHTML,status===502?/HTTP 502/:status===413?/límite de transferencia/:/sesión venció/);
  const reason=t.host.querySelector('[data-missing-reason="mp"]');reason.value='Nueva edición';reason.oninput();t.ui.render();
  assert(!('hidden' in t.host.querySelector('[data-photo-error]').attrs));assert.equal(t.ui.current.missing_evidence_reasons.mp,'Nueva edición');
  await t.click('retry-photo');assert(t.host.querySelector('[data-photo-error]').focused);
  checks.push('non-JSON '+status+' stays visible, focused and unconfirmed after editing and retry');
 }
 console.log(JSON.stringify({passed:checks.length,checks,limits:['Synthetic DOM; focus/scroll calls verified, no physical viewport inspected.']}));
})().catch(e=>{console.error(e);process.exitCode=1;});
