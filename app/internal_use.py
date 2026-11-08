"""Cadastro, retiradas e relatório de uso interno do mercado."""
import csv
import hmac
import io
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .db import get_db
from .models import User, PasswordResetToken
from .internal_models import InternalProduct, InternalWithdrawal, utcnow
from .security import hash_password, verify_password

router = APIRouter()
CLASSES = ('Limpeza', 'Insumo')


def services():
    from . import main
    return main


def tz():
    return ZoneInfo(services().APP_TIMEZONE)


def local_time(value):
    return value.replace(tzinfo=timezone.utc).astimezone(tz()) if value else None


def qty_text(value):
    return format(Decimal(value), 'f').rstrip('0').rstrip('.').replace('.', ',') if Decimal(value) % 1 else str(int(value))


def admin(request, db):
    return services().require_role(request, db, 'admin')


def operator(request, db, allow_password_change=False):
    user = services().current_user(request, db)
    if not user:
        raise HTTPException(303, headers={'Location': '/login'})
    if not user.active or user.role not in ('admin', 'employee'):
        raise HTTPException(403, 'Acesso restrito ao uso interno.')
    if user.role == 'employee' and user.must_change_password and not allow_password_change:
        raise HTTPException(303, headers={'Location': '/uso-interno/alterar-senha'})
    return user


def csrf(request):
    if not request.session.get('internal_csrf'):
        request.session['internal_csrf'] = secrets.token_hex(32)
    return request.session['internal_csrf']


def check_csrf(request, form):
    expected = request.session.get('internal_csrf', '')
    supplied = str(form.get('csrf_token') or '')
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(403, 'Formulário expirado. Atualize a página e tente novamente.')


def render(request, template, user, context=None, status=200):
    values = dict(user=user, csrf_token=csrf(request), classes=CLASSES,
                  local_time=local_time, qty_text=qty_text,
                  message=request.query_params.get('message', ''), error=None)
    values.update(context or {})
    return services().templates.TemplateResponse(request, template, values, status_code=status)


def redirect(path, **params):
    return RedirectResponse(path + ('?' + urlencode(params) if params else ''), 303)


def barcode_key(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9._/\-]{1,80}', value):
        raise ValueError('Informe um código de barras de até 80 caracteres, sem espaços.')
    # UPC-A e EAN equivalentes devem localizar a mesma embalagem.
    if value.isdigit() and len(value) in (8, 12, 13, 14):
        check = (10 - sum(int(n) * (3 if i % 2 == 0 else 1)
                         for i, n in enumerate(reversed(value[:-1]))) % 10) % 10
        if int(value[-1]) == check:
            return 'gtin:' + value.zfill(14)
    return 'code:' + value


def clean_text(value, label, maximum):
    value = str(value or '').strip()
    if not value or len(value) > maximum:
        raise ValueError(f'{label}: preencha de 1 a {maximum} caracteres.')
    return value


def parse_quantity(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'\d{1,10}([.,]\d{1,4})?', value):
        raise ValueError('Informe uma quantidade positiva com até 4 casas decimais, sem separador de milhares.')
    quantity = Decimal(value.replace(',', '.'))
    if quantity <= 0 or quantity >= Decimal('10000000000'):
        raise ValueError('A quantidade deve ser maior que zero e menor que 10 bilhões.')
    return quantity


def product_page(request, db, user, values=None, error=None, status=200):
    q = str(request.query_params.get('q', '')).strip()
    page = max(1, int(request.query_params.get('page', '1'))) if request.query_params.get('page', '1').isdigit() else 1
    query = select(InternalProduct)
    if q:
        term = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        conditions = [InternalProduct.description.ilike(term, escape='\\'), InternalProduct.sector.ilike(term, escape='\\')]
        if q.isdigit() and len(q) < 10:
            conditions.append(InternalProduct.id == int(q))
        try: conditions.append(InternalProduct.barcode_key == barcode_key(q))
        except ValueError: pass
        query = query.where(or_(*conditions))
    rows = db.scalars(query.order_by(InternalProduct.description, InternalProduct.id).offset((page - 1) * 50).limit(51)).all()
    editing = None
    if values is None and request.query_params.get('edit', '').isdigit():
        editing = db.get(InternalProduct, int(request.query_params['edit']))
        if not editing: raise HTTPException(404, 'Produto não encontrado.')
    data = values or (dict(id=editing.id, description=editing.description, barcode=editing.barcode,
                          sector=editing.sector, classification=editing.classification) if editing else {})
    sectors = db.scalars(select(InternalProduct.sector).distinct().order_by(InternalProduct.sector)).all()
    return render(request, 'internal_products.html', user, dict(products=rows[:50], has_next=len(rows) > 50,
        page=page, q=q, values=data, sectors=sectors, error=error), status)


