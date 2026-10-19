import base64,json,unittest
from uuid import uuid4
from io import BytesIO
from PIL import Image
import test_cash_closures_http as base
from erp import cash_bags as bags

class V2HTTPTests(unittest.TestCase):
 setUp=base.ClosureHTTPTests.setUp;stop=base.ClosureHTTPTests.stop;call=base.ClosureHTTPTests.call;command=base.ClosureHTTPTests.command;error=base.ClosureHTTPTests.error
 def action(self,action,c=None,client=None,**values):
  return self.call('/api/cash-closures/v2/'+(c['id']+'/' if c else '')+action,client or self.mobile,dict(request_id=uuid4().hex,confirmed=True,**({'version':c['version']} if c else {}),**values))
 def new(self):return self.action('create',identity={'register_id':self.reg,'business_date':'2026-10-16'})
 def test_api_assets_authentication_roles_and_csrf(self):
  self.error(401,self.call,'/api/cash-closures/v2');self.error(403,self.call,'/api/cash-closures/v2',self.prod)
  for asset in ('cashier-v2.js','cashier-v2-server.js','cashier-v2.css'):self.assertTrue(self.call('/'+asset))
  c=self.new();self.error(403,self.call,'/api/cash-closures/v2/'+c['id'],self.cashier)
  body=dict(request_id=uuid4().hex,confirmed=True,identity={'register_id':self.reg,'business_date':'2026-10-16'})
  self.error(403,self.call,'/api/cash-closures/v2/create',self.mobile,body,{'Content-Type':'application/json','X-ERP-Local':'1','Origin':'https://evil.invalid'})
  self.error(403,self.call,'/api/cash-closures/v2/create',self.mobile,body,{'Content-Type':'application/json'})
 def test_real_http_signed_submission_private_photo_and_pdf_preview(self):
  c=self.new();im=BytesIO();Image.new('RGB',(5,8),'blue').save(im,format='PNG')
  photo=self.action('attach',c,kind='mp',file_name='qa.png',file_type='image/png',file_data=base64.b64encode(im.getvalue()).decode())
  from erp.cashier_v2 import DECLARATION_FIELDS
  d={k:c[k] for k in DECLARATION_FIELDS};d['cash']={k:0 for k in d['cash']};d['retained']=dict(d['cash']);d['cash']['1000']=2
  batch=bags.execute(self.store,'create',dict(request_id=uuid4().hex,confirmed=True,quantity=1),{'role':'partner','username':'qa'},self.url);d['bagNumber']=bags.batch_view(self.store,batch['batch_id'])['bags'][0]['label'];d['photos']=[dict(photo,legible=True)];d['missing_evidence_reasons']={'mp':'','toteat':'Reporte aún pendiente'}
  c=self.action('save',c,declaration=d);r=self.action('review',c);c=self.action('submit',c,review_token=r['token'],strokes=[[[0,0],[.5,.5]]],consent=True)
  prefix='/api/cash-closures/v2/'+c['id'];self.assertEqual(self.call(prefix,self.mobile)['signature'],c['signature'])
  for suffix in ('preview.pdf','download.pdf'):
   self.assertTrue(self.call(prefix+'/'+suffix,self.mobile).startswith(b'%PDF-'));self.error(403,self.call,prefix+'/'+suffix,self.cashier)
  url='/api/cash-closures/'+c['id']+'/attachments/'+photo['id'];self.assertEqual(self.call(url,self.mobile),im.getvalue());self.error(403,self.call,url,self.cashier)
  self.assertTrue(self.call('/api/cash-closures/'+c['id']+'/preview.pdf',self.mobile).startswith(b'%PDF-'))
  self.call('/api/bag-access/logout',self.mobile,{})
  self.error(401,self.call,prefix,self.mobile);self.error(401,self.call,url,self.mobile)
 def test_rejects_forged_roles_and_source_values_before_write(self):
  c=self.new();d={k:c[k] for k in ('identity','cash','retained','terminals','photos','bagNumber','note','missing_evidence_reasons')};d['verified']=True
  self.error(400,self.action,'save',c,declaration=d)
  self.error(403,self.action,'cash-reference',c,opening=0,net_sales=0,movement_net=0,basis='independent_net_sales_before_movements',evidence='fake',attachment_id='bad',opening_verified=True,sales_verified=True,movements_verified=True)

if __name__=='__main__':unittest.main()
