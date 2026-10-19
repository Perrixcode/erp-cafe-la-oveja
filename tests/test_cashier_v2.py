from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import base64,json,sqlite3,unittest
from PIL import Image
from erp import cashier_v2 as v2,cash_closures as cl,cash_bags as bags,cash_control_sources as sources,cash_insights as insights
from erp.domain import DomainError
from erp.store import Store
from test_cash_closures import ClosureTests,PARTNER,CASHIER,OTHER,movement
from test_cash_insights import source,SCOPE,DAY
from erp.backups import create_backup,restore_backup,verify_backup

class V2Tests(unittest.TestCase):
 setUp=ClosureTests.setUp;cmd=ClosureTests.cmd;bag=ClosureTests.bag;snapshot=ClosureTests.snapshot
 def do(self,action,c=None,who=CASHIER,**values):
  return v2.command(self.store,action,dict(request_id=uuid4().hex,confirmed=True,**({'version':c['version']} if c else {}),**values),who,c['id'] if c else None)
 def new(self,**extra):return self.do('create',identity=dict(business_date=DAY,register_id=self.reg),**extra)
 def saved(self,c=None):
  c=c or self.new();d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['cash']={k:0 for k in d['cash']};d['retained']=dict(d['cash']);d['cash']['1000']=4;d['retained']['1000']=1;d['bagNumber']=self.bag()['label'];d['missing_evidence_reasons']={'mp':'Impresora MP sin papel; pendiente de recuperación','toteat':'Cierre administrativo pendiente de revisar'}
  return self.do('save',c,declaration=d)
 def submit(self,c):
  review=self.do('review',c)
  return self.do('submit',c,review_token=review['token'],strokes=[[[.1,.2],[.3,.4],[.5,.2]]],consent=True)
 def photo(self,c,kind='mp'):
  b=BytesIO();Image.new('RGB',(8,12),'green').save(b,format='PNG')
  return self.do('attach',c,kind=kind,file_name='ticket-qa.png',file_type='image/png',file_data=base64.b64encode(b.getvalue()).decode())
 def one_source(self):return sources.record(self.store,DAY,'collection',source(),SCOPE)
 def reference(self,c):
  photo=(c.get('photos') or [None])[0] if c['status']=='submitted' else self.photo(c,'toteat')
  return self.do('cash-reference',c,who=PARTNER,opening=1000,net_sales=4000,movement_net=-500,basis='independent_net_sales_before_movements',evidence='Reporte independiente QA del corte exacto, componentes revisados',attachment_id=photo['id'],opening_verified=True,sales_verified=True,movements_verified=True)
 def test_multiple_deliveries_same_day_register_and_empty_is_not_zero(self):
  a,b=self.new(),self.new();self.assertNotEqual(a['id'],b['id']);self.assertIsNone(a['summary']['A']);self.assertIsNone(a['summary']['mp_total']);self.assertIsNone(a['summary']['expected'])
 def test_weekend_not_new_operational_day_and_friday_remains_open(self):
  c=self.new();self.assertEqual(c['identity']['business_date'],'2026-10-16')
  for day in ('2026-10-17','2026-10-18'):
   with self.assertRaises(DomainError):self.do('create',identity=dict(business_date=day,register_id=self.reg))
 def test_b_subset_strict_integers_unknown_and_negative_rejected(self):
  c=self.new()
  for bad in (-1,True,'1',1.2,float('inf')):
   d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['cash']['1000']=bad
   with self.assertRaises((DomainError,ValueError)):self.do('save',c,declaration=d)
  d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['cash']['1000']=0;d['retained']['1000']=1
  with self.assertRaises(DomainError):self.do('save',c,declaration=d)
 def test_bag_is_A_minus_B_no_double_movement_and_legacy_history_kept(self):
  c=self.new();m=movement()
  with self.store.connect() as db:
   row=cl.get(db,c['id'],CASHIER);legacy=json.loads(row['data_json']);legacy['movements']=[m];db.execute('UPDATE cl_closures SET data_json=? WHERE id=?',(json.dumps(legacy),c['id']))
  c=self.submit(self.saved(c));self.assertEqual([c['summary'][k] for k in ('A','B','C')],[4000,1000,3000]);self.assertEqual(c['bag']['bag_amount'],3000)
  old=cl.view(self.store,c['id'],CASHIER);self.assertEqual(old['data']['movements'],[m]);self.assertEqual(old['calculation']['C'],3000);self.assertEqual(old['calculation']['post_count_net'],0)
 def test_source_auto_readonly_collection_cash_never_expected(self):
  self.one_source();c=self.saved();self.assertIsNotNone(c['identity']['session_key']);self.assertEqual(c['summary']['selected_session']['methods'][0]['reported_total'],134100);self.assertIsNone(c['summary']['expected'])
  forged={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};forged['expected']=0
  with self.assertRaises(DomainError):self.do('save',c,declaration=forged)
 def test_ambiguous_session_keeps_pending(self):
  from test_cash_toteat import collection
  sources.record(self.store,DAY,'collection',collection(),SCOPE);c=self.saved();self.assertIsNone(c['identity']['session_key']);self.assertIsNone(c['summary']['expected'])
 def test_photo_durable_digest_metadata_ownership_and_content_validation(self):
  c=self.saved();p=self.photo(c);d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['photos']=[dict(p,legible=True)];c=self.do('save',c,declaration=d);c=self.submit(c)
  reopened=v2.view(self.store,c['id'],CASHIER);self.assertEqual(reopened['photos'][0]['sha256'],p['sha256']);self.assertEqual(reopened['signature']['snapshot_hash'],c['signature']['snapshot_hash'])
  other=self.new();d={k:deepcopy(other[k]) for k in v2.DECLARATION_FIELDS};d['photos']=[dict(p,legible=True)]
  with self.assertRaises(DomainError):self.do('save',other,declaration=d)
  with self.assertRaises(DomainError):self.do('attach',other,kind='mp',file_name='fake.png',file_type='image/png',file_data=base64.b64encode(b'\x89PNG\r\n\x1a\nnot-an-image').decode())
 def test_missing_individual_evidence_reason_blocks_review(self):
  c=self.saved();d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['missing_evidence_reasons']['mp']='';c=self.do('save',c,declaration=d)
  with self.assertRaises(DomainError):self.do('review',c)
 def test_signature_snapshot_server_identity_immutable_and_no_postings(self):
  before=self.snapshot(['cc_cash_events']);c=self.submit(self.saved());self.assertEqual(c['signature']['signer_key'],'erp:cajera-qa');self.assertEqual(c['deposit_status'],'Depósito pendiente');self.assertIsNone(c['bag']['verified_amount']);self.assertEqual(c['delivery']['state'],'prepared');self.assertEqual(before,self.snapshot(['cc_cash_events']))
  with self.store.connect() as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE cv2_submissions SET folio='altered'")
  self.assertEqual(c['frozen_submission']['summary']['C'],3000)
 def test_review_invalidated_after_edit_and_revert_unchanged_save_preserves(self):
  c=self.saved();r=self.do('review',c);d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};same=self.do('save',c,declaration=d);self.assertEqual(same['version'],c['version'])
  d['note']='Corrección';changed=self.do('save',same,declaration=d);d['note']='';back=self.do('save',changed,declaration=d)
  with self.assertRaises(DomainError):self.do('submit',back,review_token=r['token'],strokes=[[[0,0],[1,1]]],consent=True)
 def test_submit_requires_explicit_consent_and_real_strokes(self):
  c=self.saved();r=self.do('review',c)
  for strokes,consent in [([],True),([[[0,0],[0,0]]],True),([[[0,0],[1,1]]],False),([[[0,0],[2,1]]],True)]:
   with self.assertRaises(DomainError):self.do('submit',c,review_token=r['token'],strokes=strokes,consent=consent)
 def test_concurrent_submit_idempotent_and_key_payload_mismatch_rejected(self):
  c=self.saved();r=self.do('review',c);p=dict(request_id=uuid4().hex,version=c['version'],confirmed=True,review_token=r['token'],strokes=[[[0,0],[1,1]]],consent=True)
  with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:v2.command(self.store,'submit',p,CASHIER,c['id']),range(2)))
  self.assertEqual(results[0],results[1]);self.assertEqual(len(results[0]['submitted_history']),1)
  with self.assertRaises(DomainError):v2.command(self.store,'submit',dict(p,consent=False),CASHIER,c['id'])
 def test_failed_commit_rolls_back_bag_and_submission(self):
  c=self.saved();r=self.do('review',c);before=self.snapshot()
  with patch('erp.cashier_v2.event',side_effect=RuntimeError('Synthetic failure')):
   with self.assertRaises(RuntimeError):self.do('submit',c,review_token=r['token'],strokes=[[[0,0],[1,1]]],consent=True)
  self.assertEqual(before,self.snapshot())
 def test_deposit_separate_idempotent_no_reception_and_blocks_correction(self):
  c=self.submit(self.saved());p=dict(request_id=uuid4().hex,version=c['version'],confirmed=True,amount=3000,bag_number=c['bagNumber'],deposit_confirmed=True)
  d=v2.command(self.store,'deposit',p,CASHIER,c['id']);self.assertEqual(d,v2.command(self.store,'deposit',p,CASHIER,c['id']));self.assertEqual(d['delivery']['state'],'prepared');self.assertIsNone(d['bag']['verified_amount'])
  with self.assertRaises(DomainError):self.do('revise',d,reason='Cambio después del depósito')
  with self.assertRaises(DomainError):self.do('deposit',d,amount=1,bag_number=d['bagNumber'],deposit_confirmed=True)
 def test_correction_new_signature_preserves_previous_snapshot(self):
  c=self.submit(self.saved());original=deepcopy(c['submitted_history'][0]);c=self.do('revise',c,reason='Recuento antes del depósito');self.assertIsNone(c['signature']);d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['cash']['1000']=5;c=self.do('save',c,declaration=d);c=self.submit(c)
  self.assertEqual(c['submitted_history'][0],original);self.assertEqual(c['signature']['version'],2);self.assertNotEqual(c['signature']['snapshot_hash'],original['snapshot_hash']);self.assertEqual(c['summary']['C'],4000)
 def test_custody_legacy_actions_keep_signed_amount_and_prevent_old_mutation(self):
  c=self.submit(self.saved());signed=deepcopy(c['frozen_submission']);old=cl.view(self.store,c['id'],CASHIER)
  for action in ('save','submit','review','attach','import-source'):
   with self.assertRaises(DomainError):self.cmd(action,old,as_user=PARTNER)
  old=self.cmd('deliver',old,as_user=CASHIER);old=self.cmd('receive',old);old=self.cmd('count',old,verified_amount=2900,reason='Conteo administrativo QA')
  c=v2.view(self.store,c['id'],CASHIER);self.assertEqual(c['frozen_submission'],signed);self.assertEqual(c['bag']['verified_difference'],-100);self.assertFalse(c['can_revise'])
 def test_cash_reference_exact_scope_requires_comment_and_late_source_freezes_signature(self):
  self.one_source();c=self.saved();c=self.reference(c);self.assertEqual(c['summary']['expected'],4500);self.assertEqual(c['summary']['difference'],-500)
  with self.assertRaises(DomainError):self.do('review',c)
  d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['note']='Reconté y mantengo una diferencia de $500';c=self.do('save',c,declaration=d);c=self.submit(c);frozen=deepcopy(c['frozen_submission'])
  self.do('cash-reference',c,who=PARTNER,opening=1000,net_sales=3500,movement_net=-500,basis='independent_net_sales_before_movements',evidence='Reporte QA actualizado y revisado',attachment_id=c['summary']['cash_reference']['attachment_id'],opening_verified=True,sales_verified=True,movements_verified=True)
  c=self.do('compare',c,who=PARTNER);self.assertEqual(c['frozen_submission'],frozen);self.assertEqual(c['events'][-1]['detail']['comparison']['difference'],0)
 def test_cash_reference_never_accepts_client_collection_or_role(self):
  self.one_source();c=self.saved();p=self.photo(c)
  body=dict(opening=1000,net_sales=4000,movement_net=-500,basis='collection.cash',evidence='QA',attachment_id=p['id'],opening_verified=True,sales_verified=True,movements_verified=True)
  for who in (CASHIER,PARTNER):
   with self.assertRaises(DomainError):self.do('cash-reference',c,who=who,**body)
 def test_role_isolation_revoked_identity_and_feature_pause_preserves_read(self):
  c=self.saved()
  with self.assertRaises(DomainError):v2.view(self.store,c['id'],OTHER)
  with self.assertRaises(DomainError):v2.command(self.store,'review',dict(request_id=uuid4().hex,confirmed=True,version=c['version']),CASHIER,c['id'],lambda db:False)
  with patch.dict('os.environ',{'OVEJA_CASHIER_V2':'0'}):
   self.assertEqual(v2.view(self.store,c['id'],CASHIER)['id'],c['id'])
   with self.assertRaises(DomainError):self.do('review',c)
 def test_chile_ambiguous_nonexistent_and_explicit_offset(self):
  for value in ('2026-09-06T00:30','2026-04-04T23:30','not-date'):
   with self.assertRaises(DomainError):v2.period(value)
  self.assertEqual(v2.period('2026-10-16T12:00'),'2026-10-16T15:00:00+00:00')
  self.assertEqual(v2.period('2026-10-16T15:00:00Z'),'2026-10-16T15:00:00+00:00')
 def test_terminal_zero_unknown_duplicates_and_no_required_tips(self):
  c=self.saved();d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['terminals']=[dict(terminal_id='Terminal QA',total=0,period_start=None,period_end=None,scope_confirmed=False)]
  c=self.do('save',c,declaration=d);self.assertEqual(c['summary']['mp_total'],0);self.assertIsNone(c['summary']['terminals'][0]['difference']);self.submit(c)
  d['terminals'].append(dict(d['terminals'][0],terminal_id='  TERMINAL   qa '))
  with self.assertRaises(DomainError):self.do('save',self.saved(),declaration=d)
 def test_source_change_between_review_and_submit_requires_review(self):
  self.one_source();c=self.saved();r=self.do('review',c);sources.record(self.store,DAY,'collection',source(total=1),SCOPE)
  with self.assertRaises(DomainError):self.do('submit',c,review_token=r['token'],strokes=[[[0,0],[1,1]]],consent=True)
 def test_get_readonly_and_pdf_no_mutation(self):
  from erp.cashier_v2_pdf import render
  c=self.submit(self.saved());before=self.snapshot();v2.view(self.store,c['id'],CASHIER)
  with self.store.connect() as db:pdf=render(db,c)
  self.assertTrue(pdf.startswith(b'%PDF-'));self.assertEqual(before,self.snapshot())
 def test_backup_restore_includes_signatures_photos_and_events(self):
  c=self.saved();p=self.photo(c);d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['photos']=[dict(p,legible=True)];c=self.submit(self.do('save',c,declaration=d))
  with TemporaryDirectory() as folder:
   root=Path(folder)/'erp';(root/'data').mkdir(parents=True)
   with self.store.connect() as src,sqlite3.connect(root/'data/erp-demo.sqlite3') as dst:src.backup(dst)
   backup=create_backup(root);restored=Path(folder)/'restored';restore_backup(backup,restored);self.assertEqual(verify_backup(backup),verify_backup(restored))
   recovered=Store(restored/'data/erp-demo.sqlite3');self.assertEqual(v2.view(recovered,c['id'],CASHIER)['frozen_submission'],c['frozen_submission'])
 def test_tampered_signature_or_photo_detected(self):
  c=self.submit(self.saved())
  with self.store.connect() as db:db.execute('DROP TRIGGER cv2_submissions_no_update');db.execute("UPDATE cv2_submissions SET signature_json='{}'")
  with self.assertRaises(DomainError):v2.view(self.store,c['id'],CASHIER)

 def test_same_source_poll_does_not_invalidate_review(self):
  self.one_source();c=self.saved();r=self.do('review',c);self.one_source();c=self.do('submit',c,review_token=r['token'],strokes=[[[0,0],[1,1]]],consent=True);self.assertEqual(c['status'],'submitted')
 def terminal_reference(self,c,name,total):
  photo=self.photo(c,'toteat')
  with self.store.connect() as db:ctx=insights.context_db(db,c['id'])
  return insights.save_terminal(self.store,dict(request_id=uuid4().hex,confirmed=True,closure_id=c['id'],source_token=ctx['token'],session_key=ctx['reference']['session']['session_key'],terminal_id=name,reference='QA-'+name,period_start=DAY+'T11:00:00Z',period_end=DAY+'T17:00:00Z',total=total,basis='gross_with_tips',scope_verified=True,adjustments_verified=True,reviewed=True,note='Synthetic independent cut',attachment_id=photo['id'],economic_scope=dict(kind='verified_cut',start=DAY+'T11:00:00Z',end=DAY+'T17:00:00Z',toteat_total=total,reference='Synthetic independent terminal cut',verified=True)),PARTNER)
 def test_terminal_differences_cannot_cancel_and_admin_preserves_each(self):
  from erp import cash_review
  self.one_source();c=self.saved();d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['terminals']=[dict(terminal_id=n,total=total,period_start=DAY+'T11:00:00Z',period_end=DAY+'T17:00:00Z',scope_confirmed=True) for n,total in [('QA-1',1100),('QA-2',900)]];c=self.do('save',c,declaration=d)
  self.terminal_reference(c,'QA-1',1000);self.terminal_reference(c,'QA-2',1000);c=v2.view(self.store,c['id'],CASHIER)
  self.assertEqual([t['difference'] for t in c['summary']['terminals']],[100,-100])
  with self.assertRaises(DomainError):self.do('review',c)
  d['note']='Reconteo con dos diferencias independientes';c=self.do('save',c,declaration=d);c=self.submit(c)
  admin=cash_review.get_detail(self.store,c['id'],PARTNER);self.assertTrue(admin['has_differences']);self.assertEqual(admin['payment_comparison']['totals']['absolute_differences'],200);self.assertEqual(admin['payment_comparison']['totals']['difference'],0)
  overview=cash_review.overview_db
  with self.store.connect() as db:r=overview(db,{'start':[DAY],'end':[DAY]},PARTNER)
  self.assertFalse(any(a['kind']=='session_link_pending' for a in r['alerts']))
 def test_metadata_tamper_rejected_even_with_unchanged_snapshot(self):
  c=self.submit(self.saved())
  with self.store.connect() as db:db.execute('DROP TRIGGER cv2_submissions_no_update');db.execute("UPDATE cv2_submissions SET folio='wrong'")
  with self.assertRaises(DomainError):v2.view(self.store,c['id'],CASHIER)

 def test_late_source_resolves_missing_session_without_rewriting_signature(self):
  c=self.saved();p=self.photo(c,'toteat');d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['photos']=[dict(p,legible=True)];c=self.submit(self.do('save',c,declaration=d));frozen=deepcopy(c['frozen_submission']);self.assertIsNone(c['identity']['session_key'])
  self.one_source();self.reference(c);c=self.do('compare',c,who=PARTNER);self.assertEqual(c['frozen_submission'],frozen);self.assertIsNone(c['summary']['expected']);self.assertEqual(c['live_summary']['expected'],4500);self.assertEqual(c['events'][-1]['detail']['comparison']['difference'],-500)
 def test_late_reference_photo_requires_admin_and_preserves_signed_photos(self):
  c=self.submit(self.saved());frozen=deepcopy(c['frozen_submission']);b=BytesIO();Image.new('RGB',(7,9),'red').save(b,format='PNG');args=dict(kind='toteat',file_name='late-qa.png',file_type='image/png',file_data=base64.b64encode(b.getvalue()).decode())
  with self.assertRaises(DomainError):self.do('reference-attach',c,**args)
  self.do('reference-attach',c,who=PARTNER,**args);after=v2.view(self.store,c['id'],CASHIER);self.assertEqual(after['frozen_submission'],frozen);self.assertEqual(after['photos'],[]);self.assertEqual(len(after['reference_attachments']),1)

 def test_signature_comment_and_comparison_do_not_mark_admin_reviewed(self):
  from erp import cash_review
  self.one_source();c=self.saved();c=self.reference(c);d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['note']='Diferencia declarada, aún no aceptada';c=self.submit(self.do('save',c,declaration=d));c=self.do('compare',c,who=PARTNER)
  detail=cash_review.get_detail(self.store,c['id'],PARTNER);self.assertFalse(detail['reviewed']);self.assertEqual(detail['resolution_state'],'open');self.assertTrue(detail['has_differences']);self.assertEqual(detail['cashier_v2']['note'],d['note']);self.assertIsNotNone(detail['cashier_v2']['signature'])
  with self.store.connect() as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM cr_reviews').fetchone()[0],0);self.assertEqual(db.execute('SELECT COUNT(*) FROM cr_difference_events').fetchone()[0],0)

 def test_admin_pending_human_review_version_and_late_evidence(self):
  from erp import cash_review
  c=self.submit(self.saved());signed=deepcopy(c['frozen_submission']);admin=cash_review.get_detail(self.store,c['id'],PARTNER)
  codes={p['code'] for p in admin['v2_pending']};self.assertTrue({'photo_mp','photo_toteat','cash_source','deposit','receipt','count','human_review'}<=codes)
  admin=cash_review.execute(self.store,'review',dict(request_id=uuid4().hex,confirmed=True,closure_id=c['id'],fingerprint=admin['fingerprint'],note='Revisión humana QA con fuentes aún pendientes'),PARTNER)
  self.assertTrue(admin['reviewed']);h=admin['v2_human_review'];self.assertEqual(h['actor'],cl.actor(PARTNER));self.assertTrue(h['created_at']);self.assertEqual(h['detail']['closure_version'],c['version']);self.assertEqual(h['detail']['snapshot_hash'],signed['signature']['snapshot_hash']);self.assertEqual(h['detail']['submission_version'],1)
  self.one_source();late=cash_review.get_detail(self.store,c['id'],PARTNER);self.assertFalse(late['reviewed']);self.assertIn('late_review',{p['code'] for p in late['v2_pending']});self.assertEqual(v2.view(self.store,c['id'],CASHIER)['frozen_submission'],signed)
  with self.store.connect() as db:panel=cash_review.overview_db(db,{'start':[DAY],'end':[DAY]},PARTNER)
  self.assertTrue(any(p['closure_id']==c['id'] and p['code']=='receipt' for p in panel['v2_pending_tasks']))

if __name__=='__main__':unittest.main()
