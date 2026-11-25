import hashlib
import io
import json
import os
import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session
from openpyxl import load_workbook

from .db import get_db, SessionLocal
from .models import (
    User,
    CashGroup,
    FinancialAccount,
    BankStatementImport,
    CashTransaction,
    CashClassificationRule,
)

try:
    import xlrd
except Exception:  # pragma: no cover - no Railway será instalado pelo requirements
    xlrd = None

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
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


def total_balance_before(db: Session, before_date: date, account_id: Optional[int] = None) -> Decimal:
    total = Decimal("0")
    q = select(FinancialAccount).where(FinancialAccount.active.is_(True))
    if account_id:
        q = q.where(FinancialAccount.id == account_id)
    accounts = db.scalars(q).all()
    for acc in accounts:
        total += account_balance(db, acc, before_date - timedelta(days=1))
    return total


def parse_optional_int_query(value: Optional[str], field_name: str = "filtro") -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field_name.capitalize()} inválido.")


def safe_finance_return_url(return_to: Optional[str], message: Optional[str] = None) -> str:
    target = return_to or "/admin/financeiro"
    parts = urlsplit(target)
    # Só permite retorno para a tela principal do financeiro neste próprio sistema.
    if parts.scheme or parts.netloc or parts.path != "/admin/financeiro":
        parts = urlsplit("/admin/financeiro")
    params = list(parse_qsl(parts.query, keep_blank_values=False))
    if message:
        params = [(k, v) for k, v in params if k != "msg"]
        params.append(("msg", message))
    query = urlencode(params)
    return parts.path + (("?" + query) if query else "")


def seed_finance_defaults():
    defaults = [
        ("Receitas", "income", None),
        ("Dinheiro", "income", "Receitas"),
        ("Cartões recebimento", "income", "Receitas"),
        ("Pix / recebimentos diversos", "income", "Receitas"),
        ("Pagamento de mercadorias", "expense", None),
        ("Fornecedores / mercadorias", "expense", "Pagamento de mercadorias"),
        ("Despesas de pessoal", "expense", None),
        ("Folha de pagamento", "expense", "Despesas de pessoal"),
        ("Colaboradores extras", "expense", "Despesas de pessoal"),
        ("Pró-labore", "expense", "Despesas de pessoal"),
        ("Diárias", "expense", "Despesas de pessoal"),
        ("FGTS", "expense", "Despesas de pessoal"),
        ("GPS / INSS", "expense", "Despesas de pessoal"),
        ("Férias", "expense", "Despesas de pessoal"),
        ("Rescisões", "expense", "Despesas de pessoal"),
        ("Benefícios", "expense", "Despesas de pessoal"),
        ("Despesas operacionais", "expense", None),
        ("Aluguel", "expense", "Despesas operacionais"),
        ("Energia", "expense", "Despesas operacionais"),
        ("Água", "expense", "Despesas operacionais"),
        ("Gás", "expense", "Despesas operacionais"),
        ("Insumos", "expense", "Despesas operacionais"),
        ("Embalagens", "expense", "Despesas operacionais"),
        ("Manutenção", "expense", "Despesas operacionais"),
        ("Logística", "expense", "Despesas operacionais"),
        ("Combustível", "expense", "Despesas operacionais"),
        ("Veículos", "expense", "Despesas operacionais"),
        ("Telefone", "expense", "Despesas operacionais"),
        ("Internet", "expense", "Despesas operacionais"),
        ("Limpeza", "expense", "Despesas operacionais"),
        ("Segurança", "expense", "Despesas operacionais"),
        ("Serviços", "expense", "Despesas operacionais"),
        ("Despesas de marketing", "expense", None),
        ("Publicidade", "expense", "Despesas de marketing"),
        ("Cartaz / Panfleto", "expense", "Despesas de marketing"),
        ("Redes sociais", "expense", "Despesas de marketing"),
        ("Despesas administrativas", "expense", None),
        ("Software", "expense", "Despesas administrativas"),
        ("Sistemas", "expense", "Despesas administrativas"),
        ("Contabilidade", "expense", "Despesas administrativas"),
        ("Jurídico", "expense", "Despesas administrativas"),
        ("Associações", "expense", "Despesas administrativas"),
        ("Material de escritório", "expense", "Despesas administrativas"),
        ("TI", "expense", "Despesas administrativas"),
        ("Impostos / tributos", "expense", None),
        ("ICMS", "expense", "Impostos / tributos"),
        ("PIS", "expense", "Impostos / tributos"),
        ("COFINS", "expense", "Impostos / tributos"),
        ("IRPJ", "expense", "Impostos / tributos"),
        ("CSLL", "expense", "Impostos / tributos"),
        ("ISS", "expense", "Impostos / tributos"),
        ("DAS", "expense", "Impostos / tributos"),
        ("Investimentos", "expense", None),
        ("Consórcio", "expense", "Investimentos"),
        ("Máquinas / equipamentos", "expense", "Investimentos"),
        ("Energia solar", "expense", "Investimentos"),
        ("Aplicações financeiras", "expense", "Investimentos"),
        ("Empréstimos", "expense", "Investimentos"),
        ("Sócios", "expense", None),
        ("Retiradas / adiantamentos", "expense", "Sócios"),
        ("Despesas financeiras", "expense", None),
        ("Taxas de cartões", "expense", "Despesas financeiras"),
        ("Tarifas bancárias", "expense", "Despesas financeiras"),
        ("Juros bancários", "expense", "Despesas financeiras"),
        ("Perdas", "expense", None),
        ("Quebras / perdas", "expense", "Perdas"),
        ("Transferências entre contas", "transfer", None),
        ("A classificar - Receitas", "income", None),
        ("A classificar - Despesas", "expense", None),
    ]
    deprecated_combined = {
        "Colaboradores extras / pró-labore / diárias",
        "Férias / rescisões / benefícios",
        "Energia / água / gás",
        "Insumos / embalagens",
        "Logística / combustível / veículos",
        "Telefone / Internet",
        "Limpeza / segurança / serviços",
        "Publicidade / cartaz / panfleto / redes sociais",
        "Software / sistemas",
        "Contabilidade / jurídico / associações",
        "Material de escritório / TI",
        "PIS / COFINS / IRPJ / CSLL / ISS / DAS",
        "Consórcio / máquinas / energia solar",
        "Tarifas bancárias / juros",
    }
    with SessionLocal() as db:
        existing = {g.name: g for g in db.scalars(select(CashGroup)).all()}
        for name, nature, parent_name in defaults:
            if name in existing:
                continue
            parent = existing.get(parent_name) if parent_name else None
            g = CashGroup(name=name, nature=nature, parent_id=parent.id if parent else None, active=True)
            db.add(g)
            db.flush()
            existing[name] = g
        # Grupos combinados antigos continuam preservados para o histórico,
        # mas saem das novas seleções para evitar lançamentos pouco detalhados.
        for name in deprecated_combined:
            if name in existing:
                existing[name].active = False
        if not db.scalar(select(FinancialAccount.id).limit(1)):
            db.add(FinancialAccount(name="Caixa físico", account_type="cash", opening_balance=Decimal("0"), active=True))
        db.commit()


