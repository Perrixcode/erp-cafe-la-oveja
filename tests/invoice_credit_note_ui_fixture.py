import json
from test_invoice_credit_notes import InvoiceCreditNoteTests
from erp import supplier_account as account,payables as ap
test=InvoiceCreditNoteTests();test.setUp()
try:
    before=account.statement(test.store,test.supplier['id'])
    test.payment(12000,[test.application(test.first,10000)])
    paid=account.statement(test.store,test.supplier['id']);note=test.note(3000)
    print(json.dumps(dict(before=before,paid=paid,after=account.statement(test.store,test.supplier['id']),payables=ap.view(test.store),result=note)))
finally:test.doCleanups()
