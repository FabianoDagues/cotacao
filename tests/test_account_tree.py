from datetime import date
from decimal import Decimal
from sqlalchemy import select
from test_internal_use import InternalUseTest
from app.models import CashGroup, CashTransaction, FinancialAccount

class AccountTreeTest(InternalUseTest):
 def test_nested_creation_filter_and_subtotals(self):
  with self.sessions() as db:
   logistics=db.scalar(select(CashGroup).where(CashGroup.name=='Logística'))
   operations=db.scalar(select(CashGroup).where(CashGroup.name=='Despesas operacionais'))
   aid=db.scalar(select(FinancialAccount.id))
   lid,oid=logistics.id,operations.id
  url='/admin/financeiro/grupos'
  self.assertEqual(self.client.post(url,data={'name':'Pedágios','nature':'expense','parent_id':lid}).status_code,200)
  self.assertEqual(self.client.post(url,data={'name':'Inválido','nature':'income','parent_id':lid}).status_code,400)
  with self.sessions() as db:
   leaf=db.scalar(select(CashGroup).where(CashGroup.name=='Pedágios'))
   db.add(CashTransaction(account_id=aid,transaction_date=date.today(),description='Pedágio teste',group_id=leaf.id,transaction_type='expense',amount=Decimal('35')))
   db.commit()
  self.assertIn('Pedágio teste', self.client.get('/admin/financeiro',params={'grupo':oid}).text)
  report=self.client.get('/admin/financeiro/sintetico')
  self.assertEqual(report.context['expense'],Decimal('35'))
  amounts={r['name']:r['expense'] for r in report.context['rows']}
  for name in ['Despesas operacionais','Despesas operacionais → Logística','Despesas operacionais → Logística → Pedágios']:
   self.assertEqual(amounts[name],Decimal('35'))
  self.assertIn('Logística → Pedágios',self.client.get('/admin/financeiro/lancamentos/novo').text)
