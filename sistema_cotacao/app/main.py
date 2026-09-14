import csv
import io
import os
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, and_
from sqlalchemy.orm import Session, selectinload
from openpyxl import load_workbook, Workbook

from .db import Base, engine, get_db, SessionLocal
from .models import User, Quote, QuoteSupplier, QuoteItem, QuoteResponse
from .security import hash_password, verify_password

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Sistema de Cotação")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "troque-esta-chave-em-producao"), https_only=os.getenv("COOKIE_SECURE", "0") == "1")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/Sao_Paulo")

def now_local() -> datetime:
    return datetime.now(ZoneInfo(APP_TIMEZONE)).replace(tzinfo=None)


def parse_decimal(value, default="0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    return Decimal(text)


def current_user(request: Request, db: Session) -> Optional[User]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.get(User, uid)


def require_role(request: Request, db: Session, role: str) -> User:
    user = current_user(request, db)
    if not user or not user.active or user.role != role:
        raise HTTPException(status_code=403, detail="Acesso negado")
    return user


def seed_admin():
    Base.metadata.create_all(bind=engine)
    email = os.getenv("ADMIN_EMAIL", "admin@empresa.local").lower()
    password = os.getenv("ADMIN_PASSWORD", "Admin123!")
    name = os.getenv("ADMIN_NAME", "Administrador")
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == email))
        if not existing:
            db.add(User(name=name, email=email, password_hash=hash_password(password), role="admin", company_name="Administração"))
            db.commit()


