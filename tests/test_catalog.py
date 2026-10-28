"""Run from project root: python -m unittest discover -s tests -v (requires httpx)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
_temp=tempfile.TemporaryDirectory()
os.environ['DATABASE_URL']='sqlite:///'+_temp.name+'/test.db'
os.environ['ADMIN_EMAIL']='admin@test.local'
os.environ['ADMIN_PASSWORD']='TestOnly123!'
from fastapi.testclient import TestClient
from app.main import app, startup, backfill_submission_status
from app.db import SessionLocal
from app.models import User, Product, Quote, QuoteItem, QuoteSupplier, QuoteResponse, PurchaseOrder
from app.catalog import barcode_value
from app.security import hash_password
from sqlalchemy import select, func

class CatalogTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.client=TestClient(app); cls.client.__enter__()
  cls.client.post('/login',data={'email':'admin@test.local','password':'TestOnly123!'})
  with SessionLocal() as db:
   user=User(name='Fornecedor',email='supplier@test.local',password_hash=hash_password('TestOnly123!'),role='supplier')
   db.add(user);db.flush();cls.sid=user.id;db.commit()
 def test_01_access(self):
  with TestClient(app) as anonymous:
   self.assertEqual(anonymous.get('/admin/produtos').status_code,403)
   anonymous.post('/login',data={'email':'supplier@test.local','password':'TestOnly123!'})
   self.assertEqual(anonymous.post('/admin/produtos/base').status_code,403)
 def test_02_catalog(self):
  for _ in range(2):self.assertEqual(self.client.post('/admin/produtos/base').status_code,200)
  with SessionLocal() as db:self.assertEqual(db.scalar(select(func.count(Product.id))),80)
  form={'code':'TEST','description':'Produto teste','barcode':'4006381333931','base_unit':'UN','active':'1'}
  self.assertIn('Produto salvo',self.client.post('/admin/produtos/salvar',data=form).text)
  self.assertIn('Produto teste',self.client.get('/admin/produtos?q=4006381333931').text)
  self.assertIn('já cadastrado',self.client.post('/admin/produtos/salvar',data={**form,'code':'OTHER'}).text)
  self.assertEqual(barcode_value('036000291452'),'00036000291452')
  with self.assertRaises(ValueError):barcode_value('4006381333932')
  with SessionLocal() as db:pid=db.scalar(select(Product.id).where(Product.code=='TEST'))
  self.assertEqual(self.client.get(f'/admin/produtos?edit={pid}').status_code,200)
 def test_03_quote(self):
  result=self.client.post('/admin/cotacoes/nova',data={'title':'Teste catálogo','deadline':(datetime.now()+timedelta(days=2)).isoformat(timespec='minutes'),'suppliers':str(self.sid)})
  self.assertEqual(result.status_code,200,result.text)
  qid=int(str(result.url).split('/')[-1])
  with SessionLocal() as db:
   pid=db.scalar(select(Product.id).where(Product.code=='TEST'))
   old=QuoteItem(quote_id=qid,code='OLD',description='Histórico',requested_qty=1,base_unit='UN');db.add(old);db.flush()
   db.add(QuoteResponse(quote_item_id=old.id,supplier_id=self.sid,offered_price=2))
   link=db.scalar(select(QuoteSupplier).where(QuoteSupplier.quote_id==qid));link.submitted_at=datetime.now();db.commit()
  url=f'/admin/cotacoes/{qid}/produtos'
  for invalid in ['0','-1','NaN','Infinity','0.00001']:
   self.client.post(url,data={'product_id':pid,'quantity':invalid})
  with SessionLocal() as db:self.assertEqual(db.scalar(select(func.count(QuoteItem.id)).where(QuoteItem.quote_id==qid)),1)
  self.assertIn('incluído',self.client.post(url,data={'product_id':pid,'quantity':'2,5'}).text)
  self.assertIn('já está',self.client.post(url,data={'product_id':pid,'quantity':'2'}).text)
  startup()
  with SessionLocal() as db:
   self.assertIsNone(db.scalar(select(QuoteSupplier).where(QuoteSupplier.quote_id==qid)).submitted_at)
   self.assertEqual(db.scalar(select(QuoteItem).where(QuoteItem.code=='TEST')).requested_qty,Decimal('2.5'))
   self.assertEqual(db.scalar(select(func.count(QuoteResponse.id))),1)
   prod=db.get(Product,pid);prod.description='Nome alterado';db.commit()
   self.assertEqual(db.scalar(select(QuoteItem).where(QuoteItem.code=='TEST')).description,'Produto teste')
   db.add(PurchaseOrder(quote_id=qid,supplier_id=self.sid,status='draft'));db.commit()
  self.assertIn('já possui pedido',self.client.post(url,data={'product_id':pid,'quantity':'1'}).text)
  with TestClient(app) as supplier:
   supplier.post('/login',data={'email':'supplier@test.local','password':'TestOnly123!'})
   self.assertEqual(supplier.get(f'/fornecedor/cotacoes/{qid}').status_code,200)
  self.assertEqual(self.client.get(f'/admin/cotacoes/{qid}/excel').status_code,200)
 def test_04_import(self):
  good='codigo;produto;unidade;codigo_barras\nIMP;Importado;UN;036000291452\n'
  self.assertIn('1 produtos importados',self.client.post('/admin/produtos/importar',files={'file':('p.csv',good.encode(),'text/csv')}).text)
  bad='codigo;produto;unidade;codigo_barras\nROLLBACK;Não salvar;UN;\nBAD;Inválido;UN;123\n'
  self.assertIn('Linha 3',self.client.post('/admin/produtos/importar',files={'file':('p.csv',bad.encode(),'text/csv')}).text)
  with SessionLocal() as db:self.assertIsNone(db.scalar(select(Product).where(Product.code=='ROLLBACK')))
  original='Codigo;Produto;Quantidade;Unidade\nLEGACY;Produto planilha;3;UN\n'
  r=self.client.post('/admin/cotacoes/nova',data={'title':'Legado','deadline':(datetime.now()+timedelta(days=2)).isoformat(timespec='minutes'),'suppliers':str(self.sid)},files={'product_file':('legacy.csv',original.encode(),'text/csv')})
  self.assertEqual(r.status_code,200);self.assertIn('Produto planilha',r.text)
 @classmethod
 def tearDownClass(cls):cls.client.__exit__(None,None,None)
