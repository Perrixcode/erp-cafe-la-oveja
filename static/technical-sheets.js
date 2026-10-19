'use strict';
window.OvejaTechnicalSheets=(()=>{
  const host=document.createElement('section');
  host.id='technical-sheets-workspace';host.className='hidden';
  host.setAttribute('aria-label','Fichas técnicas');
  $('#module-placeholder').append(host);
  const line='<span class="ft-blank" aria-label="Sin completar"></span>';
  const field=label=>`<div class="ft-field"><span>${label}</span>${line}</div>`;
  function leave(){host.classList.add('hidden');}
  function reset(){leave();host.replaceChildren();}
  function enter(){
    if(!state.user){reset();return;}
    host.classList.remove('hidden');
    host.innerHTML=`<div class="ft-list"><p class="hint">0 fichas registradas</p><div class="ap-table-wrap"><table class="ap-table"><thead><tr><th>Ficha técnica</th><th>Área / categoría</th><th>Estado</th><th>Responsable</th></tr></thead><tbody><tr><td colspan="4">Aún no hay fichas técnicas. La maqueta de abajo muestra la estructura acordada, con sus campos vacíos.</td></tr></tbody></table></div></div>
      <div class="ft-preview-heading"><h2>Maqueta de ficha técnica</h2><p class="hint">Vista de referencia en tamaño carta. Pendiente de completar y aprobar.</p></div>
      <div class="ft-preview"><article class="ft-sheet" aria-label="Maqueta vacía de ficha técnica">
        <header class="ft-header"><strong class="ft-brand">OVEJA</strong><div><strong>FICHA TÉCNICA</strong><span>COPIA OPERATIVA</span></div></header>
        <div class="ft-name">${field('NOMBRE DE LA PREPARACIÓN')}</div><div class="ft-area">${field('ÁREA / CATEGORÍA')}</div>
        <div class="ft-status"><strong>MAQUETA / CAMPOS VACÍOS</strong><span>Pendiente de validación y aprobación</span></div>
        <section class="ft-metadata" aria-label="Identificación">${['CÓDIGO','VERSIÓN','FECHA','APROBÓ'].map(field).join('')}</section>
        <section class="ft-overview"><div>${field('RENDIMIENTO')}${field('PORCIÓN DE REFERENCIA')}<div class="ft-times">${field('PREPARACIÓN')}${field('SERVICIO')}</div></div><div class="ft-photo"><span class="ft-camera" aria-hidden="true">○</span><strong>FOTO DEL RESULTADO</strong><span>Incorporar fotografía aprobada</span></div></section>
        <h3><span>01</span> INGREDIENTES Y PREPARACIÓN PREVIA</h3><table class="ft-ingredients"><thead><tr><th>INGREDIENTE</th><th>CANT.</th><th>UNIDAD</th><th>PREPARACIÓN PREVIA</th></tr></thead><tbody>${Array.from({length:4},()=>`<tr>${Array.from({length:4},()=>'<td><span aria-label="Sin completar">&nbsp;</span></td>').join('')}</tr>`).join('')}</tbody></table>
        <h3><span>02</span> ELABORACIÓN</h3><ol class="ft-steps">${Array.from({length:4},()=>`<li>${line}</li>`).join('')}</ol>
        <div class="ft-controls"><section><h4>03 &nbsp; MONTAJE Y PORCIONADO</h4>${line}${line}${line}</section><section><h4>04 &nbsp; PUNTOS DE CONTROL</h4>${line}${line}${line}</section></div>
        <div class="ft-safety">${field('ALÉRGENOS / CONTAMINACIÓN CRUZADA')}${field('CONSERVACIÓN / VIDA ÚTIL')}</div>
        <footer><span>No utilizar en producción hasta validar la receta y autorizar esta ficha.</span><span>1 / 1</span></footer>
      </article></div>`;
  }
  return {enter,leave,reset};
})();
