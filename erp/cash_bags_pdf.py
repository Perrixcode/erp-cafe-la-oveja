"""Four 70 x 120 mm preprint labels per Letter page; stable QR, no side effects."""
from io import BytesIO
import hashlib
from pathlib import Path


def labels_pdf(batch):
    if batch.get('preprinted_manifest_sha256'):
        from erp.cash_bags_import import validate_manifest, spec_for
        from erp.domain import DomainError
        validate_manifest([{'numero_visible': b['label'], 'sequence': b['number'], 'public_id': b['id'],
                            'url': batch['link_origin']+b['path']} for b in batch['bags']])
        spec=spec_for(batch['first_number'])
        if batch['preprinted_manifest_sha256'] != spec['manifest_sha256']:
            raise DomainError('El lote no coincide con el manifiesto preimpreso.',409)
        if any(b['status']=='void' for b in batch['bags']):
            raise DomainError('El lote contiene etiquetas anuladas. No se reimprime completo para evitar reutilizarlas.',409)
        original=Path(__file__).with_name('data').joinpath(spec['basename']+'.pdf').read_bytes()
        if hashlib.sha256(original).hexdigest() != spec['pdf_sha256']:
            raise DomainError('El PDF preimpreso cambió. Se requiere el original emitido.',503)
        return original
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics import renderPDF
    font='OvejaBag'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font,str(Path(__file__).resolve().parents[1]/'static/fonts/NotoSans.ttf')))
    out=BytesIO()
    pdf=canvas.Canvas(out,pagesize=letter,pageCompression=1,invariant=1)
    pdf.setTitle('Etiquetas de bolsas · 7 × 12 cm · Carta')
    pdf.setAuthor('ERP Oveja')
    width,height=70*mm,120*mm
    gap=5*mm
    margin_x=(letter[0]-2*width-gap)/2
    margin_y=(letter[1]-2*height-gap)/2
    for i,bag in enumerate(batch['bags']):
        if i and i%4==0:pdf.showPage()
        col,row=i%2,(i%4)//2
        x=margin_x+col*(width+gap)
        y=letter[1]-margin_y-(row+1)*height-row*gap
        pdf.saveState();pdf.translate(x,y)
        pdf.setLineWidth(.5);pdf.setStrokeColorRGB(.65,.7,.65);pdf.rect(0,0,width,height)
        pdf.setFillColorRGB(.12,.23,.18)
        pdf.setFont(font,10);pdf.drawString(5*mm,height-8*mm,'OVEJA · EFECTIVO')
        pdf.setFont(font,16);pdf.drawString(5*mm,height-18*mm,bag['label'])
        url=batch['link_origin']+bag['path']
        qr=QrCodeWidget(url,barLevel='M',barBorder=4)
        bounds=qr.getBounds();size=34*mm
        drawing=Drawing(size,size,transform=[size/(bounds[2]-bounds[0]),0,0,size/(bounds[3]-bounds[1]),0,0])
        drawing.add(qr);renderPDF.draw(drawing,pdf,(width-size)/2,height-57*mm)
        pdf.setFont(font,6.6)
        pdf.drawCentredString(width/2,height-60*mm,'Escanea para abrir la ficha · requiere sesión')
        # These preprint fields intentionally stay blank even after completion.
        for offset,label in [(68,'Fecha'),(76,'Caja / turno'),(84,'Responsable'),(92,'A · Total contado $'),(100,'B · Fondo retenido $'),(108,'C · Bolsa (A − B) $')]:
            yy=height-offset*mm
            pdf.setFont(font,7.3);pdf.drawString(5*mm,yy,label)
            line_start=5*mm+pdfmetrics.stringWidth(label,font,7.3)+2*mm
            pdf.line(line_start,yy-1*mm,width-5*mm,yy-1*mm)
        pdf.setFont(font,6.5)
        notice='PRUEBA LOCAL · NO OPERATIVA' if batch['is_test'] else 'Declarado ≠ recibido o verificado'
        if bag['status']=='void':notice='ANULADA · NO UTILIZAR'
        pdf.drawCentredString(width/2,4*mm,notice)
        pdf.restoreState()
    pdf.setFont(font,7)
    pdf.save()
    return out.getvalue()
