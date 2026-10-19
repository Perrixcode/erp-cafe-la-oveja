"""One bounded source refresh per existing reader cycle, using its existing credential."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import fcntl, json
from erp.store import Store
from erp import cash_toteat as ct, cash_control_sources as sources, cash_business_day as calendar
from scripts.probe_toteat_comments import ProbeFailure
from scripts.toteat_sales_worker import configuration
from erp.toteat_transport import fetch


def sync_cycle(root,reader=fetch):
 root=Path(root);store=Store(root/'data/erp-demo.sqlite3');at=ct.now()
 # Same file lock across web-triggered requests, script runs and reader workers.
 with (root/'private/toteat-closure-sync.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return {'state':'busy'}
  config=configuration(root)
  if not config or config.get('transport')!='systemd':
   with store.connect() as db:db.execute("UPDATE cl_toteat_sync SET state='error',finished_at=?,error='existing_integration_unavailable' WHERE state IN ('queued','running')",(at,))
   return {'state':'unavailable'}
  with store.connect() as db:
   db.execute('BEGIN IMMEDIATE')
   # A provider authentication/throttling rejection applies to the shared scope.
   blocked=db.execute("SELECT retry_at FROM cl_toteat_sync WHERE error IN ('provider_not_authorized','http_401','http_403','http_429') AND retry_at>? ORDER BY retry_at DESC LIMIT 1",(at,)).fetchone()
   if blocked:return {'state':'cooldown','retry_at':blocked[0]}
   row=db.execute("SELECT * FROM cl_toteat_sync WHERE (state='queued' OR (state='running' AND started_at<?)) AND (retry_at IS NULL OR retry_at<=?) ORDER BY requested_at LIMIT 1",((datetime.fromisoformat(at)-timedelta(minutes=5)).isoformat(),at)).fetchone()
   if row:business_day=row['business_date']
   else:
    today=calendar.operational_day(datetime.now(ZoneInfo('America/Santiago')).date());business_day=None
    candidates=[(today,180),(calendar.previous_day(today),600)]
    # Keep older observed open operating days fresh; never fabricate weekend turns.
    for read in sources.latest(db,(today-timedelta(days=14)).isoformat(),today.isoformat()):
     d=datetime.fromisoformat(read['business_date']).date()
     if read['endpoint']=='collection' and read['state']!='error' and calendar.is_operational(d) and any(not x.get('closed_at') for x in json.loads(read['normalized_json']).get('sessions',[])) and d not in [x[0] for x in candidates]:candidates.append((d,600))
    for d,interval in candidates:
     if d.isoformat()<'2026-10-16':continue
     prior=db.execute('SELECT * FROM cl_toteat_sync WHERE business_date=?',(d.isoformat(),)).fetchone()
     if prior and ((prior['retry_at'] and prior['retry_at']>at) or (prior['finished_at'] and ct.age(prior['finished_at'])<interval)):continue
     business_day=d.isoformat();break
    if not business_day:return {'state':'fresh'}
   db.execute("INSERT INTO cl_toteat_sync(business_date,requested_at,state,started_at) VALUES(?,?,'running',?) ON CONFLICT(business_date) DO UPDATE SET state='running',started_at=excluded.started_at",(business_day,at,at))
  results=[];error='';retry=None
  for endpoint in ('collection','sales'):
   with store.connect() as db:latest,good=ct.reads(db,business_day,endpoint)
   # Reuse the intraday sales response already collected for other ERP functions.
   if endpoint=='sales' and latest and latest['state']!='error' and ct.age(latest['read_at'])<90 and json.loads(latest['normalized_json']).get('projection_version')==3:
    results.append({'endpoint':endpoint,'id':latest['id'],'reused':True});continue
   try:
    response=reader([endpoint+'-one-day',business_day.replace('-','')],config['scope'])
    result=sources.record(store,business_day,endpoint,response['payload'],config['scope'])
    results.append(dict(result,endpoint=endpoint))
    if result['state']=='error':error='source_contract_requires_review'
   except ProbeFailure as exc:
    error='http_'+str(exc.http_status) if exc.http_status in (401,403,429) else exc.code
    reason={'provider_not_authorized':'La integración existente no autoriza esta consulta.','http_401':'La integración necesita revisión de acceso.','http_403':'La integración no autoriza esta lectura.','http_429':'Toteat solicitó esperar antes de consultar.','network_error':'No fue posible completar la lectura de Toteat.'}.get(error,'Lectura Toteat sin éxito confirmado.')
    results.append(dict(sources.record(store,business_day,endpoint,scope=config['scope'],error=reason),endpoint=endpoint))
    seconds=max(300,min(3600,getattr(exc,'retry_after',300)))
    if error in ('provider_not_authorized','http_401','http_403'):seconds=3600
    retry=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='seconds')
    break
   except (OSError,ValueError,TypeError,KeyError):
    error='source_contract_requires_review'
    results.append(dict(sources.record(store,business_day,endpoint,scope=config['scope'],error='Contrato de respuesta o lectura local incompletos; se conserva la última lectura correcta.'),endpoint=endpoint));break
  with store.connect() as db:db.execute("UPDATE cl_toteat_sync SET state=?,finished_at=?,retry_at=?,error=? WHERE business_date=?",('error' if error else 'done',ct.now(),retry,error,business_day))
  return {'business_date':business_day,'state':'error' if error else 'done','results':results}


def safe_sync_cycle(root):
 """A source projection failure cannot interrupt existing order/delivery work."""
 try:return sync_cycle(root)
 except Exception:
  try:
   with Store(Path(root)/'data/erp-demo.sqlite3').connect() as db:
    db.execute("UPDATE cl_toteat_sync SET state='error',finished_at=?,error='local_source_error' WHERE state IN ('queued','running')",(ct.now(),))
  except Exception:pass
  return {'state':'error','error':'local_source_error'}
