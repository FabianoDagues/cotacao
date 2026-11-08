"""Run: python -m unittest discover -s tests -v (httpx required)."""
import csv
import io
import os
import re
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func, inspect, event
from sqlalchemy.orm import sessionmaker
from app import main
from app.db import Base, get_db
from app.models import User, Quote, QuoteItem, Product
from app.internal_models import InternalProduct, InternalWithdrawal
from app.internal_use import barcode_key

PASSWORD='Testing123!'

class InternalUseTest(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  tmp=self.stack.enter_context(tempfile.TemporaryDirectory())
  self.engine=create_engine('sqlite:///'+tmp+'/test.db',connect_args={'check_same_thread':False})
  @event.listens_for(self.engine,'connect')
  def enable_fk(conn, _):conn.execute('PRAGMA foreign_keys=ON')
  self.addCleanup(self.engine.dispose)
  self.sessions=sessionmaker(bind=self.engine,autoflush=False)
  # Mimics existing V6/V7 tables before the V8 startup migration.
  Base.metadata.create_all(self.engine,tables=[t for t in Base.metadata.sorted_tables if not t.name.startswith('internal_')])
  with self.sessions() as db:
   for name,role in [('Admin','admin'),('Employee','employee'),('Other','employee'),('Supplier','supplier')]:
    db.add(User(name=name,email=name.lower()+'@test.local',role=role,password_hash=main.hash_password(PASSWORD),active=True,must_change_password=False))
   db.add(Quote(title='Cotação anterior',deadline=datetime(2030,1,1)))
   db.add(Product(code='OLD',description='Produto de cotação',base_unit='UN'))
   db.commit()
  self.stack.enter_context(patch.object(main,'engine',self.engine));self.stack.enter_context(patch.object(main,'SessionLocal',self.sessions))
  self.stack.enter_context(patch.dict(os.environ,{'ADMIN_EMAIL':'admin@test.local','ADMIN_PASSWORD':PASSWORD}))
  self.stack.enter_context(patch.object(main,'APP_TIMEZONE','America/Sao_Paulo'))
  def dependency():
   with self.sessions() as db:yield db
  main.app.dependency_overrides[get_db]=dependency
  self.stack.callback(lambda:main.app.dependency_overrides.pop(get_db,None))
  self.client=self.stack.enter_context(TestClient(main.app))
  self.login()
 def login(self,name='admin',password=PASSWORD):
  return self.client.post('/login',data={'email':name+'@test.local','password':password})
 def token(self,path='/admin/uso-interno/produtos'):
  r=self.client.get(path);self.assertEqual(r.status_code,200,r.text)
  return re.search(r'name="csrf_token" value="([^"]+)"',r.text).group(1)
 def create_product(self,code='7891234567895',description='Detergente 500 ml',sector='Loja',classification='Limpeza'):
  r=self.client.post('/admin/uso-interno/produtos',data={'csrf_token':self.token(),'description':description,'barcode':code,'sector':sector,'classification':classification})
  self.assertEqual(r.status_code,200,r.text)
  with self.sessions() as db:return db.scalar(select(InternalProduct.id).where(InternalProduct.barcode_key==barcode_key(code)))
 def withdrawal_form(self,code='7891234567895'):
  r=self.client.get('/uso-interno',params={'barcode':code});self.assertEqual(r.status_code,200,r.text)
  return {key:re.search(r'name="'+key+r'" value="([^"]+)"',r.text).group(1) for key in ('csrf_token','request_key','product_id')}
 def test_upgrade_and_health(self):
  self.assertIn('internal_withdrawals',inspect(self.engine).get_table_names())
  self.assertEqual(self.client.get('/healthz').json()['version'],'8.0')
  main.startup();main.startup()
  with self.sessions() as db:
   self.assertEqual(db.scalar(select(Quote.title)),'Cotação anterior')
   self.assertEqual(db.scalar(select(Product.description)),'Produto de cotação')
 def test_product_fields_validation_edit_and_alias(self):
  pid=self.create_product('036000291452')
  for code in ['0036000291452','00036000291452']:
   r=self.client.post('/admin/uso-interno/produtos',data={'csrf_token':self.token(),'description':'Duplicado','barcode':code,'sector':'Loja','classification':'Limpeza'})
   self.assertEqual(r.status_code,400);self.assertIn('já está cadastrado',r.text)
   self.assertIn('Detergente 500 ml',self.client.get('/uso-interno',params={'barcode':code}).text)
  base={'csrf_token':self.token(),'description':'Novo nome','barcode':'036000291452','sector':'Padaria','classification':'Insumo','id':pid}
  self.assertEqual(self.client.post('/admin/uso-interno/produtos',data=base).status_code,200)
  with self.sessions() as db:
   self.assertEqual(db.get(InternalProduct,pid).sector,'Padaria')
   self.assertEqual(db.scalar(select(func.count(InternalProduct.id))),1)
  for changes in [{'classification':'Outro'},{'barcode':'abc xyz'},{'description':''},{'sector':''}]:
   self.assertEqual(self.client.post('/admin/uso-interno/produtos',data={**base,**changes}).status_code,400)
  self.assertIn('Produto não cadastrado',self.client.get('/uso-interno?barcode=UNKNOWN').text)
  self.assertEqual(self.client.post('/admin/uso-interno/produtos',data={**base,'csrf_token':'wrong'}).status_code,403)
 def test_withdraw_quantity_idempotency_snapshot_and_privacy(self):
  pid=self.create_product();self.login('employee')
  form=self.withdrawal_form()
  for invalid in ['0','-1','NaN','Infinity','1e3','1.234,5','0.00001','10000000000','']:
   r=self.client.post('/uso-interno/retiradas',data={**form,'quantity':invalid})
   self.assertEqual(r.status_code,400,r.text)
  with self.sessions() as db:self.assertEqual(db.scalar(select(func.count(InternalWithdrawal.id))),0)
  for _ in range(2):
   r=self.client.post('/uso-interno/retiradas',data={**form,'quantity':'2,5'})
   self.assertEqual(r.status_code,200);self.assertIn('Retirada registrada',r.text)
  self.assertEqual(self.client.post('/uso-interno/retiradas',data={**form,'quantity':'3'}).status_code,400)
  with self.sessions() as db:
   self.assertEqual(db.scalar(select(func.count(InternalWithdrawal.id))),1)
   row=db.scalar(select(InternalWithdrawal));self.assertEqual(row.quantity,Decimal('2.5'));wid=row.id
   p=db.get(InternalProduct,pid);p.description='Mudou';p.sector='Padaria';p.classification='Insumo';db.commit()
   self.assertEqual(row.description,'Detergente 500 ml');self.assertEqual(row.sector,'Loja')
  self.login('other')
  self.assertNotIn('Retirada registrada',self.client.get(f'/uso-interno?saved={wid}').text)
  self.assertNotIn('Detergente 500 ml',self.client.get('/uso-interno').text)
  for path in ['/admin/uso-interno/produtos','/admin/uso-interno/relatorio','/admin/uso-interno/relatorio.csv','/admin/funcionarios','/admin','/fornecedor']:
   self.assertEqual(self.client.get(path).status_code,403,path)
  self.assertEqual(self.client.post(f'/admin/uso-interno/retiradas/{wid}/cancelar',data={'reason':'x'}).status_code,403)
  self.login('supplier');self.assertEqual(self.client.get('/uso-interno').status_code,403)
  self.client.post('/logout');self.assertEqual(self.client.get('/uso-interno').url.path,'/login')
 def test_employee_access_create_change_reset_disable(self):
  form={'csrf_token':self.token('/admin/funcionarios'),'name':'Novo Funcionário','email':'new@test.local','password':'Provisoria123!','active':'1'}
  self.assertEqual(self.client.post('/admin/funcionarios',data=form).status_code,200)
  self.assertEqual(self.client.post('/admin/funcionarios',data=form).status_code,400)
  with self.sessions() as db:uid=db.scalar(select(User.id).where(User.email=='new@test.local'))
  login=self.login('new','Provisoria123!');self.assertEqual(login.url.path,'/uso-interno/alterar-senha')
  self.assertEqual(self.client.get('/uso-interno').url.path,'/uso-interno/alterar-senha')
  token=self.token('/uso-interno/alterar-senha')
  payload={'csrf_token':token,'current_password':'Provisoria123!','password':'Pessoal123!','password_confirm':'Pessoal123!'}
  self.assertEqual(self.client.post('/uso-interno/alterar-senha',data={**payload,'current_password':'errada'}).status_code,400)
  result=self.client.post('/uso-interno/alterar-senha',data=payload)
  self.assertEqual(result.status_code,200);self.assertEqual(result.url.path,'/uso-interno')
  self.assertEqual(self.login('new','Provisoria123!').status_code,400)
  self.assertEqual(self.login('new','Pessoal123!').url.path,'/uso-interno')
  self.login();form.update(id=uid,password='OutraProvisoria123!',csrf_token=self.token('/admin/funcionarios'))
  self.assertEqual(self.client.post('/admin/funcionarios',data=form).status_code,200)
  self.assertEqual(self.login('new','Pessoal123!').status_code,400)
  self.assertEqual(self.login('new','OutraProvisoria123!').url.path,'/uso-interno/alterar-senha')
  self.login();form.update(password='',active='',csrf_token=self.token('/admin/funcionarios'))
  self.assertEqual(self.client.post('/admin/funcionarios',data=form).status_code,200)
  self.assertEqual(self.login('new','OutraProvisoria123!').status_code,400)
 def test_report_local_dates_totals_filters_csv_and_cancel(self):
  pid=self.create_product(description='=Detergente',sector='+Loja')
  with self.sessions() as db:
   uid=db.scalar(select(User.id).where(User.role=='employee'))
   records=[('before',datetime(2026,11,7,2,59,59),'7'),('first',datetime(2026,11,7,3),'2.5'),('last',datetime(2026,11,8,2,59,59),'1.25'),('after',datetime(2026,11,8,3),'10')]
   for key,dt,qty in records:
    db.add(InternalWithdrawal(product_id=pid,employee_id=uid,quantity=Decimal(qty),request_key=key,
       created_at=dt,description='=Detergente',barcode='00123',sector='+Loja',classification='Limpeza',employee_name='Funcionário teste'))
   db.commit();wid=db.scalar(select(InternalWithdrawal.id).where(InternalWithdrawal.request_key=='first'))
  params={'start':'2026-11-07','end':'2026-11-07'}
  report=self.client.get('/admin/uso-interno/relatorio',params=params)
  self.assertEqual(report.status_code,200);self.assertIn('3,75',report.text)
  content=self.client.get('/admin/uso-interno/relatorio.csv',params=params)
  rows=list(csv.reader(io.StringIO(content.content.decode('utf-8-sig')),delimiter=';'))
  self.assertEqual(len(rows),2);self.assertEqual(rows[1][-1],'3,75')
  self.assertEqual(rows[1][2],"'=Detergente");self.assertEqual(rows[1][3],"'00123");self.assertEqual(rows[1][4],"'+Loja")
  filtered=self.client.get('/admin/uso-interno/relatorio.csv',params={**params,'classification':'Insumo'})
  self.assertEqual(len(list(csv.reader(io.StringIO(filtered.content.decode('utf-8-sig')),delimiter=';'))),1)
  self.assertEqual(self.client.get('/admin/uso-interno/relatorio',params={'start':'invalid'}).status_code,400)
  self.assertEqual(self.client.get('/admin/uso-interno/relatorio',params={'start':'2026-11-08','end':'2026-11-07'}).status_code,400)
  data={'csrf_token':self.token('/admin/uso-interno/relatorio'),'reason':'Quantidade incorreta',**params}
  for _ in range(2):self.assertEqual(self.client.post(f'/admin/uso-interno/retiradas/{wid}/cancelar',data=data).status_code,200)
  r=self.client.get('/admin/uso-interno/relatorio.csv',params=params)
  rows=list(csv.reader(io.StringIO(r.content.decode('utf-8-sig')),delimiter=';'));self.assertEqual(rows[1][-1],'1,25')
  with self.sessions() as db:self.assertEqual(db.scalar(select(func.count(InternalWithdrawal.id))),4)
