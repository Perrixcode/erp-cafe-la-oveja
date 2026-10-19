"""Turn OCR boxes into reviewable table rows; preserve uncertainty, never infer quantities."""
import math
import re
from statistics import median
from erp.invoice_ocr import folded


def boxes_text(results,read_description=None):
    boxes=[]
    for box,text,score in results or []:
        if not text or len(box)!=4:continue
        x=sum(p[0] for p in box)/4;y=sum(p[1] for p in box)/4
        height=max(math.dist(box[0],box[3]),math.dist(box[1],box[2]),1)
        slope=(box[1][1]-box[0][1])/(box[1][0]-box[0][0]) if box[1][0]!=box[0][0] else 0
        boxes.append(dict(x=x,y=y,h=height,slope=slope,text=text.strip(),score=float(score),box=box))
    compact=lambda t:re.sub('[^a-z0-9]','',folded(t))
    labels={'tprd':'kind','tipo':'kind','codigo':'code','cod':'code','articulo':'description','descripcion':'description','detalle':'description','cantidad':'quantity','punitario':'unit_price','punitarlo':'unit_price','preciounitario':'unit_price','subtotal':'subtotal','dscto':'discount','descuento':'discount','total':'total'}
    headers=[]
    for b in boxes:
        field=labels.get(compact(b['text']))
        if field:headers.append(dict(b,field=field))
    groups=[]
    for header in headers:
        if header['field']!='quantity':continue
        peers=[h for h in headers if abs(h['y']-header['y'])<max(60,header['h']*5)]
        by={h['field']:h for h in peers}
        if all(k in by for k in ('description','quantity','unit_price','total')):groups.append(by)
    output=[]
    # Original tokens remain available; canonical lines follow only recognized labels.
    output.extend(b['text'] for b in sorted(boxes,key=lambda b:(b['y'],b['x'])))
    date_labels={'fechaemision':'Fecha Emisión','fechaemislon':'Fecha Emisión','vencimiento':'Vencimiento','vencimlento':'Vencimiento','formapago':'Forma Pago','fomapago':'Forma Pago','montoneto':'Monto Neto','montoexento':'Monto Exento','montoiva19':'Monto I.V.A (19%)','montolva19':'Monto I.V.A (19%)','montototal':'Monto Total'}
    for label in boxes:
        canonical=date_labels.get(compact(label['text']))
        if not canonical:continue
        peers=[]
        for other in boxes:
            if other['x']<=label['x']+label['h'] or other is label:continue
            offset=abs(other['y']-label['y']-label['slope']*(other['x']-label['x']))
            if offset<max(label['h'],other['h'])*.7 and re.search(r'\d|contado|cr[eé]dito',other['text'],re.I):peers.append(other)
        if len(peers)==1:output.append(canonical+': '+peers[0]['text'].lstrip(':：; '))
    for by in groups[:5]:
        heads=list(by.values());mx=sum(h['x'] for h in heads)/len(heads);my=sum(h['y'] for h in heads)/len(heads)
        denom=sum((h['x']-mx)**2 for h in heads)
        slope=sum((h['x']-mx)*(h['y']-my) for h in heads)/denom if denom else 0
        baseline=median(h['y']-slope*h['x'] for h in heads);height=median(h['h'] for h in heads)
        columns=sorted(heads,key=lambda h:h['x'])
        candidates=[]
        for b in boxes:
            y=b['y']-slope*b['x']
            if y<=baseline+height*.6:continue
            col=min(columns,key=lambda h:abs(h['x']-b['x']))['field']
            candidates.append(dict(b,row_y=y,column=col))
        anchors=sorted((b for b in candidates if b['column']=='quantity' and re.fullmatch(r'[0-9.,| /]+',b['text'])),key=lambda b:b['row_y'])
        cells_by_row=[{} for _ in anchors]
        for b in candidates:
            if not anchors:break
            idx=min(range(len(anchors)),key=lambda n:abs(anchors[n]['row_y']-b['row_y']))
            if abs(anchors[idx]['row_y']-b['row_y'])>max(6,b['h']*.65):continue
            cells_by_row[idx].setdefault(b['column'],[]).append(b)
        for cells in cells_by_row:
            if not all(k in cells for k in ('description','quantity','unit_price','total')):continue
            # A label with multiple competing values in one column requires manual review.
            if any(len(cells[k])!=1 for k in ('quantity','unit_price','total')):continue
            description=' '.join(b['text'] for b in sorted(cells['description'],key=lambda b:b['x']))
            if not re.search('[A-Za-z]',description):continue
            if read_description:description=read_description(sorted(cells['description'],key=lambda b:b['x']))
            def value(key):
                b=cells[key][0]
                if b['score']<.8:return '?'
                value=b['text'].strip('| $')
                if key in ('quantity','unit_price'):
                    # OCR often renders decimal commas as dots in numeric columns.
                    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+\.\d{2}',value):value=value[:-3]+','+value[-2:]
                    elif re.fullmatch(r'\d+\.\d{1,2}',value):value=value.replace('.',',')
                return value
            code=cells.get('code',[{'text':''}])[0]['text']
            if not re.fullmatch(r'[A-Za-z0-9_-]{3,30}',code):code='POR-REVISAR'
            output.append('\t'.join([code,description,value('quantity'),value('unit_price'),value('total')]))
    return '\n'.join(output)
