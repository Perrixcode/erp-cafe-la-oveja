'use strict';
window.OvejaDocuments=(()=>{
  const host=$('#documents-workspace');
  const dialog=document.createElement('dialog');dialog.className='dc-dialog';dialog.setAttribute('aria-label','Gestión de documentos');document.body.append(dialog);
  const statusName={draft:'Borrador',active:'Vigente',archived:'Archivado'};
  const kindName={document:'Documento',checklist_template:'Plantilla de checklist',checklist:'Checklist'};
  const base='/api/document-center';
  const xlsxMime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  let loaded=false,lastSection='',generation=0,sections=[],manager=false,selected=null,query='',filter='',page=1,uploadURL=null,busy=false;
  const e=escapeHTML;
  const post=(path,body)=>api(base+path,'POST',body);
  const requestId=()=>crypto.randomUUID();
  const fileURL=(doc,download=false)=>`${base}/documents/${encodeURIComponent(doc.id)}/file?version=${doc.version}${download?'&download=1':''}`;
  function releaseURL(){if(uploadURL){URL.revokeObjectURL(uploadURL);uploadURL=null;}}
  function close(){if(busy)return;dialog.close();dialog.replaceChildren();releaseURL();selected=null;}
  function leave(){loaded=false;generation++;host.classList.add('hidden');if(!busy)close();}
  function reset(){busy=false;close();leave();host.replaceChildren();sections=[];query='';filter='';page=1;manager=false;}
  function error(message){const box=dialog.querySelector('[data-dc-error]')||host.querySelector('[data-dc-error]');if(box){box.textContent=message;box.classList.remove('hidden');}}
  function open(title,body,footer=''){
    releaseURL();dialog.innerHTML=`<header class="dc-dialog-head"><h2>${e(title)}</h2><button type="button" class="button secondary" data-dc="close" aria-label="Cerrar">Cerrar</button></header><div class="dc-dialog-body"><p class="error hidden" data-dc-error role="alert"></p>${body}</div>${footer}`;
    if(!dialog.open)dialog.showModal();
  }
  function sectionOptions(id){return sections.map(s=>`<option value="${e(s.id)}" ${id===s.id?'selected':''}>${e(s.name)}</option>`).join('');}
  function chosenSection(){return sections.find(s=>s.name===state.moduleSection)?.id||sections[0]?.id;}
  async function enter(force=false){
    if(!force&&loaded&&lastSection===state.moduleSection){host.classList.remove('hidden');return;}
    loaded=false;
    const token=++generation;if(!state.user)return;host.classList.remove('hidden');
    if(lastSection!==state.moduleSection){page=1;lastSection=state.moduleSection;}
    host.innerHTML='<div class="load-placeholder" role="status"><span class="load-coffee" aria-hidden="true"></span><span>Cargando documentos…</span></div>';
    try{
      if(!sections.length){const initial=await api(base);if(token!==generation||state.module!=='documents')return;sections=initial.sections;manager=initial.can_manage;}
      modules.documents.sections=sections.map(s=>s.name);
      if(!modules.documents.sections.includes(state.moduleSection))state.moduleSection=sections[0]?.name||'Salón';
      $('#module-subtabs').innerHTML=sections.map(s=>`<button class="${s.name===state.moduleSection?'selected':''}" data-module-section="${e(s.name)}" aria-pressed="${s.name===state.moduleSection}">${e(s.name)}</button>`).join('');
      const params=new URLSearchParams({section:chosenSection()||'',q:query,state:filter,page:String(page)});
      const data=await api(base+'?'+params);if(token!==generation||state.module!=='documents')return;
      manager=data.can_manage;loaded=true;
      host.innerHTML=`<div class="dc-heading"><div><h2>${e(state.moduleSection)}</h2><p class="hint">${data.total} ${data.total===1?'documento':'documentos'}${manager?' · Los borradores y archivados solo los ven socios.':''}</p></div>${manager?'<div class="dc-actions"><button class="button primary" data-dc="upload">Subir documento</button><button class="button secondary" data-dc="new-checklist">Crear checklist</button><button class="button secondary" data-dc="new-section">Nueva sección</button></div>':''}</div><p class="error hidden" data-dc-error role="alert"></p>
      <form class="dc-filters" data-dc-form="filter"><label class="field">Buscar<input name="q" maxlength="120" value="${e(query)}" placeholder="Título o descripción"></label>${manager?`<label class="field">Estado<select name="state"><option value="">Todos</option>${Object.entries(statusName).map(([key,label])=>`<option value="${key}" ${filter===key?'selected':''}>${label}</option>`).join('')}</select></label>`:''}<button class="button secondary">Buscar</button></form>
      <div class="dc-grid">${data.documents.map(doc=>`<article class="dc-card"><div class="dc-card-labels"><span>${e(kindName[doc.kind])}</span><span class="dc-state dc-state-${e(doc.state)}">${e(statusName[doc.state])}</span></div><h3>${e(doc.title)}</h3><p class="dc-description">${e(doc.description||'Sin descripción.')}</p><p class="hint">Versión ${doc.version}${doc.live_version&&doc.latest_version>doc.live_version?` · Vigente: v${doc.live_version}; nueva versión pendiente`:''}</p><div class="dc-actions"><button class="button primary" data-dc="detail" data-id="${e(doc.id)}">${doc.kind==='checklist'?'Ver checklist':doc.mime===xlsxMime?'Ver archivo':'Vista previa'}</button>${manager?`<button class="button secondary" data-dc="edit" data-id="${e(doc.id)}">Editar / reemplazar</button>`:''}</div></article>`).join('')||'<div class="dc-empty"><h3>No hay documentos en esta vista</h3><p>Ajusta los filtros o sube un documento a esta sección.</p></div>'}</div>
      <div class="dc-pagination"><button class="button secondary" data-dc="previous" ${page===1?'disabled':''}>Anterior</button><span>Página ${page} · ${data.total} resultados</span><button class="button secondary" data-dc="next" ${page*data.page_size>=data.total?'disabled':''}>Siguiente</button></div>`;
    }catch(err){if(token===generation)host.innerHTML=`<div class="error" role="alert">${e(err.message)}</div><button class="button secondary" data-dc="reload">Reintentar</button>`;}
  }
  function editor(doc=null,kind='document'){
    selected=doc;kind=doc?.kind||kind;
    const current=doc?.section_id||chosenSection();
    open(doc?'Nueva versión del documento':kind==='checklist'?'Crear checklist':'Subir documento',`<form data-dc-form="save"><input type="hidden" name="kind" value="${e(kind)}">${!doc&&kind!=='checklist'?'<label class="field">Tipo<select name="uploadKind"><option value="document">Documento</option><option value="checklist_template">Plantilla de checklist adjunta</option></select></label>':''}
      <div class="dc-form-grid"><label class="field">Título<input name="title" required maxlength="160" value="${e(doc?.title||'')}"></label><label class="field">Destino<select name="section_id">${sectionOptions(current)}<option value="__new__">＋ Crear nueva sección</option></select></label></div>
      <label class="field hidden" data-new-section>Nombre de la nueva sección<input name="new_section" maxlength="60"></label><label class="field">Descripción<textarea name="description" rows="3" maxlength="2000">${e(doc?.description||'')}</textarea></label>
      ${kind==='checklist'?`<label class="field">Ítems · uno por línea<textarea name="items" rows="8" required placeholder="Revisar la vitrina\nRegistrar las existencias">${e((doc?.items||[]).join('\n'))}</textarea></label><p class="hint">Entre 1 y 100 ítems. El responsable y la fecha se registran al completar.</p>`:`<label class="field">${doc?'Archivo de reemplazo · opcional':'Archivo'}<input type="file" name="file" accept="application/pdf,image/png,image/jpeg,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.pdf,.png,.jpg,.jpeg,.xlsx" ${doc?'':'required'}></label><p class="hint">PDF, PNG, JPG o XLSX · máximo 8 MB. Excel se descarga para editar en Excel o Sheets; no se edita aquí. ${doc?'Sin archivo nuevo se conservan los bytes de la versión actual.':''}</p><div data-local-preview class="dc-local-preview"></div>`}
      ${doc?'<p class="dc-notice">Se guardará una nueva versión. La versión vigente seguirá disponible hasta que publiques el reemplazo.</p>':`<label class="field">Estado inicial<select name="state"><option value="draft">Borrador · revisar antes de publicar</option><option value="active">Vigente · disponible al equipo</option></select></label>`}
      <div class="dc-form-footer"><button type="button" class="button secondary" data-dc="close">Cancelar</button><button class="button primary" type="submit">${doc?'Guardar nueva versión':'Guardar'}</button></div></form>`);
  }
  async function showDetail(id,version=null){
    const token=generation;const doc=await api(`${base}/documents/${encodeURIComponent(id)}${version?'?version='+version:''}`);if(token!==generation||!state.user)return;
    selected=doc;
    const selectedOld=doc.version!==doc.latest_version;
    open(doc.title,`<div class="dc-card-labels"><span>${e(kindName[doc.kind])}</span><span class="dc-state dc-state-${doc.state}">${e(statusName[doc.state])}</span><span>Versión ${doc.version}</span></div><p class="dc-description">${e(doc.description)}</p>
      ${doc.kind==='checklist'?`<ol class="dc-checklist-preview">${doc.items.map(item=>`<li>${e(item)}</li>`).join('')}</ol>${manager&&doc.state==='active'?'<button class="button primary" data-dc="complete">Completar versión vigente</button>':''}`:doc.mime===xlsxMime?`<p class="dc-notice">Archivo Excel editable. Descarga el original para abrirlo en Excel o Google Sheets. El ERP no muestra ni ejecuta el contenido del libro.</p><p class="hint">${e(doc.filename)} · ${Math.ceil(doc.bytes/1024)} KB</p><div class="dc-actions"><a class="button primary" href="${e(fileURL(doc,true))}">Descargar Excel</a></div>`:`<div class="dc-preview">${doc.mime==='application/pdf'?`<iframe title="Vista previa de ${e(doc.title)}" src="${e(fileURL(doc))}"></iframe>`:`<img alt="Vista previa de ${e(doc.title)}" src="${e(fileURL(doc))}">`}</div><p class="hint">${e(doc.filename)} · ${Math.ceil(doc.bytes/1024)} KB</p><div class="dc-actions"><a class="button secondary" href="${e(fileURL(doc))}" target="_blank" rel="noopener">Abrir</a><a class="button primary" href="${e(fileURL(doc,true))}">Descargar</a></div>`}
      ${manager?`<section class="dc-management"><h3>Administrar</h3><p class="hint">Última versión: ${doc.latest_version} · ${doc.live_version?'Vigente: '+doc.live_version:'Aún sin publicación'}.</p><div class="dc-actions"><button class="button secondary" data-dc="edit-current">Editar / reemplazar</button>${doc.state!=='active'||doc.live_version!==doc.latest_version?'<button class="button primary" data-dc="state" data-state="active">Publicar última versión</button>':''}${doc.state!=='draft'?'<button class="button secondary" data-dc="state" data-state="draft">Pasar a borrador</button>':''}${doc.state!=='archived'?'<button class="button secondary" data-dc="state" data-state="archived">Archivar</button>':''}</div></section>
      <details class="dc-history"><summary>Versiones e historial</summary><label class="field">Ver versión<select data-dc-version>${doc.versions.map(v=>`<option value="${v.version}" ${v.version===doc.version?'selected':''}>v${v.version} · ${e(v.created_at)} · ${e(v.created_by)}</option>`).join('')}</select></label>${selectedOld?'<p class="dc-notice">Estás viendo una versión anterior. Editar y publicar actúan sobre la última versión.</p>':''}<ul>${doc.events.map(entry=>`<li><strong>${e(({created:'Creado',imported:'Documento existente incorporado',version_added:'Nueva versión',state_changed:'Cambio de estado',checklist_completed:'Checklist completado'})[entry.event]||entry.event)}</strong> · ${e(entry.actor)} · ${e(entry.created_at)}${entry.details.to?` · ${e(statusName[entry.details.to])}`:''}</li>`).join('')}</ul></details>
      ${doc.kind==='checklist'?`<details class="dc-history"><summary>Registros completados (${doc.runs.length})</summary>${doc.runs.map(run=>`<article><h4>${e(run.performed_on)} · ${e(run.responsible)}</h4><p>Versión ${run.version} · Guardado por ${e(run.actor)} · ${e(run.created_at)}</p><ul>${run.items.map(i=>`<li>✓ ${e(i.text)}</li>`).join('')}</ul><p class="dc-description">${e(run.notes)}</p></article>`).join('')||'<p>Aún no hay registros.</p>'}</details>`:''}`:''}`);
  }
  async function completion(){
    const doc=await api(`${base}/documents/${selected.id}?version=${selected.live_version}`);selected=doc;
    open('Completar checklist vigente',`<form data-dc-form="complete"><h3>${e(doc.title)} · v${doc.version}</h3><div class="dc-form-grid"><label class="field">Responsable<input name="responsible" maxlength="120" required value="${e(state.user.name)}"></label><label class="field">Fecha<input type="date" name="performed_on" required value="${new Date().toLocaleDateString('en-CA')}"></label></div><div class="dc-checklist">${doc.items.map((item,index)=>`<label><input type="checkbox" name="item" value="${index}" required><span>${e(item)}</span></label>`).join('')}</div><label class="field">Observaciones<textarea name="notes" maxlength="2000" rows="3"></textarea></label><p class="hint">El registro conserva los ítems y la versión utilizados. Después de guardar no se sobrescribe.</p><div class="dc-form-footer"><button type="button" class="button secondary" data-dc="close">Cancelar</button><button class="button primary">Guardar checklist completado</button></div></form>`);
  }
  async function newSection(){open('Crear sección','<form data-dc-form="section"><label class="field">Nombre<input name="name" required maxlength="60"></label><div class="dc-form-footer"><button class="button primary">Crear sección</button></div></form>');}
  function confirmation(target){
    const doc=selected;
    const message=target==='active'?`Se publicará la última versión (${doc.latest_version}) para el equipo.`:target==='archived'?'El documento dejará de aparecer al equipo. Se conservarán todas sus versiones e historial.':'El documento quedará visible solo para socios hasta volver a publicarlo.';
    open(statusName[target],`<p>${e(doc.title)}</p><p>${e(message)}</p><form data-dc-form="state"><input type="hidden" name="state" value="${target}"><div class="dc-form-footer"><button type="button" class="button secondary" data-dc="back-detail">Volver</button><button class="button primary">Confirmar</button></div></form>`);selected=doc;
  }
  async function action(event){
    const button=event.target.closest('[data-dc]');if(!button||busy)return;
    const action=button.dataset.dc;
    try{
      if(action==='close')return close();
      if(action==='reload')return enter(true);
      if(action==='upload')return editor();
      if(action==='new-checklist')return editor(null,'checklist');
      if(action==='new-section')return newSection();
      if(action==='detail')return await showDetail(button.dataset.id);
      if(action==='edit'){const doc=await api(base+'/documents/'+button.dataset.id);return editor(doc);}
      if(action==='edit-current'){const doc=await api(base+'/documents/'+selected.id);return editor(doc);}
      if(action==='back-detail')return await showDetail(selected.id);
      if(action==='state')return confirmation(button.dataset.state);
      if(action==='complete')return await completion();
      if(action==='previous'||action==='next'){page+=action==='next'?1:-1;return enter(true);}
    }catch(err){error(err.message);}
  }
  function readFile(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('No se pudo leer el archivo.'));reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.readAsDataURL(file);});}
  async function submit(event){
    const form=event.target.closest('[data-dc-form]');if(!form)return;event.preventDefault();if(busy)return;
    const kind=form.dataset.dcForm;
    if(kind==='filter'){query=form.elements.q.value;filter=form.elements.state?.value||'';page=1;return enter(true);}
    const token=generation;let documentId=selected?.id;const originUser=state.user?.username;
    // Retain one payload/id after uncertain network responses. Editing clears it below.
    busy=true;form.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{
      let path,payload;
      if(form.dcPending){({path,payload}=form.dcPending);}
      else if(kind==='section'){path='/sections';payload={name:form.elements.name.value,request_id:requestId()};}
      else if(kind==='state'){path=`/documents/${selected.id}/state`;payload={state:form.elements.state.value,expected_revision:selected.revision,request_id:requestId()};}
      else if(kind==='complete'){path=`/documents/${selected.id}/runs`;payload={version:selected.version,expected_revision:selected.revision,responsible:form.elements.responsible.value,performed_on:form.elements.performed_on.value,notes:form.elements.notes.value,checked:[...form.querySelectorAll('[name=item]')].map(i=>i.checked),request_id:requestId()};}
      else if(kind==='save'){
        let section=form.elements.section_id.value;
        if(section==='__new__'){
          form.dcSectionRequest ||= {name:form.elements.new_section.value,request_id:requestId()};
          const created=await post('/sections',form.dcSectionRequest);section=created.id;form.dcDestinationName=created.name;sections=[];
        }
        form.dcDestinationName ||= sections.find(s=>s.id===section)?.name;
        payload={title:form.elements.title.value,description:form.elements.description.value,section_id:section,kind:form.elements.uploadKind?.value||form.elements.kind.value,state:form.elements.state?.value||'draft',request_id:requestId()};
        if(selected){payload.expected_revision=selected.revision;path=`/documents/${selected.id}/versions`;}else path='/documents';
        if(payload.kind==='checklist'){payload.items=form.elements.items.value.split('\n').map(v=>v.trim()).filter(Boolean);}
        else{
          const file=form.elements.file.files[0];
          if(file){if(file.size>8*1024*1024)throw new Error('El archivo supera 8 MB.');payload.file={name:file.name,base64:await readFile(file)};}
        }
      }
      if(token!==generation||originUser!==state.user?.username)throw new Error('Cambió la vista o la sesión. Vuelve a abrir el formulario.');
      form.dcPending={path,payload};
      const result=await post(path,payload);
      if(kind==='section'){sections=[];state.moduleSection=result.name;page=1;}
      if(kind==='save'){documentId=result.id;if(form.dcDestinationName)state.moduleSection=form.dcDestinationName;sections=[];}
      busy=false;close();await enter(true);rememberView();
      if(documentId&&state.module==='documents')await showDetail(documentId);
    }catch(err){error(err.message);}
    finally{busy=false;form.querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  host.addEventListener('click',action);dialog.addEventListener('click',action);
  host.addEventListener('submit',submit);dialog.addEventListener('submit',submit);
  dialog.addEventListener('cancel',event=>{event.preventDefault();if(!busy)close();});
  dialog.addEventListener('input',event=>{const form=event.target.closest('form');if(form&&!busy){form.dcPending=null;form.dcSectionRequest=null;form.dcDestinationName=null;}});
  dialog.addEventListener('change',async event=>{
    try{
      if(event.target.matches('[data-dc-version]'))return await showDetail(selected.id,event.target.value);
      if(event.target.name==='section_id'){const input=dialog.querySelector('[data-new-section]');input.classList.toggle('hidden',event.target.value!=='__new__');input.querySelector('input').required=event.target.value==='__new__';}
      if(event.target.name==='file'){
        releaseURL();const file=event.target.files[0];const box=dialog.querySelector('[data-local-preview]');box.replaceChildren();if(!file)return;
        if(file.size>8*1024*1024){event.target.value='';throw new Error('El archivo supera 8 MB.');}
        if(!/\.(pdf|png|jpe?g|xlsx)$/i.test(file.name)){event.target.value='';throw new Error('Selecciona un PDF, PNG, JPG o XLSX.');}
        if(/\.xlsx$/i.test(file.name)){const info=document.createElement('p');info.className='hint';info.textContent=file.name+' · '+Math.ceil(file.size/1024)+' KB · Excel descargable, sin vista previa. Aún no se ha subido.';box.append(info);return;}
        const mime=/\.pdf$/i.test(file.name)?'application/pdf':/\.png$/i.test(file.name)?'image/png':'image/jpeg';
        uploadURL=URL.createObjectURL(new Blob([file],{type:mime}));
        const link=document.createElement('a');link.href=uploadURL;link.target='_blank';link.rel='noopener';link.className='button secondary';link.textContent='Vista previa antes de guardar';box.append(link);
        const info=document.createElement('p');info.className='hint';info.textContent=file.name+' · '+Math.ceil(file.size/1024)+' KB · Aún no se ha subido.';box.append(info);
      }
    }catch(err){error(err.message);}
  });
  return {enter,leave,reset};
})();
