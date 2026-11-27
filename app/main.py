import csv
import io
import os
import unicodedata
import hashlib
import secrets
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from urllib.parse import urlencode
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, and_, inspect, text, delete, union_all, literal
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from openpyxl import load_workbook, Workbook

from .db import Base, engine, get_db, SessionLocal
from .models import User, PasswordResetToken, Quote, QuoteSupplier, QuoteItem, QuoteResponse, PurchaseOrder, PurchaseOrderItem, ProductWriteOff, ProductBarcode, Department, CatalogProduct
from .security import hash_password, verify_password
from .finance import router as finance_router, seed_finance_defaults
from .closing import router as closing_router, start_worker, stop_worker, group_options
from .catalog_pdf import import_product_pdf

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Sistema de Cotação")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "troque-esta-chave-em-producao"), https_only=os.getenv("COOKIE_SECURE", "0") == "1")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
app.include_router(finance_router)
app.include_router(closing_router)
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


@app.exception_handler(StarletteHTTPException)
async def browser_http_error(request: Request, exc: StarletteHTTPException):
    # APIs continuam retornando seus códigos/JSON. Para navegação HTML, informar o motivo.
    if exc.status_code not in (403, 404) or "text/html" not in request.headers.get("accept", ""):
        return await http_exception_handler(request, exc)
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user or not user.active:
            if exc.status_code == 403:
                request.session.clear()
                return RedirectResponse("/login?sessao=expirada", 303)
            user = None
        message = exc.detail if isinstance(exc.detail, str) else "Não foi possível abrir esta página."
        return templates.TemplateResponse(request, "access_error.html", {"user": user, "status": exc.status_code, "message": message}, status_code=exc.status_code)


