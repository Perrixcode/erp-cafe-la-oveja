"""Real image/storage fixture over stdio. Temp database, no HTTP/login credentials."""
from io import BytesIO
import base64, json, sys
from PIL import Image, ImageDraw
from test_cashier_v2 import V2Tests, CASHIER
from erp import cashier_v2 as v2
from erp.domain import DomainError

case = V2Tests()
case.setUp()
try:
    value = case.saved()
    image = Image.new('RGB', (4032, 3024), 'white')
    draw = ImageDraw.Draw(image)
    for y in range(100, 2900, 130):
        draw.text((100, y), 'SYNTHETIC RECEIPT ONLY - 1234567890', fill='black', font_size=64)
    original = BytesIO()
    image.save(original, format='JPEG', quality=95)
    image.thumbnail((2200, 2200))
    encoded = BytesIO()
    image.save(encoded, format='JPEG', quality=85)
    content = encoded.getvalue()
    print(json.dumps(dict(value=value, image=base64.b64encode(content).decode(), original_bytes=len(original.getvalue()), compressed_bytes=len(content), dimensions=list(image.size))), flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        try:
            if request['action'] == 'view':
                result = v2.view(case.store, value['id'], CASHIER)
            elif request['action'] == 'stats':
                with case.store.connect() as db:
                    result = {table: db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('cl_attachments', 'cv2_images', 'cc_cash_events')}
            else:
                result = v2.command(case.store, request['action'], request['body'], CASHIER, value['id'])
            print(json.dumps(dict(ok=True, data=result)), flush=True)
        except DomainError as error:
            print(json.dumps(dict(ok=False, status=error.status, data=dict(error=str(error)))), flush=True)
finally:
    case.doCleanups()
