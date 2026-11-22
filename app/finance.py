import hashlib
import io
import json
import os
import re
import unicodedata
import uuid
from collections import defaultdict, Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session
from openpyxl import load_workbook

from .db import get_db, SessionLocal
from .account_tree import account_path, descendant_ids
from .models import (
    User,
    CashGroup,
    FinancialAccount,
    BankStatementImport,
    CashTransaction,
)

try:
    import xlrd
except Exception:  # pragma: no cover - no Railway será instalado pelo requirements
    xlrd = None

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["account_path"] = account_path
router = APIRouter()
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/Sao_Paulo")


def now_local() -> datetime:
    return datetime.now(ZoneInfo(APP_TIMEZONE)).replace(tzinfo=None)


def current_user(request: Request, db: Session) -> Optional[User]:
    uid = request.session.get("user_id")
    return db.get(User, uid) if uid else None


def require_admin(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user or not user.active or user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso negado")
    return user


def parse_money(value, default="0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    raw = str(value).strip().replace("R$", "").replace(" ", "")
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    raw = re.sub(r"[^0-9.\-]", "", raw)
    if not raw or raw in {"-", ".", "-."}:
        return Decimal(default)
    out = Decimal(raw)
    return -out if negative else out


def norm_text(value) -> str:
    s = str(value or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def parse_date_value(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
    s = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None


def tx_sign(transaction_type: str) -> Decimal:
    return Decimal("1") if transaction_type in {"income", "transfer_in"} else Decimal("-1")


def account_balance(db: Session, account: FinancialAccount, until: Optional[date] = None) -> Decimal:
    q = select(CashTransaction.transaction_type, CashTransaction.amount).where(CashTransaction.account_id == account.id)
    if until:
        q = q.where(CashTransaction.transaction_date <= until)
    total = Decimal(account.opening_balance or 0)
    for tx_type, amount in db.execute(q).all():
        total += tx_sign(tx_type) * Decimal(amount or 0)
    return total


def total_balance_before(db: Session, before_date: date) -> Decimal:
    total = Decimal("0")
    accounts = db.scalars(select(FinancialAccount).where(FinancialAccount.active.is_(True))).all()
    for acc in accounts:
        total += account_balance(db, acc, before_date - timedelta(days=1))
    return total


def seed_finance_defaults(session_factory=SessionLocal):
    defaults = [
        ("Receitas", "income", None),
        ("Dinheiro", "income", "Receitas"),
        ("Cartão de crédito", "income", "Receitas"),
        ("Cartão de débito", "income", "Receitas"),
        ("Voucher", "income", "Receitas"),
        ("Pix", "income", "Receitas"),
        ("Cartões recebimento", "income", "Receitas"),
        ("Pix / recebimentos diversos", "income", "Receitas"),
        ("Pagamento de mercadorias", "expense", None),
        ("Despesas de pessoal", "expense", None),
        ("Folha de pagamento", "expense", "Despesas de pessoal"),
        ("Diárias", "expense", "Despesas de pessoal"),
        ("INSS", "expense", "Despesas de pessoal"),
        ("Colaboradores extras / pró-labore / diárias", "expense", "Despesas de pessoal"),
        ("FGTS", "expense", "Despesas de pessoal"),
        ("GPS / INSS", "expense", "Despesas de pessoal"),
        ("Férias / rescisões / benefícios", "expense", "Despesas de pessoal"),
        ("Despesas operacionais", "expense", None),
        ("Aluguel", "expense", "Despesas operacionais"),
        ("Energia / água / gás", "expense", "Despesas operacionais"),
        ("Energia", "expense", "Despesas operacionais"),
        ("Água", "expense", "Despesas operacionais"),
        ("Telefone", "expense", "Despesas operacionais"),
        ("Insumos / embalagens", "expense", "Despesas operacionais"),
        ("Manutenção", "expense", "Despesas operacionais"),
        ("Logística / combustível / veículos", "expense", "Despesas operacionais"),
        ("Logística", "expense", "Despesas operacionais"),
        ("Transporte", "expense", "Logística"),
        ("Combustível", "expense", "Logística"),
        ("Manutenção de veículos", "expense", "Logística"),
        ("Telefone / Internet", "expense", "Despesas operacionais"),
        ("Limpeza / segurança / serviços", "expense", "Despesas operacionais"),
        ("Despesas de marketing", "expense", None),
        ("Publicidade / cartaz / panfleto / redes sociais", "expense", "Despesas de marketing"),
        ("Despesas administrativas", "expense", None),
        ("Software / sistemas", "expense", "Despesas administrativas"),
        ("Contabilidade / jurídico / associações", "expense", "Despesas administrativas"),
        ("Material de escritório / TI", "expense", "Despesas administrativas"),
        ("Impostos / tributos", "expense", None),
        ("ICMS", "expense", "Impostos / tributos"),
        ("PIS / COFINS / IRPJ / CSLL / ISS / DAS", "expense", "Impostos / tributos"),
        ("Investimentos", "expense", None),
        ("Consórcio / máquinas / energia solar", "expense", "Investimentos"),
        ("Aplicações financeiras", "expense", "Investimentos"),
        ("Empréstimos", "expense", "Investimentos"),
        ("Sócios", "expense", None),
        ("Despesas financeiras", "expense", None),
        ("Taxas de cartões", "expense", "Despesas financeiras"),
        ("Tarifas bancárias / juros", "expense", "Despesas financeiras"),
        ("Perdas", "expense", None),
        ("Transferências entre contas", "transfer", None),
        ("A classificar - Receitas", "income", None),
        ("A classificar - Despesas", "expense", None),
    ]
    with session_factory() as db:
        existing = {g.name: g for g in db.scalars(select(CashGroup)).all()}
        for name, nature, parent_name in defaults:
            if name in existing:
                continue
            parent = existing.get(parent_name) if parent_name else None
            g = CashGroup(name=name, nature=nature, parent_id=parent.id if parent else None, active=True)
            db.add(g)
            db.flush()
            existing[name] = g
        if not db.scalar(select(FinancialAccount.id).limit(1)):
            db.add(FinancialAccount(name="Caixa físico", account_type="cash", opening_balance=Decimal("0"), active=True))
        db.commit()


def finance_context(db: Session):
    accounts = db.scalars(select(FinancialAccount).order_by(FinancialAccount.account_type, FinancialAccount.name)).all()
    groups = db.scalars(select(CashGroup).order_by(CashGroup.nature, CashGroup.name)).all()
    group_map = {g.id: g for g in groups}
    for group in groups:
        group.display_path = account_path(group, group_map)
    groups.sort(key=lambda g: g.display_path.casefold())
    balances = {a.id: account_balance(db, a) for a in accounts}
    bank_total = sum((balances[a.id] for a in accounts if a.account_type == "bank" and a.active), Decimal("0"))
    cash_total = sum((balances[a.id] for a in accounts if a.account_type == "cash" and a.active), Decimal("0"))
    return accounts, groups, balances, bank_total, cash_total


def filtered_transactions(db: Session, start: date, end: date, account_id: Optional[int], group_id: Optional[int], tx_type: Optional[str]):
    q = select(CashTransaction).where(CashTransaction.transaction_date >= start, CashTransaction.transaction_date <= end)
    if account_id:
        q = q.where(CashTransaction.account_id == account_id)
    if group_id:
        groups = {g.id: g for g in db.scalars(select(CashGroup))}
        q = q.where(CashTransaction.group_id.in_(descendant_ids(group_id, groups)))
    if tx_type:
        if tx_type == "transfer":
            q = q.where(CashTransaction.transaction_type.in_(["transfer_in", "transfer_out"]))
        else:
            q = q.where(CashTransaction.transaction_type == tx_type)
    return db.scalars(q.order_by(CashTransaction.transaction_date.desc(), CashTransaction.id.desc())).all()



def transaction_return_url(tx: CashTransaction, return_query: str = "", message: str = "", db: Optional[Session] = None) -> str:
    """Retorna ao lançamento, respeitando os filtros que ainda o incluem."""
    query = parse_qs((return_query or "").lstrip("?"), keep_blank_values=False)

    def field(name: str) -> str:
        return query.get(name, [""])[0]

    day = tx.transaction_date
    start = parse_date_value(field("inicio")) or day.replace(day=1)
    end = parse_date_value(field("fim")) or day
    if start > end:
        start, end = day.replace(day=1), day
    # Se a data mudou na edição, o lançamento não deve sumir do período filtrado.
    start, end = min(start, day), max(end, day)
    params = {"inicio": start.isoformat(), "fim": end.isoformat()}

    if field("conta").isdigit() and int(field("conta")) == tx.account_id:
        params["conta"] = str(tx.account_id)

    if field("grupo").isdigit() and tx.group_id:
        selected_group = int(field("grupo"))
        if selected_group == tx.group_id:
            params["grupo"] = str(selected_group)
        elif db is not None:
            groups = {g.id: g for g in db.scalars(select(CashGroup)).all()}
            if tx.group_id in descendant_ids(selected_group, groups):
                params["grupo"] = str(selected_group)

    selected_type = field("tipo")
    if selected_type == tx.transaction_type or (selected_type == "transfer" and tx.transaction_type in {"transfer_in", "transfer_out"}):
        params["tipo"] = selected_type

    if message:
        params["msg"] = message
    return f"/admin/financeiro?{urlencode(params)}#lancamento-{tx.id}"


def summarize_transactions(transactions):
    entries = Decimal("0")
    expenses = Decimal("0")
    transfers_in = Decimal("0")
    transfers_out = Decimal("0")
    daily = defaultdict(lambda: {"income": Decimal("0"), "expense": Decimal("0"), "transfer": Decimal("0")})
    group_totals = defaultdict(lambda: {"income": Decimal("0"), "expense": Decimal("0")})
    for tx in transactions:
        amount = Decimal(tx.amount or 0)
        if tx.transaction_type == "income":
            entries += amount
            daily[tx.transaction_date]["income"] += amount
            if tx.group_id:
                group_totals[tx.group_id]["income"] += amount
        elif tx.transaction_type == "expense":
            expenses += amount
            daily[tx.transaction_date]["expense"] += amount
            if tx.group_id:
                group_totals[tx.group_id]["expense"] += amount
        elif tx.transaction_type == "transfer_in":
            transfers_in += amount
            daily[tx.transaction_date]["transfer"] += amount
        elif tx.transaction_type == "transfer_out":
            transfers_out += amount
            daily[tx.transaction_date]["transfer"] -= amount
    return entries, expenses, transfers_in, transfers_out, daily, group_totals


@router.get("/admin/financeiro", response_class=HTMLResponse)
def cashflow_page(
    request: Request,
    inicio: Optional[str] = None,
    fim: Optional[str] = None,
    conta: Optional[int] = None,
    grupo: Optional[int] = None,
    tipo: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = require_admin(request, db)
    today = now_local().date()
    start = parse_date_value(inicio) if inicio else today.replace(day=1)
    end = parse_date_value(fim) if fim else today
    if not start or not end or start > end:
        raise HTTPException(400, "Período inválido")
    txs = filtered_transactions(db, start, end, conta, grupo, tipo)
    accounts, groups, balances, bank_total, cash_total = finance_context(db)
    group_map = {g.id: g for g in groups}
    account_map = {a.id: a for a in accounts}
    entries, expenses, transfer_in, transfer_out, daily, group_totals = summarize_transactions(txs)
    opening = total_balance_before(db, start)
    daily_rows = []
    running = opening
    for d in sorted(daily):
        net = daily[d]["income"] - daily[d]["expense"]
        running += net
        daily_rows.append({"date": d, **daily[d], "net": net, "running": running})
    group_rows = []
    for gid, values in group_totals.items():
        g = group_map.get(gid)
        if g:
            group_rows.append({"group": g, **values, "net": values["income"] - values["expense"]})
    group_rows.sort(key=lambda x: abs(x["net"]), reverse=True)
    return templates.TemplateResponse(request, "cashflow.html", {
        "user": user,
        "transactions": txs,
        "accounts": accounts,
        "groups": groups,
        "balances": balances,
        "bank_total": bank_total,
        "cash_total": cash_total,
        "consolidated": bank_total + cash_total,
        "entries": entries,
        "expenses": expenses,
        "result": entries - expenses,
        "opening": opening,
        "period_end_balance": opening + entries - expenses,
        "daily_rows": daily_rows,
        "group_rows": group_rows,
        "group_map": group_map,
        "account_map": account_map,
        "start": start,
        "end": end,
        "selected_account": conta,
        "selected_group": grupo,
        "selected_type": tipo or "",
        "return_query": urlencode({k: v for k, v in {
            "inicio": start.isoformat(), "fim": end.isoformat(),
            "conta": conta, "grupo": grupo, "tipo": tipo,
        }.items() if v is not None and v != ""}),
        "message": request.query_params.get("msg"),
    })


@router.get("/admin/financeiro/imprimir", response_class=HTMLResponse)
def cashflow_print(
    request: Request,
    inicio: str,
    fim: str,
    conta: Optional[int] = None,
    grupo: Optional[int] = None,
    tipo: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = require_admin(request, db)
    start, end = parse_date_value(inicio), parse_date_value(fim)
    if not start or not end or start > end:
        raise HTTPException(400, "Período inválido")
    txs = filtered_transactions(db, start, end, conta, grupo, tipo)
    accounts, groups, balances, bank_total, cash_total = finance_context(db)
    group_map = {g.id: g for g in groups}
    account_map = {a.id: a for a in accounts}
    entries, expenses, _, _, daily, group_totals = summarize_transactions(txs)
    opening = total_balance_before(db, start)
    running = opening
    daily_rows = []
    for d in sorted(daily):
        net = daily[d]["income"] - daily[d]["expense"]
        running += net
        daily_rows.append({"date": d, **daily[d], "net": net, "running": running})
    group_rows = []
    for gid, values in group_totals.items():
        if gid in group_map:
            group_rows.append({"group": group_map[gid], **values, "net": values["income"] - values["expense"]})
    group_rows.sort(key=lambda x: abs(x["net"]), reverse=True)
    return templates.TemplateResponse(request, "cashflow_print.html", {
        "user": user, "transactions": txs, "start": start, "end": end,
        "entries": entries, "expenses": expenses, "result": entries-expenses,
        "opening": opening, "period_end_balance": opening+entries-expenses,
        "bank_total": bank_total, "cash_total": cash_total, "consolidated": bank_total+cash_total,
        "group_map": group_map, "account_map": account_map, "daily_rows": daily_rows, "group_rows": group_rows,
    })


@router.get("/admin/financeiro/lancamentos/novo", response_class=HTMLResponse)
def cash_transaction_new(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    accounts, groups, _, _, _ = finance_context(db)
    return templates.TemplateResponse(request, "cash_transaction_form.html", {"user": user, "accounts": accounts, "groups": groups, "tx": None, "today": now_local().date(), "error": None})


@router.post("/admin/financeiro/lancamentos/novo", response_class=HTMLResponse)
def cash_transaction_create(
    request: Request,
    transaction_date: str = Form(...),
    transaction_type: str = Form(...),
    account_id: int = Form(...),
    destination_account_id: Optional[int] = Form(None),
    group_id: Optional[int] = Form(None),
    description: str = Form(...),
    amount: str = Form(...),
    counterparty: str = Form(""),
    document_number: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_admin(request, db)
    accounts, groups, _, _, _ = finance_context(db)
    try:
        d = parse_date_value(transaction_date)
        value = abs(parse_money(amount))
        if not d or value <= 0:
            raise ValueError("Informe data e valor válidos.")
        if transaction_type == "transfer":
            if not destination_account_id or destination_account_id == account_id:
                raise ValueError("Selecione uma conta de destino diferente da conta de origem.")
            transfer_group = db.scalar(select(CashGroup).where(CashGroup.name == "Transferências entre contas"))
            ref = uuid.uuid4().hex
            db.add(CashTransaction(transaction_date=d, description=description.strip(), transaction_type="transfer_out", amount=value, account_id=account_id, group_id=transfer_group.id if transfer_group else None, counterparty=counterparty.strip() or None, document_number=document_number.strip() or None, notes=notes.strip() or None, source="transfer", transfer_ref=ref, created_by_user_id=user.id, reconciled=True))
            db.add(CashTransaction(transaction_date=d, description=description.strip(), transaction_type="transfer_in", amount=value, account_id=destination_account_id, group_id=transfer_group.id if transfer_group else None, counterparty=counterparty.strip() or None, document_number=document_number.strip() or None, notes=notes.strip() or None, source="transfer", transfer_ref=ref, created_by_user_id=user.id, reconciled=True))
        else:
            if transaction_type not in {"income", "expense"}:
                raise ValueError("Tipo de lançamento inválido.")
            group = db.get(CashGroup, group_id) if group_id else None
            if group and group.nature not in {transaction_type, "transfer"}:
                raise ValueError("O grupo escolhido não corresponde ao tipo do lançamento.")
            db.add(CashTransaction(transaction_date=d, description=description.strip(), transaction_type=transaction_type, amount=value, account_id=account_id, group_id=group_id or None, counterparty=counterparty.strip() or None, document_number=document_number.strip() or None, notes=notes.strip() or None, source="manual", created_by_user_id=user.id, reconciled=True))
        db.commit()
        return RedirectResponse("/admin/financeiro?msg=Lançamento salvo", 303)
    except Exception as exc:
        db.rollback()
        return templates.TemplateResponse(request, "cash_transaction_form.html", {"user": user, "accounts": accounts, "groups": groups, "tx": None, "today": now_local().date(), "error": str(exc)}, status_code=400)


@router.get("/admin/financeiro/lancamentos/{tx_id}/editar", response_class=HTMLResponse)
def cash_transaction_edit_page(tx_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    tx = db.get(CashTransaction, tx_id)
    if not tx:
        raise HTTPException(404, "Lançamento não encontrado")
    if tx.source == "daily_total":
        return RedirectResponse(f"/admin/financeiro/totais-diarios?account_id={tx.account_id}&day={tx.transaction_date}", 303)
    if tx.transfer_ref:
        raise HTTPException(400, "Transferências devem ser excluídas e lançadas novamente.")
    accounts, groups, _, _, _ = finance_context(db)
    return_query = request.url.query
    return templates.TemplateResponse(request, "cash_transaction_form.html", {
        "user": user, "accounts": accounts, "groups": groups, "tx": tx,
        "today": now_local().date(), "error": None,
        "return_query": return_query,
        "return_url": transaction_return_url(tx, return_query, db=db),
    })


@router.post("/admin/financeiro/lancamentos/{tx_id}/editar")
def cash_transaction_edit(
    tx_id: int, request: Request,
    transaction_date: str = Form(...), transaction_type: str = Form(...), account_id: int = Form(...),
    group_id: Optional[int] = Form(None), description: str = Form(...), amount: str = Form(...),
    counterparty: str = Form(""), document_number: str = Form(""), notes: str = Form(""), reconciled: Optional[str] = Form(None),
    return_query: str = Form(""), db: Session = Depends(get_db),
):
    require_admin(request, db)
    tx = db.get(CashTransaction, tx_id)
    if not tx or tx.transfer_ref:
        raise HTTPException(404, "Lançamento não encontrado ou não editável")
    if tx.source == "daily_total":
        return RedirectResponse(f"/admin/financeiro/totais-diarios?account_id={tx.account_id}&day={tx.transaction_date}", 303)
    d = parse_date_value(transaction_date); value = abs(parse_money(amount))
    if not d or value <= 0 or transaction_type not in {"income", "expense"}:
        raise HTTPException(400, "Dados inválidos")
    tx.transaction_date=d; tx.transaction_type=transaction_type; tx.account_id=account_id; tx.group_id=group_id or None
    tx.description=description.strip(); tx.amount=value; tx.counterparty=counterparty.strip() or None
    tx.document_number=document_number.strip() or None; tx.notes=notes.strip() or None; tx.reconciled=reconciled == "1"
    db.commit()
    return RedirectResponse(transaction_return_url(tx, return_query, "Lançamento alterado", db), 303)


@router.post("/admin/financeiro/lancamentos/{tx_id}/classificar")
def cash_transaction_classify(
    tx_id: int, request: Request, group_id: int = Form(...),
    return_query: str = Form(""), db: Session = Depends(get_db),
):
    require_admin(request, db)
    tx = db.get(CashTransaction, tx_id); group = db.get(CashGroup, group_id)
    if not tx or not group:
        raise HTTPException(404)
    expected = "income" if tx.transaction_type in {"income", "transfer_in"} else "expense"
    if group.nature not in {expected, "transfer"}:
        raise HTTPException(400, "Grupo incompatível com o lançamento")
    tx.group_id = group.id; tx.reconciled = True
    db.commit()
    return RedirectResponse(transaction_return_url(tx, return_query, "Lançamento classificado", db), 303)


@router.post("/admin/financeiro/lancamentos/{tx_id}/excluir")
def cash_transaction_delete(tx_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    tx = db.get(CashTransaction, tx_id)
    if not tx:
        raise HTTPException(404)
    if tx.transfer_ref:
        db.execute(delete(CashTransaction).where(CashTransaction.transfer_ref == tx.transfer_ref))
    else:
        db.delete(tx)
    db.commit()
    return RedirectResponse("/admin/financeiro?msg=Lançamento excluído", 303)


@router.get("/admin/financeiro/grupos", response_class=HTMLResponse)
def cash_groups_page(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    groups = db.scalars(select(CashGroup).order_by(CashGroup.nature, CashGroup.name)).all()
    group_map = {group.id: group for group in groups}
    children = defaultdict(list)
    for group in groups:
        group.display_path = account_path(group, group_map)
        children[group.parent_id if group.parent_id in group_map else None].append(group)
    groups.sort(key=lambda group: group.display_path.casefold())
    return templates.TemplateResponse(request, "cash_groups.html", {
        "user": user, "groups": groups, "children": children, "error": None})


@router.post("/admin/financeiro/grupos")
def cash_group_create(request: Request, name: str = Form(...), nature: str = Form(...), parent_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    require_admin(request, db)
    if nature not in {"income", "expense", "transfer"}:
        raise HTTPException(400)
    name = ' '.join(name.split())
    if not name or len(name) > 160:
        raise HTTPException(400, "Informe um nome com até 160 caracteres.")
    if parent_id:
        parent = db.get(CashGroup, parent_id)
        if not parent or not parent.active:
            raise HTTPException(400, "Selecione um grupo superior ativo.")
        if parent.nature != nature:
            raise HTTPException(400, "A natureza deve ser igual à do grupo superior.")
    if db.scalar(select(CashGroup).where(func.lower(CashGroup.name) == name.strip().lower())):
        raise HTTPException(400, "Já existe um grupo com esse nome")
    db.add(CashGroup(name=name.strip(), nature=nature, parent_id=parent_id or None, active=True)); db.commit()
    return RedirectResponse("/admin/financeiro/grupos", 303)


@router.post("/admin/financeiro/grupos/{group_id}/status")
def cash_group_status(group_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db); g=db.get(CashGroup, group_id)
    if not g: raise HTTPException(404)
    g.active = not g.active; db.commit(); return RedirectResponse("/admin/financeiro/grupos", 303)


@router.get("/admin/financeiro/contas", response_class=HTMLResponse)
def financial_accounts_page(request: Request, db: Session = Depends(get_db)):
    user=require_admin(request, db)
    accounts, _, balances, bank_total, cash_total = finance_context(db)
    return templates.TemplateResponse(request, "financial_accounts.html", {"user":user,"accounts":accounts,"balances":balances,"bank_total":bank_total,"cash_total":cash_total,"error":None})


@router.post("/admin/financeiro/contas")
def financial_account_create(request: Request, name: str=Form(...), account_type: str=Form(...), bank_name: str=Form(""), agency: str=Form(""), account_number: str=Form(""), opening_balance: str=Form("0"), db: Session=Depends(get_db)):
    require_admin(request, db)
    if account_type not in {"bank","cash"}: raise HTTPException(400)
    if db.scalar(select(FinancialAccount).where(func.lower(FinancialAccount.name)==name.strip().lower())): raise HTTPException(400,"Já existe uma conta com esse nome")
    db.add(FinancialAccount(name=name.strip(),account_type=account_type,bank_name=bank_name.strip() or None,agency=agency.strip() or None,account_number=account_number.strip() or None,opening_balance=parse_money(opening_balance),active=True)); db.commit()
    return RedirectResponse("/admin/financeiro/contas",303)


@router.post("/admin/financeiro/contas/{account_id}/editar")
def financial_account_edit(account_id:int, request:Request, name:str=Form(...), bank_name:str=Form(""), agency:str=Form(""), account_number:str=Form(""), opening_balance:str=Form("0"), active:Optional[str]=Form(None), db:Session=Depends(get_db)):
    require_admin(request,db); acc=db.get(FinancialAccount,account_id)
    if not acc: raise HTTPException(404)
    acc.name=name.strip(); acc.bank_name=bank_name.strip() or None; acc.agency=agency.strip() or None; acc.account_number=account_number.strip() or None; acc.opening_balance=parse_money(opening_balance); acc.active=active=="1"; db.commit()
    return RedirectResponse("/admin/financeiro/contas",303)


DATE_ALIASES = {"data", "data lancamento", "dt lancamento", "data movimento", "data transacao", "data da transacao"}
DESC_ALIASES = {"historico", "descricao", "lancamento", "memo", "detalhe", "descricao do lancamento", "historico lancamento"}
AMOUNT_ALIASES = {"valor", "valor lancamento", "valor movimento", "amount", "valor da transacao"}
DEBIT_ALIASES = {"debito", "valor debito", "saida", "saidas"}
CREDIT_ALIASES = {"credito", "valor credito", "entrada", "entradas"}
BALANCE_ALIASES = {"saldo", "saldo conta", "saldo atual", "saldo disponivel", "saldo r$", "saldo (r$)"}
DOC_ALIASES = {"documento", "doc", "numero documento", "nro documento", "id transacao", "identificador"}
TYPE_ALIASES = {"d/c", "dc", "tipo", "natureza", "credito/debito", "debito/credito"}
COUNTERPARTY_ALIASES = {"razao social", "favorecido", "beneficiario", "contraparte", "nome favorecido", "nome do favorecido", "pagador", "recebedor"}
TAXID_ALIASES = {"cnpj/cpf", "cpf/cnpj", "cnpj cpf", "cpf cnpj", "cnpj", "cpf"}

BANK_PROFILE_LABELS = {
    "itau": "Itaú",
    "sicoob": "Sicoob",
    "c6": "C6 Bank",
    "nubank": "Nubank",
    "generic": "Genérico",
}

FINANCE_OWN_NAME = norm_text(os.getenv("FINANCE_OWN_NAME", "MERCADO SANENGE"))
FINANCE_OWN_TAX_ID = re.sub(r"\D", "", os.getenv("FINANCE_OWN_TAX_ID", "06030826000180"))



def normalize_tax_id(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def profile_from_bank_name(bank_name: Optional[str]) -> str:
    bank = norm_text(bank_name)
    if "nubank" in bank or "nu pagamentos" in bank:
        return "nubank"
    if "itau" in bank:
        return "itau"
    if "sicoob" in bank:
        return "sicoob"
    if bank == "c6" or "c6 bank" in bank:
        return "c6"
    return "generic"


def detect_bank_profile(matrix, bank_name: Optional[str] = None) -> str:
    # Primeiro tenta reconhecer pelo conteúdo do arquivo; o nome cadastrado da conta é só fallback.
    sample_parts = []
    for row in matrix[:35]:
        sample_parts.extend(str(v or "") for v in row[:12])
    sample = norm_text(" ".join(sample_parts))
    if "itau" in sample or "saldo total disponivel dia" in sample:
        return "itau"
    if "sicoob" in sample or "sisbr" in sample or "pix receb.outra if" in sample:
        return "sicoob"
    if "c6 bank" in sample:
        return "c6"
    return profile_from_bank_name(bank_name)


def is_balance_description(description: str) -> bool:
    d = norm_text(description)
    return (
        d.startswith("saldo anterior")
        or d.startswith("saldo bloq")
        or d.startswith("saldo do dia")
        or d.startswith("saldo total disponivel dia")
        or d.startswith("saldo em conta corrente")
        or d == "saldo"
    )


def is_own_party(item) -> bool:
    text = norm_text(" ".join([
        item.get("description") or "",
        item.get("counterparty") or "",
        item.get("tax_id") or "",
    ]))
    tax = normalize_tax_id(item.get("tax_id"))
    if FINANCE_OWN_TAX_ID and tax and tax == FINANCE_OWN_TAX_ID:
        return True
    return bool(FINANCE_OWN_NAME and FINANCE_OWN_NAME in text)


def classify_statement_item(profile: str, item: dict) -> dict:
    signed = Decimal(item["signed_amount"])
    desc = norm_text(item.get("description"))
    counterparty = norm_text(item.get("counterparty"))
    full = f"{desc} {counterparty} {norm_text(item.get('tax_id'))}".strip()
    positive = signed > 0

    # Transferências da própria empresa nunca entram como receita/despesa consolidada.
    transfer_words = ("pix", "transf", "ted", "transferencia")
    if is_own_party(item) and any(w in full for w in transfer_words):
        return {
            "transaction_type": "transfer_in" if positive else "transfer_out",
            "group_name": "Transferências entre contas",
            "reconciled": False,
            "rule": "transferencia_conta_propria",
        }

    if positive:
        if any(name in full for name in ("voucher", "vale alimentacao", "vale refeicao")):
            return {"transaction_type":"income","group_name":"Voucher","reconciled":True,"rule":"voucher_identificado"}
        if "recebimento rede" in desc or "redecard" in counterparty or desc.startswith("cabal"):
            if "debito" in desc:
                return {"transaction_type":"income","group_name":"Cartão de débito","reconciled":True,"rule":"cartao_debito_identificado"}
            if "credito" in desc:
                return {"transaction_type":"income","group_name":"Cartão de crédito","reconciled":True,"rule":"cartao_credito_identificado"}
        if any(word in desc for word in ("pix receb", "recebida pelo pix", "pix qr code recebido")):
            return {"transaction_type":"income","group_name":"Pix","reconciled":True,"rule":"pix_recebido"}
    if profile == "itau":
        if positive and ("recebimento rede" in desc or desc.startswith("cabal caba") or "redecard" in counterparty):
            return {"transaction_type":"income","group_name":"Cartões recebimento","reconciled":True,"rule":"itau_cartoes"}
        if positive and "pix recebido" in desc:
            return {"transaction_type":"income","group_name":"Pix / recebimentos diversos","reconciled":True,"rule":"itau_pix_recebido"}
        if positive and desc == "recebimentos":
            return {"transaction_type":"income","group_name":"Pix / recebimentos diversos","reconciled":True,"rule":"itau_recebimentos"}
        if not positive and "vivo" in full:
            return {"transaction_type":"expense","group_name":"Telefone / Internet","reconciled":True,"rule":"itau_vivo"}
        if not positive and any(x in full for x in ("tarifa", "juros", "encargo", "iof")):
            return {"transaction_type":"expense","group_name":"Tarifas bancárias / juros","reconciled":True,"rule":"itau_tarifas"}
        if not positive and ("emprestimo" in full or "financiamento" in full):
            return {"transaction_type":"expense","group_name":"Empréstimos","reconciled":True,"rule":"itau_emprestimo"}
        # BOLETO PAGO pode ser mercadoria, serviço, imposto etc.; não forçar grupo.
        if not positive and "boleto pago" in desc:
            return {"transaction_type":"expense","group_name":"A classificar - Despesas","reconciled":False,"rule":"itau_boleto_generico"}

    if profile == "sicoob":
        card_names = ("alelo", "ticket servicos", "cooper card", "naip", "verocheque")
        if positive and any(name in full for name in card_names):
            return {"transaction_type":"income","group_name":"Cartões recebimento","reconciled":True,"rule":"sicoob_cartoes"}
        if positive and ("cred.ted-str" in desc or "cred.tr.ct.intercre" in desc) and any(name in full for name in card_names):
            return {"transaction_type":"income","group_name":"Cartões recebimento","reconciled":True,"rule":"sicoob_cartoes_credito"}
        if positive and "pix receb" in desc:
            return {"transaction_type":"income","group_name":"Pix / recebimentos diversos","reconciled":True,"rule":"sicoob_pix_recebido"}
        if not positive and ("deb pacote servicos" in desc or "pacote servicos" in desc or "tarifa" in full):
            return {"transaction_type":"expense","group_name":"Tarifas bancárias / juros","reconciled":True,"rule":"sicoob_tarifas"}
        if not positive and "emprestimo" in full:
            return {"transaction_type":"expense","group_name":"Empréstimos","reconciled":True,"rule":"sicoob_emprestimo"}
        if not positive and ("pix.emit" in desc or "pagamento pix" in full):
            return {"transaction_type":"expense","group_name":"A classificar - Despesas","reconciled":False,"rule":"sicoob_pix_pagamento"}

    # Regras seguras, independentes do banco.
    if positive and "pix receb" in desc:
        return {"transaction_type":"income","group_name":"Pix / recebimentos diversos","reconciled":True,"rule":"generic_pix_recebido"}
    if not positive and any(x in full for x in ("tarifa", "juros banc", "pacote servicos")):
        return {"transaction_type":"expense","group_name":"Tarifas bancárias / juros","reconciled":True,"rule":"generic_tarifa"}
    return {
        "transaction_type": "income" if positive else "expense",
        "group_name": "A classificar - Receitas" if positive else "A classificar - Despesas",
        "reconciled": False,
        "rule": "generic",
    }


def link_pending_internal_transfers(db: Session) -> int:
    pending = db.scalars(
        select(CashTransaction).where(
            CashTransaction.source == "bank_import",
            CashTransaction.transaction_type.in_(["transfer_in", "transfer_out"]),
            CashTransaction.transfer_ref.is_(None),
        ).order_by(CashTransaction.transaction_date, CashTransaction.id)
    ).all()
    linked = 0
    used = set()
    for tx in pending:
        if tx.id in used:
            continue
        opposite = "transfer_out" if tx.transaction_type == "transfer_in" else "transfer_in"
        candidates = [
            other for other in pending
            if other.id not in used
            and other.id != tx.id
            and other.account_id != tx.account_id
            and other.transaction_type == opposite
            and other.transaction_date == tx.transaction_date
            and Decimal(other.amount or 0) == Decimal(tx.amount or 0)
        ]
        if len(candidates) == 1:
            other = candidates[0]
            ref = uuid.uuid4().hex
            tx.transfer_ref = ref
            other.transfer_ref = ref
            tx.reconciled = True
            other.reconciled = True
            used.update({tx.id, other.id})
            linked += 1
    return linked


def read_statement_matrix(filename: str, content: bytes):
    ext = Path(filename).suffix.lower()
    if ext == ".xlsx":
        book = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        sheet = book[book.sheetnames[0]]
        return [[cell.value for cell in row] for row in sheet.iter_rows()]
    if ext == ".xls":
        if xlrd is None:
            raise ValueError("Suporte a .xls indisponível. Atualize as dependências do servidor.")
        book = xlrd.open_workbook(file_contents=content)
        sheet = book.sheet_by_index(0)
        return [sheet.row_values(i) for i in range(sheet.nrows)]
    raise ValueError("Envie um extrato .xls ou .xlsx")


def find_column(headers, aliases):
    for i, h in enumerate(headers):
        if h in aliases:
            return i
    for i, h in enumerate(headers):
        if any(a in h for a in aliases if len(a) >= 5):
            return i
    return None


def detect_statement_layout(matrix):
    best = None
    for idx, row in enumerate(matrix[:30]):
        headers = [norm_text(v) for v in row]
        date_col = find_column(headers, DATE_ALIASES)
        desc_col = find_column(headers, DESC_ALIASES)
        amount_col = find_column(headers, AMOUNT_ALIASES)
        debit_col = find_column(headers, DEBIT_ALIASES)
        credit_col = find_column(headers, CREDIT_ALIASES)
        if date_col is not None and desc_col is not None and (amount_col is not None or debit_col is not None or credit_col is not None):
            score = 3 + sum(x is not None for x in [amount_col, debit_col, credit_col, find_column(headers,BALANCE_ALIASES), find_column(headers,DOC_ALIASES), find_column(headers,TYPE_ALIASES), find_column(headers,COUNTERPARTY_ALIASES), find_column(headers,TAXID_ALIASES)])
            candidate = {"header_row":idx,"date":date_col,"desc":desc_col,"amount":amount_col,"debit":debit_col,"credit":credit_col,"balance":find_column(headers,BALANCE_ALIASES),"doc":find_column(headers,DOC_ALIASES),"kind":find_column(headers,TYPE_ALIASES),"counterparty":find_column(headers,COUNTERPARTY_ALIASES),"tax_id":find_column(headers,TAXID_ALIASES),"score":score}
            if best is None or score > best["score"]: best=candidate
    if not best:
        raise ValueError("Não consegui reconhecer as colunas do extrato. Envie este modelo de banco para criarmos um importador específico.")
    return best


def row_value(row, idx):
    return row[idx] if idx is not None and idx < len(row) else None


def parse_statement_rows(matrix):
    layout = detect_statement_layout(matrix)
    parsed=[]
    for raw in matrix[layout["header_row"]+1:]:
        d=parse_date_value(row_value(raw,layout["date"])); desc=str(row_value(raw,layout["desc"]) or "").strip()
        if not d or not desc or is_balance_description(desc):
            continue
        amount=Decimal("0")
        if layout["credit"] is not None or layout["debit"] is not None:
            credit=parse_money(row_value(raw,layout["credit"])); debit=parse_money(row_value(raw,layout["debit"])); amount=abs(credit) if credit else -abs(debit)
        else:
            amount=parse_money(row_value(raw,layout["amount"]))
            kind=norm_text(row_value(raw,layout["kind"])) if layout["kind"] is not None else ""
            if amount > 0 and kind in {"d","debito","saida","s"}: amount=-amount
            elif amount < 0 and kind in {"c","credito","entrada","e"}: amount=abs(amount)
        if amount == 0: continue
        bal=row_value(raw,layout["balance"]); balance=parse_money(bal) if bal not in (None,"") else None
        doc=str(row_value(raw,layout["doc"]) or "").strip() or None
        counterparty=str(row_value(raw,layout["counterparty"]) or "").strip() or None
        tax_id=str(row_value(raw,layout["tax_id"]) or "").strip() or None
        parsed.append({"date":d,"description":desc,"signed_amount":amount,"balance":balance,"document":doc,"counterparty":counterparty,"tax_id":tax_id,"raw":raw})
    if not parsed: raise ValueError("Nenhum lançamento válido foi encontrado no extrato.")
    return parsed


@router.get("/admin/financeiro/importar", response_class=HTMLResponse)
def bank_import_page(request:Request, db:Session=Depends(get_db)):
    user=require_admin(request,db)
    accounts=db.scalars(select(FinancialAccount).where(FinancialAccount.account_type=="bank").order_by(FinancialAccount.name)).all()
    imports=db.scalars(select(BankStatementImport).order_by(BankStatementImport.imported_at.desc()).limit(20)).all()
    account_map={a.id:a for a in accounts}
    account_profiles={a.id:BANK_PROFILE_LABELS.get(profile_from_bank_name(a.bank_name), "Genérico") for a in accounts}
    return templates.TemplateResponse(request,"bank_import.html",{"user":user,"accounts":accounts,"imports":imports,"account_map":account_map,"account_profiles":account_profiles,"error":None,"message":request.query_params.get("msg")})


@router.post("/admin/financeiro/importar", response_class=HTMLResponse)
async def bank_import_submit(request:Request, account_id:int=Form(...), statement:UploadFile=File(...), pdf_password:str=Form(""), db:Session=Depends(get_db)):
    user=require_admin(request,db); account=db.scalar(select(FinancialAccount).where(FinancialAccount.id==account_id).with_for_update())
    if not account or account.account_type!="bank": raise HTTPException(400,"Selecione uma conta bancária")
    content=await statement.read(); file_hash=hashlib.sha256(content).hexdigest()
    if db.scalar(select(BankStatementImport).where(BankStatementImport.financial_account_id==account.id,BankStatementImport.file_hash==file_hash)):
        return RedirectResponse("/admin/financeiro/importar?msg=Este extrato já foi importado",303)
    try:
        pdf_data = None
        if (statement.filename or "").lower().endswith(".pdf"):
            from .pdf_statements import parse_pdf
            pdf_data = parse_pdf(content, pdf_password)
            file_profile = pdf_data["profile"]
        else:
            matrix=read_statement_matrix(statement.filename or "extrato.xlsx",content)
            file_profile=detect_bank_profile(matrix, None)
        account_profile=profile_from_bank_name(account.bank_name)
        if file_profile != "generic" and account_profile != "generic" and file_profile != account_profile:
            raise ValueError(
                f"O arquivo parece ser do banco {BANK_PROFILE_LABELS.get(file_profile,file_profile)}, "
                f"mas a conta selecionada está cadastrada como {BANK_PROFILE_LABELS.get(account_profile,account.bank_name)}. "
                "Selecione a conta correta ou confira o arquivo antes de importar."
            )
        profile=file_profile if file_profile != "generic" else account_profile
        rows=pdf_data["rows"] if pdf_data else parse_statement_rows(matrix)
        groups_by_name={g.name:g for g in db.scalars(select(CashGroup)).all()}
        batch=BankStatementImport(
            financial_account_id=account.id,
            filename=statement.filename or "extrato",
            file_hash=file_hash,
            imported_by_user_id=user.id,
            rows_count=0,
            detected_bank=BANK_PROFILE_LABELS.get(profile, profile),
        )
        db.add(batch); db.flush()
        existing_tx_count=db.scalar(select(func.count(CashTransaction.id)).where(CashTransaction.account_id==account.id)) or 0
        imported=0; auto_classified=0; signed_sum=Decimal("0"); last_balance=None; last_balance_date=None
        occurrences = Counter()
        for item in rows:
            signed=item["signed_amount"]; balance=item["balance"]
            key_source=f'{account.id}|{item["date"]}|{norm_text(item["description"])}|{norm_text(item.get("counterparty"))}|{normalize_tax_id(item.get("tax_id"))}|{signed}|{item["document"] or ""}|{balance if balance is not None else ""}'
            if pdf_data:
                occurrences[key_source] += 1
                if occurrences[key_source] > 1:
                    key_source += f'|ocorrencia:{occurrences[key_source]}'
            import_key=hashlib.sha256(key_source.encode("utf-8")).hexdigest()
            if db.scalar(select(CashTransaction.id).where(CashTransaction.import_key==import_key)): continue
            cls=classify_statement_item(profile,item)
            if cls["transaction_type"] == "income":
                daily_total = db.scalar(select(CashTransaction.id).where(
                    CashTransaction.account_id == account.id,
                    CashTransaction.transaction_date == item["date"],
                    CashTransaction.source == "daily_total",
                ).limit(1))
                if daily_total:
                    raise ValueError(f'Existem totais diários em {item["date"]:%d/%m/%Y} nesta conta. Zere esses totais antes de importar os mesmos recebimentos pelo extrato.')
            group=groups_by_name.get(cls["group_name"])
            raw_payload={"profile":profile,"rule":cls["rule"],"tax_id":item.get("tax_id"),"row":item["raw"]}
            tx=CashTransaction(
                transaction_date=item["date"],
                description=item["description"],
                transaction_type=cls["transaction_type"],
                amount=abs(signed),
                account_id=account.id,
                group_id=group.id if group else None,
                counterparty=item.get("counterparty"),
                document_number=item["document"],
                source="bank_import",
                bank_import_id=batch.id,
                import_key=import_key,
                reconciled=bool(cls["reconciled"]),
                raw_data=json.dumps(raw_payload,default=str,ensure_ascii=False),
                created_by_user_id=user.id,
            )
            db.add(tx)
            imported+=1; signed_sum+=signed
            if cls["reconciled"]: auto_classified+=1
            if balance is not None and (last_balance_date is None or item["date"]>=last_balance_date): last_balance=balance; last_balance_date=item["date"]
        db.flush()
        linked=link_pending_internal_transfers(db)
        batch.rows_count=imported
        if pdf_data and imported:
            last_balance = pdf_data["final"]
            last_balance_date = pdf_data["balance_date"]
            if existing_tx_count == 0 and Decimal(account.opening_balance or 0) == 0:
                if pdf_data["opening"] is not None:
                    account.opening_balance = pdf_data["opening"]
                elif last_balance is not None:
                    account.opening_balance = last_balance - sum((r["signed_amount"] for r in rows), Decimal("0"))
        if last_balance is not None and (account.last_statement_date is None or last_balance_date >= account.last_statement_date):
            account.last_statement_balance=last_balance; account.last_statement_date=last_balance_date
            if not pdf_data and existing_tx_count==0 and Decimal(account.opening_balance or 0)==0:
                account.opening_balance=last_balance-signed_sum
        db.commit()
        profile_label=BANK_PROFILE_LABELS.get(profile,profile)
        msg=f"{imported} lançamentos importados ({profile_label}); {auto_classified} classificados automaticamente"
        if pdf_data and pdf_data["warnings"]:
            msg += "; " + " ".join(pdf_data["warnings"])
        if pdf_data and pdf_data["final"] is None:
            msg += "; saldo calculado pelo saldo inicial cadastrado e pelos lançamentos. Confira o saldo inicial da conta"
        if linked:
            msg += f"; {linked} transferência(s) entre contas vinculada(s)"
        return RedirectResponse(f"/admin/financeiro/importar?msg={msg}",303)
    except Exception as exc:
        db.rollback()
        accounts=db.scalars(select(FinancialAccount).where(FinancialAccount.account_type=="bank").order_by(FinancialAccount.name)).all()
        imports=db.scalars(select(BankStatementImport).order_by(BankStatementImport.imported_at.desc()).limit(20)).all()
        account_map={a.id:a for a in accounts}
        account_profiles={a.id:BANK_PROFILE_LABELS.get(profile_from_bank_name(a.bank_name), "Genérico") for a in accounts}
        return templates.TemplateResponse(request,"bank_import.html",{"user":user,"accounts":accounts,"imports":imports,"account_map":account_map,"account_profiles":account_profiles,"error":str(exc),"message":None},status_code=400)

