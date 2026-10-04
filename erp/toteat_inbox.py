"""Bandeja privada de lectura: conserva evidencia sin inventar pedidos ni agendamientos."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from scripts.toteat_open_orders_probe import inspect_open_orders

def stamp():return datetime.now(timezone.utc).isoformat()
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def text(value,maximum=2000):return value[:maximum] if isinstance(value,str) else ''
def identity(value):
    if type(value) is int and value>0:return str(value)
    if isinstance(value,str) and 0<len(value)<=100:return value
    raise ValueError('missing_identity')

def normalize(payload,scope,product_ids):
    if not isinstance(payload,dict) or payload.get('ok') is not True or not isinstance(payload.get('data'),list):
        raise ValueError('provider_success_not_confirmed')
    if len(payload['data'])>5000:raise ValueError('listing_too_large')
    restaurant,local=identity(scope.get('restaurant_id')),identity(scope.get('local_id'))
    result=[];seen=set()
    for row in payload['data']:
        if not isinstance(row,dict):raise ValueError('invalid_order')
        document=row.get('document',{})
        if not isinstance(document,dict):continue
        lines=document.get('line',[])
        if not isinstance(lines,list):continue
        matched=[line for line in lines if isinstance(line,dict) and line.get('isExtra') is False and type(line.get('productCodeToteat')) is int and line['productCodeToteat'] in product_ids]
        if not matched:continue
        if 'restaurantId' in row and str(row['restaurantId'])!=restaurant:raise ValueError('restaurant_mismatch')
        if 'localNumber' in row and str(row['localNumber'])!=local:raise ValueError('local_mismatch')
        order_id=identity(row.get('orderId'));key=canonical([restaurant,local,order_id])
        if key in seen:raise ValueError('duplicate_order_in_response')
        seen.add(key)
        comments=[]
        for path,node in [('$',row),('$.document',document)]:
            for field in ('comment','comments','notes','observations'):
                if isinstance(node.get(field),str) and node[field].strip():comments.append({'path':path+'.'+field,'text':text(node[field],None)})
        items=[]
        for line in matched:
            item={k:line.get(k) for k in ('lineNumber','productCodeToteat','quantity','isExtra','referenceLine','status','cancelled')}
            item['productName']=text(line.get('productName'),160)
            item['comments']=[{'path':'$.document.line[].'+k,'text':text(line[k],None)} for k in ('comment','comments','notes','observations') if isinstance(line.get(k),str) and line[k].strip()]
            items.append(item)
        result.append({'key':key,'order_id':order_id,'restaurant_id':restaurant,'local_id':local,
                       'order_reference':text(row.get('orderReference'),160),'channel':text(row.get('channel'),160),
                       'vendor':text(row.get('vendorName'),160),'modified_at':text(row.get('modificationDate'),80),
                       'source_status':row.get('orderStatus'),'status_label':text(row.get('status'),80),
                       'comments':comments,'items':items,'scheduling_status':'needs_review'})
    return result

class Inbox:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS received_orders(
                source_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL, digest TEXT NOT NULL,
                first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS received_history(
                id INTEGER PRIMARY KEY,source_key TEXT NOT NULL,digest TEXT NOT NULL,payload_json TEXT NOT NULL,observed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reader_status(id INTEGER PRIMARY KEY CHECK(id=1),payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS received_sales_comments(
                id INTEGER PRIMARY KEY,source_key TEXT NOT NULL,payment_id TEXT NOT NULL,
                digest TEXT NOT NULL,payload_json TEXT NOT NULL,observed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS received_sales_state(source_key TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS received_state_history(id INTEGER PRIMARY KEY,source_key TEXT NOT NULL,before_json TEXT,after_json TEXT NOT NULL,observed_at TEXT NOT NULL);''')
        self.path.chmod(0o600)

    def set_sales_state(self, key, value):
        with closing(sqlite3.connect(self.path)) as db, db:
            if not key.startswith('__'):
                previous=db.execute('SELECT payload_json FROM received_sales_state WHERE source_key=?',(key,)).fetchone()
                current={k:v for k,v in value.items() if k!='observed_at'}
                before={k:v for k,v in json.loads(previous[0]).items() if k!='observed_at'} if previous else None
                if current!=before:db.execute('INSERT INTO received_state_history(source_key,before_json,after_json,observed_at) VALUES(?,?,?,?)',(key,canonical(before),canonical(current),stamp()))
            db.execute('INSERT INTO received_sales_state VALUES(?,?) ON CONFLICT(source_key) DO UPDATE SET payload_json=excluded.payload_json', (key,canonical(value)))

    def receive_sale(self, scope, candidate, catalog):
        restaurant, local = identity(scope['restaurant_id']), identity(scope['local_id'])
        order_id = identity(candidate['order_id'])
        key = canonical([restaurant,local,order_id])
        by_id = {str(p['source_product']['idToteat']):p for p in catalog if p.get('source_product')}
        items = []
        for item in candidate['products']:
            product = by_id.get(str(item['catalog_id']))
            if not product:
                raise ValueError('unverified_sale_product')
            items.append({'lineNumber':item['line_id'],'productCodeToteat':product['source_product']['idToteat'],
                          'productName':product['flavor']+' · '+product['size'],'quantity':item['quantity'],
                          'isExtra':False,'comments':[]})
        value = {'key':key,'order_id':order_id,'restaurant_id':restaurant,'local_id':local,'channel':'',
                 'vendor':'','order_reference':'','modified_at':candidate.get('date_closed',''),
                 'source_status':None,'status_label':'CLOSED','comments':[],'items':items,'scheduling_status':'needs_review'}
        encoded = canonical(value);digest = hashlib.sha256(encoded.encode()).hexdigest();observed = stamp()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM received_orders WHERE source_key=?',(key,)).fetchone():
                db.execute('INSERT INTO received_orders VALUES(?,?,?,?,?,1)',(key,encoded,digest,observed,observed))
                db.execute('INSERT INTO received_history(source_key,digest,payload_json,observed_at) VALUES(?,?,?,?)',(key,digest,encoded,observed))
        if candidate.get('order_comments'):
            self.attach_sales_comments(scope,candidate)
        return key

    def attach_sales_comments(self, scope, candidate):
        """Añade evidencia de una venta a una comanda conocida; no crea pedidos."""
        restaurant, local = identity(scope.get('restaurant_id')), identity(scope.get('local_id'))
        order_id, payment_id = identity(candidate.get('order_id')), identity(candidate.get('payment_id'))
        key = canonical([restaurant, local, order_id])
        closed_at = candidate.get('date_closed')
        if not isinstance(closed_at, str) or not closed_at:
            raise ValueError('missing_closed_sale_date')
        datetime.fromisoformat(closed_at)
        fields = candidate.get('order_comments')
        if not isinstance(fields, list) or not fields:
            raise ValueError('missing_order_comment')
        comments = []
        for field in fields:
            # El comentario de descuento o medio de pago no es el de la orden.
            if not isinstance(field, dict) or field.get('path') != '$.comment' or not isinstance(field.get('original'), str):
                raise ValueError('unverified_order_comment_field')
            comments.append({'path': '$.comment', 'text': field['original'], 'source': 'sales'})
        products = candidate.get('products')
        if not isinstance(products, list) or not products:
            raise ValueError('missing_matching_products')
        product_ids = {identity(product.get('catalog_id')) for product in products}
        evidence = {'source': 'sales', 'order_id': order_id, 'payment_id': payment_id,
                    'date_closed': closed_at, 'comments': comments,
                    'product_ids': sorted(product_ids)}
        encoded = canonical(evidence)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            received = db.execute('SELECT payload_json FROM received_orders WHERE source_key=?', (key,)).fetchone()
            if not received:
                raise ValueError('known_order_required')
            order = json.loads(received[0])
            if (order['restaurant_id'], order['local_id'], order['order_id']) != (restaurant, local, order_id):
                raise ValueError('scope_mismatch')
            known = {identity(item['productCodeToteat']) for item in order['items']}
            if not product_ids.issubset(known):
                raise ValueError('product_identity_mismatch')
            previous = db.execute('SELECT digest FROM received_sales_comments WHERE source_key=? AND payment_id=? ORDER BY id DESC LIMIT 1', (key, payment_id)).fetchone()
            if previous and previous[0] == digest:
                return {'changed': 0, 'imported_operational_orders': 0}
            db.execute('INSERT INTO received_sales_comments(source_key,payment_id,digest,payload_json,observed_at) VALUES(?,?,?,?,?)',
                       (key, payment_id, digest, encoded, stamp()))
        return {'changed': 1, 'imported_operational_orders': 0}
    def set_status(self,data):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('INSERT INTO reader_status VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json',(canonical(data),))
    def apply(self,payload,scope,product_ids):
        rows=normalize(payload,scope,product_ids)
        evidence=inspect_open_orders(payload,product_ids)
        created=changed=0;observed=stamp()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            for row in rows:
                encoded=canonical(row);digest=hashlib.sha256(encoded.encode()).hexdigest()
                prior=db.execute('SELECT digest FROM received_orders WHERE source_key=?',(row['key'],)).fetchone()
                if prior is None:
                    db.execute('INSERT INTO received_orders VALUES(?,?,?,?,?,1)',(row['key'],encoded,digest,observed,observed));created+=1
                elif prior[0]!=digest:
                    db.execute('UPDATE received_orders SET payload_json=?,digest=?,last_seen=?,version=version+1 WHERE source_key=?',(encoded,digest,observed,row['key']));changed+=1
                else:db.execute('UPDATE received_orders SET last_seen=? WHERE source_key=?',(observed,row['key']))
                if prior is None or prior[0]!=digest:
                    db.execute('INSERT INTO received_history(source_key,digest,payload_json,observed_at) VALUES(?,?,?,?)',(row['key'],digest,encoded,observed))
            channels={}
            for row in rows:
                channel=row['channel'];label=channel if channel.isascii() and channel.replace('_','').replace('-','').isalnum() and len(channel)<=40 else 'unclassified'
                channels[label]=channels.get(label,0)+1
            status={'state':'receiving','last_success':observed,'checked_at':observed,'created':created,'changed':changed,
                    'open_matching_orders':len(rows),'channels_observed':channels,'evidence':evidence,
                    'automatic_scheduling':False,'closed_orders_coverage':False}
            db.execute('INSERT INTO reader_status VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json',(canonical(status),))
        return status

def read_inbox(path,include_orders=True):
    path=Path(path)
    if not path.exists():return {'state':'not_configured','orders':[]}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        saved=db.execute('SELECT payload_json FROM reader_status WHERE id=1').fetchone()
        result=json.loads(saved[0]) if saved else {'state':'waiting_first_read'}
        sales_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='received_sales_state'").fetchone()
        if sales_table:
            state = db.execute("SELECT payload_json FROM received_sales_state WHERE source_key='__reader__'").fetchone()
            cancellations=db.execute("SELECT payload_json FROM received_sales_state WHERE source_key='__cancellations__'").fetchone()
            if cancellations:result['cancellation_reader']=json.loads(cancellations[0])
            if state:
                result['sales_reader'] = json.loads(state[0])
                result['automatic_scheduling'] = result['sales_reader'].get('automatic_scheduling',False)
        if include_orders:
            result['orders']=[dict(json.loads(r[0]),first_seen=r[1],last_seen=r[2],version=r[3]) for r in db.execute('SELECT payload_json,first_seen,last_seen,version FROM received_orders ORDER BY last_seen DESC LIMIT 100')]
            # Evidencia separada: un refresco de abiertas nunca borra el comentario
            # recuperado de una venta, ni convierte ese texto en pago/agendamiento.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='received_sales_comments'").fetchone():
                for order in result['orders']:
                    if sales_table:
                        status = db.execute('SELECT payload_json FROM received_sales_state WHERE source_key=?',(order['key'],)).fetchone()
                        if status:
                            order['scheduling'] = json.loads(status[0])
                    latest = {}
                    for row in db.execute('SELECT payment_id,payload_json,observed_at FROM received_sales_comments WHERE source_key=? ORDER BY id', (order['key'],)):
                        latest[row[0]] = dict(json.loads(row[1]), observed_at=row[2])
                    if latest:
                        order['sales_comment_evidence'] = list(latest.values())
                        seen = {(c['path'], c['text']) for c in order['comments']}
                        for sale in latest.values():
                            for comment in sale['comments']:
                                marker = (comment['path'], comment['text'])
                                if marker not in seen:
                                    order['comments'].append(comment)
                                    seen.add(marker)
        result['stored_orders']=db.execute('SELECT COUNT(*) FROM received_orders').fetchone()[0]
        try:result['connected']=result.get('state')=='receiving' and (datetime.now(timezone.utc)-datetime.fromisoformat(result['last_success'])).total_seconds()<90
        except (KeyError,TypeError,ValueError):result['connected']=False
        return result

def get_received(path,key):
    """Solo identidades observadas por el lector; no acepta IDs inventados por UI."""
    if not isinstance(key,str) or len(key)>1000:raise ValueError('invalid_received_key')
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute('SELECT payload_json FROM received_orders WHERE source_key=?',(key,)).fetchone()
        if not row:raise ValueError('received_order_not_found')
        return json.loads(row[0])


def reception_history(path,key):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        events=[];previous=None
        for payload,observed in db.execute('SELECT payload_json,observed_at FROM received_history WHERE source_key=? ORDER BY id',(key,)):
            current=json.loads(payload)
            events.append(dict(action='comanda_recibida' if previous is None else 'comanda_actualizada',actor='Lector Toteat',reason='Evidencia de la comanda observada.',before=previous,after=current,occurred_at=observed));previous=current
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='received_state_history'").fetchone():
            for before,after,observed in db.execute('SELECT before_json,after_json,observed_at FROM received_state_history WHERE source_key=? ORDER BY id',(key,)):
                events.append(dict(action='estado_de_recepcion',actor='Lector Toteat',reason='Resultado de revisión automática.',before=json.loads(before),after=json.loads(after),occurred_at=observed))
        return events
