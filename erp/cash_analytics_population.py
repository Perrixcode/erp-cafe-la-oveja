"""Known populations and independent MP observations for cashier analysis."""
from datetime import date,datetime
import json
from erp import cash_toteat as toteat, cash_closures as cl


def enrich(db,c,line):
    v2=c.get('cashier_v2')
    presented=bool(c['submitted_at'] and c['status']!='draft')
    line.update(presented=presented,is_v2=bool(v2),session_key=c.get('external_session_key'),
                evidence_count=db.execute('SELECT COUNT(*) FROM cl_attachments WHERE closure_id=?',(c['id'],)).fetchone()[0],
                evidence_reasons={},comparison_origin='legacy',mp_observations=[])
    if v2:
        latest=v2.get('history',[])
        submission_id=latest[-1]['id'] if latest else None
        late=[e for e in v2['events'] if e['kind']=='late_comparison' and e['submission_id']==submission_id]
        comparison=late[-1]['detail']['comparison'] if late and presented else v2['summary']
        line['comparison_origin']='late_comparison' if late and presented else 'signed_snapshot' if presented else 'draft'
        line['comparison_at']=late[-1]['created_at'] if late and presented else c['submitted_at']
        line['session_key']=(comparison.get('selected_session') or {}).get('session_key') or line['session_key']
        line['cash']['difference']=comparison.get('difference')
        line['source_coverage']='comparable_reference' if comparison.get('expected') is not None else 'incomplete'
        line['cash']['eligible']=bool(presented and c['reviewed'] and line['owner']['safe'] and comparison.get('difference') is not None)
        line['evidence_reasons']=v2.get('missing_evidence_reasons',{})
        line['correction_versions']=sum(e['kind']=='signature_invalidated_for_revision' for e in v2['events']) or max(0,len(latest)-1)
        declaration=latest[-1]['snapshot']['declaration'] if presented and latest else json.loads(db.execute('SELECT declaration_json FROM cv2_drafts WHERE closure_id=?',(c['id'],)).fetchone()[0])
        declared={cl.key(t['terminal_id']):t for t in declaration['terminals']}
        for t in comparison.get('terminals',[]):
            source=declared.get(cl.key(t['terminal_id']),{})
            line['mp_observations'].append(dict(id=c['id']+':'+cl.key(t['terminal_id']),closure_id=c['id'],date=c['business_date'],
                terminal_id=t['terminal_id'],period_start=source.get('period_start'),period_end=source.get('period_end'),
                declared=t['declared'],expected=t['expected'],difference=t['difference'],report_id=t['report_id'],
                comparable=bool(presented and t['difference'] is not None),reviewed=c['reviewed'],
                eligible=bool(presented and c['reviewed'] and line['owner']['safe'] and t['difference'] is not None),
                owner=line['owner'],origin=line['comparison_origin'],reasons=[] if t['difference'] is not None else ['Terminal, período o referencia de origen pendientes.']))
    line['cash']['comparable']=bool(presented and line['cash']['difference'] is not None)
    line['delivery']['comparable']=bool(presented and line['delivery']['difference'] is not None)
    return line


def mp_rows(lines,reports):
    by_closure={r['id']:r for r in lines}
    result=[dict(m) for r in lines for m in r['mp_observations']]
    for report in reports:
        row=by_closure.get(report['closure_id'])
        if not row or row['is_v2'] or report['superseded']:continue
        comp=report['comparison'];safe=row['owner']['safe'] and report['source']['responsible']['key']==row['owner']['key']
        comparable=bool(row['presented'] and comp['comparable'])
        result.append(dict(id=report['id'],closure_id=row['id'],date=row['date'],terminal_id=report['terminal_id'],
            period_start=report['period_start'],period_end=report['period_end'],declared=report['total'],
            expected=comp.get('compared_total'),difference=comp['difference'],report_id=report['id'],
            comparable=comparable,reviewed=row['reviewed'],eligible=bool(comparable and row['reviewed'] and safe),
            owner=row['owner'],origin='terminal_report',scope=comp.get('scope'),reasons=list(comp['reasons'])))
    # The same terminal/economic interval cannot contribute twice through multiple closures or coupons.
    periods={}
    for row in result:
        if row['comparable'] and row['period_start'] and row['period_end']:
            periods.setdefault(cl.key(row['terminal_id']),[]).append(row)
    for group in periods.values():
        conflicting=set()
        for i,row in enumerate(group):
            start=datetime.fromisoformat(row['period_start'].replace('Z','+00:00'))
            end=datetime.fromisoformat(row['period_end'].replace('Z','+00:00'))
            for other in group[i+1:]:
                other_start=datetime.fromisoformat(other['period_start'].replace('Z','+00:00'))
                other_end=datetime.fromisoformat(other['period_end'].replace('Z','+00:00'))
                if max(start,other_start)<min(end,other_end):
                    conflicting.update((row['id'],other['id']))
        for row in group:
            if row['id'] in conflicting:
                row.update(eligible=False,comparable=False)
                row['reasons'].append('Observaciones del mismo terminal con períodos superpuestos; revisar duplicidad antes de evaluar.')
    legacy={}
    for row in result:
        if row.get('scope')=='administrative_session':legacy.setdefault((row['closure_id'],row['period_start'],row['period_end']),[]).append(row)
    for group in legacy.values():
        if len(group)>1:
            for row in group:
                row.update(eligible=False,comparable=False)
                row['reasons'].append('Un total agregado de sesión no acredita varios terminales individuales.')
    return result


