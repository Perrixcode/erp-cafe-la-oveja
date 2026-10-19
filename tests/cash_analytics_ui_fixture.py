"""Synthetic API responses for terminal-only UI contract checks."""
import json
from test_cash_balance import BalanceTests
from test_cash_analytics_population import PopulationTests
from erp import cash_control as cc

balance=BalanceTests();balance.setUp()
insights=PopulationTests();insights.setUp()
try:
    query=dict(start=['2026-10-16'],end=['2026-10-16'],scope=['operations'])
    unknown=cc.overview(balance.store,query)
    balance.origin();balance.sales();location=balance.location('Caja FICTICIA');balance.move(location,None,20,kind='expense')
    configured=cc.overview(balance.store,query)
    insights.test_cash_shortage_and_mp_surplus_do_not_offset_and_use_separate_denominators()
    print(json.dumps(dict(unknown=unknown,configured=configured,analysis=insights.dashboard()),ensure_ascii=False))
finally:
    insights.doCleanups();balance.doCleanups()
