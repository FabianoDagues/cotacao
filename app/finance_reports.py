"""Daily receipts and period summaries using the financial ledger."""
import hashlib
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .account_tree import account_path
from .db import get_db
from .models import CashGroup, CashTransaction, FinancialAccount
from .finance import require_admin, now_local, parse_date_value, templates, filtered_transactions
from .internal_use import csrf, check_csrf

router = APIRouter()
CHANNELS = (('credit', 'Cartão de crédito'), ('debit', 'Cartão de débito'),
            ('voucher', 'Voucher'), ('pix', 'Pix'))


def amount_input(value):
    value = str(value or '0').strip()
    if not re.fullmatch(r'\d{1,12}([.,]\d{1,2})?', value):
        raise ValueError('Informe valores positivos ou zero, com até duas casas decimais e sem separador de milhares.')
    return Decimal(value.replace(',', '.'))


@router.api_route('/admin/financeiro/totais-diarios', methods=['GET', 'POST'])
async def daily_receipts(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    accounts = db.scalars(select(FinancialAccount).where(FinancialAccount.active.is_(True)).order_by(FinancialAccount.name)).all()
    data = request.query_params
    error = None
    saved = False
    if request.method == 'POST':
        async with request.form() as form:
            check_csrf(request, form)
            data = dict(form)
    day = parse_date_value(data.get('day')) if data.get('day') else now_local().date()
    try:
        account_id = int(data.get('account_id') or 0)
    except ValueError:
        account_id = 0
    account = db.get(FinancialAccount, account_id)
    groups = {g.name: g for g in db.scalars(select(CashGroup).where(CashGroup.nature == 'income'))}
    existing = {}
    if day and account:
        entries = db.scalars(select(CashTransaction).where(
            CashTransaction.account_id == account_id,
            CashTransaction.transaction_date == day,
            CashTransaction.source == 'daily_total',
        ))
        existing = {tx.group_id: tx for tx in entries}
    values = {key: str(existing[groups[name].id].amount) if name in groups and groups[name].id in existing else '0'
              for key, name in CHANNELS}
    if request.method == 'POST':
        values = {key: str(data.get(key) or '0') for key, name in CHANNELS}
        try:
            if not day or not account or not account.active:
                raise ValueError('Selecione a data e uma conta financeira ativa.')
            # Serialize daily saves and bank imports for this account on PostgreSQL.
            db.scalar(select(FinancialAccount).where(FinancialAccount.id == account_id).with_for_update())
            amounts = {key: amount_input(values[key]) for key, name in CHANNELS}
            imported = db.scalar(select(CashTransaction.id).where(
                CashTransaction.account_id == account_id,
                CashTransaction.transaction_date == day,
                CashTransaction.source == 'bank_import',
                CashTransaction.transaction_type == 'income',
            ).limit(1))
            if imported and any(amounts.values()):
                raise ValueError('Já existem recebimentos importados nesta conta e data. Classifique esses lançamentos para evitar somar os mesmos valores novamente.')
            for key, name in CHANNELS:
                group = groups.get(name)
                if not group or not group.active:
                    raise ValueError(f'Ative o grupo {name} antes de salvar.')
                identity = hashlib.sha256(f'daily|{account_id}|{day}|{key}'.encode()).hexdigest()
                tx = db.scalar(select(CashTransaction).where(CashTransaction.import_key == identity))
                if amounts[key] == 0:
                    if tx: db.delete(tx)
                    continue
                if tx is None:
                    tx = CashTransaction(account_id=account_id, transaction_date=day, group_id=group.id,
                                         source='daily_total', transaction_type='income',
                                         import_key=identity, created_by_user_id=user.id,
                                         description=f'Total diário — {name}', reconciled=True)
                    db.add(tx)
                tx.amount = amounts[key]
            db.commit()
            saved = True
        except (ValueError, IntegrityError, InvalidOperation) as exc:
            db.rollback()
            error = str(exc) if isinstance(exc, ValueError) else 'Outro lançamento foi salvo ao mesmo tempo. Carregue o dia novamente.'
    return templates.TemplateResponse(request, 'finance_daily.html', dict(
        user=user, accounts=accounts, account_id=account_id, day=day or now_local().date(),
        channels=CHANNELS, values=values, error=error, saved=saved, csrf_token=csrf(request)),
        status_code=400 if error else 200)


@router.get('/admin/financeiro/sintetico')
def summary_report(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    today = now_local().date()
    start = parse_date_value(request.query_params.get('inicio')) if request.query_params.get('inicio') else today.replace(day=1)
    end = parse_date_value(request.query_params.get('fim')) if request.query_params.get('fim') else today
    try:
        account_id = int(request.query_params.get('conta') or 0)
    except ValueError:
        raise HTTPException(400, 'Conta inválida.')
    if not start or not end or start > end:
        raise HTTPException(400, 'Período inválido.')
    accounts = db.scalars(select(FinancialAccount).order_by(FinancialAccount.name)).all()
    if account_id and not any(a.id == account_id for a in accounts):
        raise HTTPException(400, 'Conta não encontrada.')
    groups = {g.id:g for g in db.scalars(select(CashGroup))}
    totals = defaultdict(lambda:dict(income=Decimal('0'), expense=Decimal('0')))
    transfers = dict(transfer_in=Decimal('0'), transfer_out=Decimal('0'))
    for tx in filtered_transactions(db, start, end, account_id or None, None, None):
        if tx.transaction_type in transfers:
            transfers[tx.transaction_type] += tx.amount
        else:
            totals[tx.group_id][tx.transaction_type] += tx.amount
    for _, name in CHANNELS:
        for g in groups.values():
            if g.name == name:
                totals[g.id]  # Show each payment channel even in a month with no receipts.
    income = sum((r['income'] for r in totals.values()), Decimal('0'))
    expense = sum((r['expense'] for r in totals.values()), Decimal('0'))
    rolled = defaultdict(lambda: dict(income=Decimal('0'), expense=Decimal('0')))
    for gid, amounts in totals.items():
        current = gid
        seen = set()
        while current not in seen:
            seen.add(current)
            rolled[current]['income'] += amounts['income']
            rolled[current]['expense'] += amounts['expense']
            group = groups.get(current)
            if not group or group.parent_id is None:
                break
            current = group.parent_id
    rows = []
    for gid, amounts in rolled.items():
        group = groups.get(gid)
        path = account_path(group, groups) if group else 'Sem classificação'
        rows.append(dict(name=path, parent='', **amounts))
    rows.sort(key=lambda r: r['name'].casefold())
    return templates.TemplateResponse(request, 'finance_summary.html',dict(
        user=user, start=start,end=end,accounts=accounts,account_id=account_id,rows=rows,
        income=income,expense=expense,result=income-expense,transfers=transfers))