def finance_context(db: Session):
    accounts = db.scalars(select(FinancialAccount).order_by(FinancialAccount.account_type, FinancialAccount.name)).all()
    groups = db.scalars(select(CashGroup).order_by(CashGroup.nature, CashGroup.name)).all()
    balances = {a.id: account_balance(db, a) for a in accounts}
    bank_total = sum((balances[a.id] for a in accounts if a.account_type == "bank" and a.active), Decimal("0"))
    cash_total = sum((balances[a.id] for a in accounts if a.account_type == "cash" and a.active), Decimal("0"))
    return accounts, groups, balances, bank_total, cash_total


def group_hierarchy(groups):
    group_map = {g.id: g for g in groups}
    parents = [g for g in groups if not g.parent_id]
    children_by_parent = defaultdict(list)
    for g in groups:
        if g.parent_id:
            children_by_parent[g.parent_id].append(g)
    for values in children_by_parent.values():
        values.sort(key=lambda x: x.name.lower())
    return group_map, parents, children_by_parent


def group_parts(group_id: Optional[int], group_map):
    if not group_id or group_id not in group_map:
        return None, None
    g = group_map[group_id]
    if g.parent_id and g.parent_id in group_map:
        return group_map[g.parent_id], g
    return g, None


def resolve_group_selection(db: Session, parent_id: Optional[int], subgroup_id: Optional[int], expected_nature: str) -> Optional[CashGroup]:
    """Compatibilidade com versões antigas: prioriza sempre o subgrupo."""
    if subgroup_id:
        return resolve_subgroup_selection(db, subgroup_id, expected_nature)
    if not parent_id:
        return None
    parent = db.get(CashGroup, parent_id)
    if not parent or not parent.active or parent.parent_id:
        raise ValueError("Grupo principal inválido ou inativo.")
    if parent.nature != expected_nature:
        raise ValueError("O grupo não corresponde ao tipo do lançamento.")
    has_children = db.scalar(select(CashGroup.id).where(CashGroup.parent_id == parent.id, CashGroup.active.is_(True)).limit(1))
    if has_children and not parent.name.startswith("A classificar"):
        raise ValueError("Selecione uma categoria/subgrupo. O grupo será definido automaticamente.")
    return parent


def resolve_subgroup_selection(db: Session, subgroup_id: Optional[int], expected_nature: str) -> Optional[CashGroup]:
    if not subgroup_id:
        raise ValueError("Selecione uma categoria/subgrupo.")
    subgroup = db.get(CashGroup, subgroup_id)
    if not subgroup or not subgroup.active or not subgroup.parent_id:
        raise ValueError("Categoria/subgrupo inválido ou inativo.")
    parent = db.get(CashGroup, subgroup.parent_id)
    if not parent or not parent.active:
        raise ValueError("O grupo desta categoria está inativo.")
    if subgroup.nature != expected_nature or parent.nature != expected_nature:
        raise ValueError("A categoria não corresponde ao tipo do lançamento.")
    return subgroup


def parse_category_filter(value: Optional[str]):
    """Retorna (grupo_id, subgrupo_id) a partir de g:123 ou s:456."""
    if not value:
        return None, None
    try:
        prefix, raw_id = value.split(":", 1)
        ident = int(raw_id)
    except (ValueError, AttributeError):
        return None, None
    if prefix == "g":
        return ident, None
    if prefix == "s":
        return None, ident
    return None, None

