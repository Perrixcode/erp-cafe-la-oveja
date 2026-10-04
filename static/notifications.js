/* Seguimiento local de avisos: ver un aviso nunca cambia el estado del pedido. */
(function(root) {
  'use strict';
  function advance(previous, eventIds) {
    const ids = eventIds.filter(id => Number.isSafeInteger(id) && id > 0);
    const valid = Number.isSafeInteger(previous) && previous >= 0;
    const maximum = Math.max(valid ? previous : 0, ...ids);
    return {maximum, fresh:valid ? ids.filter(id => id > previous) : []};
  }
  class NoticeTracker {
    constructor(storage, key) { this.storage=storage; this.key=key; this.memory=null; }
    consume(notices) {
      let previous=this.memory;
      try { const raw=this.storage.getItem(this.key); if(raw!==null && /^\d+$/.test(raw)) previous=Number(raw); } catch {}
      const result=advance(previous,notices.map(n=>n.event_id));
      this.memory=result.maximum;
      try { this.storage.setItem(this.key,String(result.maximum)); } catch {}
      return result.fresh;
    }
  }
  const api={advance,NoticeTracker};
  if(typeof module==='object' && module.exports) module.exports=api;
  else root.OvejaNotifications=api;
})(typeof globalThis!=='undefined'?globalThis:this);
