'use strict';
window.OvejaDeliveryReview=(()=>{
  const sections=['Analítica de repartos','Liquidación de repartidores','Conciliación de repartos'];
  let active=false,activeSection='',sequence=0,month='',snapshot=null,audit=null,settlement=null,summary=null,page=0,drivers=['all'];
  const visible=()=>active&&state.partner&&state.module==='delivery'&&sections.includes(state.moduleSection);
  const form=()=>$('#review-filter');
  const money=n=>n===null||n===undefined?'Sin dato':clp(n);
  const payable=n=>n===null||n===undefined?'Pendiente':money(n);
  const label=d=>d===null?'Sin repartidor':d;
  const driver=()=>drivers.length===1?drivers[0]:'selected';
  const matches=d=>drivers.includes('all')||drivers.includes(d??'missing');
  const selected=()=>snapshot.records.filter(r=>matches(r.driver));
  const totals=rows=>{const base=rows.filter(r=>r.analysis_status==='structured_subtotal');return {records:rows.length,base_records:base.length,base:base.reduce((s,r)=>s+r.amount_clp,0),known_gross:rows.reduce((s,r)=>s+(r.amount_clp||0),0),pending:rows.length-base.length,unknown_amounts:rows.filter(r=>r.amount_clp===null).length};};
  const diff=(a,b)=>a===null||b===null?'Sin dato':money(a-b);
  const card=(title,value,note)=>`<article class="review-stat"><span>${escapeHTML(title)}</span><strong>${escapeHTML(value)}</strong><small>${escapeHTML(note||'')}</small></article>`;
  const table=(heads,rows)=>`<div class="chat-table-wrap"><table class="chat-table"><thead><tr>${heads.map(h=>`<th>${escapeHTML(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
  const row=cells=>`<tr>${cells.map((c,i)=>`<${i?'td':'th'}>${c}</${i?'td':'th'}>`).join('')}</tr>`;
  const query=(extra={})=>{const params=new URLSearchParams({month,...extra});drivers.forEach(d=>params.append('driver',d));return params;};
  const draftDrivers=()=>$('#review-driver-all').checked?['all']:[...form().querySelectorAll('[name=reviewDriver]:checked')].map(e=>e.value);
  function pickerLabel(){const chosen=draftDrivers();$('#review-driver-selection').textContent=chosen.includes('all')?'Todos los repartidores':chosen.length===1?label(chosen[0]==='missing'?null:chosen[0]):chosen.length?chosen.length+' seleccionados':'Selecciona repartidores';}
  function renderPicker(){
    $('#review-driver-all').checked=drivers.includes('all');
    $('#review-driver-options').innerHTML=(snapshot?.couriers||[]).map(c=>`<label class="review-driver-option"><input type="checkbox" name="reviewDriver" value="${escapeHTML(c.driver??'missing')}" ${matches(c.driver)?'checked':''}><span>${escapeHTML(label(c.driver))}</span></label>`).join('');
    pickerLabel();
  }
  const notice='<p class="source-alert">PROVISIONAL · Las fuentes documentan registros y reclamaciones; no prueban ejecución ni derecho al cobro. No hay aprobación ni ejecución de pagos. Las diferencias no acreditan fraude ni sobrepago.</p>';
  function leave(){sequence++;active=false;$('#review-results').replaceChildren();}
  function reset(){leave();month='';snapshot=audit=settlement=summary=null;drivers=['all'];renderPicker();}
  function enter(){
    if(!state.partner){form().classList.add('hidden');$('#review-results').innerHTML='<p class="empty compact">Disponible para socios autenticados.</p>';return;}
    // Background board/notification refreshes must not replace a report being read.
    if(active&&activeSection===state.moduleSection)return;
    active=true;activeSection=state.moduleSection;form().classList.remove('hidden');
    if(!month){const m=new URLSearchParams(location.search).get('month');month=/^\d{4}-\d{2}$/.test(m||'')?m:state.board.today.slice(0,7);}
    form().elements.month.value=month;form().elements.group.closest('label').classList.toggle('hidden',state.moduleSection==='Liquidación de repartidores');
    load();
  }
  async function load(){
    const id=++sequence;page=0;form().querySelectorAll('[type=submit]').forEach(b=>b.disabled=true);const progress=window.OvejaLoading.begin($('#review-results'),{label:'Cargando análisis de repartos…',blocking:!$('#review-results').children.length});
    try{
      const [s,a]=await Promise.all([api('/api/delivery/chat?'+new URLSearchParams({month})),api('/api/delivery/audit?'+new URLSearchParams({month}))]);
      if(id!==sequence||!visible())return;snapshot=s;audit=a;
      const allowed=new Set((s.couriers||[]).map(c=>c.driver??'missing'));
      drivers=drivers.includes('all')?['all']:drivers.filter(d=>allowed.has(d));if(!drivers.length)drivers=['all'];
      renderPicker();
      const nextSummary=s.available?await api('/api/delivery/summary?'+query()):null;
      if(id!==sequence||!visible())return;summary=nextSummary;
      if(s.available&&state.moduleSection==='Liquidación de repartidores'){const nextSettlement=await api('/api/delivery/settlement?'+query());if(id!==sequence||!visible())return;settlement=nextSettlement;}
      render();rememberView();
    }catch(error){if(id===sequence&&visible())progress.fail(error.message,load);}
    finally{progress.done();if(id===sequence)form().querySelectorAll('[type=submit]').forEach(b=>b.disabled=false);}
  }
  function render(){
    if(!visible()||!snapshot)return;
    if(!snapshot.available){$('#review-results').innerHTML='<p class="empty compact">No hay registro auditado para este período. Selecciona un mes importado.</p>';return;}
    $('#review-results').innerHTML=notice+(state.moduleSection==='Analítica de repartos'?analytics():state.moduleSection==='Liquidación de repartidores'?liquidation():reconcile());
  }
  function globalComparison(){const t=snapshot.totals,f=snapshot.toteat,complete=f.total!==null;return `<h2>Chat y Toteat · alcance global</h2><div class="review-stats">${card('Base chat',money(t.base),`${t.base_records} fichas en base · ${t.records} candidatas`)}${card('Toteat',money(f.total),`${f.records} registros${complete?'':' · cobertura parcial'} · ${f.known_days}/${f.total_days} días`)}${card('Diferencia base − Toteat',diff(t.base,f.total),complete?`${t.base_records-f.records} fichas/registros de diferencia`:'Faltan días para comparar el mes')}</div><p class="hint">WhatsApp: fecha de envío sin zona declarada. Toteat: fecha operativa del turno. Ficha y registro de tarifa son unidades distintas; no hay cruce confirmado por pedido. Toteat no tiene atribución individual. Última lectura: ${escapeHTML(f.last_sync?new Date(f.last_sync).toLocaleString('es-CL',{timeZone:'America/Santiago'}):'Sin dato')}. ${f.review_count||0} registros de Toteat con advertencias.</p>`;}
  function analytics(){
    const rows=selected(),t=totals(rows),all=driver()==='all',m=summary.metrics;
    const rate=x=>`${x.count}/${x.denominator} · ${x.percent===null?'Sin dato':x.percent+'%'}`;
    const group=form().elements.group.value;
    const groups=group==='month'?[{day:month}]:group==='weeks'?snapshot.weeks:group==='couriers'?snapshot.couriers.filter(c=>matches(c.driver)):snapshot.days;
    let content=summaryPanel()+`<h2>Chat · análisis de referencia</h2><div class="review-stats">${card('Base chat (referencia)',money(t.base),`${t.base_records} fichas en base de ${t.records} candidatas`)}${card('Tarifa promedio de base',money(m.average_base),'Importe base / fichas en base')}${card('Variación mensual del promedio',m.average_change_percent===null?'Sin comparación':m.average_change_percent+'%',m.previous_average_base===null?'Falta mes anterior auditado comparable':`Mes anterior: ${money(m.previous_average_base)}`)}</div><div class="review-stats">${card('Pendientes',rate(m.pending),'Sobre fichas candidatas')}${card('Posibles duplicados / reasignación',rate(m.possible_duplicates),'Sobre fichas candidatas')}${card('Intentos con ficha por revisar',rate(m.attempts_under_review),'Mensajes sin ficha fuera del denominador')}</div><p class="hint">Mayor cantidad en base: ${escapeHTML(m.highest_volume_days.join(', ')||'Sin dato')}. Mayor importe en base: ${escapeHTML(m.highest_amount_days.join(', ')||'Sin dato')}. ${snapshot.incidents.length} incidencias contextuales del mes fuera de los totales.</p>`;
    if(all)content+=globalComparison()+(audit.available?threeSourceTable():'');
    content+=`<p class="hint">Variación mensual de la base: ${m.amount_change_percent===null?'Sin comparación':m.amount_change_percent+'%'} en importe y ${m.count_change_percent===null?'Sin comparación':m.count_change_percent+'%'} en fichas. Se comparan bases documentales auditadas; no mide entregas ejecutadas.</p>`;
    content+=`<h2>Desglose ${group==='weeks'?'semanal':group==='couriers'?'por repartidor':group==='month'?'mensual':'diario'}</h2>`+table(['Período / repartidor','Candidatas / base','Base chat','Bruto conocido','Pendientes','Toteat / registros','Δ base − Toteat'],groups.map(g=>{
      const rs=group==='month'?rows:rows.filter(r=>group==='couriers'?r.driver===g.driver:group==='weeks'?r.sent_at.slice(0,10)>=g.start&&r.sent_at.slice(0,10)<=g.end:r.sent_at.startsWith(g.day));
      const n=totals(rs),f=group==='month'?snapshot.toteat.total:g.toteat,c=group==='month'?(f===null?null:snapshot.toteat.records):g.toteat_records,can=all&&group!=='couriers';
      const title=group==='couriers'?label(g.driver):group==='weeks'?g.start+' a '+g.end:g.day;
      return row([escapeHTML(title),`${n.records} / ${n.base_records}`,money(n.records?n.base:null),money(n.records?n.known_gross:null),`${n.pending}<small>${n.unknown_amounts} sin importe</small>`,can?`${money(f)}<small>${c??'Sin dato'} registros</small>`:'No atribuible',can?`${diff(n.records?n.base:null,f)}<small>${c===null||!n.records?'Sin dato':n.base_records-c} fichas/registros</small>`:'No atribuible']);
    }));
    return content+'<p class="hint">Semanas lunes a domingo recortadas al mes. Un día sin fichas no demuestra ausencia de repartos. El bruto conocido incluye pendientes y no es pagadero.</p>';
  }
  function liquidation(){
    const s=settlement,t=s.totals;
    return `<div class="delivery-overview"><article class="delivery-total"><span class="eyebrow">LIQUIDACIÓN PROVISIONAL · ${escapeHTML(s.month)}</span><strong>${payable(t.net)}</strong><p>${t.missing_reports?'Subtotal con planilla':'Total a pagar'} · ${escapeHTML(s.driver_label)}</p><span class="timing-label">${t.base_records} fichas en base</span></article><article class="delivery-context"><h2>Base de cálculo</h2><p>Monto declarado en planilla: <strong>${money(t.principal)}</strong></p><p>Base chat (referencia): <strong>${money(t.base)}</strong></p>${t.missing_reports?`<p>${t.missing_reports} filas sin planilla: total pendiente, fuera del subtotal.</p>`:''}<p>${t.pending} fichas pendientes · ${t.unknown_amounts} importes desconocidos.</p><small>Generado: ${escapeHTML(new Date(s.generated_at).toLocaleString('es-CL',{timeZone:'America/Santiago'}))} · Chile</small></article></div><p class="source-alert">${escapeHTML(s.notice)}</p><div class="review-actions"><button class="button primary" data-review-pdf>Vista previa PDF</button><a class="button secondary" href="/api/delivery/settlement/pdf?${query({download:'1'})}">Descargar PDF privado</a></div><div id="review-pdf"></div>${summaryPanel()}${reportCards(s.reports)}<h2>Chat · detalle de referencia</h2>${table(['Fecha del mensaje','Fichas / base','Base chat','Retenido conocido','Sin importe'],s.days.map(d=>row([d.day,`${d.records} / ${d.base_records}`,money(d.records?d.base:null),money(d.records?d.known_gross-d.base:null),d.unknown_amounts])))}${casesHTML(s.cases)}<h2>Fichas pendientes de esta selección</h2>${pendingHTML(s.records.filter(r=>r.analysis_status!=='structured_subtotal'))}<p class="hint">El PDF incluye el detalle de todas las fichas seleccionadas y las incidencias contextuales del mes. No acredita pago.</p>`;
  }
  function summaryPanel(){
    if(!summary)return '';
    const rows=summary.rows;
    const adjustments=summary.adjustments;
    const covered=rows.filter(r=>r.principal!==null),missing=rows.length-covered.length;
    const total=covered.length?covered.reduce((sum,r)=>sum+r.net,0):null;
    return `<h2>Resumen por repartidor y conceptos</h2><div class="review-actions"><button class="button primary" data-summary-adjust>Agregar adicional o descuento</button><button class="button secondary" data-summary-preview>Ver resumen PDF</button><a class="button secondary" href="/api/delivery/summary.pdf?${query({download:'1'})}">Descargar resumen PDF</a><a class="button secondary" href="/api/delivery/summary.csv?${query()}">Exportar CSV para Excel</a></div><p class="hint">El monto declarado en la planilla es la base de cálculo. Total a pagar = planilla + adicionales − descuentos. El chat se conserva como referencia.</p>${table(['Repartidor','Base chat (referencia)','Monto declarado en planilla','Adicionales','Descuentos','Total a pagar'],rows.map(r=>row([escapeHTML(r.label),money(r.base),money(r.principal),money(r.additions),money(r.deductions),`<strong>${payable(r.net)}</strong>`])))}<p><strong>${missing?'Subtotal con planilla':'Total a pagar'}: ${payable(total)}</strong> · importe provisional.</p>${missing?`<p class="hint">${missing} filas sin planilla: total pendiente, fuera del subtotal. Sus conceptos se conservan para aplicar al disponer de su planilla.</p>`:''}<p class="hint">Los anticipos declarados requieren registro como descuento. Este resumen no registra ejecución de pagos.</p><div id="summary-pdf"></div><details class="review-daily"><summary>Conceptos e historial · ${adjustments.length}</summary>${adjustments.length?table(['Repartidor / concepto','Tipo / importe','Registro','Estado / acción'],adjustments.map(a=>row([escapeHTML(a.driver)+'<small>'+escapeHTML(a.concept)+'</small>',(a.kind==='addition'?'Adicional':'Descuento')+'<small>'+money(a.amount)+'</small>',escapeHTML(a.actor)+'<small>'+escapeHTML(new Date(a.created_at).toLocaleString('es-CL',{timeZone:'America/Santiago'}))+'</small>',a.void_at?'Anulado<small>'+escapeHTML(a.void_reason)+' · '+escapeHTML(a.void_actor)+' · '+escapeHTML(new Date(a.void_at).toLocaleString('es-CL',{timeZone:'America/Santiago'}))+'</small>':`<button class="text-button" data-summary-void="${escapeHTML(a.id)}">Anular con motivo</button>`]))):'<p class="hint">Todavía no hay adicionales ni descuentos registrados.</p>'}</details>`;
  }
  function adjustmentForm(id){
    const item=id?summary.adjustments.find(a=>a.id===id):null;
    if(id&&!item)return;
    const options=summary.rows.filter(r=>r.driver!==null).map(r=>`<option value="${escapeHTML(r.driver)}" ${r.driver===driver()?'selected':''}>${escapeHTML(r.label)}</option>`).join('');
    const fields=item?`<p>${escapeHTML(item.driver)} · ${escapeHTML(item.concept)} · ${money(item.amount)}</p><label class="field">Motivo de anulación<input name="reason" required minlength="5" maxlength="500"></label>`:`<div class="form-grid"><label class="field">Repartidor<select name="driver" required><option value="">Selecciona un repartidor</option>${options}</select></label><label class="field">Tipo<select name="kind" required><option value="">Selecciona el tipo</option><option value="addition">Adicional</option><option value="deduction">Descuento</option></select></label><label class="field full">Concepto<input name="concept" required minlength="3" maxlength="200" placeholder="Ej.: viaje adicional, anticipo, ajuste acordado"></label><label class="field">Importe en CLP<input name="amount" type="number" min="1" max="100000000" step="1" required></label></div>`;
    modal(item?'Anular concepto':'Agregar adicional o descuento',`Período ${month} · registro de socio`, `<form id="adjustment-form" data-id="${escapeHTML(id||'')}" data-version="${item?.version||''}" data-month="${month}" data-request="${crypto.randomUUID()}"><div id="form-error" class="error hidden"></div>${fields}<p class="hint">${item?'La anulación conserva el registro original y su responsable.':'El importe se aplica sobre el monto de la planilla. Si falta la planilla, queda registrado con total pendiente. No ejecuta pagos.'}</p></form>`, '<button class="button secondary" data-action="close">Cancelar</button><button class="button primary" type="submit" form="adjustment-form">Guardar</button>','CONCEPTOS · SOLO SOCIOS');
  }
  document.addEventListener('submit',async e=>{
    if(e.target.id!=='adjustment-form')return;e.preventDefault();if(state.saving)return;
    const f=e.target,b=document.querySelector('[form="adjustment-form"][type="submit"]');state.saving=true;b.disabled=true;
    try{
      const body={request_id:f.dataset.request};let path='/api/delivery/adjustments';
      if(f.dataset.id){path+='/'+f.dataset.id+'/void';body.version=Number(f.dataset.version);body.reason=f.elements.reason.value;}
      else Object.assign(body,{month:f.dataset.month,driver:f.elements.driver.value,kind:f.elements.kind.value,amount:Number(f.elements.amount.value),concept:f.elements.concept.value});
      await api(path,'POST',body);$('#modal').close();await load();toast('Concepto guardado con historial.');
    }catch(error){showFormError(error.message);}finally{state.saving=false;b.disabled=false;}
  });
  document.addEventListener('click',e=>{
    if(!visible()||!summary)return;
    const b=e.target.closest('[data-summary-adjust],[data-summary-void],[data-summary-preview]');if(!b)return;
    if(b.hasAttribute('data-summary-preview'))$('#summary-pdf').innerHTML=`<iframe class="review-pdf" title="Resumen de repartidores · PDF privado" src="/api/delivery/summary.pdf?${query()}"></iframe>`;
    else adjustmentForm(b.dataset.summaryVoid);
  });
  function reportCards(reports){return reports.length?`<h2>Informes individuales · importes declarados</h2><div class="review-reports">${reports.map(r=>`<article class="review-report"><h3>${escapeHTML(r.conductor)}</h3><strong>${money(r.bruto_declarado_informe_clp)}</strong><p>${r.cantidad_importes_informe} tarifas en informe · ${r.chat_count} fichas en base chat (${money(r.chat_base)}).</p><p>Diferencia informe − base: <strong>${money(r.difference)}</strong> · ${r.count_difference} registros.</p><p>${escapeHTML(r.detalle_diferencia)}</p>${r.adelanto_declarado_clp!=null?`<p class="source-alert">Adelanto declarado ${money(r.adelanto_declarado_clp)}: sin comprobante validado, no se descuenta. Saldo declarado ${money(r.saldo_declarado_clp)}: no aprobado.</p>`:''}<small>${escapeHTML(r.estado)}. Toteat: no atribuible individualmente.</small></article>`).join('')}</div>`:'<p class="hint">Sin informe individual importado para esta selección. No equivale a importe cero.</p>';}
  function refs(text){return [...new Set(String(text).match(/\d+\.\d+/g)||[])].map(id=>`<button class="text-button" data-review-evidence="${id}">Chat ${id}</button>`).join(' ');}
  function casesHTML(cases){return `<h2>Casos documentales pendientes · ${cases.length}</h2>${cases.map(c=>`<article class="review-report"><h3>${escapeHTML(c.caso_id)} · ${escapeHTML(c.fecha)} · ${money(c.importe_clp)}</h3><p>${escapeHTML(c.hallazgo)}</p><p><strong>Por acreditar:</strong> ${escapeHTML(c.evidencia_pendiente)}</p><small>${escapeHTML(c.estado)} · ${escapeHTML(c.ubicacion_informe)}</small><div>${refs(c.chat_registros_id)}</div></article>`).join('')}`;}
  function pendingHTML(rows){return rows.length?table(['Fecha / referencia','Repartidor','Importe original','Estado'],rows.map(r=>row([escapeHTML(r.sent_at.replace('T',' '))+refs(r.id),escapeHTML(label(r.driver)),money(r.amount_clp),escapeHTML(r.status_label)]))):'<p class="hint">Sin fichas pendientes en esta selección.</p>';}
  function threeSourceTable(){
    const comp=audit.comparison,group=form().elements.group.value;
    if(driver()!=='all'||group==='couriers'){
      const reports=audit.reports.filter(r=>matches(r.conductor));
      return '<h2>Las tres fuentes · por repartidor</h2>'+table(['Repartidor','Toteat','Base chat / fichas','Planilla / tarifas','Δ planilla − su base chat'],reports.map(r=>row([escapeHTML(r.conductor),'No atribuible',`${money(r.chat_base)}<small>${r.chat_count} fichas</small>`,`${money(r.bruto_declarado_informe_clp)}<small>${r.cantidad_importes_informe} tarifas</small>`,`${money(r.difference)}<small>${r.count_difference} registros</small>`])))+'<p class="hint">Toteat no identifica repartidores. Cada planilla se compara con la base de chat del mismo repartidor. Sin planilla no equivale a cero.</p>';
    }
    const rows=group==='weeks'?comp.weeks:group==='month'?[comp.month]:comp.days;
    return '<h2>Las tres fuentes · '+(group==='weeks'?'por semana':group==='month'?'por mes':'por día')+'</h2><p class="hint">Toteat y chat: alcance global. Planillas: suma de importes declarados de '+comp.report_drivers.map(escapeHTML).join(' y ')+'. Su cobertura es parcial y puede haber reclamaciones superpuestas.</p>'+table(['Período','Toteat global / registros','Base chat global / fichas','Planillas / tarifas declaradas','Δ chat − Toteat','Base chat de repartidores con planilla','Δ planillas − esa base'],rows.map(d=>row([escapeHTML(d.start===d.end?d.start:d.start+' a '+d.end),`${money(d.toteat_amount)}<small>${d.toteat_count??'Sin dato'} registros · ${d.toteat_known_days}/${d.days} días</small>`,`${money(d.chat_amount)}<small>${d.chat_count??'Sin dato'} fichas</small>`,`${money(d.report_amount)}<small>${d.report_count??'Sin dato'} tarifas</small>`,`${money(d.chat_toteat_difference)}<small>${d.chat_toteat_count_difference??'Sin dato'} fichas/registros</small>`,`${money(d.report_chat_amount)}<small>${d.report_chat_count??'Sin dato'} fichas</small>`,`${money(d.report_chat_difference)}<small>${d.report_chat_count_difference??'Sin dato'} tarifas/fichas</small>`])))+'<p class="hint">No se resta el total parcial de las planillas al total global de Toteat. Las fechas del chat y del turno pueden diferir; este cuadro compara registros, no certifica repartos ni pagos.</p>';
  }
  function reconcile(){
    let html=globalComparison();
    if(!audit.available)return html+'<p class="empty compact">Sin informes individuales importados para este período.</p>';
    const reports=audit.reports.filter(r=>matches(r.conductor)),cases=audit.cases.filter(c=>drivers.includes('all')||drivers.some(d=>d!=='missing'&&c.conductores_relacionados.includes(d)));
    html+=threeSourceTable()+reportCards(reports);
    html+=`<h2>Originales privados</h2><div class="review-actions">${audit.documents.filter(d=>matches(d.driver)).map(d=>`<a class="button secondary" href="/api/delivery/audit/document?${new URLSearchParams({month,id:d.document_key,download:'1'})}">${escapeHTML(d.driver)} · ${d.filename.endsWith('.pdf')?'PDF':'XLSX'} original</a>${d.filename.endsWith('.pdf')?`<button class="button secondary" data-review-original="${d.document_key}">Vista previa original</button>`:''}`).join('')}</div><div id="review-pdf"></div><p class="hint">Originales íntegros, verificados con SHA-256. Las dos planillas no cubren el total global y pueden superponerse entre sí.</p>`;
    for(const r of reports)html+=`<details class="review-daily"><summary>Comparación diaria · ${escapeHTML(r.conductor)}</summary>${table(['Fecha','Informe / tarifas','Base chat / fichas','Δ informe − base','Δ cantidad'],r.days.map(d=>row([d.day,`${money(d.report_amount)}<small>${d.report_count??'Sin dato'} tarifas</small>`,`${money(d.chat_amount)}<small>${d.chat_count??'Sin dato'} fichas</small>`,money(d.difference),d.count_difference??'Sin dato'])))}</details>`;
    html+='<p class="hint">Las fechas sin valores son «Sin dato», no ceros confirmados. La primera mención conservada en la base no resuelve una disputa de atribución.</p>'+casesHTML(cases);
    const rows=audit.rows.filter(r=>matches(r.conductor_informe)),pages=Math.max(1,Math.ceil(rows.length/25));page=Math.min(page,pages-1);
    html+=`<h2>Cruce trazable · ${rows.length} importes</h2><p class="hint">Coincidencias documentales por fecha, importe y posición en la fuente; no equivalen a un vínculo confirmado con un pedido comercial.</p>`+table(['Informe / ubicación','Fecha / importe declarado','Chat original','Observación'],rows.slice(page*25,page*25+25).map(r=>row([escapeHTML(r.conductor_informe)+`<small>#${r.ordinal_informe} · ${escapeHTML(r.ubicacion_fuente)}</small>`,`${r.fecha_informe}<small>${money(r.importe_informe_clp)}</small>`,`${refs(r.chat_registro_id)}<small>${escapeHTML(r.conductor_explicito_chat)} · ${money(r.importe_original_chat_clp)}</small><small>Líneas ${r.chat_linea_inicio}–${r.chat_linea_fin}</small>`,escapeHTML(r.observacion)])))+`<div class="review-actions"><button class="button secondary" data-review-page="-1" ${page?'':'disabled'}>Anterior</button><span>Página ${page+1} de ${pages}</span><button class="button secondary" data-review-page="1" ${page+1<pages?'':'disabled'}>Siguiente</button></div><details><summary>Integridad y alcance</summary><p class="hint review-hash">Paquete SHA-256: ${escapeHTML(audit.package_sha256)}<br>Fuente chat: ${escapeHTML(audit.source_sha256)}<br>Importado por ${escapeHTML(audit.actor)}.</p>${audit.limits.map(l=>`<p class="hint">${escapeHTML(l)}</p>`).join('')}</details>`;
    return html;
  }
  async function evidence(id){const seq=sequence;try{const d=await api('/api/delivery/chat/evidence?'+new URLSearchParams({source:snapshot.source_sha256,record:id}));if(seq!==sequence||!visible())return;modal('Evidencia del chat','Referencia '+id,`<p class="hint">${escapeHTML(d.ordinal_notice)}</p>${d.messages.map(m=>`<article class="history-entry"><strong>Mensaje ${m.id} · líneas ${m.line_start}–${m.line_end}</strong><p>${escapeHTML(m.timestamp)} · ${escapeHTML(m.sender)}</p><pre class="chat-evidence">${escapeHTML(m.text)}</pre></article>`).join('')}`,'<button class="button secondary" data-action="close">Cerrar</button>','AUDITORÍA · SOLO SOCIOS');}catch(e){if(visible())toast(e.message);}}
  document.addEventListener('submit',e=>{if(e.target.id!=='review-filter')return;e.preventDefault();const chosen=draftDrivers();if(!chosen.length){toast('Selecciona al menos un repartidor.');return;}drivers=chosen;$('#review-driver-picker').open=false;month=form().elements.month.value;load();});
  document.addEventListener('change',e=>{if(!visible()||e.target.closest('form')?.id!=='review-filter')return;
    if(e.target.id==='review-driver-all'){form().querySelectorAll('[name=reviewDriver]').forEach(c=>c.checked=e.target.checked);pickerLabel();}
    else if(e.target.name==='reviewDriver'){$('#review-driver-all').checked=false;pickerLabel();}
    else if(e.target.name==='group')render();
  });
  document.addEventListener('click',e=>{if(e.target.closest('[data-review-clear]')){form().querySelectorAll('[name=reviewDriver],#review-driver-all').forEach(c=>c.checked=false);pickerLabel();}});
  document.addEventListener('click',e=>{if(!visible())return;const b=e.target.closest('[data-review-evidence],[data-review-page],[data-review-pdf],[data-review-original]');if(!b)return;
    if(b.dataset.reviewEvidence)evidence(b.dataset.reviewEvidence);
    else if(b.dataset.reviewPage){page+=Number(b.dataset.reviewPage);render();}
    else {const url=b.hasAttribute('data-review-pdf')?'/api/delivery/settlement/pdf?'+query():'/api/delivery/audit/document?'+new URLSearchParams({month,id:b.dataset.reviewOriginal});$('#review-pdf').innerHTML=`<iframe class="review-pdf" title="PDF privado · vista previa" src="${url}"></iframe><p class="hint">Vista previa dentro del ERP. Puedes descargar el archivo con el botón superior.</p>`;}
  });
  return {enter,leave,reset,sections,get month(){return month;}};
})();