@router.get('/admin/uso-interno/produtos')
def products(request: Request, db: Session = Depends(get_db)):
    return product_page(request, db, admin(request, db))


@router.post('/admin/uso-interno/produtos')
async def save_product(request: Request, db: Session = Depends(get_db)):
    user = admin(request, db)
    async with request.form() as form:
        check_csrf(request, form)
        values = {key: str(form.get(key) or '') for key in ('id', 'description', 'barcode', 'sector', 'classification')}
        try:
            description = clean_text(values['description'], 'Descrição', 255)
            sector = clean_text(values['sector'], 'Setor', 100)
            barcode = values['barcode'].strip()
            key = barcode_key(barcode)
            if values['classification'] not in CLASSES: raise ValueError('Escolha Limpeza ou Insumo.')
            pid = int(values['id']) if values['id'] else 0
            product = db.get(InternalProduct, pid) if pid else InternalProduct()
            if product is None: raise HTTPException(404, 'Produto não encontrado.')
            product.description, product.sector = description, sector
            product.barcode, product.barcode_key = barcode, key
            product.classification = values['classification']
            db.add(product); db.commit()
            return redirect('/admin/uso-interno/produtos', message=f'Produto #{product.id} salvo.')
        except (ValueError, IntegrityError) as exc:
            db.rollback()
            error = str(exc) if isinstance(exc, ValueError) else 'Este código de barras já está cadastrado. Edite o produto existente.'
            return product_page(request, db, user, values, error, 400)


def withdrawal_page(request, db, user, product=None, error=None, status=200, form_key=None, quantity='1'):
    recent = db.scalars(select(InternalWithdrawal).where(InternalWithdrawal.employee_id == user.id)
                        .order_by(InternalWithdrawal.created_at.desc(), InternalWithdrawal.id.desc()).limit(10)).all()
    saved = None
    saved_id = request.query_params.get('saved', '')
    if saved_id.isdigit():
        saved = db.scalar(select(InternalWithdrawal).where(InternalWithdrawal.id == int(saved_id), InternalWithdrawal.employee_id == user.id))
    return render(request, 'internal_withdraw.html', user, dict(product=product, recent=recent,
        saved=saved, error=error, quantity=quantity, barcode=request.query_params.get('barcode', ''),
        request_key=form_key or secrets.token_hex(32)), status)


@router.get('/uso-interno')
def withdrawals(request: Request, barcode: str = '', db: Session = Depends(get_db)):
    user = operator(request, db)
    product, error = None, None
    if barcode.strip():
        try:
            product = db.scalar(select(InternalProduct).where(InternalProduct.barcode_key == barcode_key(barcode)))
            if not product: error = 'Produto não cadastrado para uso interno. Peça ao administrador para cadastrá-lo.'
        except ValueError as exc: error = str(exc)
    return withdrawal_page(request, db, user, product, error)


@router.post('/uso-interno/retiradas')
async def create_withdrawal(request: Request, db: Session = Depends(get_db)):
    user = operator(request, db)
    async with request.form() as form:
        check_csrf(request, form)
        key = str(form.get('request_key') or '')
        product = None
        try:
            if not re.fullmatch('[a-f0-9]{64}', key): raise ValueError('Atualize a página e leia o produto novamente.')
            pid = int(form.get('product_id') or 0)
            product = db.get(InternalProduct, pid)
            if not product: raise ValueError('Produto não encontrado. Leia o código novamente.')
            quantity = parse_quantity(form.get('quantity'))
            existing = db.scalar(select(InternalWithdrawal).where(InternalWithdrawal.request_key == key))
            if existing:
                if (existing.employee_id, existing.product_id, existing.quantity) != (user.id, pid, quantity):
                    raise ValueError('Este formulário já foi utilizado. Inicie uma nova retirada.')
                return redirect('/uso-interno', saved=existing.id)
            withdrawal = InternalWithdrawal(product_id=pid, employee_id=user.id, quantity=quantity,
                request_key=key, description=product.description, barcode=product.barcode,
                sector=product.sector, classification=product.classification, employee_name=user.name)
            db.add(withdrawal)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                existing = db.scalar(select(InternalWithdrawal).where(InternalWithdrawal.request_key == key))
                if existing and (existing.employee_id, existing.product_id, existing.quantity) == (user.id, pid, quantity):
                    return redirect('/uso-interno', saved=existing.id)
                raise
            return redirect('/uso-interno', saved=withdrawal.id)
        except (ValueError, InvalidOperation) as exc:
            db.rollback()
            return withdrawal_page(request, db, user, product, str(exc), 400,
                                   key if re.fullmatch('[a-f0-9]{64}', key) else None,
                                   str(form.get('quantity') or ''))