@app.on_event("startup")
def startup():
    seed_admin()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", 303)
    return RedirectResponse("/admin" if user.role == "admin" else "/fornecedor", 303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not user.active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {"error": "E-mail ou senha inválidos."}, status_code=400)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    return RedirectResponse("/admin" if user.role == "admin" else "/fornecedor", 303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    quotes = db.scalars(select(Quote).order_by(Quote.created_at.desc())).all()
    return templates.TemplateResponse(request, "admin_dashboard.html", {"user": user, "quotes": quotes, "now": now_local()})


@app.get("/admin/fornecedores", response_class=HTMLResponse)
def admin_suppliers(request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    suppliers = db.scalars(select(User).where(User.role == "supplier").order_by(User.company_name, User.name)).all()
    return templates.TemplateResponse(request, "admin_suppliers.html", {"user": user, "suppliers": suppliers, "error": None})


@app.post("/admin/fornecedores")
def create_supplier(
    request: Request,
    name: str = Form(...),
    company_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    require_role(request, db, "admin")
    normalized = email.strip().lower()
    if db.scalar(select(User).where(User.email == normalized)):
        suppliers = db.scalars(select(User).where(User.role == "supplier")).all()
        return templates.TemplateResponse(request, "admin_suppliers.html", {"user": current_user(request, db), "suppliers": suppliers, "error": "Este e-mail já está cadastrado."}, status_code=400)
    db.add(User(name=name.strip(), company_name=company_name.strip(), email=normalized, password_hash=hash_password(password), role="supplier"))
    db.commit()
    return RedirectResponse("/admin/fornecedores", 303)


@app.get("/admin/cotacoes/nova", response_class=HTMLResponse)
def new_quote_page(request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    suppliers = db.scalars(select(User).where(and_(User.role == "supplier", User.active == True)).order_by(User.company_name)).all()
    return templates.TemplateResponse(request, "admin_quote_new.html", {"user": user, "suppliers": suppliers, "error": None})


def read_product_rows(upload: UploadFile):
    filename = (upload.filename or "").lower()
    raw = upload.file.read()
    rows = []
    if filename.endswith(".xlsx"):
        wb = load_workbook(io.BytesIO(raw), data_only=True)
        ws = wb.active
        data = list(ws.iter_rows(values_only=True))
        if not data:
            return []
        headers = [str(x or "").strip().lower() for x in data[0]]
        for values in data[1:]:
            row = dict(zip(headers, values))
            rows.append(row)
    elif filename.endswith(".csv"):
        text = raw.decode("utf-8-sig")
        sample = text[:2000]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        for r in reader:
            rows.append({str(k or "").strip().lower(): v for k, v in r.items()})
    else:
        raise ValueError("Envie um arquivo .xlsx ou .csv")
    return rows


def pick(row, *keys):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


@app.post("/admin/cotacoes/nova")
def create_quote(
    request: Request,
    title: str = Form(...),
    deadline: str = Form(...),
    suppliers: list[int] = Form(default=[]),
    product_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = require_role(request, db, "admin")
    all_suppliers = db.scalars(select(User).where(and_(User.role == "supplier", User.active == True)).order_by(User.company_name)).all()
    try:
        deadline_dt = datetime.fromisoformat(deadline)
        rows = read_product_rows(product_file)
        if not rows:
            raise ValueError("A planilha não contém produtos.")
        if not suppliers:
            raise ValueError("Selecione ao menos um fornecedor.")
        quote = Quote(title=title.strip(), deadline=deadline_dt)
        db.add(quote)
        db.flush()
        for sid in suppliers:
            supplier = db.get(User, sid)
            if supplier and supplier.role == "supplier":
                db.add(QuoteSupplier(quote_id=quote.id, supplier_id=sid))
        imported = 0
        for row in rows:
            code = pick(row, "codigo", "código", "cod", "sku") or ""
            description = pick(row, "produto", "descricao", "descrição", "item")
            qty = pick(row, "quantidade", "qtd", "qtde")
            unit = pick(row, "unidade", "un", "und")
            if not description:
                continue
            db.add(QuoteItem(quote_id=quote.id, code=str(code).strip(), description=str(description).strip(), requested_qty=parse_decimal(qty, "1"), base_unit=str(unit or "UN").strip().upper()))
            imported += 1
        if imported == 0:
            raise ValueError("Nenhum produto válido encontrado. Use colunas: Código, Produto, Quantidade e Unidade.")
        db.commit()
        return RedirectResponse(f"/admin/cotacoes/{quote.id}", 303)
    except (ValueError, InvalidOperation) as exc:
        db.rollback()
        return templates.TemplateResponse(request, "admin_quote_new.html", {"user": user, "suppliers": all_suppliers, "error": str(exc)}, status_code=400)


@app.get("/admin/cotacoes/{quote_id}", response_class=HTMLResponse)
def admin_quote_detail(quote_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    quote = db.scalar(select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.items).selectinload(QuoteItem.responses).selectinload(QuoteResponse.supplier)))
    if not quote:
        raise HTTPException(404)
    supplier_ids = db.scalars(select(QuoteSupplier.supplier_id).where(QuoteSupplier.quote_id == quote_id)).all()
    suppliers = db.scalars(select(User).where(User.id.in_(supplier_ids))).all() if supplier_ids else []
    summary = []
    for item in quote.items:
        valid = [r for r in item.responses if r.normalized_price is not None]
        valid.sort(key=lambda r: r.normalized_price)
        summary.append({"item": item, "responses": valid, "min": valid[0] if valid else None, "max": valid[-1] if valid else None})
    return templates.TemplateResponse(request, "admin_quote_detail.html", {"user": user, "quote": quote, "suppliers": suppliers, "summary": summary, "now": now_local()})


@app.post("/admin/respostas/{response_id}/editar")
def admin_edit_response(
    response_id: int,
    request: Request,
    offered_unit: str = Form(...),
    factor: str = Form(...),
    offered_price: str = Form(...),
    db: Session = Depends(get_db),
):
    require_role(request, db, "admin")
    response = db.get(QuoteResponse, response_id)
    if not response:
        raise HTTPException(404)
    try:
        f = parse_decimal(factor)
        p = parse_decimal(offered_price)
        if f <= 0 or p < 0:
            raise ValueError
        response.offered_unit = offered_unit.strip().upper()
        response.base_units_per_offered_unit = f
        response.offered_price = p
        quote_id = response.item.quote_id
        db.commit()
        return RedirectResponse(f"/admin/cotacoes/{quote_id}", 303)
    except Exception:
        raise HTTPException(400, "Preço ou fator inválido")


@app.post("/admin/cotacoes/{quote_id}/prazo")
def update_deadline(quote_id: int, request: Request, deadline: str = Form(...), db: Session = Depends(get_db)):
    require_role(request, db, "admin")
    quote = db.get(Quote, quote_id)
    if not quote:
        raise HTTPException(404)
    quote.deadline = datetime.fromisoformat(deadline)
    db.commit()
    return RedirectResponse(f"/admin/cotacoes/{quote_id}", 303)


@app.get("/admin/cotacoes/{quote_id}/excel")
def export_quote_excel(quote_id: int, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "admin")
    quote = db.scalar(select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.items).selectinload(QuoteItem.responses).selectinload(QuoteResponse.supplier)))
    if not quote:
        raise HTTPException(404)
    wb = Workbook()
    ws = wb.active
    ws.title = "Comparativo"
    ws.append(["Código", "Produto", "Qtd", "Unidade base", "Fornecedor", "Unidade cotada", "Fator", "Preço cotado", "Preço normalizado", "Marca", "Prazo dias"])
    for item in quote.items:
        if not item.responses:
            ws.append([item.code, item.description, float(item.requested_qty), item.base_unit])
        for r in sorted(item.responses, key=lambda x: x.normalized_price or Decimal("999999999")):
            ws.append([item.code, item.description, float(item.requested_qty), item.base_unit, r.supplier.company_name or r.supplier.name, r.offered_unit, float(r.base_units_per_offered_unit), float(r.offered_price), float(r.normalized_price) if r.normalized_price is not None else None, r.brand, r.delivery_days])
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    ascii_title = unicodedata.normalize("NFKD", quote.title).encode("ascii", "ignore").decode("ascii")
    safe = "".join(c if c.isalnum() else "_" for c in ascii_title)[:50] or "cotacao"
    headers = {"Content-Disposition": f'attachment; filename="comparativo_{safe}.xlsx"'}
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)


@app.get("/fornecedor", response_class=HTMLResponse)
def supplier_dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "supplier")
    quote_ids = db.scalars(select(QuoteSupplier.quote_id).where(QuoteSupplier.supplier_id == user.id)).all()
    quotes = db.scalars(select(Quote).where(Quote.id.in_(quote_ids)).order_by(Quote.deadline.desc())).all() if quote_ids else []
    return templates.TemplateResponse(request, "supplier_dashboard.html", {"user": user, "quotes": quotes, "now": now_local()})