def population(lines,mp):
    return dict(total=len(lines),presented=sum(r['presented'] for r in lines),reviewed=sum(r['reviewed'] and r['presented'] for r in lines),
                identity_safe=sum(r['owner']['safe'] for r in lines),comparable_cash=sum(r['cash']['comparable'] for r in lines),
                comparable_delivery=sum(r['delivery']['comparable'] for r in lines),included_cash=sum(r['cash']['eligible'] for r in lines),
                terminal_observations=len(mp),comparable_mp=sum(r['comparable'] for r in mp),included_mp=sum(r['eligible'] for r in mp),
                corrections=sum(r['correction_versions'] for r in lines),evidence=sum(r['evidence_count'] for r in lines),
                with_pending_evidence=sum(any(p.get('code','').startswith('photo_') for p in r['pending_work']) for r in lines),without_attachments=sum(r['evidence_count']==0 for r in lines))


def known_sessions(db,f,lines,start,end):
    registers={r['provider_register_id']:dict(r) for r in db.execute('SELECT id,name,provider_register_id FROM cl_registers')}
    reads=[dict(r) for r in db.execute('''SELECT * FROM cc_source_reads WHERE id IN (SELECT MAX(id) FROM cc_source_reads
      WHERE endpoint='collection' AND state<>'error' AND business_date BETWEEN ? AND ? GROUP BY business_date)''',(start,end))]
    result=[];seen=set()
    for read in reads:
        for s in json.loads(read['normalized_json']).get('sessions',[]):
            if s['session_key'] in seen:continue
            seen.add(s['session_key']);reg=registers.get(s['register_id'])
            if f['register'] and (not reg or f['register']!=reg['id']) or f['shift'] and f['shift']!=s['shift_name']:continue
            linked=[r for r in lines if r['session_key']==s['session_key']]
            if f['cashier'] and not linked:continue  # No source identity is guessed for an unlinked session.
            result.append(dict(session_key=s['session_key'],day=s['business_date'],register=s['register_name'],register_id=reg['id'] if reg else None,
                shift=s['shift_name'],opened_at=s['opened_at'],closed_at=s['closed_at'],read_id=read['id'],read_at=read['read_at'],
                closure_ids=[r['id'] for r in linked],state='linked' if linked else 'open_without_link' if not s['closed_at'] else 'closed_without_link',
                attribution='linked_closure' if linked else 'unknown',responsible=None,
                notice='Sesión conocida sin vínculo a una declaración; no se inventa un turno esperado ni responsable.' if not linked else 'Se conserva cada declaración vinculada; puede haber varias entregas.'))
    return result


def coverage(db,start,end):
    rows=[dict(r) for r in db.execute('''SELECT business_date,endpoint,state,reason,read_at FROM cc_source_reads WHERE id IN
      (SELECT MAX(id) FROM cc_source_reads WHERE business_date BETWEEN ? AND ? GROUP BY business_date,endpoint)''',(start,end))]
    days=(date.fromisoformat(end)-date.fromisoformat(start)).days+1
    return dict(complete=False,days_requested=days,days_with_observations=len({r['business_date'] for r in rows if r['state']!='error'}),
                failed_readings=sum(r['state']=='error' for r in rows),reason='Las fuentes actuales no acreditan cobertura completa de ventas y movimientos. Fechas posteriores al inicio no garantizan cobertura.')
