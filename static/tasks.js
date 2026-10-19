'use strict';
window.OvejaTasks=(()=>{
  let epoch=0,contexts={},edit=null;
  if(['127.0.0.1','localhost'].includes(location.hostname)&&initialView.get('local_demo')==='1')$('#access-setup code').textContent='python3 private/release/scripts/start_task_demo.py --create-user';
  const labels={kitchen:'Cocina',floor:'Salón'};
  const e=escapeHTML;
  const host=document.createElement('div');host.id='tasks-workspace';host.className='hidden';
  $('#module-placeholder').append(host);
  const area=()=>state.moduleSection==='Salón'?'floor':'kitchen';
  const allowed=()=>state.user?.role==='partner';
  const opt=(values,value)=>Object.entries(values).map(([k,v])=>`<option value="${e(k)}" ${k===value?'selected':''}>${e(v)}</option>`).join('');
  const input=(label,name,value='',extra='')=>`<label class="field">${label}<input name="${name}" value="${e(value)}" ${extra}></label>`;
  const instructions=(value='')=>`<label class="field">Instrucciones para esta jornada<textarea name="instructions" maxlength="1000" rows="2">${e(value)}</textarea></label>`;
  const sorted=rows=>[...rows].sort((a,b)=>a.order-b.order||a.id.localeCompare(b.id));
  function reset(){epoch++;contexts={};edit=null;host.replaceChildren();host.classList.add('hidden');}
  function leave(){host.classList.add('hidden');}
  function enter(){
    host.classList.remove('hidden');
    if(!allowed()){host.innerHTML='<p class="empty compact">El asignador está disponible exclusivamente para socios y socias.</p>';return;}
    const key=area();
    if(!contexts[key]){
      const pane=document.createElement('div');pane.className='tasks-pane';pane.dataset.tasksArea=key;host.append(pane);
      contexts[key]={area:key,date:(/^\d{4}-\d{2}-\d{2}$/.test(initialView.get('task_date')||'')?initialView.get('task_date'):state.board?.today)||new Date().toLocaleDateString('en-CA'),pane,data:null,busy:false,request:0};
      load(contexts[key]);
    }
    for(const [k,c] of Object.entries(contexts))c.pane.classList.toggle('hidden',k!==key);
  }
  function drafts(c,exclude){
    return [...c.pane.querySelectorAll('form[data-dirty]')].filter(f=>f.id!==exclude).map(f=>({id:f.id,version:f.dataset.version,fields:[...f.querySelectorAll('input,select,textarea')].map(el=>({key:el.name,row:el.closest('[data-row]')?.dataset.row,value:el.value,checked:el.checked}))}));
  }
  function restore(c,saved){
    for(const draft of saved){const f=c.pane.querySelector('#'+draft.id);if(!f)continue;f.dataset.dirty='1';if(draft.version)f.dataset.version=draft.version;for(const el of f.querySelectorAll('input,select,textarea')){const old=draft.fields.find(v=>v.key===el.name&&v.row===el.closest('[data-row]')?.dataset.row&&(el.type!=='checkbox'||v.value===el.value));if(old){if(el.type==='checkbox')el.checked=old.checked;else el.value=old.value;}}}
    syncComposer(c);markDirty(c);
  }
  function markDirty(c){const n=c.pane.querySelectorAll('form[data-dirty]').length;c.pane.querySelector('[data-tasks-dirty]').textContent=n?'Hay cambios sin guardar. La impresión usa la última lista guardada.':'';}
  async function load(c,exclude){
    const token=epoch,request=++c.request,saved=drafts(c,exclude);c.busy=true;
    const progress=window.OvejaLoading.begin(c.pane,{label:'Cargando asignador…',blocking:!c.data});
    try{const data=await api(`/api/tasks?area=${c.area}&date=${c.date}`);if(token!==epoch||request!==c.request||!allowed())return;c.data=data;render(c);restore(c,saved);if(state.module==='tasks'&&area()===c.area)rememberView();}
    catch(error){if(token===epoch&&request===c.request)progress.fail(error.message,()=>load(c,exclude));}
    finally{progress.done();if(token===epoch&&request===c.request)c.busy=false;}
  }
  function rowFields(c,r){return `<div class="tasks-fields">${input('Cantidad','quantity',r.quantity||'1','inputmode="decimal" required maxlength="10"')}${input('Unidad','unit',r.unit,'required maxlength="30" placeholder="kg, porciones, unidades, lotes"')}<label class="field">Prioridad<select name="priority">${opt(c.data.priorities,r.priority||'normal')}</select></label>${input('Orden','order',r.order||1,'type="number" min="1" max="9999" required')}</div>${instructions(r.instructions)}`;}
  function catalog(c){return `<details class="tasks-card"><summary>Catálogo reutilizable · ${c.data.catalog.length} tareas</summary><p class="tasks-hint">Carga manual por categoría. Editar aquí no cambia las listas ya guardadas.</p><button class="button secondary" data-tasks-new="catalog">Crear tarea</button>${c.data.catalog.map(t=>`<div class="tasks-catalog-row"><div><strong>${e(t.title)}</strong><small>${e(t.category)} · ${e(t.unit)}</small></div><button class="text-button" data-tasks-edit="catalog" data-id="${t.id}">Editar ${e(t.title)}</button></div>`).join('')||'<p>Aún no hay tareas. Crea la primera para comenzar.</p>'}</details>`;}
  function workers(c){return `<details class="tasks-card"><summary>Trabajadores destinatarios · ${c.data.workers.length}</summary><p class="tasks-hint">Solo reciben pautas. Este registro no les crea una cuenta ni acceso al ERP.</p><button class="button secondary" data-tasks-new="workers">Agregar trabajador</button>${c.data.workers.map(w=>`<div class="tasks-catalog-row"><strong>${e(w.name)}</strong><button class="text-button" data-tasks-edit="workers" data-id="${w.id}">Editar ${e(w.name)}</button></div>`).join('')||'<p>Agrega las personas de esta área.</p>'}</details>`;}
  function composer(c){
    if(!c.data.catalog.length||!c.data.workers.length)return '<section class="tasks-card"><h2>Preparar una lista</h2><p>Primero agrega tareas al catálogo y trabajadores de esta área usando los paneles de arriba.</p></section>';
    const categories=Object.fromEntries([...new Set(c.data.catalog.map(t=>t.category))].map(t=>[t,t]));
    return `<details class="tasks-card" open><summary>Preparar / agregar tareas a las listas del ${e(c.date)}</summary><form id="tasks-${c.area}-assign" data-tasks-form="assign"><p class="tasks-hint">Selecciona una o más personas. Cada una recibirá las tareas y cantidades indicadas; no se dividen entre ellas.</p><fieldset><legend>Destinatarios</legend><div class="tasks-checks">${c.data.workers.map(w=>`<label><input type="checkbox" name="worker" value="${w.id}">${e(w.name)}</label>`).join('')}</div></fieldset><div class="tasks-toolbar"><label class="field">Filtrar categoría<select name="category" data-tasks-category><option value="">Todas</option>${opt(categories,'')}</select></label></div><p class="tasks-hint">Las tareas seleccionadas permanecen visibles al filtrar categorías. Cantidades definidas fuera del ERP; no se convierten unidades ni se descuenta stock.</p>${c.data.catalog.map((t,i)=>`<article class="tasks-item" data-row="${t.id}" data-category="${e(t.category)}"><label class="task-pick"><input type="checkbox" name="task" value="${t.id}"><strong>${e(t.title)}</strong></label><small>${e(t.category)}</small><p>${e(t.description)}</p>${`<div data-task-fields class="hidden"><fieldset class="tasks-fieldset" disabled>${rowFields(c,{unit:t.unit,order:i+1})}</fieldset></div>`}</article>`).join('')}<button type="submit" class="button primary">Asignar tareas seleccionadas</button></form></details>`;
  }
  function listCard(c,list){return `<section class="tasks-card" data-list-worker="${list.worker_id}"><h2>${e(list.worker_name)}</h2><p class="tasks-status">${e(list.work_date)} · ${list.items.length} tareas · ${list.items.filter(i=>i.status==='done').length} completadas</p><form id="tasks-list-${list.id}" data-tasks-form="list" data-id="${list.id}" data-version="${list.version}">${sorted(list.items).map(r=>`<article class="tasks-item" data-row="${r.id}"><h3>${r.order}. ${e(r.title)}</h3><small>${e(r.category)}</small><p>${e(r.description)}</p>${rowFields(c,r)}<label class="field">Estado · solo socios<select name="status">${opt(c.data.statuses,r.status)}</select></label></article>`).join('')}<div class="tasks-actions"><button type="submit" class="button primary">Guardar lista de ${e(list.worker_name)}</button><a class="button secondary" target="_blank" rel="noopener noreferrer" href="/api/tasks/lists/${list.id}/print">Imprimir / PDF de ${e(list.worker_name)}</a></div><p class="tasks-hint">Imprime la última versión guardada. Último cambio: ${e(list.actor)} · ${e(new Date(list.updated_at).toLocaleString('es-CL'))}</p></form><details><summary>Copiar esta lista a otra fecha</summary><form id="tasks-copy-${list.id}" data-tasks-form="copy" data-id="${list.id}"><div class="tasks-toolbar">${input('Nueva fecha','date','','type="date" required')}<label class="field">Destinatario de la copia<select name="worker_id">${c.data.workers.map(w=>`<option value="${w.id}" ${w.id===list.worker_id?'selected':''}>${e(w.name)}</option>`).join('')}</select></label><button class="button secondary" type="submit">Copiar lista</button></div><p class="tasks-hint">Copia la versión guardada. Todas las tareas empiezan pendientes; no reemplaza listas existentes.</p></form></details></section>`;}
  function render(c){
    c.pane.innerHTML=`<form id="tasks-${c.area}-filter" data-tasks-form="filter" class="tasks-toolbar">${input('Fecha de trabajo','date',c.date,'type="date" required')}<button class="button secondary" type="submit">Ver fecha</button><label class="field">Fechas con listas<select data-tasks-history><option value="">Elegir fecha guardada</option>${c.data.dates.map(d=>`<option value="${d}">${d}</option>`).join('')}</select></label><button type="button" class="text-button" data-tasks-refresh>Actualizar</button></form><p class="tasks-hint" role="status" data-tasks-dirty></p><div class="tasks-grid">${catalog(c)}${workers(c)}</div>${composer(c)}<div class="tasks-toolbar"><h2>Listas guardadas · ${e(c.date)}</h2><label class="field">Ver trabajador<select data-tasks-worker><option value="">Todos</option>${c.data.workers.map(w=>`<option value="${w.id}">${e(w.name)}</option>`).join('')}</select></label></div>${c.data.lists.map(l=>listCard(c,l)).join('')||'<p class="tasks-card">Sin listas guardadas para esta fecha.</p>'}`;
  }
  function editEntity(c,kind,id){
    const record=id?c.data[kind].find(r=>r.id===id):null;
    edit={c,kind,record};
    const fields=kind==='workers'?input('Nombre del trabajador','name',record?.name||'','required maxlength="100"'):`${input('Nombre de la tarea','title',record?.title||'','required maxlength="160"')}${input('Categoría','category',record?.category||'','required maxlength="80" placeholder="Ej.: Preparación, Apertura, Limpieza"')}${input('Unidad habitual','unit',record?.unit||'unidades','required maxlength="30"')}<label class="field">Descripción general<textarea name="description" rows="3" maxlength="1000">${e(record?.description||'')}</textarea></label>`;
    modal(record?'Editar registro':kind==='workers'?'Agregar trabajador':'Crear tarea',labels[c.area],`<form id="tasks-edit" class="tasks-pane"><div id="form-error" class="error hidden" role="alert"></div><div class="tasks-grid">${fields}</div><div class="tasks-actions"><button class="button primary" type="submit">Guardar ${kind==='workers'?'trabajador':'tarea'}</button></div></form>`,'','ASIGNADOR · SOLO SOCIOS');
  }
  function rowData(row){return Object.fromEntries([...row.querySelectorAll('input,select,textarea')].filter(el=>el.name!=='task').map(el=>[el.name,el.name==='order'?Number(el.value):el.value]));}
  const requests=new WeakMap();
  async function save(c,form,path,method,payload,message){
    if(c.busy)return;const token=epoch; c.busy=true;state.saving=true;
    const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
    const signature=JSON.stringify([path,payload]);let attempt=requests.get(form);
    if(attempt?.signature!==signature){attempt={signature,id:crypto.randomUUID()};requests.set(form,attempt);}
    try{await api(path,method,{...payload,request_id:attempt.id});if(token!==epoch||!allowed())return;
      if(form.id==='tasks-edit'){$('#modal').close();edit=null;}
      form.removeAttribute('data-dirty');requests.delete(form);toast(message);await load(c,form.id);
    }catch(error){if(token===epoch){if(form.id==='tasks-edit')showFormError(error.message);else toast(error.message);}}
    finally{if(token===epoch){c.busy=false;state.saving=false;buttons.forEach(b=>b.disabled=false);}}
  }
  function canReload(c){return !c.pane.querySelector('form[data-dirty]')||window.confirm('Hay cambios sin guardar. ¿Descartarlos y cargar la fecha?');}
  document.addEventListener('submit',event=>{
    const form=event.target;if(form.id!=='tasks-edit'&&!form.dataset.tasksForm)return;
    event.preventDefault();if(!allowed())return;
    const c=form.id==='tasks-edit'?edit?.c:contexts[form.closest('[data-tasks-area]').dataset.tasksArea];if(!c||c.busy)return;
    const body=Object.fromEntries(new FormData(form));
    if(form.id==='tasks-edit'){const {kind,record}=edit;save(c,form,`/api/tasks/${kind}${record?'/'+record.id:''}`,record?'PUT':'POST',{...body,area:c.area,...(record?{version:record.version}:{})},'Registro guardado.');return;}
    const kind=form.dataset.tasksForm,list=c.data.lists.find(l=>l.id===Number(form.dataset.id));
    if(kind==='filter'){if(canReload(c)){c.date=body.date;c.pane.querySelectorAll('form[data-dirty]').forEach(f=>f.removeAttribute('data-dirty'));load(c);}return;}
    if(kind==='assign'){
      const worker_ids=new FormData(form).getAll('worker').map(Number),items=[...form.querySelectorAll('[data-row]')].filter(r=>$('[name=task]',r).checked).map(r=>{const task=c.data.catalog.find(t=>t.id===Number(r.dataset.row));return {...rowData(r),task_id:task.id,version:task.version};});
      if(!worker_ids.length||!items.length){toast('Selecciona al menos una persona y una tarea.');return;}
      save(c,form,'/api/tasks/assign','POST',{area:c.area,date:c.date,worker_ids,items,versions:Object.fromEntries(worker_ids.map(id=>[id,c.data.lists.find(l=>l.worker_id===id)?.version||0]))},'Tareas asignadas.');
    }else if(kind==='list')save(c,form,`/api/tasks/lists/${list.id}`,'PUT',{version:Number(form.dataset.version),items:[...form.querySelectorAll('[data-row]')].map(r=>({...rowData(r),id:r.dataset.row}))},'Lista guardada.');
    else if(kind==='copy')save(c,form,`/api/tasks/lists/${list.id}/copy`,'POST',{version:list.version,date:body.date,worker_id:Number(body.worker_id)},`Copia guardada para ${body.date}. Todas las tareas pendientes.`);
  });
  host.addEventListener('input',event=>{const c=contexts[event.target.closest('[data-tasks-area]')?.dataset.tasksArea],form=event.target.closest('form');if(c&&form&&['assign','list'].includes(form.dataset.tasksForm)&&event.target.name!=='category'){form.dataset.dirty='1';markDirty(c);}});
  host.addEventListener('change',event=>{
    const el=event.target,c=contexts[el.closest('[data-tasks-area]')?.dataset.tasksArea];if(!c)return;
    if(el.hasAttribute('data-tasks-category')||el.name==='task')syncComposer(c);
    if(el.hasAttribute('data-tasks-worker'))c.pane.querySelectorAll('[data-list-worker]').forEach(r=>r.classList.toggle('hidden',Boolean(el.value)&&r.dataset.listWorker!==el.value));
    if(el.hasAttribute('data-tasks-history')&&el.value&&!c.busy&&canReload(c)){c.date=el.value;c.pane.querySelectorAll('form[data-dirty]').forEach(f=>f.removeAttribute('data-dirty'));load(c);}
  });
  host.addEventListener('click',event=>{
    if(!allowed())return;const button=event.target.closest('button'),c=contexts[button?.closest('[data-tasks-area]')?.dataset.tasksArea];if(!c||c.busy)return;
    if(button.hasAttribute('data-tasks-new'))editEntity(c,button.dataset.tasksNew);
    if(button.hasAttribute('data-tasks-edit'))editEntity(c,button.dataset.tasksEdit,Number(button.dataset.id));
    if(button.hasAttribute('data-tasks-refresh')&&canReload(c)){c.pane.querySelectorAll('form[data-dirty]').forEach(f=>f.removeAttribute('data-dirty'));load(c);}
  });
  function syncComposer(c){
    const form=c.pane.querySelector('[data-tasks-form="assign"]');if(!form)return;
    const category=form.elements.category.value;
    for(const row of form.querySelectorAll('[data-row]')){
      const checked=row.querySelector('[name=task]').checked;
      row.querySelector('[data-task-fields]').classList.toggle('hidden',!checked);
      row.querySelector('fieldset').disabled=!checked;
      row.classList.toggle('hidden',!checked&&Boolean(category)&&row.dataset.category!==category);
    }
  }
  return {enter,leave,reset,get date(){return contexts[area()]?.date;}};
})();
