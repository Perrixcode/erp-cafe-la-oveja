'use strict';
const test=require('node:test');const assert=require('node:assert/strict');
const {create}=require('../static/loading-state.js');
function harness(){
  let time=0,next=0;const timers=new Map(),views=[];
  const manager=create({now:()=>time,setTimer:(fn,delay)=>{timers.set(++next,{fn,at:time+delay});return next;},clearTimer:id=>timers.delete(id),view:(key,options)=>{const v={key,options,shown:false,removed:false,error:null,events:[],start(){this.events.push('start');},show(){this.shown=true;this.events.push('show');},remove(){this.removed=true;this.events.push('remove');},error(message,retry){this.failure=message;this.retry=retry;this.events.push('error');}};views.push(v);return v;}});
  function tick(delta){const end=time+delta;for(;;){const task=[...timers.entries()].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!task)break;timers.delete(task[0]);time=task[1].at;task[1].fn();}time=end;}
  return {manager,views,tick,timers};
}
test('fast reads never show a spinner',()=>{const h=harness(),r=h.manager.begin('list');h.tick(100);r.done();h.tick(1000);assert.equal(h.views[0].shown,false);assert.equal(h.timers.size,0);});
test('visible indicator stays for minimum display time',()=>{const h=harness(),r=h.manager.begin('list');h.tick(180);assert.equal(h.views[0].shown,true);h.tick(20);r.done();h.tick(199);assert.equal(h.views[0].removed,false);h.tick(1);assert.equal(h.views[0].removed,true);});
test('a superseded response cannot dismiss the current indicator',()=>{const h=harness(),first=h.manager.begin('list');h.tick(200);const second=h.manager.begin('list');first.done();h.tick(200);assert.equal(h.views[1].removed,false);first.fail('late error',()=>{});assert.equal(h.views[0].failure,undefined);second.done();h.tick(220);assert.equal(h.views[1].removed,true);});
test('navigation cancellation clears pending and visible timers',()=>{const h=harness();h.manager.begin('list');h.tick(200);h.manager.begin('detail');h.manager.cancelAll();h.tick(2000);assert.equal(h.timers.size,0);assert.ok(h.views.every(v=>v.removed));assert.equal(h.views[1].shown,false);});
test('background refresh explicitly uses nonblocking mode',()=>{const h=harness(),r=h.manager.begin('list',{blocking:false});h.tick(200);assert.equal(h.views[0].options.blocking,false);r.done();h.tick(250);assert.equal(h.views[0].removed,true);});
test('error survives finally and retry is explicit and runs once',async()=>{const h=harness();let count=0;const r=h.manager.begin('list');r.fail('Sin conexión',()=>{count++;});r.done();h.tick(1000);assert.equal(h.views[0].removed,false);assert.equal(count,0);h.views[0].retry();await Promise.resolve();assert.equal(count,1);assert.equal(h.views[0].removed,true);});
test('independent regions do not dismiss one another',()=>{const h=harness(),one=h.manager.begin('one'),two=h.manager.begin('two');one.done();h.tick(200);assert.equal(h.views[1].shown,true);assert.equal(h.views[1].removed,false);two.cancel();});
test('a late failure from an old route cannot reappear',()=>{const h=harness(),r=h.manager.begin('list');h.manager.cancelAll();r.fail('outdated',()=>{});r.done();h.tick(2000);assert.equal(h.views[0].failure,undefined);assert.equal(h.views[0].shown,false);});
