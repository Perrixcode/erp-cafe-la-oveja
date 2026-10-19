"""Offline Latin recognition of detected description cells. Never correct numbers or codes."""
import hashlib
import re
from pathlib import Path

MODELS={
    'latin-v3.onnx':'e9d7a33667e8aaa702862975186adf2012e3f390cc0f9422865957125f8071cf',
    'latin-v5.onnx':'b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a',
}


def spacing(text):
    # Separate attached package sizes; never substitute letters with digits.
    text=re.sub(r'(?<=[A-Za-zÁÉÍÓÚáéíóúñÑ])(?=\d)|(?<=\d)(?=[A-Za-zÁÉÍÓÚáéíóúñÑ])',' ',text)
    text=re.sub(r'(\d)\s*[x×]\s*(\d)',r'\1x\2',text,flags=re.I)
    return ' '.join(text.split())


class DescriptionReader:
    def __init__(self,model_dir):
        from rapidocr_onnxruntime.ch_ppocr_rec import TextRecognizer
        self.engines=[]
        for name,digest in MODELS.items():
            path=Path(model_dir)/name
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==digest:
                self.engines.append((name,TextRecognizer(dict(model_path=str(path),intra_op_num_threads=1,inter_op_num_threads=1,
                                                              rec_batch_num=1,rec_img_shape=[3,48,320],use_cuda=False,use_dml=False))))
        self.readings=[]

    def read(self,image,cells):
        import cv2
        import numpy as np
        raw=' '.join(cell['text'] for cell in cells)
        if not self.engines or len(self.readings)>=100:return raw
        crops=[];variants=[(margin,scale) for margin in (-.1,0,.08) for scale in (1,2)]
        for cell in cells:
            for margin,scale in variants:
                pts=np.float32(cell['box']);left=pts[3]-pts[0];right=pts[2]-pts[1]
                # Multiple bounded crops expose unstable glyphs and package sizes.
                pts[0]+=left*margin;pts[1]+=right*margin;pts[3]-=left*margin;pts[2]-=right*margin
                w=max(2,int(max(np.linalg.norm(pts[1]-pts[0]),np.linalg.norm(pts[2]-pts[3]))))*scale
                h=max(2,int(max(np.linalg.norm(pts[3]-pts[0]),np.linalg.norm(pts[2]-pts[1]))))*scale
                if w>4000 or h>1000 or w*h>1000000:return raw
                crops.append(cv2.warpPerspective(image,cv2.getPerspectiveTransform(pts,np.float32([[0,0],[w,0],[w,h],[0,h]])),(w,h),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_REPLICATE))
        alternatives=[]
        for name,engine in self.engines:
            results,_=engine(crops)
            if results and len(results)==len(crops):
                for index,(margin,scale) in enumerate(variants):
                    rows=results[index::len(variants)]
                    alternatives.append(dict(model=name,variant=dict(margin=margin,scale=scale),text=spacing(' '.join(t for t,s in rows)),confidence=round(min(float(s) for t,s in rows),3)))
        # Scores from different models are not calibrated against each other.
        # v3 Latin is the primary reader; v5 supplies a visible independent alternative.
        primary=next((r for r in alternatives if r['model']=='latin-v3.onnx' and r['variant']==dict(margin=-.1,scale=1) and r['confidence']>=.8),None)
        normalized=primary['text'] if primary else spacing(raw)
        self.readings.append(dict(raw=raw,normalized=normalized,alternatives=alternatives,needs_review=True))
        return normalized
