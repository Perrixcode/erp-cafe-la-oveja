"""Bounded offline OCR worker. Inputs are private local files, never URLs."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile


def limit_resources():
    # Bound allocations in the child, before importing native OCR libraries.
    # RLIMIT_DATA covers heap and anonymous mappings on Linux, not shared code.
    if sys.platform.startswith('linux'):
        import resource
        cap=288*1024*1024
        _,hard=resource.getrlimit(resource.RLIMIT_DATA)
        if hard!=resource.RLIM_INFINITY:cap=min(cap,hard)
        resource.setrlimit(resource.RLIMIT_DATA,(cap,cap))
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):
        os.environ[name]='1'


def bounded_image(path):
    """Decode once, normalize EXIF, and bound pixels before loading OCR models."""
    from PIL import Image,ImageOps
    with Image.open(path) as original:
        if not 0<original.width*original.height<=40000000:raise ValueError('image too large')
        original.draft('RGB',(1280,1280))
        original.thumbnail((1280,1280),Image.Resampling.LANCZOS)
        oriented=ImageOps.exif_transpose(original)
        if oriented.mode in ('RGBA','LA') or 'transparency' in oriented.info:
            rgba=oriented.convert('RGBA');image=Image.new('RGB',rgba.size,'white');image.paste(rgba,mask=rgba.getchannel('A'))
        else:image=oriented.convert('RGB')
        return image


def read_strips(engine,array,height=384,overlap=96):
    """Keep full-width text lines while bounding detector activation memory."""
    import math
    rows=array.shape[0]
    count=max(1,math.ceil(max(0,rows-overlap)/(height-overlap)))
    starts=[round(i*max(0,rows-height)/max(1,count-1)) for i in range(count)]
    result=[]
    for index,top in enumerate(starts):
        bottom=min(rows,top+height)
        lower=0 if index==0 else (top+min(rows,starts[index-1]+height))/2
        upper=rows if index==count-1 else (bottom+starts[index+1])/2
        boxes,_=engine(array[top:bottom])
        for box,text,score in boxes or []:
            center=sum(p[1] for p in box)/4+top
            if lower<=center<upper:result.append([[[p[0],p[1]+top] for p in box],text,score])
    return result

# Dedicated optional runtime keeps the web application's dependencies unchanged.
runtime=Path('/opt/oveja-erp/ocr-runtime/rapidocr-1.4.4')
if runtime.is_dir():sys.path.insert(0,str(runtime))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def offline(event,args):
    if event in {'socket.connect','socket.bind','socket.getaddrinfo','sqlite3.connect'}:raise RuntimeError('OCR database and network access prohibited')

def main():
    sys.addaudithook(offline)
    import fcntl
    lockdir=Path(tempfile.gettempdir())/('oveja-invoice-ocr-'+str(os.getuid()))
    lockdir.mkdir(mode=0o700,exist_ok=True)
    if lockdir.is_symlink() or lockdir.stat().st_uid!=os.getuid():raise ValueError('invalid lock directory')
    lockfd=os.open(lockdir/'reader.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:sys.exit(3)
    limit_resources()
    source=Path(sys.argv[1]);mime=sys.argv[2]
    if not source.is_file() or not 0<source.stat().st_size<=8388608:raise ValueError('invalid input')
    from erp.ocr_layout import boxes_text
    engine=None;descriptions=None;raw_pages=[]
    def image_text(path):
        nonlocal engine,descriptions
        extra=''
        with bounded_image(path) as im:
            # Run the secondary reader before allocating the ONNX models; never
            # ask it to decode the full-size original alongside those models.
            if mime!='application/pdf' and shutil.which('tesseract'):
                normalized=Path(folder)/'normalized.png';im.save(normalized)
                result=subprocess.run(['tesseract',str(normalized),'stdout','--psm','3'],capture_output=True,timeout=15,check=False)
                if not result.returncode:extra=result.stdout.decode(errors='replace')
            import cv2,numpy as np
            cv2.setNumThreads(1)
            array=np.asarray(im)[:,:,::-1].copy()
        if engine is None:
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            from rapidocr_onnxruntime import RapidOCR
            engine=RapidOCR(intra_op_num_threads=1,inter_op_num_threads=1,max_side_len=1280,det_limit_side_len=960,det_limit_type='max',det_box_thresh=.35,rec_batch_num=1,cls_batch_num=1,
                            det_mean=np.array([.5,.5,.5],dtype=np.float32),det_std=np.array([.5,.5,.5],dtype=np.float32))
        results=read_strips(engine,array)
        raw_pages.append(boxes_text(results))
        if descriptions is None:
            from erp.ocr_descriptions import DescriptionReader
            descriptions=DescriptionReader(runtime/'latin-models')
        result=boxes_text(results,lambda cells:descriptions.read(array,cells))
        if extra:raw_pages.append(extra);result+='\n\n'+extra
        return result

    text=[]
    with tempfile.TemporaryDirectory(prefix='oveja-ocr-pages-') as folder:
        if mime=='application/pdf':
            info=subprocess.run(['pdfinfo',str(source)],capture_output=True,timeout=5,check=True).stdout.decode(errors='replace')
            import re
            match=re.search(r'^Pages:\s*(\d+)',info,re.M)
            if not match or not 1<=int(match[1])<=20:raise ValueError('PDF page limit')
            scanned=0
            for page in range(1,int(match[1])+1):
                result=subprocess.run(['pdftotext','-layout','-f',str(page),'-l',str(page),str(source),'-'],capture_output=True,timeout=10,check=True).stdout.decode(errors='replace')
                if len(result.strip())>40:text.append(result);raw_pages.append(result);continue
                scanned+=1
                if scanned>5:raise ValueError('scanned page limit')
                prefix=Path(folder)/('page-'+str(page))
                subprocess.run(['pdftoppm','-f',str(page),'-l',str(page),'-singlefile','-scale-to','2200','-png',str(source),str(prefix)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=12,check=True)
                text.append(image_text(prefix.with_suffix('.png')))
        else:
            text.append(image_text(source))
    import json
    result=json.dumps(dict(reader_version='latin-cells-bounded-2',text='\n\n'.join(text),raw_text='\n\n'.join(raw_pages),line_readings=descriptions.readings if descriptions else []),ensure_ascii=False)
    if len(result.encode())>200000:raise ValueError('output limit')
    print(result)

if __name__=='__main__':
    try:main()
    except Exception:
        print('Local OCR could not complete within supported limits.',file=sys.stderr);sys.exit(2)
