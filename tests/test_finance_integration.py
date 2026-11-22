"""Financial routes coexist with internal products and an existing finance database."""
import io
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import select, func
from test_internal_use import InternalUseTest
from app import main, finance
from app.models import CashTransaction, FinancialAccount, CashGroup
from app.internal_models import InternalProduct

class FinanceIntegrationTest(InternalUseTest):
 def test_finance_routes_and_existing_data(self):
  paths=['/admin/financeiro','/admin/financeiro/imprimir?inicio=2026-01-01&fim=2026-12-31','/admin/financeiro/contas','/admin/financeiro/grupos','/admin/financeiro/importar','/admin/financeiro/lancamentos/novo']
  for path in paths:self.assertEqual(self.client.get(path).status_code,200,path)
  self.create_product()
  with self.sessions() as db:
   account=db.scalar(select(FinancialAccount.id))
   group_count=db.scalar(select(func.count(CashGroup.id)))
  data=dict(transaction_date=date.today().isoformat(),transaction_type='income',account_id=account,description='Receita de teste',amount='125,50')
  self.assertEqual(self.client.post('/admin/financeiro/lancamentos/novo',data=data).status_code,200)
  with self.sessions() as db:
   tx=db.scalar(select(CashTransaction));self.assertEqual(tx.amount,Decimal('125.50'));tid=tx.id
  main.startup();main.startup()
  with self.sessions() as db:
   self.assertEqual(db.get(CashTransaction,tid).description,'Receita de teste')
   self.assertEqual(db.scalar(select(func.count(InternalProduct.id))),1)
   self.assertEqual(db.scalar(select(func.count(CashGroup.id))),group_count)
  self.assertEqual(self.client.get(f'/admin/financeiro/lancamentos/{tid}/editar').status_code,200)
  self.client.post('/admin/financeiro/contas',data=dict(name='Banco teste',account_type='bank',opening_balance='0'))
  with self.sessions() as db: bank=db.scalar(select(FinancialAccount.id).where(FinancialAccount.name=='Banco teste'))
  # Fixture at the statement-parser boundary exercises the actual upload, deduplication and persistence.
  matrix=[['Data','Descrição','Valor'],[date.today().isoformat(),'Recebimento teste','20,00']]
  with patch.object(finance,'read_statement_matrix',return_value=matrix):
   for _ in range(2):
    self.assertEqual(self.client.post('/admin/financeiro/importar',data={'account_id':bank},files={'statement':('teste.xlsx',b'test fixture')}).status_code,200)
  with self.sessions() as db:self.assertEqual(db.scalar(select(func.count(CashTransaction.id)).where(CashTransaction.source=='bank_import')),1)
  self.login('employee')
  for path in paths:self.assertEqual(self.client.get(path).status_code,403,path)
  self.assertEqual(self.client.get('/uso-interno').status_code,200)

 def test_edit_statement_returns_to_same_row_and_keeps_filters(self):
  from urllib.parse import parse_qs, urlsplit
  with self.sessions() as db:
   account=db.scalar(select(FinancialAccount))
   group=db.scalar(select(CashGroup).where(CashGroup.name=='Aluguel'))
   tx=CashTransaction(transaction_date=date(2026,11,10),transaction_type='expense',
     account_id=account.id,group_id=group.id,description='Tarifa do extrato',
     amount=Decimal('15.00'),source='bank_import',reconciled=False)
   db.add(tx);db.commit();tx_id=tx.id;account_id=account.id;group_id=group.id
  filters={'inicio':'2026-11-01','fim':'2026-11-30','conta':str(account_id),'tipo':'expense'}
  page=self.client.get('/admin/financeiro',params=filters)
  self.assertEqual(page.status_code,200)
  self.assertIn(f'id="lancamento-{tx_id}"',page.text)
  self.assertIn(f'/admin/financeiro/lancamentos/{tx_id}/editar?inicio=',page.text)
  edit=self.client.get(f'/admin/financeiro/lancamentos/{tx_id}/editar',params=filters)
  self.assertEqual(edit.status_code,200)
  self.assertIn('name="return_query"',edit.text)
  self.assertIn(f'#lancamento-{tx_id}',edit.text)
  from urllib.parse import urlencode
  posted=dict(transaction_date='2026-11-10',transaction_type='expense',account_id=str(account_id),
    group_id=str(group_id),description='Tarifa corrigida',amount='16,00',return_query=urlencode(filters))
  result=self.client.post(f'/admin/financeiro/lancamentos/{tx_id}/editar',data=posted,follow_redirects=False)
  self.assertEqual(result.status_code,303)
  url=urlsplit(result.headers['location'])
  self.assertEqual(url.path,'/admin/financeiro')
  self.assertEqual(url.fragment,f'lancamento-{tx_id}')
  query=parse_qs(url.query)
  for key,value in filters.items():self.assertEqual(query[key],[value])
  final=self.client.get(result.headers['location'])
  self.assertIn('Tarifa corrigida',final.text)
  self.assertIn(f'id="lancamento-{tx_id}"',final.text)

  # Mesmo com mudança de data, tipo e grupo, o registro permanece visível.
  posted.update(transaction_date='2026-12-03',transaction_type='income',group_id='',description='Receita reclassificada')
  changed=self.client.post(f'/admin/financeiro/lancamentos/{tx_id}/editar',data=posted,follow_redirects=False)
  target=urlsplit(changed.headers['location']);new_filters=parse_qs(target.query)
  self.assertEqual(target.fragment,f'lancamento-{tx_id}')
  self.assertEqual(new_filters['inicio'],['2026-11-01'])
  self.assertEqual(new_filters['fim'],['2026-12-03'])
  self.assertEqual(new_filters['conta'],[str(account_id)])
  self.assertNotIn('tipo',new_filters)
  self.assertIn('Receita reclassificada',self.client.get(changed.headers['location']).text)

 def test_classify_statement_keeps_row_in_view(self):
  from urllib.parse import urlencode, urlsplit, parse_qs
  with self.sessions() as db:
   account=db.scalar(select(FinancialAccount))
   group=db.scalar(select(CashGroup).where(CashGroup.name=='Aluguel'))
   tx=CashTransaction(transaction_date=date(2026,11,10),transaction_type='expense',
     account_id=account.id,description='Pagamento em aberto',amount=Decimal('89.00'),
     source='bank_import',reconciled=False)
   db.add(tx);db.commit();tx_id=tx.id;account_id=account.id;group_id=group.id
  filters={'inicio':'2026-11-01','fim':'2026-11-30','conta':str(account_id),'tipo':'expense'}
  page=self.client.get('/admin/financeiro',params=filters)
  self.assertIn('name="return_query"',page.text)
  result=self.client.post(f'/admin/financeiro/lancamentos/{tx_id}/classificar',
    data={'group_id':str(group_id),'return_query':urlencode(filters)},follow_redirects=False)
  self.assertEqual(result.status_code,303)
  target=urlsplit(result.headers['location'])
  self.assertEqual(target.fragment,f'lancamento-{tx_id}')
  for key,value in filters.items():self.assertEqual(parse_qs(target.query)[key],[value])
  self.assertIn(f'id="lancamento-{tx_id}"',self.client.get(result.headers['location']).text)
