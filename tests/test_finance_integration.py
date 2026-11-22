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
