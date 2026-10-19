from copy import deepcopy
from uuid import uuid4
import unittest
import test_cash_insights as legacy
import test_cashier_v2 as v2base
from test_cash_closures import PARTNER,CASHIER,filled
from erp import cash_insights as ci,cash_control_sources as sources,cash_review as review,cashier_v2 as v2

DAY='2026-10-16'

class PopulationTests(unittest.TestCase):
    setUp=legacy.InsightTests.setUp;cmd=legacy.InsightTests.cmd;bag=legacy.InsightTests.bag;create=legacy.InsightTests.create
    snapshot=legacy.InsightTests.snapshot;review=legacy.InsightTests.review;context=legacy.InsightTests.context
    dashboard=legacy.InsightTests.dashboard;save=legacy.InsightTests.save;reviewed=legacy.InsightTests.reviewed

    def test_known_open_friday_without_closure_is_pending_not_zero_or_a_cashier_failure(self):
        sources.record(self.store,DAY,'collection',legacy.source(closed=None),legacy.SCOPE)
        before=self.snapshot();v=self.dashboard();self.assertEqual(before,self.snapshot())
        self.assertEqual((v['population']['total'],v['population']['known_sessions'],v['population']['without_linked_closure']),(0,1,1))
        self.assertEqual(v['known_sessions'][0]['state'],'open_without_link');self.assertIsNone(v['known_sessions'][0]['responsible'])
        self.assertIsNone(v['current']['cash']['shortage']);self.assertIsNone(v['current']['cash']['shortage_frequency_percent']);self.assertEqual(v['operations']['pending'],[])

    def test_post_start_date_does_not_claim_complete_previous_coverage(self):
        sources.record(self.store,DAY,'collection',legacy.source(),legacy.SCOPE)
        v=self.dashboard(compare_start=DAY,compare_end=DAY)
        self.assertFalse(v['previous_full_coverage']);self.assertFalse(v['previous_coverage']['complete'])
        self.assertEqual(v['previous_coverage']['days_with_observations'],1)

    def test_cash_shortage_and_mp_surplus_do_not_offset_and_use_separate_denominators(self):
        sources.record(self.store,DAY,'collection',legacy.source(total=1000),legacy.SCOPE)
        d=filled();d['opening']['amount']+=10;d['difference_explanation']='Diferencia sintética de efectivo revisada'
        c=self.cmd('submit',self.review(self.create(d)),as_user=CASHIER)
        ctx=self.context(c)
        self.save(dict(request_id=uuid4().hex,confirmed=True,closure_id=c['id'],source_token=ctx['token'],session_key=ctx['reference']['session']['session_key'],
            terminal_id='QA-TERMINAL',reference='QA-CUPON',period_start=DAY+'T11:00:00Z',period_end=DAY+'T18:00:00Z',total=1010,basis='gross_with_tips',
            scope_verified=True,adjustments_verified=True,reviewed=True,note='Período sintético coincidente'))
        self.reviewed(c);self.create(shift='Borrador conocido')
        v=self.dashboard();self.assertEqual(v['current']['cash']['shortage'],10);self.assertEqual(v['current']['mp']['surplus'],10)
        self.assertEqual(v['current']['cash']['surplus'],0);self.assertEqual(v['current']['mp']['shortage'],0)
        self.assertEqual(v['current']['cash']['shortage_frequency_percent'],100);self.assertEqual(v['current']['mp']['surplus_frequency_percent'],100)
        self.assertEqual((v['population']['total'],v['population']['presented'],v['population']['reviewed']),(2,1,1))
        self.assertEqual(v['current']['cash']['reviewed_known'],1);self.assertEqual(v['current']['cash']['records'],2)
        self.assertEqual(v['rows'][0]['owner']['key'],'erp:cajera-qa')