def report_data(request, db):
    today = datetime.now(tz()).date()
    start_text = request.query_params.get('start', '') or today.isoformat()
    end_text = request.query_params.get('end', '') or start_text
    sector = request.query_params.get('sector', '').strip()
    classification = request.query_params.get('classification', '')
    values = dict(start=start_text, end=end_text, sector=sector, classification=classification)
    try:
        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        if end < start: raise ValueError('A data final deve ser igual ou posterior à inicial.')
        if (end - start).days > 366: raise ValueError('Selecione um período de até 367 dias por consulta.')
        if classification and classification not in CLASSES: raise ValueError('Classificação inválida.')
        first = datetime.combine(start, time.min, tzinfo=tz()).astimezone(timezone.utc).replace(tzinfo=None)
        last = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz()).astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError) as exc:
        raise ValueError('Confira as datas e os filtros. ' + str(exc)) from exc
    query = select(InternalWithdrawal).where(InternalWithdrawal.created_at >= first, InternalWithdrawal.created_at < last)
    if sector: query = query.where(InternalWithdrawal.sector == sector)
    if classification: query = query.where(InternalWithdrawal.classification == classification)
    rows = db.scalars(query.order_by(InternalWithdrawal.created_at, InternalWithdrawal.id)).all()
    grouped = defaultdict(lambda: Decimal('0'))
    valid = []
    for row in rows:
        if row.cancelled_at: continue
        valid.append(row)
        key = (local_time(row.created_at).date(), row.product_id, row.description, row.barcode, row.sector, row.classification)
        grouped[key] += row.quantity
    summary = [dict(day=k[0], product_id=k[1], description=k[2], barcode=k[3], sector=k[4], classification=k[5], quantity=v)
               for k, v in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][2], item[0][1]))]
    return dict(filters=values, summary=summary, rows=rows, withdrawal_count=len(valid),
                product_count=len({row.product_id for row in valid}), cancelled_count=len(rows) - len(valid))


@router.get('/admin/uso-interno/relatorio')
def report(request: Request, db: Session = Depends(get_db)):
    user = admin(request, db)
    sectors = db.scalars(select(InternalWithdrawal.sector).distinct().order_by(InternalWithdrawal.sector)).all()
    try:
        data = report_data(request, db)
    except ValueError as exc:
        return render(request, 'internal_report.html', user, dict(error=str(exc), summary=[], rows=[], sectors=sectors,
            filters={key: request.query_params.get(key, '') for key in ('start', 'end', 'sector', 'classification')},
            withdrawal_count=0, product_count=0, cancelled_count=0, page=1, pages=1, query=''), 400)
    page_value = request.query_params.get('page', '1')
    page = max(1, int(page_value)) if page_value.isdigit() else 1
    pages = max(1, (len(data['rows']) + 49) // 50)
    page = min(page, pages)
    data['rows'] = list(reversed(data['rows']))[(page - 1) * 50:page * 50]
    return render(request, 'internal_report.html', user, dict(**data, sectors=sectors, page=page, pages=pages,
                  query=urlencode(data['filters'])))


def csv_text(value):
    text_value = str(value)
    return "'" + text_value if text_value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text_value


@router.get('/admin/uso-interno/relatorio.csv')
def report_csv(request: Request, db: Session = Depends(get_db)):
    admin(request, db)
    try: data = report_data(request, db)
    except ValueError as exc: raise HTTPException(400, str(exc))
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Data', 'ID do produto', 'Descrição', 'Código de barras (texto)', 'Setor', 'Classificação', 'Quantidade'])
    for row in data['summary']:
        writer.writerow([row['day'].strftime('%d/%m/%Y'), row['product_id'], csv_text(row['description']),
                         "'" + row['barcode'], csv_text(row['sector']), row['classification'], qty_text(row['quantity'])])
    return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="uso_interno_{data["filters"]["start"]}_{data["filters"]["end"]}.csv"',
        'Cache-Control': 'no-store'})


