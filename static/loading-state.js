'use strict';
/* Shared visual state only. Requests, permissions and retries stay with callers. */
(function(root){
  function browserView(target,options){
    const node=document.createElement('div');
    node.className='load-state '+(options.blocking?'load-state--blocking':'load-state--background');
    const content=document.createElement('div');content.className='load-state-content';
    const spinner=document.createElement('span');spinner.className=options.blocking?'load-coffee':'load-spinner';spinner.setAttribute('aria-hidden','true');
    const text=document.createElement('span');text.textContent=options.label;
    content.append(spinner,text);node.append(content);node.setAttribute('role','status');node.setAttribute('aria-live','polite');node.setAttribute('aria-atomic','true');
    return {
      start(){target.classList.add('load-area');if(options.blocking)target.classList.add('load-area--blocking');target.setAttribute('aria-busy','true');},
      show(){if(target.isConnected)target.append(node);},
      error(message,retry){
        target.removeAttribute('aria-busy');target.classList.remove('load-area--blocking');
        node.className='load-state load-state--error'+(options.blocking?' load-state--empty-error':'');
        node.setAttribute('role','alert');content.replaceChildren();
        const title=document.createElement('strong');title.textContent='No se pudo cargar la información';
        const detail=document.createElement('span');detail.textContent=message;
        const button=document.createElement('button');button.type='button';button.className='button secondary';button.textContent='Reintentar';button.addEventListener('click',retry,{once:true});
        content.append(title,detail,button);target.prepend(node);
      },
      remove(){node.remove();target.removeAttribute('aria-busy');target.classList.remove('load-area--blocking');}
    };
  }
  function create({setTimer=setTimeout,clearTimer=clearTimeout,now=Date.now,view=browserView}={}){
    const active=new Map();
    function begin(target,{label='Cargando información…',blocking=true,delay=180,minimum=220}={}){
      if(!target)return {done(){},cancel(){},fail(){}};
      active.get(target)?.cancel();
      const ui=view(target,{label,blocking});let timer=null,endTimer=null,shownAt=null,phase='pending';
      function clear(){clearTimer(timer);clearTimer(endTimer);}
      function remove(){clear();if(active.get(target)!==handle)return;active.delete(target);ui.remove();phase='closed';}
      const handle={
        cancel:remove,
        done(){
          if(phase==='error'||phase==='closed'||phase==='finishing')return;
          clearTimer(timer);phase='finishing';
          const remaining=shownAt===null?0:Math.max(0,minimum-(now()-shownAt));
          if(remaining){ui.show();endTimer=setTimer(remove,remaining);}else remove();
        },
        fail(message,retry){
          if(active.get(target)!==handle||phase==='closed')return;
          clear();phase='error';
          ui.error(message,()=>{remove();Promise.resolve().then(retry).catch(()=>{});});
        }
      };
      active.set(target,handle);ui.start();
      timer=setTimer(()=>{if(active.get(target)!==handle||phase!=='pending')return;shownAt=now();ui.show();},delay);
      return handle;
    }
    return {begin,cancelAll(){for(const handle of [...active.values()])handle.cancel();}};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={create};
  else root.OvejaLoading=create();
})(typeof window==='undefined'?{}:window);