class V2PopulationTests(unittest.TestCase):
    setUp=v2base.V2Tests.setUp;cmd=v2base.V2Tests.cmd;bag=v2base.V2Tests.bag;snapshot=v2base.V2Tests.snapshot
    do=v2base.V2Tests.do;new=v2base.V2Tests.new;saved=v2base.V2Tests.saved;submit=v2base.V2Tests.submit
    photo=v2base.V2Tests.photo;one_source=v2base.V2Tests.one_source;reference=v2base.V2Tests.reference;terminal_reference=v2base.V2Tests.terminal_reference

    def dashboard(self):return ci.overview(self.store,dict(start=[DAY],end=[DAY]),PARTNER)
    def human(self,c):
        detail=review.get_detail(self.store,c['id'],PARTNER)
        return review.execute(self.store,'review',dict(request_id=uuid4().hex,confirmed=True,closure_id=c['id'],fingerprint=detail['fingerprint'],note='Revisión humana QA; pendientes documentados'),PARTNER)

    def test_v2_terminals_are_not_omitted_or_duplicated_by_reference_reports(self):
        self.one_source();c=self.saved();d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS}
        d['terminals']=[dict(terminal_id=n,total=amount,period_start=DAY+'T11:00:00Z',period_end=DAY+'T17:00:00Z',scope_confirmed=True) for n,amount in [('QA-1',1100),('QA-2',900)]]
        d['note']='Dos diferencias sintéticas, conservadas separadamente';c=self.do('save',c,declaration=d)
        self.terminal_reference(c,'QA-1',1000);self.terminal_reference(c,'QA-2',1000)
        c=self.submit(v2.view(self.store,c['id'],CASHIER));signed=deepcopy(c['frozen_submission']);self.human(c)
        before=self.snapshot();v=self.dashboard();self.assertEqual(before,self.snapshot());self.assertEqual(len(v['mp_rows']),2)
        self.assertEqual((v['current']['mp']['shortage'],v['current']['mp']['surplus']),(100,100))
        self.assertEqual(v['current']['mp']['reviewed_known'],2);self.assertEqual(v['current']['mp']['shortage_frequency_percent'],50)
        self.assertEqual(v['population']['presented'],1);self.assertEqual(v['population']['terminal_observations'],2)
        self.assertEqual(v2.view(self.store,c['id'],CASHIER)['frozen_submission'],signed)

    def test_late_comparison_updates_analysis_without_changing_signed_declaration(self):
        self.one_source();c=self.reference(self.saved());d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS};d['note']='Diferencia sintética original -500';c=self.submit(self.do('save',c,declaration=d))
        signed=deepcopy(c['frozen_submission']);self.human(c);self.assertEqual(self.dashboard()['current']['cash']['shortage'],500)
        self.do('cash-reference',c,who=PARTNER,opening=1000,net_sales=3500,movement_net=-500,basis='independent_net_sales_before_movements',evidence='Corte sintético posterior revisado',attachment_id=c['summary']['cash_reference']['attachment_id'],opening_verified=True,sales_verified=True,movements_verified=True)
        c=self.do('compare',c,who=PARTNER);self.assertEqual(self.dashboard()['current']['cash']['reviewed_known'],0)
        self.human(c);v=self.dashboard();self.assertEqual(v['current']['cash']['shortage'],0);self.assertEqual(v['rows'][0]['comparison_origin'],'late_comparison')
        self.assertEqual(v2.view(self.store,c['id'],CASHIER)['frozen_submission'],signed)

    def test_different_terminal_period_remains_pending(self):
        self.one_source();c=self.saved();d={k:deepcopy(c[k]) for k in v2.DECLARATION_FIELDS}
        d['terminals']=[dict(terminal_id='QA-1',total=1100,period_start=DAY+'T11:00:00Z',period_end=DAY+'T16:00:00Z',scope_confirmed=True)]
        c=self.do('save',c,declaration=d);self.terminal_reference(c,'QA-1',1000);c=self.submit(v2.view(self.store,c['id'],CASHIER));self.human(c)
        v=self.dashboard();self.assertIsNone(v['mp_rows'][0]['difference']);self.assertEqual(v['population']['comparable_mp'],0);self.assertIsNone(v['current']['mp']['surplus'])

    def test_overlapping_terminal_periods_are_pending_but_adjacent_cuts_are_allowed(self):
        from erp.cash_analytics_population import mp_rows
        base=dict(closure_id='a',date=DAY,terminal_id='Terminal QA',period_start=DAY+'T11:00:00Z',period_end=DAY+'T17:00:00Z',comparable=True,eligible=True,reasons=[])
        def observations(start):
            return [dict(id='a',is_v2=True,mp_observations=[dict(deepcopy(base),id='a')]),dict(id='b',is_v2=True,mp_observations=[dict(deepcopy(base),id='b',closure_id='b',period_start=start,period_end=DAY+'T18:00:00Z')])]
        overlapped=mp_rows(observations(DAY+'T16:00:00Z'),[])
        self.assertTrue(all(not r['eligible'] and not r['comparable'] for r in overlapped))
        adjacent=mp_rows(observations(DAY+'T17:00:00Z'),[])
        self.assertTrue(all(r['eligible'] for r in adjacent))

if __name__=='__main__':unittest.main()