@app.get("/fornecedor/cotacoes/{quote_id}", response_class=HTMLResponse)
def supplier_quote_page(quote_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "supplier")
    allowed = db.scalar(select(QuoteSupplier).where(and_(QuoteSupplier.quote_id == quote_id, QuoteSupplier.supplier_id == user.id)))
    if not allowed:
        raise HTTPException(403)
    quote = db.scalar(select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.items)))
    if not quote:
        raise HTTPException(404)
    responses = db.scalars(select(QuoteResponse).where(QuoteResponse.supplier_id == user.id, QuoteResponse.quote_item_id.in_([i.id for i in quote.items]))).all() if quote.items else []
    by_item = {r.quote_item_id: r for r in responses}
    return templates.TemplateResponse(request, "supplier_quote.html", {"user": user, "quote": quote, "responses": by_item, "now": now_local(), "error": None})


@app.post("/fornecedor/cotacoes/{quote_id}")
async def supplier_save_quote(quote_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "supplier")
    allowed = db.scalar(select(QuoteSupplier).where(and_(QuoteSupplier.quote_id == quote_id, QuoteSupplier.supplier_id == user.id)))
    quote = db.scalar(select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.items)))
    if not allowed or not quote:
        raise HTTPException(403)
    if now_local() > quote.deadline:
        raise HTTPException(403, "O prazo desta cotação foi encerrado.")
    form = await request.form()
    try:
        for item in quote.items:
            price_raw = str(form.get(f"price_{item.id}") or "").strip()
            if not price_raw:
                continue
            price = parse_decimal(price_raw)
            factor = parse_decimal(form.get(f"factor_{item.id}") or "1")
            if price < 0 or factor <= 0:
                raise ValueError(f"Valor inválido no item {item.description}")
            response = db.scalar(select(QuoteResponse).where(and_(QuoteResponse.quote_item_id == item.id, QuoteResponse.supplier_id == user.id)))
            if not response:
                response = QuoteResponse(quote_item_id=item.id, supplier_id=user.id, offered_price=price)
                db.add(response)
            response.offered_unit = str(form.get(f"unit_{item.id}") or item.base_unit).strip().upper()
            response.base_units_per_offered_unit = factor
            response.offered_price = price
            response.brand = str(form.get(f"brand_{item.id}") or "").strip() or None
            dd = str(form.get(f"delivery_{item.id}") or "").strip()
            response.delivery_days = int(dd) if dd.isdigit() else None
            response.notes = str(form.get(f"notes_{item.id}") or "").strip() or None
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    return RedirectResponse(f"/fornecedor/cotacoes/{quote_id}", 303)