@router.post('/admin/uso-interno/retiradas/{withdrawal_id}/cancelar')
async def cancel(withdrawal_id: int, request: Request, db: Session = Depends(get_db)):
    user = admin(request, db)
    async with request.form() as form:
        check_csrf(request, form)
        row = db.scalar(select(InternalWithdrawal).where(InternalWithdrawal.id == withdrawal_id).with_for_update())
        if not row: raise HTTPException(404, 'Retirada não encontrada.')
        try: reason = clean_text(form.get('reason'), 'Motivo do cancelamento', 255)
        except ValueError as exc: return redirect('/admin/uso-interno/relatorio', message=str(exc))
        if not row.cancelled_at:
            row.cancelled_at, row.cancelled_by_id, row.cancellation_reason = utcnow(), user.id, reason
            db.commit()
        return redirect('/admin/uso-interno/relatorio', start=str(form.get('start') or ''), end=str(form.get('end') or ''),
                        sector=str(form.get('sector') or ''), classification=str(form.get('classification') or ''),
                        message=f'Retirada #{row.id} cancelada. O histórico foi preservado.')


def employee_page(request, db, user, values=None, error=None, status=200):
    editing = None
    if values is None and request.query_params.get('edit', '').isdigit():
        editing = db.get(User, int(request.query_params['edit']))
        if not editing or editing.role != 'employee': raise HTTPException(404)
    data = values or (dict(id=editing.id, name=editing.name, email=editing.email, active=editing.active) if editing else dict(active=True))
    employees = db.scalars(select(User).where(User.role == 'employee').order_by(User.name)).all()
    return render(request, 'internal_employees.html', user, dict(employees=employees, values=data, error=error), status)


@router.get('/admin/funcionarios')
def employees(request: Request, db: Session = Depends(get_db)):
    return employee_page(request, db, admin(request, db))


@router.post('/admin/funcionarios')
async def save_employee(request: Request, db: Session = Depends(get_db)):
    user = admin(request, db)
    async with request.form() as form:
        check_csrf(request, form)
        values = {key: str(form.get(key) or '') for key in ('id', 'name', 'email')}
        values['active'] = form.get('active') == '1'
        try:
            name = clean_text(values['name'], 'Nome', 120)
            email = clean_text(values['email'], 'E-mail', 180).lower()
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email): raise ValueError('Informe um e-mail válido.')
            uid = int(values['id']) if values['id'] else 0
            employee = db.get(User, uid) if uid else User(role='employee')
            if not employee or employee.role != 'employee': raise HTTPException(404)
            password = str(form.get('password') or '')
            if (not uid or password) and not 8 <= len(password) <= 128:
                raise ValueError('A senha provisória deve ter entre 8 e 128 caracteres.')
            employee.name, employee.email, employee.active = name, email, values['active']
            if password:
                employee.password_hash = hash_password(password)
                employee.must_change_password = True
                if uid:
                    for token in db.scalars(select(PasswordResetToken).where(PasswordResetToken.user_id == uid, PasswordResetToken.used_at.is_(None))):
                        token.used_at = utcnow()
            db.add(employee); db.commit()
            return redirect('/admin/funcionarios', message='Funcionário salvo. Uma senha provisória exige troca no próximo acesso.' if password else 'Funcionário salvo.')
        except (ValueError, IntegrityError) as exc:
            db.rollback()
            return employee_page(request, db, user, values,
                str(exc) if isinstance(exc, ValueError) else 'Este e-mail já está cadastrado no sistema.', 400)


@router.get('/uso-interno/alterar-senha')
def password_page(request: Request, db: Session = Depends(get_db)):
    user = operator(request, db, allow_password_change=True)
    if user.role != 'employee': raise HTTPException(403)
    return render(request, 'internal_password.html', user)


@router.post('/uso-interno/alterar-senha')
async def password_change(request: Request, db: Session = Depends(get_db)):
    user = operator(request, db, allow_password_change=True)
    if user.role != 'employee': raise HTTPException(403)
    async with request.form() as form:
        check_csrf(request, form)
        try:
            if not verify_password(str(form.get('current_password') or ''), user.password_hash):
                raise ValueError('A senha atual/provisória está incorreta.')
            password = str(form.get('password') or '')
            if not 8 <= len(password) <= 128: raise ValueError('A nova senha deve ter entre 8 e 128 caracteres.')
            if password != form.get('password_confirm'): raise ValueError('As senhas novas não são iguais.')
            if verify_password(password, user.password_hash): raise ValueError('Escolha uma senha diferente da atual/provisória.')
            user.password_hash, user.must_change_password = hash_password(password), False
            db.commit()
            request.session.clear(); request.session.update(user_id=user.id, role=user.role)
            return redirect('/uso-interno', message='Senha alterada. Você já pode registrar retiradas.')
        except ValueError as exc:
            return render(request, 'internal_password.html', user, dict(error=str(exc)), 400)
