'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function api(){
  const host={setAttribute(){},addEventListener(){},classList:{add(){},remove(){}},replaceChildren(){}};
  const sandbox={window:{},document:{createElement:()=>host},$:()=>({append(){}}),escapeHTML:String,
    URLSearchParams,location:{search:''},Intl,Date,console,Map};
  vm.runInNewContext(fs.readFileSync(__dirname+'/../static/cash-control.js','utf8'),sandbox);
  return sandbox.window.OvejaCashControl;
}
test('Chile cut-off preserves local day independent of browser timezone',()=>{
  const control=api();assert.equal(control.chileInstant('2026-10-16T00:00'),'2026-10-16T03:00:00.000Z');
  assert.equal(control.chileInstant('2026-10-16T23:59'),'2026-10-17T02:59:00.000Z');
});
test('Chile winter offset is derived instead of hard-coded',()=>{
  assert.equal(api().chileInstant('2026-07-16T08:00'),'2026-07-16T12:00:00.000Z');
});
test('nonexistent local time at Chile spring transition is rejected',()=>{
  assert.throws(()=>api().chileInstant('2026-09-06T00:30'),/hora no existe/);
});