def ensure_runtime_schema():
    """Aplica pequenas migrações compatíveis com bancos já existentes."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "users" in tables:
        columns = {column["name"] for column in inspector.get_columns("users")}
        if "must_change_password" not in columns:
            with engine.begin() as conn:
                default_value = "FALSE" if engine.dialect.name == "postgresql" else "0"
                conn.execute(text(f"ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT {default_value}"))

    # Colunas novas são opcionais para preservar registros anteriores.
    additions = {"users": {"department_id": "INTEGER REFERENCES departments(id)"},
                 "catalog_products": {"closing_group_id": "INTEGER REFERENCES cash_groups(id)", "source_real_cost": "NUMERIC(14,4)", "sale_price": "NUMERIC(14,4)", "icms": "VARCHAR(40)", "traditional_margin": "NUMERIC(14,4)", "source_status": "VARCHAR(20)", "source_barcode": "VARCHAR(80)", "ncm": "VARCHAR(20)"},
                 "product_write_offs": {"closing_group_id": "INTEGER REFERENCES cash_groups(id)", "department_id": "INTEGER REFERENCES departments(id)", "department_name": "VARCHAR(120)", "operator_name": "VARCHAR(120)", "catalog_barcode": "VARCHAR(80)"}}
    with engine.begin() as conn:
        for table, fields in additions.items():
            if table in tables:
                old = {c["name"] for c in inspector.get_columns(table)}
                for name, ddl in fields.items():
                    if name not in old:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

    # Marca quando cada fornecedor efetivamente clicou em salvar/enviar a cotação.
    if "quote_suppliers" in tables:
        qs_columns = {column["name"] for column in inspector.get_columns("quote_suppliers")}
        if "submitted_at" not in qs_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE quote_suppliers ADD COLUMN submitted_at TIMESTAMP NULL"))


def backfill_submission_status():
    """Preserva o status de cotações respondidas antes da atualização que criou submitted_at."""
    with SessionLocal() as db:
        links = db.scalars(select(QuoteSupplier).where(QuoteSupplier.submitted_at.is_(None))).all()
        changed = False
        for link in links:
            item_ids = list(db.scalars(select(QuoteItem.id).where(QuoteItem.quote_id == link.quote_id)).all())
            if not item_ids:
                continue
            responses = db.scalars(
                select(QuoteResponse).where(
                    QuoteResponse.supplier_id == link.supplier_id,
                    QuoteResponse.quote_item_id.in_(item_ids),
                )
            ).all()
            if responses:
                link.submitted_at = max((r.updated_at for r in responses if r.updated_at), default=now_local())
                changed = True
        if changed:
            db.commit()


def seed_admin():
    email = os.getenv("ADMIN_EMAIL", "admin@empresa.local").lower()
    password = os.getenv("ADMIN_PASSWORD", "Admin123!")
    name = os.getenv("ADMIN_NAME", "Administrador")
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == email))
        if not existing:
            db.add(User(name=name, email=email, password_hash=hash_password(password), role="admin", company_name="Administração", must_change_password=False))
            db.commit()


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    # create_all é chamado novamente para garantir tabelas novas após pequenas migrações.
    Base.metadata.create_all(bind=engine)
    backfill_submission_status()
    seed_admin()
    seed_finance_defaults()
    start_worker()


@app.on_event("shutdown")
def shutdown_closing():
    stop_worker()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or not user.active:
        request.session.clear()
        return RedirectResponse("/login", 303)
    if user.role in ("supplier", "employee") and user.must_change_password:
        return RedirectResponse("/fornecedor/alterar-senha", 303)
    return RedirectResponse("/admin" if user.role == "admin" else "/admin/baixas/nova" if user.role == "employee" else "/fornecedor", 303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": "Sua sessão expirou ou não está ativa. Entre novamente." if request.query_params.get("sessao") == "expirada" else None})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not user.active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {"error": "E-mail ou senha inválidos."}, status_code=400)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    if user.role in ("supplier", "employee") and user.must_change_password:
        return RedirectResponse("/fornecedor/alterar-senha", 303)
    return RedirectResponse("/admin" if user.role == "admin" else "/admin/baixas/nova" if user.role == "employee" else "/fornecedor", 303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    quotes = db.scalars(select(Quote).order_by(Quote.created_at.desc())).all()
    submission_stats = {}
    for quote in quotes:
        links = db.scalars(select(QuoteSupplier).where(QuoteSupplier.quote_id == quote.id)).all()
        total = len(links)
        sent = sum(1 for link in links if link.submitted_at is not None)
        submission_stats[quote.id] = {"total": total, "sent": sent, "pending": total - sent}
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {"user": user, "quotes": quotes, "now": now_local(), "submission_stats": submission_stats},
    )


@app.get("/admin/fornecedores", response_class=HTMLResponse)
def admin_suppliers(request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    suppliers = db.scalars(select(User).where(User.role == "supplier").order_by(User.company_name, User.name)).all()
    created = request.query_params.get("criado") == "1"
    updated = request.query_params.get("editado") == "1"
    deleted = request.query_params.get("excluido") == "1"
    return templates.TemplateResponse(
        request,
        "admin_suppliers.html",
        {
            "user": user,
            "suppliers": suppliers,
            "error": None,
            "reset_link": None,
            "reset_supplier": None,
            "created": created,
            "updated": updated,
            "deleted": deleted,
        },
    )


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
        return templates.TemplateResponse(request, "admin_suppliers.html", {"user": current_user(request, db), "suppliers": suppliers, "error": "Este e-mail já está cadastrado.", "reset_link": None, "reset_supplier": None, "created": False}, status_code=400)
    db.add(User(name=name.strip(), company_name=company_name.strip(), email=normalized, password_hash=hash_password(password), role="supplier", must_change_password=True))
    db.commit()
    return RedirectResponse("/admin/fornecedores?criado=1", 303)


@app.get("/admin/fornecedores/{supplier_id}/editar", response_class=HTMLResponse)
def edit_supplier_page(supplier_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_role(request, db, "admin")
    supplier = db.get(User, supplier_id)
    if not supplier or supplier.role != "supplier":
        raise HTTPException(404, "Fornecedor não encontrado")
    return templates.TemplateResponse(
        request,
        "admin_supplier_edit.html",
        {"user": admin, "supplier": supplier, "error": None},
    )


@app.post("/admin/fornecedores/{supplier_id}/editar", response_class=HTMLResponse)
def edit_supplier_submit(
    supplier_id: int,
    request: Request,
    name: str = Form(...),
    company_name: str = Form(...),
    email: str = Form(...),
    active: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = require_role(request, db, "admin")
    supplier = db.get(User, supplier_id)
    if not supplier or supplier.role != "supplier":
        raise HTTPException(404, "Fornecedor não encontrado")

    normalized = email.strip().lower()
    duplicate = db.scalar(select(User).where(User.email == normalized, User.id != supplier.id))
    if duplicate:
        return templates.TemplateResponse(
            request,
            "admin_supplier_edit.html",
            {"user": admin, "supplier": supplier, "error": "Este e-mail já está sendo usado por outro usuário."},
            status_code=400,
        )

    supplier.company_name = company_name.strip()
    supplier.name = name.strip()
    supplier.email = normalized
    supplier.active = active == "1"
    db.commit()
    return RedirectResponse("/admin/fornecedores?editado=1", 303)


@app.post("/admin/fornecedores/{supplier_id}/excluir")
def delete_supplier(supplier_id: int, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "admin")
    supplier = db.get(User, supplier_id)
    if not supplier or supplier.role != "supplier":
        raise HTTPException(404, "Fornecedor não encontrado")

    # Exclusão definitiva: remove também respostas e vínculos do fornecedor com cotações.
    # As cotações e seus produtos permanecem; apenas a participação deste fornecedor é removida.
    order_ids = list(db.scalars(select(PurchaseOrder.id).where(PurchaseOrder.supplier_id == supplier.id)).all())
    if order_ids:
        db.execute(delete(PurchaseOrderItem).where(PurchaseOrderItem.order_id.in_(order_ids)))
    db.execute(delete(PurchaseOrder).where(PurchaseOrder.supplier_id == supplier.id))
    db.execute(delete(QuoteResponse).where(QuoteResponse.supplier_id == supplier.id))
    db.execute(delete(QuoteSupplier).where(QuoteSupplier.supplier_id == supplier.id))
    db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == supplier.id))
    db.delete(supplier)
    db.commit()
    return RedirectResponse("/admin/fornecedores?excluido=1", 303)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@app.post("/admin/fornecedores/{supplier_id}/reset-senha", response_class=HTMLResponse)
def admin_generate_password_reset(supplier_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_role(request, db, "admin")
    supplier = db.get(User, supplier_id)
    if not supplier or supplier.role != "supplier":
        raise HTTPException(404, "Fornecedor não encontrado")

    now = datetime.utcnow()
    # Invalida links anteriores ainda não utilizados para que apenas o mais recente funcione.
    previous = db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == supplier.id,
            PasswordResetToken.used_at.is_(None),
        )
    ).all()
    for item in previous:
        item.used_at = now

    raw_token = secrets.token_urlsafe(32)
    reset = PasswordResetToken(
        user_id=supplier.id,
        token_hash=token_digest(raw_token),
        expires_at=now + timedelta(hours=1),
        created_by_user_id=admin.id,
    )
    db.add(reset)
    db.commit()

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    reset_link = f"{public_base_url}/redefinir-senha/{raw_token}" if public_base_url else str(request.url_for("reset_password_page", token=raw_token))
    suppliers = db.scalars(select(User).where(User.role == "supplier").order_by(User.company_name, User.name)).all()
    return templates.TemplateResponse(
        request,
        "admin_suppliers.html",
        {
            "user": admin,
            "suppliers": suppliers,
            "error": None,
            "reset_link": reset_link,
            "reset_supplier": supplier,
            "created": False,
        },
    )


def find_valid_reset_token(db: Session, raw_token: str) -> Optional[PasswordResetToken]:
    digest = token_digest(raw_token)
    reset = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == digest))
    if not reset or reset.used_at is not None or reset.expires_at < datetime.utcnow():
        return None
    return reset


@app.get("/redefinir-senha/{token}", response_class=HTMLResponse, name="reset_password_page")
def reset_password_page(token: str, request: Request, db: Session = Depends(get_db)):
    reset = find_valid_reset_token(db, token)
    supplier = db.get(User, reset.user_id) if reset else None
    return templates.TemplateResponse(
        request,
        "reset_password.html",
        {"user": None, "token": token, "valid": bool(reset and supplier and supplier.active), "supplier": supplier, "error": None, "success": False},
        status_code=200 if reset and supplier and supplier.active else 400,
    )


@app.post("/redefinir-senha/{token}", response_class=HTMLResponse)
def reset_password_submit(
    token: str,
    request: Request,
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    reset = find_valid_reset_token(db, token)
    supplier = db.get(User, reset.user_id) if reset else None
    if not reset or not supplier or not supplier.active:
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {"user": None, "token": token, "valid": False, "supplier": supplier, "error": "Este link expirou ou já foi utilizado.", "success": False},
            status_code=400,
        )
    if password != password_confirm:
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {"user": None, "token": token, "valid": True, "supplier": supplier, "error": "As duas senhas não são iguais.", "success": False},
            status_code=400,
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {"user": None, "token": token, "valid": True, "supplier": supplier, "error": "A nova senha deve ter pelo menos 8 caracteres.", "success": False},
            status_code=400,
        )

    supplier.password_hash = hash_password(password)
    supplier.must_change_password = False
    reset.used_at = datetime.utcnow()
    # Invalida qualquer outro link de redefinição que tenha sido criado para o mesmo fornecedor.
    remaining = db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == supplier.id,
            PasswordResetToken.used_at.is_(None),
        )
    ).all()
    for item in remaining:
        item.used_at = reset.used_at
    db.commit()
    return templates.TemplateResponse(
        request,
        "reset_password.html",
        {"user": None, "token": token, "valid": False, "supplier": supplier, "error": None, "success": True},
    )


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
    quote = db.scalar(
        select(Quote).where(Quote.id == quote_id).options(
            selectinload(Quote.items).selectinload(QuoteItem.responses).selectinload(QuoteResponse.supplier)
        )
    )
    if not quote:
        raise HTTPException(404)

    quote_supplier_links = db.scalars(
        select(QuoteSupplier).where(QuoteSupplier.quote_id == quote_id).order_by(QuoteSupplier.id)
    ).all()
    supplier_ids = [link.supplier_id for link in quote_supplier_links]
    supplier_map = {
        supplier.id: supplier
        for supplier in (
            db.scalars(select(User).where(User.id.in_(supplier_ids))).all() if supplier_ids else []
        )
    }
    participants = []
    for link in quote_supplier_links:
        supplier = supplier_map.get(link.supplier_id)
        if supplier:
            participants.append({"supplier": supplier, "submitted_at": link.submitted_at})
    participants.sort(key=lambda row: ((row["supplier"].company_name or row["supplier"].name or "").lower()))
    suppliers = [row["supplier"] for row in participants]
    sent_participants = [row for row in participants if row["submitted_at"] is not None]
    pending_participants = [row for row in participants if row["submitted_at"] is None]

    all_active_suppliers = db.scalars(
        select(User).where(and_(User.role == "supplier", User.active == True)).order_by(User.company_name, User.name)
    ).all()
    current_ids = set(supplier_ids)
    available_suppliers = [supplier for supplier in all_active_suppliers if supplier.id not in current_ids]

    summary = []
    for item in quote.items:
        valid = [r for r in item.responses if r.normalized_price is not None]
        valid.sort(key=lambda r: r.normalized_price)
        summary.append({"item": item, "responses": valid, "min": valid[0] if valid else None, "max": valid[-1] if valid else None})

    corrected_raw = request.query_params.get("corrigido")
    try:
        corrected_response_id = int(corrected_raw) if corrected_raw else None
    except ValueError:
        corrected_response_id = None

    return templates.TemplateResponse(
        request,
        "admin_quote_detail.html",
        {
            "user": user,
            "quote": quote,
            "suppliers": suppliers,
            "participants": participants,
            "sent_participants": sent_participants,
            "pending_participants": pending_participants,
            "available_suppliers": available_suppliers,
            "summary": summary,
            "now": now_local(),
            "supplier_added": request.query_params.get("fornecedor_adicionado") == "1",
            "supplier_already_added": request.query_params.get("fornecedor_ja_adicionado") == "1",
            "corrected_response_id": corrected_response_id,
        },
    )


@app.post("/admin/cotacoes/{quote_id}/fornecedores/adicionar")
def add_supplier_to_quote(
    quote_id: int,
    request: Request,
    supplier_id: int = Form(...),
    db: Session = Depends(get_db),
):
    require_role(request, db, "admin")
    quote = db.get(Quote, quote_id)
    if not quote:
        raise HTTPException(404)

    supplier = db.get(User, supplier_id)
    if not supplier or supplier.role != "supplier" or not supplier.active:
        raise HTTPException(400, "Fornecedor inválido ou inativo")

    existing = db.scalar(
        select(QuoteSupplier).where(
            and_(QuoteSupplier.quote_id == quote_id, QuoteSupplier.supplier_id == supplier_id)
        )
    )
    if existing:
        return RedirectResponse(f"/admin/cotacoes/{quote_id}?fornecedor_ja_adicionado=1", 303)

    db.add(QuoteSupplier(quote_id=quote_id, supplier_id=supplier_id))
    db.commit()
    return RedirectResponse(f"/admin/cotacoes/{quote_id}?fornecedor_adicionado=1", 303)


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
        return RedirectResponse(f"/admin/cotacoes/{quote_id}?corrigido={response_id}#resp-{response_id}", 303)
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



def build_supplier_order_rows(db: Session, quote: Quote, supplier_id: int, order: Optional[PurchaseOrder]):
    """Monta as linhas do fechamento. Por padrão seleciona o menor preço de cada item."""
    existing_by_item = {item.quote_item_id: item for item in (order.items if order else [])}
    rows = []
    total = Decimal("0")
    for item in quote.items:
        supplier_response = next((r for r in item.responses if r.supplier_id == supplier_id and r.normalized_price is not None), None)
        if not supplier_response:
            continue
        valid = [r for r in item.responses if r.normalized_price is not None]
        min_response = min(valid, key=lambda r: r.normalized_price) if valid else None
        existing = existing_by_item.get(item.id)
        selected = existing is not None or (order is None and min_response is not None and min_response.id == supplier_response.id)
        factor = Decimal(supplier_response.base_units_per_offered_unit or 1)
        default_qty = (Decimal(item.requested_qty) / factor).quantize(Decimal("1"), rounding=ROUND_CEILING) if factor > 0 else Decimal(item.requested_qty)
        quantity = Decimal(existing.quantity_offered) if existing else default_qty
        line_total = (quantity * Decimal(supplier_response.offered_price)) if selected else Decimal("0")
        base_equivalent = quantity * factor
        if selected:
            total += line_total
        rows.append(
            {
                "item": item,
                "response": supplier_response,
                "is_lowest": bool(min_response and min_response.id == supplier_response.id),
                "selected": selected,
                "quantity": quantity,
                "base_equivalent": base_equivalent,
                "line_total": line_total,
            }
        )
    return rows, total


@app.get("/admin/cotacoes/{quote_id}/pedidos/{supplier_id}", response_class=HTMLResponse)
def admin_supplier_order_page(quote_id: int, supplier_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    link = db.scalar(select(QuoteSupplier).where(QuoteSupplier.quote_id == quote_id, QuoteSupplier.supplier_id == supplier_id))
    if not link:
        raise HTTPException(404, "Fornecedor não participa desta cotação")
    supplier = db.get(User, supplier_id)
    quote = db.scalar(
        select(Quote).where(Quote.id == quote_id).options(
            selectinload(Quote.items).selectinload(QuoteItem.responses).selectinload(QuoteResponse.supplier)
        )
    )
    if not quote or not supplier:
        raise HTTPException(404)
    order = db.scalar(
        select(PurchaseOrder).where(PurchaseOrder.quote_id == quote_id, PurchaseOrder.supplier_id == supplier_id).options(
            selectinload(PurchaseOrder.items)
        )
    )
    rows, total = build_supplier_order_rows(db, quote, supplier_id, order)
    return templates.TemplateResponse(
        request,
        "admin_supplier_order.html",
        {
            "user": user,
            "quote": quote,
            "supplier": supplier,
            "submission": link,
            "order": order,
            "rows": rows,
            "total": total,
            "saved": request.query_params.get("salvo") == "1",
            "finalized": request.query_params.get("finalizado") == "1",
            "error": None,
        },
    )


@app.post("/admin/cotacoes/{quote_id}/pedidos/{supplier_id}", response_class=HTMLResponse)
async def admin_supplier_order_save(quote_id: int, supplier_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    link = db.scalar(select(QuoteSupplier).where(QuoteSupplier.quote_id == quote_id, QuoteSupplier.supplier_id == supplier_id))
    supplier = db.get(User, supplier_id)
    quote = db.scalar(
        select(Quote).where(Quote.id == quote_id).options(
            selectinload(Quote.items).selectinload(QuoteItem.responses).selectinload(QuoteResponse.supplier)
        )
    )
    if not link or not supplier or not quote:
        raise HTTPException(404)
    order = db.scalar(
        select(PurchaseOrder).where(PurchaseOrder.quote_id == quote_id, PurchaseOrder.supplier_id == supplier_id).options(
            selectinload(PurchaseOrder.items)
        )
    )
    form = await request.form()
    action = str(form.get("action") or "save")
    try:
        selected_rows = []
        for item in quote.items:
            if str(form.get(f"include_{item.id}") or "") != "1":
                continue
            response = next((r for r in item.responses if r.supplier_id == supplier_id), None)
            if not response:
                continue
            qty = parse_decimal(form.get(f"qty_{item.id}"), "0")
            if qty <= 0:
                raise ValueError(f"Informe uma quantidade maior que zero para {item.description}.")
            selected_rows.append((item, response, qty))
        if action == "finalize" and not selected_rows:
            raise ValueError("Selecione ao menos um item para finalizar o pedido.")

        if not order:
            order = PurchaseOrder(quote_id=quote_id, supplier_id=supplier_id, status="draft")
            db.add(order)
            db.flush()
        else:
            db.execute(delete(PurchaseOrderItem).where(PurchaseOrderItem.order_id == order.id))

        for item, response, qty in selected_rows:
            db.add(
                PurchaseOrderItem(
                    order_id=order.id,
                    quote_item_id=item.id,
                    response_id=response.id,
                    quantity_offered=qty,
                )
            )
        order.status = "ready" if action == "finalize" else "draft"
        order.updated_at = datetime.utcnow()
        db.commit()
        flag = "finalizado=1" if action == "finalize" else "salvo=1"
        return RedirectResponse(f"/admin/cotacoes/{quote_id}/pedidos/{supplier_id}?{flag}", 303)
    except (ValueError, InvalidOperation) as exc:
        db.rollback()
        order = db.scalar(
            select(PurchaseOrder).where(PurchaseOrder.quote_id == quote_id, PurchaseOrder.supplier_id == supplier_id).options(
                selectinload(PurchaseOrder.items)
            )
        )
        rows, total = build_supplier_order_rows(db, quote, supplier_id, order)
        return templates.TemplateResponse(
            request,
            "admin_supplier_order.html",
            {
                "user": user,
                "quote": quote,
                "supplier": supplier,
                "submission": link,
                "order": order,
                "rows": rows,
                "total": total,
                "saved": False,
                "finalized": False,
                "error": str(exc),
            },
            status_code=400,
        )


@app.get("/admin/cotacoes/{quote_id}/pedidos/{supplier_id}/imprimir", response_class=HTMLResponse)
def admin_supplier_order_print(quote_id: int, supplier_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    supplier = db.get(User, supplier_id)
    quote = db.scalar(
        select(Quote).where(Quote.id == quote_id).options(
            selectinload(Quote.items).selectinload(QuoteItem.responses).selectinload(QuoteResponse.supplier)
        )
    )
    order = db.scalar(
        select(PurchaseOrder).where(PurchaseOrder.quote_id == quote_id, PurchaseOrder.supplier_id == supplier_id).options(
            selectinload(PurchaseOrder.items)
        )
    )
    if not supplier or not quote or not order:
        raise HTTPException(404, "Salve o fechamento antes de gerar o pedido.")
    rows, total = build_supplier_order_rows(db, quote, supplier_id, order)
    selected_rows = [row for row in rows if row["selected"]]
    return templates.TemplateResponse(
        request,
        "admin_supplier_order_print.html",
        {
            "user": user,
            "quote": quote,
            "supplier": supplier,
            "order": order,
            "rows": selected_rows,
            "total": total,
            "generated_at": now_local(),
        },
    )


@app.get("/fornecedor/alterar-senha", response_class=HTMLResponse)
def supplier_change_password_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or not user.active or user.role not in ("supplier", "employee"):
        raise HTTPException(403, "Acesso negado")
    return templates.TemplateResponse(
        request,
        "change_password.html",
        {"user": user, "first_access": bool(user.must_change_password), "error": None},
    )


@app.post("/fornecedor/alterar-senha", response_class=HTMLResponse)
def supplier_change_password_submit(
    request: Request,
    current_password: str = Form(default=""),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if not user or not user.active or user.role not in ("supplier", "employee"):
        raise HTTPException(403, "Acesso negado")
    first_access = bool(user.must_change_password)

    if not first_access and not verify_password(current_password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"user": user, "first_access": False, "error": "A senha atual está incorreta."},
            status_code=400,
        )
    if password != password_confirm:
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"user": user, "first_access": first_access, "error": "As duas senhas novas não são iguais."},
            status_code=400,
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"user": user, "first_access": first_access, "error": "A nova senha deve ter pelo menos 8 caracteres."},
            status_code=400,
        )
    if verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"user": user, "first_access": first_access, "error": "Escolha uma senha diferente da senha atual/provisória."},
            status_code=400,
        )

    user.password_hash = hash_password(password)
    user.must_change_password = False
    db.commit()
    return RedirectResponse("/admin/baixas/nova" if user.role == "employee" else "/fornecedor?senha=alterada", 303)


@app.get("/fornecedor", response_class=HTMLResponse)
def supplier_dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "supplier")
    if user.must_change_password:
        return RedirectResponse("/fornecedor/alterar-senha", 303)
    quote_ids = db.scalars(select(QuoteSupplier.quote_id).where(QuoteSupplier.supplier_id == user.id)).all()
    quotes = db.scalars(select(Quote).where(Quote.id.in_(quote_ids)).order_by(Quote.deadline.desc())).all() if quote_ids else []
    password_changed = request.query_params.get("senha") == "alterada"
    return templates.TemplateResponse(request, "supplier_dashboard.html", {"user": user, "quotes": quotes, "now": now_local(), "password_changed": password_changed})


@app.get("/fornecedor/cotacoes/{quote_id}", response_class=HTMLResponse)
def supplier_quote_page(quote_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "supplier")
    if user.must_change_password:
        return RedirectResponse("/fornecedor/alterar-senha", 303)
    allowed = db.scalar(select(QuoteSupplier).where(and_(QuoteSupplier.quote_id == quote_id, QuoteSupplier.supplier_id == user.id)))
    if not allowed:
        raise HTTPException(403)
    quote = db.scalar(select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.items)))
    if not quote:
        raise HTTPException(404)
    responses = db.scalars(select(QuoteResponse).where(QuoteResponse.supplier_id == user.id, QuoteResponse.quote_item_id.in_([i.id for i in quote.items]))).all() if quote.items else []
    by_item = {r.quote_item_id: r for r in responses}
    return templates.TemplateResponse(
        request,
        "supplier_quote.html",
        {
            "user": user,
            "quote": quote,
            "responses": by_item,
            "now": now_local(),
            "error": None,
            "saved": request.query_params.get("salvo") == "1",
        },
    )


@app.post("/fornecedor/cotacoes/{quote_id}")
async def supplier_save_quote(quote_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "supplier")
    if user.must_change_password:
        return RedirectResponse("/fornecedor/alterar-senha", 303)
    allowed = db.scalar(select(QuoteSupplier).where(and_(QuoteSupplier.quote_id == quote_id, QuoteSupplier.supplier_id == user.id)))
    quote = db.scalar(select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.items)))
    if not allowed or not quote:
        raise HTTPException(403)
    if now_local() > quote.deadline:
        raise HTTPException(403, "O prazo desta cotação foi encerrado.")
    form = await request.form()
    try:
        item_ids = [item.id for item in quote.items]
        has_response = bool(
            db.scalar(
                select(QuoteResponse.id).where(
                    QuoteResponse.supplier_id == user.id,
                    QuoteResponse.quote_item_id.in_(item_ids),
                ).limit(1)
            )
        ) if item_ids else False
        for item in quote.items:
            price_raw = str(form.get(f"price_{item.id}") or "").strip()
            if not price_raw:
                continue
            has_response = True
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
        if not has_response:
            raise ValueError("Preencha ao menos um preço antes de enviar a cotação.")
        # O clique em Salvar minha cotação é considerado o envio da proposta.
        allowed.submitted_at = now_local()
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    return RedirectResponse(f"/fornecedor/cotacoes/{quote_id}?salvo=1", 303)


@app.get("/admin/pedidos", response_class=HTMLResponse)
def admin_orders(request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    quotes = db.scalars(select(Quote).order_by(Quote.created_at.desc())).all()
    selected = request.query_params.get("cotacao", "")
    query = select(QuoteSupplier, Quote, User, PurchaseOrder).join(Quote, Quote.id == QuoteSupplier.quote_id).join(User, User.id == QuoteSupplier.supplier_id).outerjoin(PurchaseOrder, and_(PurchaseOrder.quote_id == QuoteSupplier.quote_id, PurchaseOrder.supplier_id == QuoteSupplier.supplier_id))
    if selected.isdigit():
        query = query.where(Quote.id == int(selected))
    rows = db.execute(query.order_by(Quote.created_at.desc(), User.company_name, User.name)).all()
    return templates.TemplateResponse(request, "admin_orders.html", {"user": user, "quotes": quotes, "selected": selected, "rows": rows})


WRITE_OFF_REASONS = ["Vencimento", "Avaria", "Quebra / perda", "Consumo interno", "Doação", "Furto / extravio", "Devolução", "Ajuste de estoque", "Outros"]


def write_off_values(form):
    try:
        day = date.fromisoformat(str(form.get("movement_date", "")))
    except ValueError:
        raise ValueError("Informe uma data válida.")
    description = str(form.get("description", "")).strip()
    code = str(form.get("code", "")).strip()
    unit = str(form.get("unit", "")).strip().upper()
    reason = str(form.get("reason", ""))
    notes = str(form.get("notes", "")).strip()
    if not description or len(description) > 255 or len(code) > 80 or not unit or len(unit) > 40 or len(notes) > 4000:
        raise ValueError("Confira os dados do produto e os limites dos campos.")
    if reason not in WRITE_OFF_REASONS:
        raise ValueError("Selecione um motivo de baixa válido.")
    quantity = parse_decimal(form.get("quantity"))
    cost_raw = str(form.get("unit_cost", "")).strip()
    cost = parse_decimal(cost_raw) if cost_raw else None
    for number in (quantity, cost):
        if number is not None and (not number.is_finite() or number < 0 or number >= Decimal("10000000000") or number != number.quantize(Decimal("0.0001"))):
            raise ValueError("Informe valores válidos com até quatro casas decimais.")
    if quantity <= 0:
        raise ValueError("A quantidade precisa ser maior que zero.")
    return dict(movement_date=day, code=code, description=description, unit=unit, quantity=quantity, unit_cost=cost, reason=reason, notes=notes or None)


def normalize_product_search(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", str(value or "").casefold()) if not unicodedata.combining(c)).split())


def legacy_product_lookup(db, term, mode):
    """Consulta todos os produtos cotados, baixas ativas e vínculos explícitos.
    Nenhum código é convertido em número: zeros à esquerda são preservados.
    """
    exact = mode == "code"
    if exact:
        alias = db.get(ProductBarcode, term)
        if alias:
            return [{"code": alias.code, "description": alias.description, "unit": alias.unit, "barcode": alias.barcode}], False
    q = select(QuoteItem.code.label("code"), QuoteItem.description.label("description"), QuoteItem.base_unit.label("unit"), literal("").label("barcode")).distinct()
    w = select(ProductWriteOff.code, ProductWriteOff.description, ProductWriteOff.unit, literal("")).where(ProductWriteOff.cancelled_at.is_(None)).distinct()
    b = select(ProductBarcode.code, ProductBarcode.description, ProductBarcode.unit, ProductBarcode.barcode)
    if exact:
        q = q.where(QuoteItem.code == term)
        w = w.where(ProductWriteOff.code == term)
        b = b.where(ProductBarcode.code == term)
    source = union_all(b, q, w).subquery()
    query = select(source).order_by(source.c.description, source.c.code, source.c.unit, source.c.barcode.desc()).execution_options(yield_per=200)
    tokens = normalize_product_search(term).split()
    results, seen = [], set()
    for code, description, unit, barcode in db.execute(query):
        code, description, unit = code or "", description or "", (unit or "UN").upper()
        if not description.strip(): continue
        if not exact and not all(t in normalize_product_search(f"{description} {code} {barcode or ''}") for t in tokens): continue
        key = (code, normalize_product_search(description), unit)
        if key in seen: continue
        seen.add(key)
        results.append(dict(code=code, description=description, unit=unit, barcode=barcode or ""))
        if len(results) > 30: break
    return results[:30], len(results) > 30


def product_lookup(db, term, mode):
    exact = mode == "code"
    # Zero é um marcador de ausência de código de barras, não um código pesquisável.
    tokens = normalize_product_search(term).split()
    results = []
    stmt = select(CatalogProduct).order_by(CatalogProduct.description)
    if exact:
        stmt = stmt.where((CatalogProduct.code == term) | ((CatalogProduct.barcode == term) & CatalogProduct.source_barcode.is_(None)) | ((CatalogProduct.source_barcode == term) if term.strip('0') else literal(False)))
    for p in db.scalars(stmt.execution_options(yield_per=200)):
        if exact or all(t in normalize_product_search(f"{p.description} {p.code} {p.source_barcode if p.source_barcode is not None else p.barcode}") for t in tokens):
            results.append(catalog_dict(p))
            if len(results) > 30: return results[:30], True
    old, more = legacy_product_lookup(db, term, mode)
    # O cadastro geral é a fonte prioritária; não repetir a mesma identificação histórica.
    for item in old:
        if any((item['code'] and item['code'] in (p['code'], p['barcode'])) or (item['barcode'] and item['barcode'] == p['barcode']) or (normalize_product_search(item['description']) == normalize_product_search(p['description']) and item['unit'] == p['unit'] and (not item['code'] or not p['code'])) for p in results):
            continue
        results.append(item)
    return results[:30], more or len(results)>30


def catalog_dict(p):
    return dict(code=p.code or p.barcode, description=p.description, unit=p.unit, barcode=p.source_barcode if p.source_barcode is not None else p.barcode, unit_cost=str(p.unit_cost), catalog_barcode=p.barcode, source_status=p.source_status)


def require_write_off_user(request, db):
    user = current_user(request, db)
    if not user or not user.active or user.role not in ("admin", "employee") or (user.role == "employee" and user.must_change_password):
        raise HTTPException(403, "Acesso negado. Faça login e conclua a troca da senha inicial.")
    return user


def department_for(db, user):
    return db.get(Department, user.department_id) if user.department_id else None


def checked_department(db, user):
    dept = department_for(db, user)
    if not dept or not dept.active:
        raise ValueError("Seu usuário precisa estar vinculado a um setor ativo. Solicite o vínculo ao administrador.")
    return dept


def apply_catalog(db, form, data, row=None):
    barcode = str(form.get("catalog_barcode", "")).strip()
    if barcode:
        p = db.get(CatalogProduct, barcode)
        if not p: raise ValueError("Produto não encontrado no cadastro. Busque novamente.")
        # Preservar custo e identidade históricos ao editar o mesmo produto.
        if not row or row.catalog_barcode != barcode:
            data.update(code=p.code or p.barcode, description=p.description, unit=p.unit, unit_cost=p.unit_cost)
        else:
            data.update(code=row.code, description=row.description, unit=row.unit, unit_cost=row.unit_cost)
    if not row or row.catalog_barcode != (barcode or None):
        data["closing_group_id"] = p.closing_group_id if barcode else None
    data['catalog_barcode'] = barcode or None


@app.get("/admin/baixas/buscar-produtos")
def product_write_off_search(request: Request, q: str = "", modo: str = "description", db: Session = Depends(get_db)):
    require_write_off_user(request, db)
    q = q.strip()
    if modo not in ("code", "description") or len(q) > 255 or (modo == "code" and len(q) > 80):
        raise HTTPException(400, "Busca inválida.")
    if not q or (modo == "description" and len(q) < 2):
        return {"products": [], "more": False}
    products, more = product_lookup(db, q, modo)
    return {"products": products, "more": more}


@app.post("/admin/baixas/ler-camera")
async def product_camera_read(request: Request, db: Session = Depends(get_db)):
    require_write_off_user(request, db)
    from starlette.concurrency import run_in_threadpool
    from .barcode_camera import MAX_BYTES, CameraImageError, read_codes, slots
    # Somente quadros JPEG/PNG da página autenticada; limite também no corpo recebido.
    if request.headers.get('content-type', '').split(';')[0] not in ('image/jpeg', 'image/png'):
        raise HTTPException(415, 'Formato de imagem não permitido.')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Origem não permitida.')
    import asyncio
    raw = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_BYTES:
                    raise HTTPException(413, 'Imagem muito grande.')
                raw.extend(chunk)
    except TimeoutError as exc:
        raise HTTPException(408, 'Tempo de recebimento da imagem excedido.') from exc
    if not slots.acquire(blocking=False):
        raise HTTPException(429, 'Leitor ocupado. Tente novamente.')
    try:
        try:
            codes = await run_in_threadpool(read_codes, bytes(raw))
        except CameraImageError as exc:
            raise HTTPException(400, str(exc)) from exc
        from fastapi.responses import JSONResponse
        return JSONResponse({'codes': codes}, headers={'Cache-Control': 'no-store'})
    finally:
        slots.release()


def save_product_barcode(db, form, data):
    if form.get("remember_barcode") != "1": return
    barcode = str(form.get("barcode", "")).strip()
    if not barcode or len(barcode) > 80 or any(c.isspace() or not c.isprintable() for c in barcode):
        raise ValueError("Informe um código de barras válido, sem espaços, para vincular.")
    current = db.get(ProductBarcode, barcode)
    identity = (data["code"], normalize_product_search(data["description"]), data["unit"])
    if current:
        if identity != (current.code, normalize_product_search(current.description), current.unit):
            raise ValueError("Este código de barras já está vinculado a outro produto. Desmarque o vínculo e confira o código antes de salvar.")
        return
    # Um código já conhecido com outra identificação não deve ser sobrescrito.
    matches, more = product_lookup(db, barcode, "code")
    if more or any((p["code"], normalize_product_search(p["description"]), p["unit"]) != identity for p in matches):
        raise ValueError("O código lido já identifica outro produto no histórico. Confira antes de criar o vínculo.")
    db.add(ProductBarcode(barcode=barcode, code=data["code"], description=data["description"], unit=data["unit"]))


def render_write_off_form(request, db, user, row=None, values=None, error=None):
    data = values if values is not None else ({key: getattr(row, key) for key in ("movement_date", "code", "description", "unit", "quantity", "unit_cost", "reason", "notes")} if row else {"movement_date": now_local().date().isoformat(), "unit": "UN", "quantity": "1"})
    if row and values is None: data["catalog_barcode"] = row.catalog_barcode or ""
    if not row and not data.get("reason"): data["reason"] = "Consumo interno"
    dept = department_for(db, user)
    return templates.TemplateResponse(request, "product_write_off_form.html", {"dept": dept, "user": user, "row": row, "values": data, "reasons": WRITE_OFF_REASONS, "error": error, "saved": request.query_params.get("salvo") == "1" and not error}, status_code=400 if error else 200)


@app.get("/admin/baixas", response_class=HTMLResponse)
def product_write_offs(request: Request, db: Session = Depends(get_db)):
    user = require_write_off_user(request, db)
    today = now_local().date()
    start = request.query_params.get("inicio", today.replace(day=1).isoformat())
    end = request.query_params.get("fim", today.isoformat())
    reason = request.query_params.get("motivo", "")
    search = request.query_params.get("busca", "").strip()[:255]
    show_cancelled = request.query_params.get("canceladas") == "1"
    query = select(ProductWriteOff)
    if user.role == "employee": query = query.where(ProductWriteOff.created_by_user_id == user.id)
    sector = request.query_params.get("setor", "") if user.role == "admin" else ""
    operator = request.query_params.get("usuario", "") if user.role == "admin" else ""
    if sector == "sem": query = query.where(ProductWriteOff.department_id.is_(None))
    elif sector.isdigit(): query = query.where(ProductWriteOff.department_id == int(sector))
    if operator.isdigit(): query = query.where(ProductWriteOff.created_by_user_id == int(operator))
    error = None
    try:
        if start and end and date.fromisoformat(start) > date.fromisoformat(end):
            raise ValueError()
        if start: query = query.where(ProductWriteOff.movement_date >= date.fromisoformat(start))
        if end: query = query.where(ProductWriteOff.movement_date <= date.fromisoformat(end))
    except ValueError:
        error = "Período inválido. Confira as datas."
    if not show_cancelled: query = query.where(ProductWriteOff.cancelled_at.is_(None))
    if reason in WRITE_OFF_REASONS: query = query.where(ProductWriteOff.reason == reason)
    if search: query = query.where((ProductWriteOff.description.icontains(search, autoescape=True)) | (ProductWriteOff.code.icontains(search, autoescape=True)))
    rows = [] if error else db.scalars(query.order_by(ProductWriteOff.movement_date.desc(), ProductWriteOff.id.desc())).all()
    active = [r for r in rows if not r.cancelled_at]
    cost_total = sum((r.quantity * r.unit_cost for r in active if r.unit_cost is not None), Decimal("0"))
    operators = {u.id:u.name for u in db.scalars(select(User).where(User.role.in_(["admin", "employee"])))} if user.role == "admin" else {user.id:user.name}
    departments = db.scalars(select(Department).order_by(Department.name)).all() if user.role == "admin" else []
    report = request.query_params.get("relatorio") == "1"
    totals = {}
    master_codes = {p.code or p.barcode:p.barcode for p in db.scalars(select(CatalogProduct))}
    for r in active:
        key = (r.catalog_barcode or master_codes.get(r.code) or r.code or normalize_product_search(r.description), r.unit)
        if key not in totals: totals[key] = dict(code=r.code, description=r.description, unit=r.unit, quantity=Decimal(0), cost=Decimal(0), missing=0)
        t = totals[key]; t['quantity'] += r.quantity
        if r.unit_cost is None: t['missing'] += 1
        else: t['cost'] += r.quantity * r.unit_cost
    return templates.TemplateResponse(request, "product_write_off_report.html" if report else "product_write_offs.html", {"sector":sector,"operator":operator,"departments":departments,"operators":operators,"totals":sorted(totals.values(), key=lambda t:t['description'].casefold()),"user": user, "rows": rows, "start": start, "end": end, "reason": reason, "search": search, "show_cancelled": show_cancelled, "reasons": WRITE_OFF_REASONS, "error": error, "cost_total": cost_total, "missing_cost": sum(r.unit_cost is None for r in active), "active_count": len(active)})


@app.get("/admin/baixas/nova", response_class=HTMLResponse)
def product_write_off_new(request: Request, db: Session = Depends(get_db)):
    user = require_write_off_user(request, db)
    values = None
    if request.query_params.get("salvo") == "1":
        try:
            day = date.fromisoformat(request.query_params.get("data", ""))
        except ValueError:
            day = now_local().date()
        reason = request.query_params.get("motivo", "")
        values = {"movement_date": day.isoformat(), "reason": reason if reason in WRITE_OFF_REASONS else "", "unit": "UN", "quantity": "1"}
    return render_write_off_form(request, db, user, values=values)


@app.post("/admin/baixas/nova", response_class=HTMLResponse)
async def product_write_off_create(request: Request, db: Session = Depends(get_db)):
    user = require_write_off_user(request, db)
    form = await request.form()
    try:
        data = write_off_values(form)
        dept = checked_department(db, user)
        apply_catalog(db, form, data)
        if user.role == 'employee':
            if not data['catalog_barcode']:
                # Produtos antigos continuam disponíveis, mas somente com identificação conhecida.
                matches, _ = legacy_product_lookup(db, data['code'] or data['description'], 'code' if data['code'] else 'description')
                if not any((p['code'], normalize_product_search(p['description']), p['unit']) == (data['code'], normalize_product_search(data['description']), data['unit']) for p in matches):
                    raise ValueError('Produto não encontrado. Solicite o cadastro ao administrador.')
                data['unit_cost'] = None
        else:
            save_product_barcode(db, form, data)
    except (ValueError, InvalidOperation) as exc:
        return render_write_off_form(request, db, user, values=dict(form), error=str(exc) if isinstance(exc, ValueError) else "Valor numérico inválido.")
    db.add(ProductWriteOff(**data, created_by_user_id=user.id, department_id=dept.id, department_name=dept.name, operator_name=user.name))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render_write_off_form(request, db, user, values=dict(form), error="Não foi possível salvar o vínculo. Confira o código e tente novamente.")
    day = data["movement_date"].isoformat()
    if form.get("action") == "next":
        return RedirectResponse("/admin/baixas/nova?" + urlencode({"salvo": "1", "data": day, "motivo": data["reason"]}), 303)
    return RedirectResponse(f"/admin/baixas?inicio={day}&fim={day}", 303)


@app.get("/admin/baixas/{row_id}/editar", response_class=HTMLResponse)
def product_write_off_edit(request: Request, row_id: int, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    row = db.get(ProductWriteOff, row_id)
    if not row: raise HTTPException(404, "Baixa não encontrada.")
    if row.cancelled_at: raise HTTPException(403, "Uma baixa cancelada não pode ser editada.")
    return render_write_off_form(request, db, user, row=row)


@app.post("/admin/baixas/{row_id}/editar", response_class=HTMLResponse)
async def product_write_off_update(request: Request, row_id: int, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    row = db.scalar(select(ProductWriteOff).where(ProductWriteOff.id == row_id).with_for_update())
    if not row: raise HTTPException(404, "Baixa não encontrada.")
    if row.cancelled_at: raise HTTPException(403, "Uma baixa cancelada não pode ser editada.")
    form = await request.form()
    try:
        data = write_off_values(form)
        apply_catalog(db, form, data, row)
        save_product_barcode(db, form, data)
    except (ValueError, InvalidOperation) as exc:
        return render_write_off_form(request, db, user, row=row, values=dict(form), error=str(exc) if isinstance(exc, ValueError) else "Valor numérico inválido.")
    for key, val in data.items(): setattr(row, key, val)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render_write_off_form(request, db, user, row=row, values=dict(form), error="Não foi possível salvar o vínculo. Confira o código e tente novamente.")
    day = data["movement_date"].isoformat()
    return RedirectResponse(f"/admin/baixas?inicio={day}&fim={day}", 303)


@app.post("/admin/baixas/{row_id}/cancelar")
def product_write_off_cancel(request: Request, row_id: int, db: Session = Depends(get_db)):
    user = require_role(request, db, "admin")
    row = db.scalar(select(ProductWriteOff).where(ProductWriteOff.id == row_id).with_for_update())
    if not row: raise HTTPException(404, "Baixa não encontrada.")
    if not row.cancelled_at:
        row.cancelled_at = datetime.utcnow()
        row.cancelled_by_user_id = user.id
        db.commit()
    return RedirectResponse("/admin/baixas?canceladas=1&inicio=&fim=", 303)


@app.get('/admin/setores', response_class=HTMLResponse)
def departments_page(request: Request, db: Session = Depends(get_db)):
    user = require_role(request, db, 'admin')
    return templates.TemplateResponse(request, 'departments.html', {'user':user,'departments':db.scalars(select(Department).order_by(Department.name)).all()})


@app.post('/admin/setores')
def department_save(request: Request, name: str = Form(...), department_id: str = Form(''), active: str = Form('1'), db: Session = Depends(get_db)):
    require_role(request, db, 'admin')
    name = name.strip()
    if not name or len(name)>120: raise HTTPException(400,'Informe um nome de setor válido.')
    row = db.get(Department, int(department_id)) if department_id.isdigit() else None
    if department_id and not row: raise HTTPException(404,'Setor não encontrado.')
    if any(normalize_product_search(d.name)==normalize_product_search(name) and (not row or d.id!=row.id) for d in db.scalars(select(Department))):
        raise HTTPException(400,'Já existe um setor com esse nome.')
    if not row: row=Department(name=name); db.add(row)
    row.name=name; row.active=active=='1'
    db.commit()
    return RedirectResponse('/admin/setores',303)


@app.get('/admin/usuarios', response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db)):
    user=require_role(request,db,'admin')
    users=db.scalars(select(User).where(User.role.in_(['admin','employee'])).order_by(User.name)).all()
    return templates.TemplateResponse(request,'employees.html',{'user':user,'users':users,'departments':db.scalars(select(Department).order_by(Department.name)).all()})


@app.post('/admin/usuarios')
def user_save(request: Request, name: str=Form(...), email: str=Form(...), department_id: str=Form(''), password: str=Form(''), user_id: str=Form(''), active: str=Form('1'), db:Session=Depends(get_db)):
    require_role(request,db,'admin')
    name,email=name.strip(),email.strip().lower()
    if not name or len(name)>120 or not email or len(email)>180 or '@' not in email: raise HTTPException(400,'Nome ou e-mail inválido.')
    row=db.get(User,int(user_id)) if user_id.isdigit() else None
    if user_id and (not row or row.role not in ('admin','employee')): raise HTTPException(404,'Usuário interno não encontrado.')
    dept=db.get(Department,int(department_id)) if department_id.isdigit() else None
    if (not row or row.role=='employee') and (not dept or not dept.active) and active=='1': raise HTTPException(400,'Selecione um setor ativo.')
    if department_id and not dept: raise HTTPException(400,'Setor inválido.')
    if row and row.role=='admin':
        # Nesta tela, administradores só recebem o vínculo; não alterar credenciais/permissões.
        row.department_id=dept.id if dept else None
    else:
        if password and (len(password)<8 or len(password)>256): raise HTTPException(400,'Senha provisória deve ter de 8 a 256 caracteres.')
        other=db.scalar(select(User).where(User.email==email))
        if other and (not row or other.id!=row.id): raise HTTPException(400,'E-mail já cadastrado.')
        if not row:
            if not password: raise HTTPException(400,'Informe uma senha provisória.')
            row=User(role='employee',name=name,email=email,password_hash=hash_password(password),must_change_password=True);db.add(row)
        row.name=name;row.email=email;row.active=active=='1';row.department_id=dept.id if dept else None
        if password: row.password_hash=hash_password(password);row.must_change_password=True
    db.commit()
    return RedirectResponse('/admin/usuarios',303)


def catalog_header(v):
    return ''.join(c for c in normalize_product_search(v) if c.isalnum())


CATALOG_HEADERS={
    'barcode':{'codigodebarras','codigobarras','ean','ean13','gtin','barcode','codbarras','codbarra','barras','codigobarra'},
    'description':{'descricao','descricaoproduto','descricaodoproduto','produto','nome','nomeproduto','mercadoria'},
    'cost':{'precocusto','precodecusto','custounitario','custo','valorcusto','valorunitariocusto','custoproduto','custoreposicao','custoaquisicao'},
    'code':{'codigo','codigointerno','codproduto','codigoproduto','codigodoproduto','sku','cod'},
    'unit':{'unidade','un','und','unid','unidademedida','unidadedemedida'},
    'closing_category':{'categoriadebaixa','categoriabaixa','subgrupofinanceiro'},
}


def cell_code(value, fmt=''):
    if value is None: return ''
    if isinstance(value,bool): raise ValueError('Código inválido.')
    if isinstance(value,(float,int)):
        if value<0 or int(value)!=value or value>=10**15: raise ValueError('Código numérico inválido ou com precisão perdida. Use texto.')
        code=str(int(value))
        if fmt and set(fmt)=={'0'}: code=code.zfill(len(fmt))
        return code
    return str(value).strip()


def read_catalog_rows(raw, filename):
    if filename.lower().endswith('.xlsx'):
        wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
        try:
            for sheet in wb.worksheets:
                yield sheet.title, ((i,[(c.value,c.number_format or '') for c in row]) for i,row in enumerate(sheet.iter_rows(),1))
        finally: wb.close()
    elif filename.lower().endswith('.xls'):
        try: import xlrd
        except ImportError: raise ValueError('Suporte XLS não instalado. Atualize as dependências.')
        wb=xlrd.open_workbook(file_contents=raw,formatting_info=True)
        for sheet in wb.sheets():
            def rows():
                for i in range(sheet.nrows):
                    yield i+1,[(sheet.cell_value(i,j),wb.format_map[wb.xf_list[sheet.cell_xf_index(i,j)].format_key].format_str) for j in range(sheet.ncols)]
            yield sheet.name,rows()
    else: raise ValueError('Selecione um arquivo XLS ou XLSX.')


def import_catalog(db, raw, filename, update_cost=False):
    if filename.lower().endswith('.pdf'):
        return import_product_pdf(db, raw, update_cost)
    # Uma planilha de produtos por arquivo; rejeitar ambiguidades em vez de adivinhar colunas.
    data=[]; errors=[]; found=False; excluded=0
    category_names={}
    for g,parent in group_options(db):
        for name in (g.name, parent.name+" / "+g.name, parent.name+" › "+g.name):
            category_names.setdefault(normalize_product_search(name),set()).add(g.id)
    for sheet, rows in read_catalog_rows(raw,filename):
        cols=None
        for i,cells in rows:
            values=[v for v,f in cells]
            if cols is None:
                if i>30: break
                matches={key:[j for j,v in enumerate(values) if catalog_header(v) in aliases] for key,aliases in CATALOG_HEADERS.items()}
                if all(matches[k] for k in ('barcode','description','cost')):
                    if any(len(v)>1 for v in matches.values()): raise ValueError('Há colunas de produto ambíguas. Use o modelo de importação.')
                    if found: raise ValueError('Mais de uma aba com cadastro. Importe uma aba por arquivo.')
                    cols={k:v[0] for k,v in matches.items() if v}; found=True
                continue
            if not any(v is not None and str(v).strip() for v in values): continue
            def get(k): return cells[cols[k]] if k in cols and cols[k]<len(cells) else (None,'')
            try:
                barcode=cell_code(*get('barcode')); code=cell_code(*get('code'))
                desc=str(get('description')[0] or '').strip(); unit=str(get('unit')[0] or 'UN').strip().upper()
                if desc.casefold().startswith('zz'): excluded+=1;continue
                raw_cost=get('cost')[0]
                if raw_cost is None or str(raw_cost).strip()=='': raise ValueError('Custo ausente.')
                cost=parse_decimal(raw_cost)
                if not barcode or len(barcode)>80 or any(c.isspace() for c in barcode) or not desc or len(desc)>255 or len(code)>80 or len(unit)>40 or not cost.is_finite() or cost<0 or cost>=Decimal('10000000000') or cost!=cost.quantize(Decimal('0.0001')): raise ValueError('Código de barras, descrição ou custo inválido.')
                category=str(get('closing_category')[0] or '').strip()
                ids=category_names.get(normalize_product_search(category),set())
                if category and len(ids)!=1: raise ValueError("Categoria de baixa não encontrada ou ambígua: "+category[:160])
                data.append((barcode,code,desc,unit,cost,i,next(iter(ids)) if ids else None))
                if len(data)>100000: raise ValueError('Limite de 100 mil linhas por arquivo.')
            except (ValueError,InvalidOperation,OverflowError) as e:
                errors.append(f'{sheet}, linha {i}: {e}')
                if len(errors)>=20: break
        if errors: break
    if not found: raise ValueError('Cabeçalhos não encontrados: Código de barras, Descrição, Preço de custo. Use o modelo.')
    if errors: raise ValueError('Nenhum produto gravado. '+' | '.join(errors))
    if not data and not excluded: raise ValueError('Planilha sem produtos.')
    existing={p.barcode:p for p in db.scalars(select(CatalogProduct))}; by_code={}
    for p in existing.values():
        if p.code: by_code.setdefault(p.code,set()).add(p.barcode)
    # Vínculos já cadastrados e códigos históricos: não criar outro registro de busca para a mesma identidade.
    aliases={p.barcode:p for p in db.scalars(select(ProductBarcode))}
    legacy={}
    for c,d,u in db.execute(select(QuoteItem.code,QuoteItem.description,QuoteItem.base_unit).distinct()):
        if c: legacy.setdefault(c,set()).add((normalize_product_search(d),u.upper()))
    for c,d,u in db.execute(select(ProductWriteOff.code,ProductWriteOff.description,ProductWriteOff.unit).distinct()):
        if c: legacy.setdefault(c,set()).add((normalize_product_search(d),u.upper()))
    added=skipped=updated=linked=0; seen={}
    categorized=0
    pdf_bars={}
    for p in existing.values():
        if p.source_barcode and p.source_barcode.strip('0'): pdf_bars.setdefault(p.source_barcode,[]).append(p)
    for barcode,code,desc,unit,cost,i,category_id in data:
        pdf_matches=pdf_bars.get(barcode,[])
        if pdf_matches:
            if code: pdf_matches=[p for p in pdf_matches if p.code==code]
            if len(pdf_matches)!=1: raise ValueError(f'Linha {i}: código de barras de cadastro PDF ambíguo. Informe o código interno correto.')
            barcode=pdf_matches[0].barcode
        alias=aliases.get(barcode)
        if alias and code and alias.code and code!=alias.code: raise ValueError(f'Linha {i}: código de barras já vinculado a outro código interno. Nenhum produto gravado.')
        if alias and not code: code=alias.code
        if not code and barcode in legacy: code=barcode
        signature=(code,normalize_product_search(desc),unit,cost,category_id)
        if barcode in seen:
            if seen[barcode]!=signature: raise ValueError(f'Linha {i}: código de barras repetido com dados diferentes. Nenhum produto gravado.')
            skipped+=1;continue
        seen[barcode]=signature
        p=existing.get(barcode)
        if p:
            if code and p.code and code!=p.code: raise ValueError(f'Linha {i}: código interno diverge do cadastro. Nenhum produto gravado.')
            if category_id and not p.closing_group_id: p.closing_group_id=category_id;categorized+=1
            if update_cost and p.unit_cost!=cost: p.unit_cost=cost;updated+=1
            else: skipped+=1
            continue
        if code and code in by_code and barcode not in by_code[code]: raise ValueError(f'Linha {i}: código interno já cadastrado com outro código de barras. Confira o arquivo.')
        historical=legacy.get(code or barcode,set())
        if historical and (normalize_product_search(desc),unit) not in historical and not alias:
            raise ValueError(f'Linha {i}: código histórico com descrição/unidade diferente. Confira antes de importar.')
        if alias or historical: linked+=1
        else: added+=1
        p=CatalogProduct(barcode=barcode,code=code,description=desc,unit=unit,unit_cost=cost,closing_group_id=category_id); db.add(p);existing[barcode]=p
        if code: by_code.setdefault(code,set()).add(barcode)
    db.commit()
    return dict(added=added,linked=linked,skipped=skipped,updated=updated,categorized=categorized,excluded=excluded)


@app.get('/admin/produtos',response_class=HTMLResponse)
def catalog_page(request:Request,db:Session=Depends(get_db)):
    user=require_role(request,db,'admin');q=request.query_params.get('q','').strip()[:255]
    stmt=select(CatalogProduct).order_by(CatalogProduct.description)
    if q: stmt=stmt.where(CatalogProduct.description.icontains(q,autoescape=True)|CatalogProduct.barcode.icontains(q,autoescape=True)|CatalogProduct.source_barcode.icontains(q,autoescape=True)|CatalogProduct.code.icontains(q,autoescape=True))
    return templates.TemplateResponse(request,'catalog.html',{'user':user,'q':q,'products':db.scalars(stmt.limit(200)).all(),'closing_options':group_options(db),'result':None,'error':None})


@app.post('/admin/produtos/importar',response_class=HTMLResponse)
async def catalog_import(request:Request,file:UploadFile=File(...),update_cost:str=Form(''),db:Session=Depends(get_db)):
    user=require_role(request,db,'admin');result=None;error=None
    try:
        raw=await file.read(25*1024*1024+1)
        if len(raw)>25*1024*1024: raise ValueError('Arquivo acima de 25 MB.')
        result=import_catalog(db,raw,file.filename or '',update_cost=='1')
    except Exception as exc:
        db.rollback();error=str(exc) if isinstance(exc,ValueError) else 'Não foi possível ler ou gravar o arquivo. Confira o formato e os dados.'
    finally: await file.close()
    return templates.TemplateResponse(request,'catalog.html',{'user':user,'q':'','products':db.scalars(select(CatalogProduct).order_by(CatalogProduct.description).limit(200)).all(),'closing_options':group_options(db),'result':result,'error':error},status_code=400 if error else 200)


@app.get('/admin/produtos/modelo')
def catalog_model(request:Request,db:Session=Depends(get_db)):
    require_role(request,db,'admin')
    wb=Workbook();ws=wb.active;ws.title='Produtos';ws.append(['Código de barras','Descrição','Preço de custo','Código interno','Unidade','Categoria de baixa'])
    for col in ('A','D'): ws.column_dimensions[col].number_format='@'
    for col,width in [('A',23),('B',45),('C',20),('D',20),('E',15),('F',40)]: ws.column_dimensions[col].width=width
    stream=io.BytesIO();wb.save(stream);stream.seek(0)
    return StreamingResponse(stream,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename=modelo_cadastro_produtos.xlsx'})
