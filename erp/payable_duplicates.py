"""Read-only duplicate evidence shared by manual entry and SII imports."""
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from erp.domain import DomainError


class HistoricalIdentityWarning(DomainError):
    def __init__(self, candidate, matches, actor, target=None):
        super().__init__('Este folio figura en el historial de una factura cuyo folio fue corregido. Revisa el antecedente y confirma si corresponde a otro documento.', 409)
        proof = dict(candidate=candidate, matches=matches, actor=actor, target=target)
        self.historical_duplicate = dict(matches=matches, token=hashlib.sha256(
            json.dumps(proof, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        self.proof = proof


def normalized(value):
    value = ''.join(c for c in unicodedata.normalize('NFD', str(value).casefold()) if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9 ]', '', value).split())


def folio(value):
    value = str(value or '').strip().upper()
    return str(int(value)) if value.isascii() and value.isdigit() else value


def identified_folio(value):
    number = folio(value)
    return bool(number and number not in ('0', 'X') and not number.startswith('SALDO-FILA-'))


class DuplicateIndex:
    def __init__(self, db):
        self.by_folio = defaultdict(list)
        self.without_folio = defaultdict(list)
        suppliers = {r['id']: dict(r) for r in db.execute('SELECT id,name,tax_id FROM ap_suppliers')}
        identities = {key: {'names': {normalized(s['name'])}, 'ruts': {s['tax_id']} - {''}} for key, s in suppliers.items()}
        supplier_events = db.execute("SELECT supplier_id,before_json,after_json FROM ap_events WHERE action='supplier_saved'")
        for event in supplier_events:
            identity = identities.get(event['supplier_id'])
            if identity is None:
                continue
            for field in ('before_json', 'after_json'):
                data = json.loads(event[field])
                if data.get('name'):
                    identity['names'].add(normalized(data['name']))
                if data.get('tax_id'):
                    identity['ruts'].add(data['tax_id'])
        names = defaultdict(set)
        self.supplier_names = {key: identity['names'] for key, identity in identities.items()}
        for identity in identities.values():
            for name in identity['names']:
                names[name].update(identity['ruts'])
        invoices = {}
        for row in db.execute('''SELECT i.*,s.name AS supplier_name,
            (SELECT COALESCE(SUM(p.amount),0) FROM ap_payments p WHERE p.invoice_id=i.id AND p.voided_at IS NULL) AS paid
            FROM ap_invoices i JOIN ap_suppliers s ON s.id=i.supplier_id'''):
            invoice = dict(row)
            from erp import payables as ap
            invoice['paid'] = ap.paid(db, invoice['id'])
            invoice['credited'] = ap.credited(db, invoice['id'])
            invoice['state'] = 'Anulada' if invoice['voided_at'] else ('Saldada con nota de crédito' if invoice['credited'] else 'Pagada') if invoice['paid'] + invoice['credited'] >= invoice['amount'] else 'Con abonos' if invoice['paid'] else 'Registrada'
            invoices[invoice['id']] = invoice
            self.add(invoice, identities[invoice['supplier_id']]['ruts'], 'ERP', invoice_id=invoice['id'])
        for row in db.execute('SELECT * FROM ap_invoice_sources'):
            invoice = invoices[row['invoice_id']]
            original = dict(invoice, folio=row['source_folio'], document_type=row['source_type'], amount=row['source_amount'], issued_on=row['source_date'])
            self.add(original, {row['source_tax_id']}, 'Importación anterior', invoice_id=invoice['id'])
        for row in db.execute("SELECT invoice_id,evidence_json FROM ap_import_resolutions WHERE action='link'"):
            invoice = invoices[row['invoice_id']]
            candidate = json.loads(row['evidence_json'])['candidate']
            original = dict(invoice, folio=candidate['folio'], document_type=candidate['document_type'],
                            amount=candidate['amount'], issued_on=candidate['issued_on'])
            self.add(original, {candidate['tax_id']}, 'Vínculo revisado', invoice_id=invoice['id'])
        for event in db.execute("SELECT invoice_id,before_json,after_json FROM ap_events WHERE action='invoice_saved'"):
            current = invoices.get(event['invoice_id'])
            if not current:
                continue
            for field in ('before_json', 'after_json'):
                old = json.loads(event[field])
                if not old.get('folio') or old.get('supplier_id') not in identities:
                    continue
                old.update(supplier_name=suppliers[old['supplier_id']]['name'], state=current['state'])
                self.add(old, identities[old['supplier_id']]['ruts'], 'Historial de cambios', invoice_id=current['id'], current=current)
        links = {(r['baseline_id'], r['source_row']): r['invoice_id'] for r in db.execute('SELECT * FROM ap_baseline_links')}
        for baseline in db.execute('SELECT id,original FROM ap_initial_baselines'):
            snapshot = json.loads(baseline['original'])
            seen = set()
            for section in ('ledger', 'form'):
                for row in snapshot.get(section, []):
                    key = (normalized(row['name']), folio(row.get('folio')), row.get('amount'))
                    if key in seen:
                        continue
                    seen.add(key)
                    linked = links.get((baseline['id'], row['row'])) if section == 'ledger' else None
                    state = 'Pagada según apertura' if section == 'ledger' and row.get('balance') is not None and row['balance'] <= 0 else 'Antecedente de apertura'
                    original = dict(row, supplier_name=row['name'], document_type=None, state=state)
                    self.add(original, names[normalized(row['name'])], 'Carga inicial', invoice_id=linked, source_row=row['row'])

    def add(self, row, ruts, source, invoice_id=None, source_row=None, current=None):
        number = folio(row.get('folio'))
        item = dict(invoice_id=invoice_id, supplier_id=row.get('supplier_id'), supplier_name=row['supplier_name'],
                    folio=number, amount=row.get('amount'), document_type=row.get('document_type'),
                    issued_on=row.get('issued_on', ''), state=row.get('state', 'Registrada'),
                    source=source, source_row=source_row, ruts=set(ruts), name=normalized(row['supplier_name']))
        # Only a replaced folio in invoice edit history is an advisory match.
        # Import sources, reviewed links and opening evidence keep their guards.
        item['historical_only'] = bool(current and number != folio(current['folio']))
        if item['historical_only']:
            item.update(current_folio=current['folio'], current_version=current['version'],
                        current_document_type=current['document_type'], current_supplier_name=current['supplier_name'])
        if not identified_folio(number):
            self.without_folio[item['amount']].append(item)
        else:
            self.by_folio[number].append(item)

    def find(self, candidate, exclude_id=None):
        number, tax_id = folio(candidate.get('folio')), candidate.get('tax_id', '')
        name = normalized(candidate.get('name', candidate.get('supplier_name', '')))
        results, seen = [], set()
        candidates = self.by_folio[number]
        # A selected supplier and an actual folio identify the manual document.
        # An opening balance without a folio cannot identify it by amount alone,
        # and its historic payment state must not block that document's entry.
        # Preserve existing review hints for unresolved CSV supplier identities.
        if candidate.get('supplier_id') is None or not identified_folio(number):
            candidates = candidates + self.without_folio[candidate.get('amount')]
        for match in candidates:
            if exclude_id and match['invoice_id'] == exclude_id:
                continue
            if match['document_type'] and match['document_type'] != candidate.get('document_type'):
                continue
            same_folio = match['folio'] == number
            same_supplier = candidate.get('supplier_id') is not None and candidate['supplier_id'] == match['supplier_id']
            same_rut = bool(tax_id and tax_id in match['ruts'])
            if tax_id and match['ruts'] and not same_rut:
                continue
            # A manual invoice has an explicitly selected supplier. A folio or
            # amount from another supplier's opening evidence is not its identity.
            # Keep the broader review hints for unresolved SII supplier identities.
            known_names = self.supplier_names.get(candidate.get('supplier_id'), {name})
            same_legacy_name = not match['supplier_id'] and match['name'] in known_names
            if candidate.get('supplier_id') is not None and not (same_supplier or same_rut or same_legacy_name):
                continue
            exact = bool(same_folio and match['document_type'] and (same_supplier or same_rut))
            possible = (same_folio and (same_legacy_name or name == match['name'] or match['amount'] == candidate.get('amount'))) or (not same_folio and match['amount'] == candidate.get('amount'))
            if not exact and not possible:
                continue
            key = (match['invoice_id'], match['source'] if not match['invoice_id'] else '', match['source_row'] if not match['invoice_id'] else None)
            if key in seen:
                continue
            seen.add(key)
            results.append({k: v for k, v in match.items() if k not in ('ruts', 'name', 'supplier_id')} | {'exact': exact, 'tax_ids': sorted(match['ruts'])})
        return sorted(results, key=lambda value: (value['historical_only'], not value['exact']))[:5]
