"""Archivo opcional del bot. Un fallo de archivo nunca cambia su resultado ni respuesta."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile


def atomic(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor,name=tempfile.mkstemp(prefix='.archive-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as output:output.write(payload)
        os.chmod(name,0o600);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def archive_photo(root,image):
    try:
        if not isinstance(image,bytes) or not 0<len(image)<=8*1024*1024:return
        extension='jpg' if image.startswith(b'\xff\xd8\xff') else 'png' if image.startswith(b'\x89PNG\r\n\x1a\n') else 'webp' if image.startswith(b'RIFF') and image[8:12]==b'WEBP' else None
        if not extension:return
        path=Path(root)/(hashlib.sha256(image).hexdigest()+'.'+extension)
        if not path.exists():atomic(path,image)
    except Exception:logging.warning('ERP: comprobante pendiente de archivo')


def archive_selected(root,revision_id,fingerprint,selection,source,option):
    try:
        if not re.fullmatch('[a-f0-9]{64}',fingerprint) or type(revision_id) is not int or revision_id<1:return
        if str(source.get('orderId'))!=str(selection['id']):return
        # Solo campos de venta necesarios, nunca URLs de pago ni documento completo.
        fields=('lineNumber','productCodeToteat','productName','quantity','unitPriceAfterTax','amountAfterTax','cancelled','isExtra','comment','comments')
        document=source.get('document') or {}
        value={'version':1,'revision_id':revision_id,'fingerprint':fingerprint,'selected_option':option,
               'sale':{k:selection.get(k) for k in ('id','mesa','total','comentario','cliente')},
               'products':[{k:line.get(k) for k in fields if k in line} for line in document.get('line',[]) if isinstance(line,dict)]}
        atomic(Path(root)/(str(revision_id)+'.json'),json.dumps(value,ensure_ascii=False).encode())
    except Exception:logging.warning('ERP: detalle de venta pendiente de archivo')
