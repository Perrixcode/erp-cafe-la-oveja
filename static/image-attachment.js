'use strict';
(() => {
  const LIMIT=8388608,images=['image/jpeg','image/png'];
  function error(file,paste=false){
    if(!file||!(paste?images:[...images,'application/pdf']).includes(file.type))return paste?'Pega una imagen JPG o PNG. Para PDF, usa el selector.':'Selecciona PDF, JPG o PNG.';
    if(!file.size||file.size>LIMIT)return 'Adjunta un archivo de hasta 8 MB.';
    return '';
  }
  if(typeof module!=='undefined')module.exports={error,LIMIT};
  if(typeof document==='undefined')return;
  const controls=new Set();let sequence=0;
  function enhance(root){
    for(const old of controls)if(!old.input.isConnected){old.release();controls.delete(old);}
    root.querySelectorAll('input[type=file]').forEach(input=>{
      if(input.dataset.pasteImage||!input.accept.split(',').some(t=>images.includes(t.trim())))return;
      input.dataset.pasteImage='1';const label=input.closest('label'),anchor=label||input;
      const wrapper=document.createElement('div');wrapper.className='image-attachment';anchor.before(wrapper);wrapper.append(anchor);
      const zone=document.createElement('div');zone.className='image-paste';zone.tabIndex=0;zone.setAttribute('role','group');zone.setAttribute('aria-label','Pegar imagen del portapapeles');
      zone.textContent='Haz clic aquí y pega tu foto con ⌘V o Ctrl+V';
      const status=document.createElement('p');status.className='hint';status.id='image-status-'+(++sequence);status.setAttribute('role','status');zone.setAttribute('aria-describedby',status.id);
      const preview=document.createElement('img');preview.className='image-preview';preview.hidden=true;preview.alt='Vista previa del archivo seleccionado';
      const remove=document.createElement('button');remove.type='button';remove.className='button secondary';remove.textContent='Quitar archivo';remove.hidden=true;
      wrapper.append(zone,preview,status,remove);let reader=null,generation=0;
      const release=()=>{generation++;if(reader?.readyState===1)reader.abort();reader=null;preview.onerror=null;};controls.add({input,release});
      function show(file){release();preview.hidden=true;preview.removeAttribute('src');remove.hidden=!file;
        status.textContent=file?file.name+' · '+Math.ceil(file.size/1024)+' KB · listo para revisar. Usa el selector o pega otra imagen para reemplazar.':'Sin archivo seleccionado. El documento se envía solo al guardar o leer.';
        if(file&&images.includes(file.type)){
          const token=generation,localReader=new FileReader();reader=localReader;
          const failed=()=>{if(token===generation){preview.hidden=true;status.textContent='Archivo seleccionado. No se pudo mostrar la vista previa; revisa que sea una imagen válida.';}};
          localReader.onerror=failed;
          localReader.onload=()=>{if(token!==generation||!input.isConnected)return;preview.onerror=failed;preview.src=String(localReader.result);preview.hidden=false;};
          // data: is already permitted by the application's restrictive img-src CSP.
          localReader.readAsDataURL(file);
        }
      }
      input.addEventListener('change',()=>{const file=input.files[0],message=file&&error(file);if(message){input.value='';show(null);status.textContent=message;return;}show(file);});
      remove.addEventListener('click',()=>{input.value='';input.dispatchEvent(new Event('change',{bubbles:true}));zone.focus();});
      zone.addEventListener('paste',event=>{
        if(document.activeElement!==zone||input.disabled)return;
        const files=[...(event.clipboardData?.files||[])];if(!files.length)return;
        event.preventDefault();if(files.length!==1){status.textContent='Pega una sola imagen por archivo.';return;}
        const message=error(files[0],true);if(message){status.textContent=message;return;}
        const file=files[0],transfer=new DataTransfer();transfer.items.add(new File([file],file.name||('imagen-pegada.'+(file.type==='image/png'?'png':'jpg')),{type:file.type}));
        input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));
      });
      show(input.files[0]);
    });
  }
  window.OvejaImageAttachment={enhance};
})();
