"""PDF integration regressions without embedding bank statements."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import select, func
from test_internal_use import InternalUseTest
from app.finance import account_balance
from app.models import FinancialAccount, CashTransaction
from app.pdf_statements import item

class PdfImportTest(InternalUseTest):
 def test_missing_balance_occurrences_and_retry(self):
  with self.sessions() as db:
   account=FinancialAccount(name='Teste PDF',bank_name='Nubank',account_type='bank',opening_balance=100)
   db.add(account);db.commit();aid=account.id
  row=item(date.today(),'Transferência recebida',Decimal('20'))
  parsed=dict(profile='nubank',rows=[row.copy(),row.copy()],opening=None,final=None,balance_date=None,warnings=[])
  with patch('app.pdf_statements.parse_pdf',return_value=parsed):
   for _ in range(2):
    result=self.client.post('/admin/financeiro/importar',data={'account_id':aid},files={'statement':('teste.pdf',b'fixture')})
    self.assertEqual(result.status_code,200)
  with self.sessions() as db:
   account=db.get(FinancialAccount,aid)
   self.assertEqual(account_balance(db,account),Decimal('140'))
   self.assertIsNone(account.last_statement_balance)
   self.assertEqual(db.scalar(select(func.count(CashTransaction.id))),2)
  parsed['profile']='itau'
  with patch('app.pdf_statements.parse_pdf',return_value=parsed):
   result=self.client.post('/admin/financeiro/importar',data={'account_id':aid},files={'statement':('outro.pdf',b'other fixture')})
   self.assertEqual(result.status_code,400)
  with self.sessions() as db:self.assertEqual(db.scalar(select(func.count(CashTransaction.id))),2)