def remember_classification_rule(db: Session, tx: CashTransaction, group: CashGroup):
    if tx.source != "bank_import" or tx.transaction_type not in {"income", "expense"}:
        return
    if group.name.startswith("A classificar"):
        return
    key = norm_text(tx.description)[:255]
    if not key:
        return
    rule = db.scalar(select(CashClassificationRule).where(
        CashClassificationRule.account_id == tx.account_id,
        CashClassificationRule.description_key == key,
        CashClassificationRule.transaction_type == tx.transaction_type,
    ))
    if rule:
        rule.group_id = group.id
        rule.active = True
        rule.updated_at = now_local()
    else:
        db.add(CashClassificationRule(
            account_id=tx.account_id, description_key=key, transaction_type=tx.transaction_type,
            group_id=group.id, active=True, hits=0, created_at=now_local(), updated_at=now_local()
        ))


def filtered_transactions(
    db: Session, start: date, end: date, account_id: Optional[int],
    group_id: Optional[int], subgroup_id: Optional[int], tx_type: Optional[str]
):
    q = select(CashTransaction).where(CashTransaction.transaction_date >= start, CashTransaction.transaction_date <= end)
    if account_id:
        q = q.where(CashTransaction.account_id == account_id)
    if subgroup_id:
        q = q.where(CashTransaction.group_id == subgroup_id)
    elif group_id:
        child_ids = list(db.scalars(select(CashGroup.id).where(CashGroup.parent_id == group_id)).all())
        q = q.where(CashTransaction.group_id.in_([group_id] + child_ids))
    if tx_type:
        if tx_type == "transfer":
            q = q.where(CashTransaction.transaction_type.in_(["transfer_in", "transfer_out"]))
        else:
            q = q.where(CashTransaction.transaction_type == tx_type)
    return db.scalars(q.order_by(CashTransaction.transaction_date.desc(), CashTransaction.id.desc())).all()


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


def build_group_rows(group_totals, group_map):
    rows = []
    for gid, values in group_totals.items():
        if gid not in group_map:
            continue
        parent, subgroup = group_parts(gid, group_map)
        rows.append({
            "group": parent,
            "subgroup": subgroup,
            **values,
            "net": values["income"] - values["expense"],
        })
    rows.sort(key=lambda x: (x["group"].name if x["group"] else "", x["subgroup"].name if x["subgroup"] else ""))
    return rows


