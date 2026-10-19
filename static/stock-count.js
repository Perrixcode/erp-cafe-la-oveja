'use strict';
window.OvejaStockCount=(()=>{
  let rows=[],page=0,pageSize=10,search='',generation=0;
  const normalized=value=>value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  const productCode=p=>String(p.source_product?.localCode||p.source_product?.idToteat||p.sku);
  const changed=r=>r.badPhysical||r.physical!==r.originalPhysical||r.reserved!==r.originalReserved;
  const visibleRows=()=>rows.filter(r=>normalized(`${r.product.flavor} ${r.product.size} ${productCode(r.product)}`).includes(normalized(search)));
  function reset(){generation++;rows=[];page=0;search='';}
  function open(row=null){
    if(!can('stock')||state.scope!=='operations')return;
    reset();pageSize=10;
    rows=[...state.board.catalog].sort((a,b)=>a.flavor.localeCompare(b.flavor,'es',{numeric:true})||a.size.localeCompare(b.size,'es',{numeric:true})).map((p,index)=>{
      const current=state.board.stock.rows.find(r=>r.flavor===p.flavor&&r.size===p.size);
      const physical=current?.physical===null||current?.physical===undefined?'':String(current.physical),reserved=String(current?.reserved??0);
      return {index,product:{...p},physical,reserved,originalPhysical:physical,originalReserved:reserved,version:current?.version??0};
    });
    if(!rows.length){toast('Primero incorpora productos enteros al catálogo.');return;}
    if(row)search=row.flavor;
    modal('Ingresar stock físico','Conteo de tortas enteras · guarda varios productos a la vez',`<form id="stock-batch-form"><div id="form-error" class="error hidden" role="alert"></div><div class="stock-count-filters"><label class="field">Buscar producto<input type="search" id="stock-search" placeholder="Nombre, formato o código" value="${escapeHTML(search)}"></label><label class="field">Productos por página<select id="stock-page-size">${[10,25,50,100].map(n=>`<option value="${n}">${n} productos</option>`).join('')}</select></label></div><p class="hint">Escribe el conteo físico y las enteras reservadas para trozar o vitrina. Vacío significa desconocido; cero significa sin unidades. Los cambios se conservan al buscar o cambiar de página.</p><div id="stock-count-list"></div><div class="stock-count-footer"><p id="stock-count-pending" role="status"></p><label class="field">Motivo del conteo<input name="reason" required maxlength="500" placeholder="Ej.: conteo físico de apertura"></label></div></form>`,`<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="stock-batch-form" id="stock-count-save">Guardar cambios</button>`,'STOCK FÍSICO');
    $('#modal').classList.add('stock-count-modal');render();$('#stock-search').focus();
  }
  function quantity(r,key){
    const title=key==='physical'?'Físico':'Reserva vitrina';
    return `<div class="stock-stepper"><button type="button" data-stock-step="-1" data-index="${r.index}" data-key="${key}" aria-label="Restar ${escapeHTML(title)} de ${escapeHTML(r.product.flavor+' '+r.product.size)}">−</button><input data-stock-quantity="${key}" data-index="${r.index}" type="number" inputmode="numeric" min="0" max="999" step="1" ${key==='reserved'?'required':''} value="${escapeHTML(r[key])}" placeholder="Sin dato" aria-label="${escapeHTML(title+' · '+r.product.flavor+' · '+r.product.size)}"><button type="button" data-stock-step="1" data-index="${r.index}" data-key="${key}" aria-label="Sumar ${escapeHTML(title)} de ${escapeHTML(r.product.flavor+' '+r.product.size)}">+</button></div>`;
  }
  function availability(r){
    if(r.physical==='')return 'Desconocido';
    const p=Number(r.physical),q=Number(r.reserved);
    return r.reserved!==''&&Number.isInteger(p)&&Number.isInteger(q)&&p>=q&&q>=0&&p<=999?String(p-q):'Revisar';
  }
  function render(){
    const shown=visibleRows(),pages=Math.max(1,Math.ceil(shown.length/pageSize));page=Math.min(page,pages-1);
    $('#stock-count-list').innerHTML=`<p class="hint">${shown.length} productos encontrados · página ${page+1} de ${pages}</p><div class="stock-count-scroll"><table class="stock-count-table"><thead><tr><th>Producto</th><th>Stock físico</th><th>Reserva vitrina</th><th>Disponible</th></tr></thead><tbody>${shown.slice(page*pageSize,(page+1)*pageSize).map(r=>`<tr data-stock-row="${r.index}" class="${changed(r)?'stock-changed':''}"><th>${escapeHTML(r.product.flavor)}<small>${escapeHTML(r.product.size)} · ${escapeHTML(productCode(r.product))}</small></th><td>${quantity(r,'physical')}</td><td>${quantity(r,'reserved')}</td><td><output data-stock-available="${r.index}">${availability(r)}</output><small data-stock-status="${r.index}">${changed(r)?'Modificado':''}</small></td></tr>`).join('')||'<tr><td colspan="4">No hay productos con ese nombre.</td></tr>'}</tbody></table></div><div class="stock-count-pagination"><button type="button" class="button secondary" data-stock-page="-1" ${page===0?'disabled':''}>Anterior</button><button type="button" class="button secondary" data-stock-page="1" ${page>=pages-1?'disabled':''}>Siguiente</button></div>`;
    updatePending();
  }
  function updatePending(){
    const count=rows.filter(changed).length;
    $('#stock-count-pending').textContent=`${count} productos modificados en total · incluye otras páginas y filtros.`;
    $('#stock-count-save').textContent=count?`Guardar ${count} ${count===1?'cambio':'cambios'}`:'Guardar cambios';
    $('#stock-count-save').disabled=!count||state.saving;
  }
  function updateRow(r){
    $(`[data-stock-available="${r.index}"]`).textContent=availability(r);
    $(`[data-stock-status="${r.index}"]`).textContent=changed(r)?'Modificado':'';
    $(`[data-stock-row="${r.index}"]`).classList.toggle('stock-changed',changed(r));updatePending();
  }
  document.addEventListener('input',event=>{
    if(event.target.id==='stock-search'){search=event.target.value;page=0;render();return;}
    if(!event.target.matches('[data-stock-quantity]'))return;
    const r=rows[Number(event.target.dataset.index)];r[event.target.dataset.stockQuantity]=event.target.value;if(event.target.dataset.stockQuantity==='physical')r.badPhysical=event.target.validity.badInput;updateRow(r);
  });
  document.addEventListener('change',event=>{if(event.target.id==='stock-page-size'){pageSize=Number(event.target.value);page=0;render();}});
  document.addEventListener('click',event=>{
    const b=event.target.closest('[data-stock-step],[data-stock-page]');if(!b||state.saving)return;
    if(b.dataset.stockPage){page+=Number(b.dataset.stockPage);render();return;}
    const r=rows[Number(b.dataset.index)],key=b.dataset.key;
    r[key]=String(Math.min(999,Math.max(0,(Number(r[key])||0)+Number(b.dataset.stockStep))));
    if(key==='physical')r.badPhysical=false;$(`[data-stock-quantity="${key}"][data-index="${r.index}"]`).value=r[key];updateRow(r);
  });
  document.addEventListener('submit',async event=>{
    if(event.target.id!=='stock-batch-form')return;event.preventDefault();if(state.saving)return;
    const dirty=rows.filter(changed);if(!dirty.length)return;
    if(dirty.length>100){showFormError('Guarda como máximo 100 productos por vez.');return;}
    for(const r of dirty){
      const p=r.physical===''?null:Number(r.physical),q=Number(r.reserved);
      if(r.badPhysical||(p!==null&&(!Number.isInteger(p)||p<0||p>999))||r.reserved===''||!Number.isInteger(q)||q<0||q>999||(p!==null&&q>p)){
        showFormError(`${r.product.flavor} (${r.product.size}): usa cantidades enteras entre 0 y 999; la reserva no puede superar el físico.`);return;
      }
    }
    const request=generation,form=event.target,reason=form.elements.reason.value;
    state.saving=true;updatePending();for(const input of form.querySelectorAll('input,select,button'))input.disabled=true;
    try{
      await api('/api/stock/batch','PUT',{entries:dirty.map(r=>({stock:{flavor:r.product.flavor,size:r.product.size,physical:r.physical===''?null:Number(r.physical),reserved:Number(r.reserved)},version:r.version})),reason});
      if(request!==generation)return;
      $('#modal').close();await loadBoard();toast(`${dirty.length} conteos guardados con responsable e historial.`);
    }catch(error){if(request===generation)showFormError(error.message);}
    finally{state.saving=false;for(const input of form.querySelectorAll('input,select,button'))input.disabled=false;if($('#stock-batch-form'))render();}
  });
  $('#modal').addEventListener('close',()=>{if($('#stock-batch-form'))reset();});
  return {open,reset};
})();
