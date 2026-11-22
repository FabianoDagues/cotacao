"""Daily receipts replace totals, and reports respect period/account filters."""
import re
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import select, func
from test_internal_use import InternalUseTest
from app.models import CashTransaction, CashGroup, FinancialAccount
from app.pdf_statements import item

class DailyFinanceTest(InternalUseTest):
 def test_daily_totals_and_summary(self):
  with self.sessions() as db:
   account=db.scalar(select(FinancialAccount)); aid=account.id
  today=date.today()
  url='/admin/financeiro/totais-diarios'
  page=self.client.get(url)
  token=re.search(r'name="csrf_token" value="([^"]+)',self.client.get(url,params={'account_id':aid}).text)[1]
  data=dict(csrf_token=token,account_id=aid,day=today.isoformat(),credit='100',debit='50',voucher='20',pix='30')
  for _ in range(2):self.assertEqual(self.client.post(url,data=data).status_code,200)
  self.assertEqual(self.client.post(url,data={**data,'pix':'40'}).status_code,200)
  self.assertEqual(self.client.post(url,data={**data,'credit':'-1'}).status_code,400)
  self.assertEqual(self.client.post(url,data={**data,'csrf_token':'wrong'}).status_code,403)
  with self.sessions() as db:
   self.assertEqual(db.scalar(select(func.count(CashTransaction.id))),4)
   energy=db.scalar(select(CashGroup).where(CashGroup.name=='Energia'))
   db.add_all([
    CashTransaction(account_id=aid,transaction_date=today,transaction_type='expense',amount=10,group_id=energy.id,description='Conta de energia'),
    CashTransaction(account_id=aid,transaction_date=today,transaction_type='transfer_in',amount=999,description='Transferência'),
    CashTransaction(account_id=aid,transaction_date=today-timedelta(days=1),transaction_type='income',amount=888,description='Dia anterior')])
   db.commit()
  report=self.client.get('/admin/financeiro/sintetico',params={'inicio':str(today),'fim':str(today),'conta':aid})
  self.assertEqual(report.status_code,200)
  self.assertEqual(report.context['income'],Decimal('210'))
  self.assertEqual(report.context['expense'],Decimal('10'))
  self.assertEqual(report.context['result'],Decimal('200'))
  self.assertEqual(report.context['transfers']['transfer_in'],Decimal('999'))
  self.assertIn('Energia',report.text)
  self.assertEqual(self.client.get('/admin/financeiro/sintetico?inicio=2026-12-31&fim=2026-01-01').status_code,400)
  self.login('employee')
  self.assertEqual(self.client.post(url,data=data).status_code,403)
  self.assertEqual(self.client.get('/admin/financeiro/sintetico').status_code,403)

 def test_import_and_daily_cannot_duplicate_receipts(self):
  with self.sessions() as db:
   a=FinancialAccount(name='Banco teste',bank_name='Nubank',account_type='bank',opening_balance=0)
   db.add(a);db.commit();aid=a.id
  url='/admin/financeiro/totais-diarios'
  page=self.client.get(url,params={'account_id':aid})
  token=re.search(r'name="csrf_token" value="([^"]+)',page.text)[1]
  data=dict(csrf_token=token,account_id=aid,day=str(date.today()),credit='1',debit='0',voucher='0',pix='0')
  self.assertEqual(self.client.post(url,data=data).status_code,200)
  parsed=dict(profile='nubank',rows=[item(date.today(),'Pix recebido',Decimal('1'))],opening=Decimal('0'),final=Decimal('1'),balance_date=date.today(),warnings=[])
  with patch('app.pdf_statements.parse_pdf',return_value=parsed):
   def upload():
    return self.client.post('/admin/financeiro/importar',data={'account_id':aid},files={'statement':('p.pdf',b'fixture')})
   self.assertEqual(upload().status_code,400)
   self.assertEqual(self.client.post(url,data={**data,'credit':'0'}).status_code,200)
   self.assertEqual(upload().status_code,200)
  self.assertEqual(self.client.post(url,data=data).status_code,400)
  with self.sessions() as db:self.assertEqual(db.scalar(select(func.count(CashTransaction.id))),1)
