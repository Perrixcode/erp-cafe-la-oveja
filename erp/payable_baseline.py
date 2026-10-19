"""One-time initialization from a reviewed opening ledger. No ongoing Sheet link."""
import hashlib
import json
from pathlib import Path
from erp import payables as ap, supplier_opening as opening
from erp.domain import DomainError
from erp.store import now


def initialize(store,path,actor,expected_total,expected_rows):
    raw=Path(path).read_bytes()
    if len(raw)>16*1024*1024:raise DomainError('El archivo es demasiado grande.')
    snapshot=opening.validate(json.loads(raw))
    key=hashlib.sha256(raw).hexdigest()
    pending=[r for r in snapshot['ledger'] if (r.get('balance') or 0)>0]
    total=sum(r['balance'] for r in pending)
    if total!=expected_total or len(pending)!=expected_rows:
        raise DomainError('El total o la cantidad no coinciden con el saldo inicial aprobado.',409)
    for row in pending:
        if row['balance']>row['amount'] or row['amount']-(row.get('paid') or 0)!=row['balance']:
            raise DomainError('Hay un saldo inconsistente. Concilia la fila antes de inicializar.',409)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT * FROM ap_initial_baselines WHERE id=?',(key,)).fetchone()
        if old:return {'baseline_id':key,'already_initialized':True,'total':old['total'],'rows':old['rows']}
        if db.execute('SELECT 1 FROM ap_initial_baselines').fetchone() or db.execute('SELECT 1 FROM ap_invoices').fetchone():
            raise DomainError('Ya hay facturas operativas. No se agrega otra apertura sobre ellas.',409)
        db.execute('INSERT INTO ap_initial_baselines VALUES(?,?,?,?,?,?,?)',(key,snapshot['file']['title'],raw,total,len(pending),actor,now()))
        for row in pending:
            existing=db.execute('SELECT id FROM ap_suppliers WHERE name=?',(row['name'],)).fetchall()
            if len(existing)>1:raise DomainError('Proveedor ambiguo en la apertura.',409)
            supplier=existing[0]['id'] if existing else ap.mutate(db,'supplier',{'name':row['name']},actor,None)['id']
            folio=str(row.get('folio') or '')
            real_folio=folio.isascii() and folio.isdigit() and int(folio)>7
            reference=folio if folio.isdigit() else f'SALDO-FILA-{row["row"]}'
            due=opening.date_text(row.get('due')) or ''
            try:ap.day(due,'Vencimiento',False)
            except DomainError:due=''
            invoice=ap.mutate(db,'invoice',dict(supplier_id=supplier,document_type='invoice' if real_folio else 'other',folio=reference,
                             issued_on='',due_on=due,amount=row['amount'],condition='unknown',
                             description='Saldo inicial de apertura. '+('Folio pendiente de confirmar.' if not real_folio else '')),
                             actor,None,check_duplicates=False)
            db.execute('INSERT INTO ap_baseline_links VALUES(?,?,?,?)',(invoice['id'],key,row['row'],json.dumps(row,ensure_ascii=False)))
            before_paid=row['amount']-row['balance']
            if before_paid:
                db.execute('INSERT INTO ap_payments(invoice_id,amount,paid_on,method,reference,notes,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',
                           (invoice['id'],before_paid,'','other','Abono anterior al ERP','Declarado en el saldo inicial; fecha de pago no informada.',actor,now()))
            ap.event(db,'initial_balance',actor,'Ingreso inicial único solicitado. Desde esta apertura los cambios se registran en el ERP.',
                     row,{'amount':row['amount'],'previous_paid':before_paid,'balance':row['balance']},supplier=supplier,invoice=invoice['id'])
        actual=db.execute('SELECT SUM(amount) FROM ap_invoices').fetchone()[0]-db.execute('SELECT COALESCE(SUM(amount),0) FROM ap_payments WHERE voided_at IS NULL').fetchone()[0]
        if actual!=total:raise DomainError('La apertura no conserva el total; no se aplicó.',409)
        return {'baseline_id':key,'already_initialized':False,'total':total,'rows':len(pending)}