@router.get("/admin/financeiro", response_class=HTMLResponse)
def cashflow_page(
    request: Request,
    inicio: Optional[str] = None,
    fim: Optional[str] = None,
    conta: Optional[str] = None,
    categoria: Optional[str] = None,
    grupo: Optional[int] = None,
    subgrupo: Optional[int] = None,
    tipo: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = require_admin(request, db)
    today = now_local().date()
    start = parse_date_value(inicio) if inicio else today.replace(day=1)
    end = parse_date_value(fim) if fim else today
    if not start or not end or start > end:
        raise HTTPException(400, "Período inválido")
    account_id = parse_optional_int_query(conta, "conta")
    category_group, category_subgroup = parse_category_filter(categoria)
    if categoria:
        grupo, subgrupo = category_group, category_subgroup
    txs = filtered_transactions(db, start, end, account_id, grupo, subgrupo, tipo)
    accounts, groups, balances, bank_total, cash_total = finance_context(db)
    group_map, parent_groups, children_by_parent = group_hierarchy(groups)
    account_map = {a.id: a for a in accounts}
    entries, expenses, transfer_in, transfer_out, daily, group_totals = summarize_transactions(txs)
    opening = total_balance_before(db, start, account_id)
    daily_rows = []
    running = opening
    for d in sorted(daily):
        net = daily[d]["income"] - daily[d]["expense"]
        movement = net + daily[d]["transfer"]
        running += movement
        daily_rows.append({"date": d, **daily[d], "net": net, "movement": movement, "running": running})
    period_movement = entries + transfer_in - expenses - transfer_out
    group_rows = build_group_rows(group_totals, group_map)
    return_params = [("inicio", start.isoformat()), ("fim", end.isoformat())]
    if account_id:
        return_params.append(("conta", str(account_id)))
    selected_category_value = categoria or (f"s:{subgrupo}" if subgrupo else (f"g:{grupo}" if grupo else ""))
    if selected_category_value:
        return_params.append(("categoria", selected_category_value))
    if tipo:
        return_params.append(("tipo", tipo))
    return_to = "/admin/financeiro?" + urlencode(return_params)
    return templates.TemplateResponse(request, "cashflow.html", {
        "user": user,
        "transactions": txs,
        "accounts": accounts,
        "groups": groups,
        "parent_groups": parent_groups,
        "children_by_parent": children_by_parent,
        "balances": balances,
        "bank_total": bank_total,
        "cash_total": cash_total,
        "consolidated": bank_total + cash_total,
        "entries": entries,
        "expenses": expenses,
        "transfer_in": transfer_in,
        "transfer_out": transfer_out,
        "result": entries - expenses,
        "period_movement": period_movement,
        "opening": opening,
        "period_end_balance": opening + period_movement,
        "daily_rows": daily_rows,
        "group_rows": group_rows,
        "group_map": group_map,
        "account_map": account_map,
        "start": start,
        "end": end,
        "selected_account": account_id,
        "selected_group": grupo,
        "selected_subgroup": subgrupo,
        "selected_category": selected_category_value,
        "selected_type": tipo or "",
        "return_to": return_to,
        "return_to_encoded": quote(return_to, safe=""),
        "today": today,
        "last7": today - timedelta(days=6),
        "month_start": today.replace(day=1),
        "message": request.query_params.get("msg"),
    })


@router.get("/admin/financeiro/imprimir", response_class=HTMLResponse)
def cashflow_print(
    request: Request,
    inicio: str,
    fim: str,
    conta: Optional[str] = None,
    categoria: Optional[str] = None,
    grupo: Optional[int] = None,
    subgrupo: Optional[int] = None,
    tipo: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = require_admin(request, db)
    start, end = parse_date_value(inicio), parse_date_value(fim)
    if not start or not end or start > end:
        raise HTTPException(400, "Período inválido")
    account_id = parse_optional_int_query(conta, "conta")
    category_group, category_subgroup = parse_category_filter(categoria)
    if categoria:
        grupo, subgrupo = category_group, category_subgroup
    txs = filtered_transactions(db, start, end, account_id, grupo, subgrupo, tipo)
    accounts, groups, balances, bank_total, cash_total = finance_context(db)
    group_map, _, _ = group_hierarchy(groups)
    account_map = {a.id: a for a in accounts}
    entries, expenses, transfer_in, transfer_out, daily, group_totals = summarize_transactions(txs)
    opening = total_balance_before(db, start, account_id)
    running = opening
    daily_rows = []
    for d in sorted(daily):
        net = daily[d]["income"] - daily[d]["expense"]
        movement = net + daily[d]["transfer"]
        running += movement
        daily_rows.append({"date": d, **daily[d], "net": net, "movement": movement, "running": running})
    period_movement = entries + transfer_in - expenses - transfer_out
    group_rows = build_group_rows(group_totals, group_map)
    return templates.TemplateResponse(request, "cashflow_print.html", {
        "user": user, "transactions": txs, "start": start, "end": end,
        "entries": entries, "expenses": expenses, "transfer_in": transfer_in, "transfer_out": transfer_out,
        "result": entries-expenses, "period_movement": period_movement,
        "opening": opening, "period_end_balance": opening+period_movement,
        "bank_total": bank_total, "cash_total": cash_total, "consolidated": bank_total+cash_total,
        "group_map": group_map, "account_map": account_map, "daily_rows": daily_rows, "group_rows": group_rows,
    })


@router.get("/admin/financeiro/lancamentos/novo", response_class=HTMLResponse)
def cash_transaction_new(request: Request, tipo: Optional[str] = None, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    accounts, groups, _, _, _ = finance_context(db)
    group_map, parent_groups, _ = group_hierarchy(groups)
    initial_type = tipo if tipo in {"income", "expense", "transfer"} else "expense"
    return templates.TemplateResponse(request, "cash_transaction_form.html", {
        "user": user, "accounts": accounts, "groups": groups, "group_map": group_map,
        "parent_groups": parent_groups, "tx": None, "today": now_local().date(), "error": None,
        "initial_type": initial_type, "selected_parent_id": None, "selected_subgroup_id": None,
        "message": request.query_params.get("msg"),
    })


@router.post("/admin/financeiro/lancamentos/novo", response_class=HTMLResponse)
def cash_transaction_create(
    request: Request,
    transaction_date: str = Form(...),
    transaction_type: str = Form(...),
    account_id: int = Form(...),
    destination_account_id: Optional[int] = Form(None),
    group_id: Optional[int] = Form(None),
    subgroup_id: Optional[int] = Form(None),
    description: str = Form(...),
    amount: str = Form(...),
    counterparty: str = Form(""),
    document_number: str = Form(""),
    notes: str = Form(""),
    save_action: str = Form("close"),
    db: Session = Depends(get_db),
):
    user = require_admin(request, db)
    accounts, groups, _, _, _ = finance_context(db)
    group_map, parent_groups, _ = group_hierarchy(groups)
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
            common = dict(transaction_date=d, description=description.strip(), amount=value, group_id=transfer_group.id if transfer_group else None, counterparty=counterparty.strip() or None, document_number=document_number.strip() or None, notes=notes.strip() or None, source="transfer", transfer_ref=ref, created_by_user_id=user.id, reconciled=True)
            db.add(CashTransaction(transaction_type="transfer_out", account_id=account_id, **common))
            db.add(CashTransaction(transaction_type="transfer_in", account_id=destination_account_id, **common))
        else:
            if transaction_type not in {"income", "expense"}:
                raise ValueError("Tipo de lançamento inválido.")
            group = resolve_subgroup_selection(db, subgroup_id, transaction_type)
            db.add(CashTransaction(transaction_date=d, description=description.strip(), transaction_type=transaction_type, amount=value, account_id=account_id, group_id=group.id if group else None, counterparty=counterparty.strip() or None, document_number=document_number.strip() or None, notes=notes.strip() or None, source="manual", created_by_user_id=user.id, reconciled=True))
        db.commit()
        if save_action == "new":
            return RedirectResponse(f"/admin/financeiro/lancamentos/novo?tipo={transaction_type}&msg=Lançamento salvo. Cadastre o próximo.", 303)
        return RedirectResponse("/admin/financeiro?msg=Lançamento salvo", 303)
    except Exception as exc:
        db.rollback()
        return templates.TemplateResponse(request, "cash_transaction_form.html", {
            "user": user, "accounts": accounts, "groups": groups, "group_map": group_map,
            "parent_groups": parent_groups, "tx": None, "today": now_local().date(), "error": str(exc),
            "initial_type": transaction_type, "selected_parent_id": group_id, "selected_subgroup_id": subgroup_id,
        }, status_code=400)


@router.get("/admin/financeiro/lancamentos/{tx_id}/editar", response_class=HTMLResponse)
def cash_transaction_edit_page(tx_id: int, request: Request, return_to: Optional[str] = None, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    tx = db.get(CashTransaction, tx_id)
    if not tx:
        raise HTTPException(404, "Lançamento não encontrado")
    if tx.transfer_ref:
        raise HTTPException(400, "Transferências devem ser excluídas e lançadas novamente.")
    accounts, groups, _, _, _ = finance_context(db)
    group_map, parent_groups, _ = group_hierarchy(groups)
    parent, subgroup = group_parts(tx.group_id, group_map)
    return templates.TemplateResponse(request, "cash_transaction_form.html", {
        "user": user, "accounts": accounts, "groups": groups, "group_map": group_map,
        "parent_groups": parent_groups, "tx": tx, "today": now_local().date(), "error": None,
        "initial_type": tx.transaction_type,
        "selected_parent_id": parent.id if parent else None,
        "selected_subgroup_id": subgroup.id if subgroup else None,
        "return_to": safe_finance_return_url(return_to),
    })


@router.post("/admin/financeiro/lancamentos/{tx_id}/editar")
def cash_transaction_edit(
    tx_id: int, request: Request,
    transaction_date: str = Form(...), transaction_type: str = Form(...), account_id: int = Form(...),
    group_id: Optional[int] = Form(None), subgroup_id: Optional[int] = Form(None), description: str = Form(...), amount: str = Form(...),
    counterparty: str = Form(""), document_number: str = Form(""), notes: str = Form(""), reconciled: Optional[str] = Form(None),
    return_to: str = Form(""), db: Session = Depends(get_db),
):
    require_admin(request, db)
    tx = db.get(CashTransaction, tx_id)
    if not tx or tx.transfer_ref:
        raise HTTPException(404, "Lançamento não encontrado ou não editável")
    d = parse_date_value(transaction_date); value = abs(parse_money(amount))
    if not d or value <= 0 or transaction_type not in {"income", "expense"}:
        raise HTTPException(400, "Dados inválidos")
    try:
        group = resolve_subgroup_selection(db, subgroup_id, transaction_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    tx.transaction_date=d; tx.transaction_type=transaction_type; tx.account_id=account_id; tx.group_id=group.id if group else None
    tx.description=description.strip(); tx.amount=value; tx.counterparty=counterparty.strip() or None
    tx.document_number=document_number.strip() or None; tx.notes=notes.strip() or None; tx.reconciled=reconciled == "1"
    if group:
        remember_classification_rule(db, tx, group)
    db.commit()
    return RedirectResponse(safe_finance_return_url(return_to, "Lançamento alterado"), 303)


@router.post("/admin/financeiro/lancamentos/{tx_id}/classificar")
def cash_transaction_classify(
    tx_id: int, request: Request, subgroup_id: int = Form(...), group_id: Optional[int] = Form(None), db: Session = Depends(get_db)
):
    require_admin(request, db)
    tx = db.get(CashTransaction, tx_id)
    if not tx or tx.transaction_type not in {"income", "expense"}:
        raise HTTPException(404)
    try:
        group = resolve_subgroup_selection(db, subgroup_id, tx.transaction_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not group:
        raise HTTPException(400, "Selecione uma categoria/subgrupo.")
    tx.group_id = group.id; tx.reconciled = True
    remember_classification_rule(db, tx, group)
    db.commit()
    return RedirectResponse("/admin/financeiro?msg=Lançamento classificado e regra lembrada para próximas importações", 303)


@router.get("/admin/financeiro/lancamentos/{tx_id}/transferencia", response_class=HTMLResponse)
def cash_transaction_transfer_page(tx_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    tx = db.get(CashTransaction, tx_id)
    if not tx or tx.transfer_ref or tx.transaction_type not in {"income", "expense"}:
        raise HTTPException(404, "Lançamento não disponível para conversão em transferência")
    accounts = db.scalars(select(FinancialAccount).where(FinancialAccount.active.is_(True)).order_by(FinancialAccount.name)).all()
    current_account = db.get(FinancialAccount, tx.account_id)
    return templates.TemplateResponse(request, "cash_transfer_from_transaction.html", {
        "user": user, "tx": tx, "accounts": accounts, "current_account": current_account, "error": None
    })


@router.post("/admin/financeiro/lancamentos/{tx_id}/transferencia", response_class=HTMLResponse)
def cash_transaction_transfer_submit(tx_id: int, request: Request, other_account_id: int = Form(...), db: Session = Depends(get_db)):
    user = require_admin(request, db)
    tx = db.get(CashTransaction, tx_id)
    if not tx or tx.transfer_ref or tx.transaction_type not in {"income", "expense"}:
        raise HTTPException(404, "Lançamento não disponível para conversão em transferência")
    other = db.get(FinancialAccount, other_account_id)
    if not other or not other.active or other.id == tx.account_id:
        raise HTTPException(400, "Selecione outra conta financeira.")
    transfer_group = db.scalar(select(CashGroup).where(CashGroup.name == "Transferências entre contas"))
    ref = uuid.uuid4().hex
    current_is_in = tx.transaction_type == "income"
    tx.transaction_type = "transfer_in" if current_is_in else "transfer_out"
    tx.group_id = transfer_group.id if transfer_group else None
    tx.transfer_ref = ref
    tx.reconciled = True
    counterpart = CashTransaction(
        transaction_date=tx.transaction_date,
        description=tx.description,
        transaction_type="transfer_out" if current_is_in else "transfer_in",
        amount=tx.amount,
        account_id=other.id,
        group_id=transfer_group.id if transfer_group else None,
        counterparty=tx.counterparty,
        document_number=tx.document_number,
        notes=f"Contrapartida automática da transferência do lançamento #{tx.id}. Será conciliada quando o extrato da outra conta for importado.",
        source="transfer", transfer_ref=ref, reconciled=False, created_by_user_id=user.id,
    )
    db.add(counterpart)
    db.commit()
    return RedirectResponse("/admin/financeiro?msg=Transferência registrada entre as contas", 303)


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
    group_map, parents, children_by_parent = group_hierarchy(groups)
    return templates.TemplateResponse(request, "cash_groups.html", {
        "user": user, "groups": groups, "group_map": group_map, "parents": parents,
        "children_by_parent": children_by_parent, "error": None,
        "message": request.query_params.get("msg")
    })


@router.post("/admin/financeiro/grupos")
def cash_group_create(
    request: Request, name: str = Form(...), nature: str = Form("expense"),
    parent_id: Optional[int] = Form(None), db: Session = Depends(get_db)
):
    require_admin(request, db)
    clean_name = name.strip()
    if not clean_name:
        raise HTTPException(400, "Informe o nome.")
    parent = db.get(CashGroup, parent_id) if parent_id else None
    duplicate_q = select(CashGroup.id).where(func.lower(CashGroup.name) == clean_name.lower())
    if parent_id:
        duplicate_q = duplicate_q.where(CashGroup.parent_id == parent_id)
    else:
        duplicate_q = duplicate_q.where(CashGroup.parent_id.is_(None))
    if db.scalar(duplicate_q):
        raise HTTPException(400, "Já existe um item com esse nome neste grupo")
    if parent_id and (not parent or parent.parent_id):
        raise HTTPException(400, "Grupo principal inválido")
    if parent:
        nature = parent.nature
    elif nature not in {"income", "expense", "transfer"}:
        raise HTTPException(400, "Natureza inválida")
    db.add(CashGroup(name=clean_name, nature=nature, parent_id=parent.id if parent else None, active=True))
    db.commit()
    return RedirectResponse("/admin/financeiro/grupos?msg=Item adicionado", 303)


@router.post("/admin/financeiro/grupos/{group_id}/editar")
def cash_group_edit(
    group_id: int, request: Request, name: str = Form(...), nature: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    require_admin(request, db)
    g = db.get(CashGroup, group_id)
    if not g:
        raise HTTPException(404)
    clean_name = name.strip()
    if not clean_name:
        raise HTTPException(400, "Informe o nome")
    duplicate_q = select(CashGroup.id).where(
        CashGroup.id != g.id,
        func.lower(CashGroup.name) == clean_name.lower(),
    )
    if g.parent_id:
        duplicate_q = duplicate_q.where(CashGroup.parent_id == g.parent_id)
    else:
        duplicate_q = duplicate_q.where(CashGroup.parent_id.is_(None))
    if db.scalar(duplicate_q):
        raise HTTPException(400, "Já existe outro item com esse nome neste grupo")
    g.name = clean_name
    if not g.parent_id and nature in {"income", "expense"} and nature != g.nature:
        g.nature = nature
        for child in db.scalars(select(CashGroup).where(CashGroup.parent_id == g.id)).all():
            child.nature = nature
    db.commit()
    return RedirectResponse("/admin/financeiro/grupos?msg=Alteração salva", 303)


@router.post("/admin/financeiro/grupos/{group_id}/status")
def cash_group_status(group_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db); g=db.get(CashGroup, group_id)
    if not g: raise HTTPException(404)
    g.active = not g.active; db.commit(); return RedirectResponse("/admin/financeiro/grupos?msg=Status atualizado", 303)


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
    "c6": "C6 Bank (perfil ainda não calibrado)",
    "generic": "Genérico",
}

FINANCE_OWN_NAME = norm_text(os.getenv("FINANCE_OWN_NAME", "MERCADO SANENGE"))
FINANCE_OWN_TAX_ID = re.sub(r"\D", "", os.getenv("FINANCE_OWN_TAX_ID", "06030826000180"))



def normalize_tax_id(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def profile_from_bank_name(bank_name: Optional[str]) -> str:
    bank = norm_text(bank_name)
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


def learned_classification_for_item(db: Session, account: FinancialAccount, item: dict):
    signed = Decimal(item["signed_amount"])
    tx_type = "income" if signed > 0 else "expense"
    key = norm_text(item.get("description"))[:255]
    if not key:
        return None
    rule = db.scalar(select(CashClassificationRule).where(
        CashClassificationRule.account_id == account.id,
        CashClassificationRule.description_key == key,
        CashClassificationRule.transaction_type == tx_type,
        CashClassificationRule.active.is_(True),
    ))
    if not rule:
        return None
    group = db.get(CashGroup, rule.group_id)
    if not group or not group.active or group.nature != tx_type:
        return None
    rule.hits = int(rule.hits or 0) + 1
    rule.updated_at = now_local()
    return {
        "transaction_type": tx_type,
        "group_id": group.id,
        "group_name": group.name,
        "reconciled": True,
        "rule": "classificacao_aprendida",
    }


def existing_imported_transaction(db: Session, account_id: int, item: dict):
    signed = Decimal(item["signed_amount"])
    amount = abs(signed)
    candidates = db.scalars(select(CashTransaction).where(
        CashTransaction.account_id == account_id,
        CashTransaction.transaction_date == item["date"],
        CashTransaction.amount == amount,
        CashTransaction.source == "bank_import",
    )).all()
    desc = norm_text(item.get("description"))
    counterparty = norm_text(item.get("counterparty"))
    document = norm_text(item.get("document"))
    tax_id = normalize_tax_id(item.get("tax_id"))
    for tx in candidates:
        if norm_text(tx.description) != desc:
            continue
        if norm_text(tx.counterparty) != counterparty:
            continue
        if norm_text(tx.document_number) != document:
            continue
        if tax_id:
            try:
                raw = json.loads(tx.raw_data or "{}")
            except Exception:
                raw = {}
            existing_tax = normalize_tax_id(raw.get("tax_id"))
            if existing_tax and existing_tax != tax_id:
                continue
        # O sinal precisa coincidir, aceitando transferências já reconhecidas.
        positive_types = {"income", "transfer_in"}
        existing_positive = tx.transaction_type in positive_types
        if existing_positive != (signed > 0):
            continue
        return tx
    return None


def synthetic_transfer_match(db: Session, account_id: int, item: dict):
    signed = Decimal(item["signed_amount"])
    expected_type = "transfer_in" if signed > 0 else "transfer_out"
    candidates = db.scalars(select(CashTransaction).where(
        CashTransaction.account_id == account_id,
        CashTransaction.transaction_date == item["date"],
        CashTransaction.amount == abs(signed),
        CashTransaction.transaction_type == expected_type,
        CashTransaction.source == "transfer",
        CashTransaction.reconciled.is_(False),
        CashTransaction.transfer_ref.is_not(None),
    )).all()
    return candidates[0] if len(candidates) == 1 else None


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

    if profile == "itau":
        if positive and ("recebimento rede" in desc or desc.startswith("cabal caba") or "redecard" in counterparty):
            return {"transaction_type":"income","group_name":"Cartões recebimento","reconciled":True,"rule":"itau_cartoes"}
        if positive and "pix recebido" in desc:
            return {"transaction_type":"income","group_name":"Pix / recebimentos diversos","reconciled":True,"rule":"itau_pix_recebido"}
        if positive and desc == "recebimentos":
            return {"transaction_type":"income","group_name":"Pix / recebimentos diversos","reconciled":True,"rule":"itau_recebimentos"}
        if not positive and "vivo" in full:
            return {"transaction_type":"expense","group_name":"Telefone","reconciled":True,"rule":"itau_vivo"}
        if not positive and any(x in full for x in ("tarifa", "juros", "encargo", "iof")):
            return {"transaction_type":"expense","group_name":"Tarifas bancárias","reconciled":True,"rule":"itau_tarifas"}
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
            return {"transaction_type":"expense","group_name":"Tarifas bancárias","reconciled":True,"rule":"sicoob_tarifas"}
        if not positive and "emprestimo" in full:
            return {"transaction_type":"expense","group_name":"Empréstimos","reconciled":True,"rule":"sicoob_emprestimo"}
        if not positive and ("pix.emit" in desc or "pagamento pix" in full):
            return {"transaction_type":"expense","group_name":"A classificar - Despesas","reconciled":False,"rule":"sicoob_pix_pagamento"}

    # Regras seguras, independentes do banco.
    if positive and "pix receb" in desc:
        return {"transaction_type":"income","group_name":"Pix / recebimentos diversos","reconciled":True,"rule":"generic_pix_recebido"}
    if not positive and any(x in full for x in ("tarifa", "juros banc", "pacote servicos")):
        return {"transaction_type":"expense","group_name":"Tarifas bancárias","reconciled":True,"rule":"generic_tarifa"}
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
    rules=db.scalars(select(CashClassificationRule).where(CashClassificationRule.active.is_(True)).order_by(CashClassificationRule.updated_at.desc()).limit(100)).all()
    groups=db.scalars(select(CashGroup)).all()
    account_map={a.id:a for a in accounts}
    group_map={g.id:g for g in groups}
    account_profiles={a.id:BANK_PROFILE_LABELS.get(profile_from_bank_name(a.bank_name), "Genérico") for a in accounts}
    return templates.TemplateResponse(request,"bank_import.html",{
        "user":user,"accounts":accounts,"imports":imports,"rules":rules,"account_map":account_map,"group_map":group_map,
        "account_profiles":account_profiles,"error":None,"message":request.query_params.get("msg")
    })


@router.post("/admin/financeiro/regras/{rule_id}/excluir")
def classification_rule_delete(rule_id:int, request:Request, db:Session=Depends(get_db)):
    require_admin(request,db)
    rule=db.get(CashClassificationRule,rule_id)
    if not rule:
        raise HTTPException(404)
    db.delete(rule); db.commit()
    return RedirectResponse("/admin/financeiro/importar?msg=Regra de classificação removida",303)


@router.post("/admin/financeiro/importar", response_class=HTMLResponse)
async def bank_import_submit(request:Request, account_id:int=Form(...), statement:UploadFile=File(...), db:Session=Depends(get_db)):
    user=require_admin(request,db); account=db.get(FinancialAccount,account_id)
    if not account or account.account_type!="bank": raise HTTPException(400,"Selecione uma conta bancária")
    content=await statement.read(); file_hash=hashlib.sha256(content).hexdigest()
    if db.scalar(select(BankStatementImport).where(BankStatementImport.financial_account_id==account.id,BankStatementImport.file_hash==file_hash)):
        return RedirectResponse("/admin/financeiro/importar?msg=Este arquivo de extrato já foi importado. Nenhum lançamento foi duplicado",303)
    try:
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
        rows=parse_statement_rows(matrix)
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
        imported=0; auto_classified=0; learned_count=0; duplicates=0; matched_transfers=0
        signed_sum=Decimal("0"); last_balance=None; last_balance_date=None
        for item in rows:
            signed=Decimal(item["signed_amount"]); balance=item["balance"]
            key_source=f'{account.id}|{item["date"]}|{norm_text(item["description"])}|{norm_text(item.get("counterparty"))}|{normalize_tax_id(item.get("tax_id"))}|{signed}|{norm_text(item["document"] or "")}'
            import_key=hashlib.sha256(key_source.encode("utf-8")).hexdigest()

            # Proteção em dois níveis: chave estável e comparação do lançamento já importado.
            if db.scalar(select(CashTransaction.id).where(CashTransaction.import_key==import_key)) or existing_imported_transaction(db, account.id, item):
                duplicates += 1
                if balance is not None and (last_balance_date is None or item["date"]>=last_balance_date):
                    last_balance=balance; last_balance_date=item["date"]
                continue

            raw_payload={"profile":profile,"tax_id":item.get("tax_id"),"row":item["raw"]}

            # Se uma transferência foi criada manualmente pelo outro lado, concilia em vez de duplicar.
            synthetic=synthetic_transfer_match(db, account.id, item)
            if synthetic:
                synthetic.description=item["description"]
                synthetic.counterparty=item.get("counterparty")
                synthetic.document_number=item["document"]
                synthetic.source="bank_import"
                synthetic.bank_import_id=batch.id
                synthetic.import_key=import_key
                synthetic.reconciled=True
                raw_payload["rule"]="contrapartida_transferencia_manual"
                synthetic.raw_data=json.dumps(raw_payload,default=str,ensure_ascii=False)
                matched_transfers += 1
                if balance is not None and (last_balance_date is None or item["date"]>=last_balance_date):
                    last_balance=balance; last_balance_date=item["date"]
                continue

            learned=learned_classification_for_item(db, account, item)
            cls=learned or classify_statement_item(profile,item)
            group=db.get(CashGroup,cls.get("group_id")) if cls.get("group_id") else groups_by_name.get(cls["group_name"])
            raw_payload["rule"]=cls["rule"]
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
            if learned:
                learned_count += 1
            if cls["reconciled"]:
                auto_classified+=1
            if balance is not None and (last_balance_date is None or item["date"]>=last_balance_date): last_balance=balance; last_balance_date=item["date"]
        db.flush()
        linked=link_pending_internal_transfers(db)
        batch.rows_count=imported + matched_transfers
        if last_balance is not None:
            account.last_statement_balance=last_balance; account.last_statement_date=last_balance_date
            if existing_tx_count==0 and Decimal(account.opening_balance or 0)==0:
                account.opening_balance=last_balance-signed_sum
        db.commit()
        profile_label=BANK_PROFILE_LABELS.get(profile,profile)
        msg=f"{imported} novos lançamentos importados ({profile_label})"
        if auto_classified:
            msg += f"; {auto_classified} classificados automaticamente"
        if learned_count:
            msg += f" ({learned_count} pela memória de classificação)"
        if duplicates:
            msg += f"; {duplicates} duplicado(s) ignorado(s)"
        if matched_transfers:
            msg += f"; {matched_transfers} contrapartida(s) de transferência conciliada(s)"
        if linked:
            msg += f"; {linked} transferência(s) entre contas vinculada(s)"
        return RedirectResponse(f"/admin/financeiro/importar?msg={msg}",303)
    except Exception as exc:
        db.rollback()
        accounts=db.scalars(select(FinancialAccount).where(FinancialAccount.account_type=="bank").order_by(FinancialAccount.name)).all()
        imports=db.scalars(select(BankStatementImport).order_by(BankStatementImport.imported_at.desc()).limit(20)).all()
        rules=db.scalars(select(CashClassificationRule).where(CashClassificationRule.active.is_(True)).order_by(CashClassificationRule.updated_at.desc()).limit(100)).all()
        groups=db.scalars(select(CashGroup)).all()
        account_map={a.id:a for a in accounts}; group_map={g.id:g for g in groups}
        account_profiles={a.id:BANK_PROFILE_LABELS.get(profile_from_bank_name(a.bank_name), "Genérico") for a in accounts}
        return templates.TemplateResponse(request,"bank_import.html",{
            "user":user,"accounts":accounts,"imports":imports,"rules":rules,"account_map":account_map,"group_map":group_map,
            "account_profiles":account_profiles,"error":str(exc),"message":None
        },status_code=400)

