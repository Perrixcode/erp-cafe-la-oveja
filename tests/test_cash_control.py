"""Synthetic cash-control invariants. No live credentials, network or production data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from erp import cash_bags as bags,cash_control as cc,cash_control_sources as src
from erp.domain import DomainError
from erp.store import Store

PARTNER={'role':'partner','username':'Socio QA'}
CASHIER={'role':'cashier','username':'Cajera QA'}
SCOPE={'restaurant_id':'123','local_id':'1'}
QUERY={'start':['2026-10-16'],'end':['2026-10-16'],'scope':['tests']}
ORIGIN='http://localhost:8879'


def collection(day='2026-10-16',**changes):
    session=dict(openedDate=day+'T11:00:00',closedDate=day+'T14:00:00',initialAmount=57200.0,finalAmount=491700,
        movements=[],paymentMethods=[{'paymentMethodID':1000,'paymentMethod':'cash','amount':84900.0}],registerName='CAJA 1')
    session.update(changes)
    return {'ok':True,'data':{'date':day,'shifts':{'1':{'name':'Turno QA','registers':{'1':[session]}}}}}


class CashControlTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.store=Store(Path(t.name)/'synthetic.sqlite3');bags.initialize(self.store);cc.initialize(self.store)
        clock=patch('erp.cash_control.stamp',return_value='2026-10-16T20:00:00+00:00');clock.start();self.addCleanup(clock.stop)
    def version(self):
        with self.store.connect() as db:return db.execute('SELECT version FROM cc_settings').fetchone()[0]
    def command(self,action,**data):
        return cc.execute(self.store,action,dict(request_id=uuid4().hex,version=self.version(),confirmed=True,**data),PARTNER)
    def location(self,name,kind='register'):return self.command('location',name=name,kind=kind)['id']
    def opening(self,loc,amount,at='2026-10-16T03:00:00Z'):
        return self.command('opening',location_id=loc,amount=amount,as_of=at,evidence='Conteo físico ficticio y corte confirmado')
    def move(self,source,dest,amount=10000,kind='transfer',**extra):
        return self.command('movement',from_id=source,to_id=dest,amount=amount,kind=kind,occurred_at='2026-10-16T12:00:00Z',reference='QA-'+uuid4().hex,note='Hecho físico ficticio',**extra)
    def bag(self,**fields):
        batch=bags.execute(self.store,'create',dict(request_id=uuid4().hex,confirmed=True,quantity=1),PARTNER,ORIGIN)
        b=bags.batch_view(self.store,batch['batch_id'])['bags'][0]
        values=dict(request_id=uuid4().hex,confirmed=True,version=1,business_date='2026-10-16',register_name='Caja QA',shift_name='Turno QA',responsible='Responsable original QA',counted=84900,retained=74900,**fields)
        return bags.execute(self.store,'complete',values,CASHIER,ORIGIN,b['id'])
    def view(self,**filters):return cc.overview(self.store,{**QUERY,**{k:[v] for k,v in filters.items()}})
    def dump(self):
        with self.store.connect() as db:return list(db.iterdump())

    def test_empty_data_is_unknown_not_cash_zero_and_get_is_read_only(self):
        before=self.dump();v=self.view()
        self.assertIsNone(v['metrics']['declared']['value']);self.assertIsNone(v['metrics']['expected']['value'])
        self.assertIsNone(v['ledger']['absolute_balance']['value']);self.assertEqual(v['ledger']['locations'],[])
        self.assertEqual(self.dump(),before)

    def test_additive_migration_and_sqlite_restore_preserve_existing_bags(self):
        import sqlite3
        b=self.bag()
        with self.store.connect() as db:
            before={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in ['cash_bags','cash_bag_events','cash_bag_sequence','cash_bag_batches','cash_bag_requests']}
        cc.initialize(self.store);cc.initialize(self.store)
        destination=self.store.path.with_name('restore.sqlite3')
        with self.store.connect() as db:
            self.assertEqual(before,{table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in before})
            with sqlite3.connect(destination) as backup:db.backup(backup)
        restored=Store(destination)
        self.assertEqual(bags.bag_view(restored,b['id']),bags.bag_view(self.store,b['id']))
        self.assertEqual(cc.overview(restored,QUERY)['metrics'],self.view()['metrics'])

    def test_invalid_collection_dates_and_amount_do_not_become_open_zero(self):
        for change in [dict(closedDate='invalid'),dict(closedDate='2026-10-15T10:00:00'),dict(initialAmount=-10)]:
            self.assertEqual(src.record(self.store,'2026-10-16','collection',collection(**change),SCOPE)['state'],'error')
            self.assertEqual(self.view()['source_sessions'],[])

    def test_control_start_clamps_prior_week_without_invented_opening(self):
        self.bag();v=self.view(start='2026-10-12',end='2026-10-18')
        self.assertEqual(v['filters']['effective_start'],'2026-10-16');self.assertEqual(len(v['daily']),3)
        self.assertEqual(v['metrics']['declared']['value'],10000);self.assertIsNone(v['metrics']['expected']['value'])
        self.assertEqual(v['ledger']['locations'],[])
        self.assertEqual(self.view(start='2026-10-01',end='2026-10-15')['daily'],[])

    def test_sum_declared_and_counted_use_matching_bags_not_all_declared(self):
        one=self.bag();self.bag()
        bags.execute(self.store,'verify',dict(request_id=uuid4().hex,confirmed=True,version=2,verified_amount=9900,note='Diferencia ficticia'),PARTNER,ORIGIN,one['id'])
        v=self.view();self.assertEqual(v['metrics']['declared']['value'],20000)
        self.assertEqual(v['metrics']['counted']['value'],9900);self.assertEqual(v['metrics']['pending']['value'],10000)
        self.assertEqual(v['metrics']['difference']['value'],-100);self.assertEqual(v['metrics']['counted']['state'],'partial')
        self.assertIsNone(v['metrics']['received']['value']);self.assertIsNone(v['metrics']['expected']['value'])
        self.assertTrue(all(b['reconciliation']=='source_pending' for b in v['rows']))

    def test_test_batches_are_separate_and_manual_names_do_not_match_toteat_ids(self):
        self.bag();src.record(self.store,'2026-10-16','collection',collection(),SCOPE)
        self.assertEqual(self.view(scope='operations')['counts']['declarations'],0)
        self.assertEqual(self.view(register='Toteat:1')['counts']['declarations'],0)
        self.assertEqual(len(self.view(register='Toteat:1')['source_sessions']),1)
        self.assertEqual(len(self.view(register='Caja QA')['source_sessions']),0)

    def test_noop_save_preserves_original_version_actor_time_history(self):
        b=self.bag();before=bags.bag_view(self.store,b['id'])
        data={k:b[k] for k in bags.FIELDS};data.update(request_id=uuid4().hex,version=2,confirmed=True)
        result=bags.execute(self.store,'complete',data,PARTNER,ORIGIN,b['id'])
        after=bags.bag_view(self.store,b['id']);self.assertTrue(result['unchanged']);self.assertEqual(after,before)
        self.assertEqual(after['declared_by'],CASHIER['username'])
        self.assertEqual(bags.execute(self.store,'complete',data,PARTNER,ORIGIN,b['id']),result)

    def test_correction_requires_reason_and_preserves_original_responsible(self):
        b=self.bag();data={k:b[k] for k in bags.FIELDS};data.update(request_id=uuid4().hex,version=2,confirmed=True,counted=85000,responsible='Persona corregida QA')
        with self.assertRaises(DomainError):bags.execute(self.store,'complete',data,PARTNER,ORIGIN,b['id'])
        bags.execute(self.store,'complete',dict(data,reason='Corrección sustentada ficticia'),PARTNER,ORIGIN,b['id'])
        after=bags.bag_view(self.store,b['id']);self.assertEqual(after['original_declaration']['responsible'],'Responsable original QA')
        self.assertEqual(after['last_editor']['actor'],'Socio QA');self.assertEqual(after['last_editor']['action'],'correct')
        self.assertEqual(after['declared_by'],CASHIER['username']);self.assertEqual(after['declared_at'],b['declared_at'])
        self.assertEqual(after['history'][0]['reason'],'Corrección sustentada ficticia')

    def test_legacy_second_complete_projects_original_identity_without_rewriting_history(self):
        b=self.bag()
        with self.store.connect() as db:
            first=db.execute("SELECT * FROM cash_bag_events WHERE bag_id=? AND action='complete'",(b['id'],)).fetchone()
            original=json.loads(first['after_json']);original['declaration_identity']={'account_name':'Cajera QA','email':'qa@example.invalid'}
            db.execute('UPDATE cash_bag_events SET after_json=? WHERE id=?',(json.dumps(original),first['id']))
            later=dict(b,declared_by='Socio QA',version=3)
            db.execute('INSERT INTO cash_bag_events(bag_id,batch_id,action,actor,after_json,created_at) VALUES(?,?,?,?,?,?)',(b['id'],b['batch_id'],'complete','Socio QA',json.dumps(later),'2026-10-16T14:22:56Z'))
        before=self.dump();v=bags.bag_view(self.store,b['id'])
        self.assertEqual(v['declaration_identity']['account_name'],'Cajera QA');self.assertEqual(v['last_editor']['actor'],'Socio QA')
        self.assertEqual(self.dump(),before)

    def test_openings_are_configurable_not_seeded_and_zero_requires_confirmation(self):
        loc=self.location('Caja QA');v=self.view();self.assertIsNone(v['ledger']['locations'][0]['final_balance'])
        self.opening(loc,0);v=self.view();self.assertEqual(v['ledger']['locations'][0]['initial_balance'],0)
        self.assertEqual(v['ledger']['locations'][0]['final_balance'],0);self.assertIsNone(v['ledger']['absolute_balance']['value'])
        with self.assertRaises(DomainError):self.opening(loc,100)
        with self.assertRaises(DomainError):self.opening(self.location('Otra QA'),100,'2026-10-15T12:00:00Z')

    def test_flows_can_begin_without_opening_but_balances_remain_unknown(self):
        a=self.location('Caja QA');b=self.location('Custodia QA','custody');self.move(a,b)
        v=self.view();self.assertEqual(v['ledger']['net_recorded']['value'],0)
        self.assertTrue(all(l['final_balance'] is None for l in v['ledger']['locations']))
        self.opening(a,20000);self.opening(b,0)
        self.assertEqual({l['name']:l['final_balance'] for l in self.view()['ledger']['locations']},{'Caja QA':10000,'Custodia QA':10000})
        with self.assertRaises(DomainError):self.opening(self.location('Corte QA'),10,'2026-10-17T12:00:00Z')

    def test_internal_transfer_and_later_expense_never_double_deduct(self):
        a=self.location('Caja QA');b=self.location('Socio QA','custody');self.opening(a,50000);self.opening(b,0)
        self.move(a,b,20000);self.move(b,None,8000,'expense');self.move(b,a,3000)
        v=self.view();balances={l['name']:l['final_balance'] for l in v['ledger']['locations']}
        self.assertEqual(balances,{'Caja QA':33000,'Socio QA':9000});self.assertEqual(sum(balances.values()),42000)
        self.assertEqual(v['ledger']['net_recorded']['value'],-8000)
        with self.assertRaises(DomainError):self.move(b,None,10000,'expense')

    def test_opening_before_recorded_flows_cannot_hide_negative_history(self):
        a=self.location('Caja QA');self.move(a,None,12000,'expense');before=self.dump()
        with self.assertRaises(DomainError):self.opening(a,10000)
        self.assertEqual(before,self.dump())
        with self.assertRaises(DomainError):self.opening(a,12000,'2026-10-16T13:00:00Z')

    def test_custom_ranges_carry_balances_and_do_not_sum_daily_floats(self):
        a=self.location('Caja QA');self.opening(a,50000);self.move(a,None,8000,'expense')
        day=self.view();week=self.view(start='2026-10-12',end='2026-10-18')
        self.assertEqual(day['ledger']['locations'][0]['final_balance'],42000)
        self.assertEqual(week['ledger']['locations'][0]['final_balance'],42000)
        nextday=self.view(start='2026-10-17',end='2026-10-17')
        self.assertEqual(nextday['ledger']['locations'][0]['initial_balance'],42000)
        self.assertIsNone(nextday['ledger']['net_recorded']['value'])

    def test_idempotency_stale_versions_and_duplicate_references(self):
        a=self.location('Caja QA');self.opening(a,50000)
        body=dict(version=self.version(),request_id=uuid4().hex,confirmed=True,from_id=a,to_id=None,kind='expense',amount=10000,occurred_at='2026-10-16T12:00:00Z',reference='Comprobante QA único',note='QA')
        result=cc.execute(self.store,'movement',body,PARTNER);self.assertEqual(cc.execute(self.store,'movement',body,PARTNER),result)
        with self.assertRaises(DomainError):cc.execute(self.store,'movement',dict(body,request_id=uuid4().hex),PARTNER)
        with self.assertRaises(DomainError):cc.execute(self.store,'movement',dict(body,request_id=uuid4().hex,version=self.version()),PARTNER)
        self.assertEqual(len(self.view()['ledger']['events']),1)

    def test_concurrent_commands_only_apply_once_with_same_version(self):
        a=self.location('Caja QA');self.opening(a,50000);version=self.version()
        def task(i):
            try:return cc.execute(self.store,'movement',dict(version=version,request_id=uuid4().hex,confirmed=True,from_id=a,to_id=None,kind='expense',amount=30000,occurred_at='2026-10-16T12:00:00Z',reference='Concurrente '+str(i),note='QA'),PARTNER)
            except DomainError as error:return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(task,range(2)))
        self.assertEqual(sum(isinstance(r,dict) for r in result),1);self.assertIn(409,result)
        self.assertEqual(self.view()['ledger']['locations'][0]['final_balance'],20000)

    def test_source_observations_never_create_cash_balance_or_expected(self):
        src.record(self.store,'2026-10-16','collection',collection(),SCOPE)
        v=self.view();self.assertEqual(v['source_sessions'][0]['opening'],57200)
        self.assertEqual(v['source_sessions'][0]['cash_reported'],84900)
        self.assertIsNone(v['metrics']['expected']['value']);self.assertEqual(v['ledger']['events'],[]);self.assertEqual(v['ledger']['locations'],[])
        self.assertEqual(v['coverage']['complete_days'],0)

    def test_open_session_and_missing_cash_field_are_unknown_not_zero(self):
        src.record(self.store,'2026-10-16','collection',collection(closedDate='',paymentMethods=[]),SCOPE)
        s=self.view()['source_sessions'][0];self.assertIsNone(s['cash_reported']);self.assertIsNone(s['closed_at'])

    def test_provider_failure_retains_history_but_does_not_use_stale_totals(self):
        src.record(self.store,'2026-10-16','collection',collection(),SCOPE)
        src.record(self.store,'2026-10-16','collection',scope=SCOPE,error='Sin conexión')
        v=self.view();self.assertEqual(v['source_sessions'],[]);self.assertEqual(v['coverage']['latest_issues'][0]['reason'],'Sin conexión')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM cc_source_reads').fetchone()[0],2)

    def test_ambiguous_session_and_changed_scope_are_not_silently_accepted(self):
        p=collection();sessions=p['data']['shifts']['1']['registers']['1'];sessions.append(dict(sessions[0]))
        result=src.record(self.store,'2026-10-16','collection',p,SCOPE);self.assertEqual(result['state'],'error')
        with self.assertRaises(DomainError):src.record(self.store,'2026-10-16','collection',collection(),dict(SCOPE,local_id='2'))

    def test_sales_net_change_and_physical_tips_not_added_twice(self):
        row=dict(orderId=10000000000000000001,paymentId=10000000000000000002,registerId=1,registerName='Caja QA',change=3000,
                 gratuity=1000,totalWithGratuity=17000,paymentForms=[dict(id=1000,amount=20000,tip=1000)])
        src.record(self.store,'2026-10-16','sales',{'ok':True,'data':[row]},SCOPE)
        self.assertEqual(self.view()['source_sales'][0]['net'],17000)
        row['fiscalType']='NC';src.record(self.store,'2026-10-16','sales',{'ok':True,'data':[row]},SCOPE)
        self.assertIsNone(self.view()['source_sales'][0]['net'])

    def test_mixed_payment_and_unmapped_cash_are_not_guessed(self):
        row=dict(orderId=1,paymentId=2,registerId=1,registerName='Caja QA',change=1000,
                 paymentForms=[dict(id=1000,method='CASH',amount=10000),dict(id=5,method='CARD',amount=5000)])
        src.record(self.store,'2026-10-16','sales',{'ok':True,'data':[row]},SCOPE)
        self.assertIsNone(self.view()['source_sales'][0]['net'])
        row['paymentForms']=[dict(id=9999,method='CASH',amount=10000)];row['change']=0
        src.record(self.store,'2026-10-16','sales',{'ok':True,'data':[row]},SCOPE)
        self.assertIsNone(self.view()['source_sales'][0]['net'])

    def test_identical_source_payments_deduplicate_and_empty_is_not_complete(self):
        row=dict(orderId=1,paymentId=2,registerId=1,change=0,paymentForms=[])
        self.assertEqual(src.record(self.store,'2026-10-16','sales',{'ok':True,'data':[row,row]},SCOPE)['state'],'partial')
        src.record(self.store,'2026-10-16','sales',{'ok':True,'data':[]},SCOPE)
        v=self.view();self.assertIsNone(v['metrics']['expected']['value']);self.assertEqual(v['coverage']['complete_days'],0)

    def test_linking_session_is_explicit_and_does_not_mark_reconciled(self):
        b=self.bag();src.record(self.store,'2026-10-16','collection',collection(),SCOPE)
        session=self.view()['source_sessions'][0]
        self.command('closure-link',bag_id=b['id'],session_key=session['session_key'],read_id=session['read_id'])
        v=self.view();self.assertEqual(v['rows'][0]['reconciliation'],'source_pending')
        before=self.dump()
        with self.assertRaises(DomainError):self.command('closure-link',bag_id=b['id'],session_key=session['session_key'],read_id=session['read_id'])
        self.assertEqual(before,self.dump())
        src.record(self.store,'2026-10-16','collection',collection(initialAmount=60000),SCOPE)
        revised=self.view()['rows'][0]
        self.assertEqual(revised['reconciliation'],'source_changed');self.assertIsNone(revised['linked_cash_reported'])
        self.assertEqual(revised['closure_link']['source_read_id'],session['read_id'])

    def test_invalid_filters_inputs_and_roles(self):
        for q in [dict(start='2026-02-30'),dict(start='2026-10-18',end='2026-10-16'),dict(start='2020-01-01'),dict(scope='all')]:
            with self.assertRaises(DomainError):self.view(**q)
        with self.assertRaises(DomainError):cc.overview(self.store,{'start':['2026-10-16','2026-10-17']})
        with self.assertRaises(DomainError):cc.execute(self.store,'location',dict(request_id=uuid4().hex,confirmed=True,version=1,name='QA',kind='safe'),CASHIER)
        a=self.location('Caja QA')
        for amount in [True,-1,1.1,None]:
            with self.assertRaises(DomainError):self.opening(a,amount)
        with self.assertRaises(DomainError):self.opening(a,1,'2026-10-16T12:00:00')

    def test_custody_payment_link_has_no_second_money_effect(self):
        a=self.location('Custodia QA','custody');self.opening(a,20000);event=self.move(a,None,8000,'expense')
        with self.store.connect() as db:
            db.execute("INSERT INTO ap_suppliers(id,name,created_at,updated_at) VALUES(1,'Proveedor QA','QA','QA')")
            db.execute("INSERT INTO ap_account_entries(id,supplier_id,kind,amount,currency,occurred_on,method,actor,created_at) VALUES(1,1,'payment',8000,'CLP','2026-10-16','cash','QA','QA')")
        before=self.view()['ledger']['net_recorded']['value']
        self.command('payment-link',event_id=event['id'],payment_source='ap_account_entries',payment_id=1,amount=8000)
        self.assertEqual(self.view()['ledger']['net_recorded']['value'],before)
        self.assertEqual(self.view()['ledger']['locations'][0]['final_balance'],12000)
        with self.assertRaises(DomainError):self.command('payment-link',event_id=event['id'],payment_source='ap_account_entries',payment_id=1,amount=1)
        with self.store.connect() as db:db.execute("UPDATE ap_account_entries SET voided_at='QA' WHERE id=1")
        self.assertEqual(self.view()['ledger']['events'][0]['payment_links'][0]['state'],'payment_requires_review')
        self.assertEqual(self.view()['ledger']['locations'][0]['final_balance'],12000)


if __name__=='__main__':unittest.main()
