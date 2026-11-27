"""Fechamento gerencial de baixas: não gera movimento bancário nem de caixa."""
import calendar
import hashlib
import logging
import os
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, delete, text
from sqlalchemy.orm import Session
from .db import SessionLocal, get_db
from .models import (CashGroup, CatalogProduct, ProductWriteOff, User, WriteOffClosing,
                     WriteOffClosingLine, WriteOffClosingConfig)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent/'templates')
logger = logging.getLogger(__name__)
_stop = threading.Event()
_worker = None


def local_today():
    return datetime.now(ZoneInfo(os.getenv('APP_TIMEZONE', 'America/Sao_Paulo'))).date()


def month_end(period):
    return period.replace(day=calendar.monthrange(period.year, period.month)[1])


def parse_period(value):
    try:
        period = date.fromisoformat(value+'-01')
        if period > local_today().replace(day=1): raise ValueError()
        return period
    except (ValueError, TypeError):
        raise HTTPException(400, 'Competência inválida ou futura.')


def admin(request, db):
    uid = request.session.get('user_id')
    user = db.get(User, uid) if uid else None
    if not user or not user.active or user.role != 'admin':
        raise HTTPException(403, 'Acesso negado')
    return user


def group_options(db):
    groups = {g.id:g for g in db.scalars(select(CashGroup))}
    return sorted([(g,groups[g.parent_id]) for g in groups.values()
                   if g.active and g.nature=='expense' and g.parent_id in groups
                   and groups[g.parent_id].active and groups[g.parent_id].nature=='expense'
                   and not groups[g.parent_id].parent_id and not g.name.startswith('A classificar')],
                  key=lambda pair:(pair[1].name.casefold(),pair[0].name.casefold()))


def lock_closing(db):
    # Serialize recomputations across Railway processes. SQLite is used for local testing.
    if db.get_bind().dialect.name=='postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(71213001)'))
    elif db.get_bind().dialect.name=='sqlite':
        db.execute(text('BEGIN IMMEDIATE'))


def init_config():
    with SessionLocal() as db:
        lock_closing(db)
        if not db.get(WriteOffClosingConfig,1):
            db.add(WriteOffClosingConfig(id=1,start_period=local_today().replace(day=1)))
        db.commit()


def preview(db, period):
    options = {g.id:(g,parent) for g,parent in group_options(db)}
    rows = db.scalars(select(ProductWriteOff).where(
        ProductWriteOff.movement_date>=period, ProductWriteOff.movement_date<=month_end(period),
        ProductWriteOff.cancelled_at.is_(None)).order_by(ProductWriteOff.id)).all()
    products = {p.barcode:p for p in db.scalars(select(CatalogProduct).where(CatalogProduct.barcode.in_({r.catalog_barcode for r in rows if r.catalog_barcode})))}
    totals = defaultdict(lambda:[Decimal('0'),0]); pending=[]; detail=[]
    for r in rows:
        p=products.get(r.catalog_barcode)
        # Snapshot on new baixa; historical records may use a category assigned later in the catalogue.
        gid=r.closing_group_id if r.closing_group_id is not None else (p.closing_group_id if p else None)
        missing=[]
        if gid not in options: missing.append('Categoria ausente ou inativa')
        if r.unit_cost is None: missing.append('Custo histórico não informado')
        amount=r.quantity*r.unit_cost if r.unit_cost is not None else None
        detail.append(dict(row=r,gid=gid,amount=amount,missing=missing))
        if missing:
            pending.append(detail[-1]); continue
        totals[gid][0]+=amount; totals[gid][1]+=1
    lines=[]
    for gid,(amount,count) in sorted(totals.items()):
        g,parent=options[gid]
        lines.append(dict(group_id=gid,parent_id=parent.id,group_name=g.name,parent_name=parent.name,
                          amount=amount.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),item_count=count))
    signature=hashlib.sha256(repr([(d['row'].id,d['gid'],str(d['amount']),d['missing']) for d in detail]+lines).encode()).hexdigest()
    return lines,pending,detail,signature


def close_period(db, period, source, today=None):
    today=today or local_today()
    lock_closing(db)
    lines,pending,detail,signature=preview(db,period)
    status='pending' if pending else ('final' if month_end(period)<today else 'provisional')
    old=db.get(WriteOffClosing,period)
    if old and old.signature==signature and old.status==status:
        db.commit();return status
    if not old:
        old=WriteOffClosing(period=period);db.add(old);db.flush()
    old.status=status;old.source=source;old.updated_at=datetime.utcnow();old.signature=signature;old.pending_count=len(pending)
    # Replace, never append. If incomplete, remove stale totals instead of publishing an incomplete close.
    db.execute(delete(WriteOffClosingLine).where(WriteOffClosingLine.period==period))
    if not pending:
        # Fixar a categoria das baixas históricas após a apuração definitiva.
        if status=='final':
            for d in detail:
                if d['row'].closing_group_id is None: d['row'].closing_group_id=d['gid']
        for line in lines: db.add(WriteOffClosingLine(period=period,**line))
    db.commit()
    return status


def automatic_pass(today=None):
    today=today or local_today(); current=today.replace(day=1)
    with SessionLocal() as db:
        config=db.get(WriteOffClosingConfig,1)
        if not config:return
        start=config.start_period
        # Process all months with activity since activation, plus previously requested closures.
        days=db.scalars(select(ProductWriteOff.movement_date).where(ProductWriteOff.movement_date>=start,ProductWriteOff.movement_date<current).distinct()).all()
        periods={d.replace(day=1) for d in days}
        periods.update(db.scalars(select(WriteOffClosing.period).where(WriteOffClosing.period<current)).all())
    for period in sorted(periods):
        try:
            with SessionLocal() as db: close_period(db,period,'automatic',today)
        except Exception:
            logger.exception('Falha no fechamento automático da competência %s; será tentado novamente.',period)


def start_worker():
    global _worker
    init_config()
    if _worker and _worker.is_alive():return
    _stop.clear()
    def run():
        while not _stop.is_set():
            try: automatic_pass()
            except Exception: logger.exception('Falha no ciclo de fechamento automático')
            _stop.wait(60)
    _worker=threading.Thread(target=run,name='fechamento-baixas',daemon=True);_worker.start()


def stop_worker():
    _stop.set()
    if _worker: _worker.join(timeout=3)


def finance_pending(db,start,end,account_id=None,tipo=None):
    if account_id or tipo not in (None,'','expense'):return []
    return [c for c in db.scalars(select(WriteOffClosing).where(WriteOffClosing.status=='pending', WriteOffClosing.period>=start.replace(day=1), WriteOffClosing.period<=end).order_by(WriteOffClosing.period)) if start<=month_end(c.period)<=end]


def finance_lines(db,start,end,account_id=None,grupo=None,subgrupo=None,tipo=None):
    if account_id or tipo not in (None,'','expense'):return []
    rows=db.execute(select(WriteOffClosingLine,WriteOffClosing).join(WriteOffClosing,WriteOffClosing.period==WriteOffClosingLine.period).where(
        WriteOffClosing.status.in_(['final','provisional']),WriteOffClosingLine.period<=end,
        WriteOffClosingLine.period>=start.replace(day=1)).order_by(WriteOffClosingLine.period,WriteOffClosingLine.group_name)).all()
    result=[]
    for line,closing in rows:
        day=month_end(line.period)
        if not start<=day<=end or (subgrupo and line.group_id!=subgrupo) or (grupo and line.parent_id!=grupo):continue
        result.append(dict(date=day,period=line.period,group_name=line.group_name,parent_name=line.parent_name,amount=line.amount,item_count=line.item_count,status=closing.status))
    return result


@router.get('/admin/baixas/fechamento',response_class=HTMLResponse)
def closing_page(request:Request, mes:str='',db:Session=Depends(get_db)):
    user=admin(request,db);period=parse_period(mes or local_today().strftime('%Y-%m'))
    lines,pending,detail,signature=preview(db,period)
    saved=db.get(WriteOffClosing,period)
    history=db.scalars(select(WriteOffClosing).order_by(WriteOffClosing.period.desc()).limit(24)).all()
    config=db.get(WriteOffClosingConfig,1)
    return templates.TemplateResponse(request,'write_off_closing.html',dict(user=user,period=period,last_day=month_end(period),lines=lines,pending=pending,detail=detail,saved=saved,stale=bool(saved and saved.signature!=signature),history=history,options=group_options(db),total=sum((l['amount'] for l in lines),Decimal('0')),config=config,message=request.query_params.get('msg')))


@router.post('/admin/baixas/fechamento')
def closing_submit(request:Request,mes:str=Form(...),db:Session=Depends(get_db)):
    admin(request,db);period=parse_period(mes)
    # Authentication starts a transaction; close it before acquiring the write lock.
    db.rollback();status=close_period(db,period,'manual')
    return RedirectResponse('/admin/baixas/fechamento?mes='+mes+'&msg='+status,303)


@router.post('/admin/baixas/fechamento/classificar')
def classify_row(request:Request,mes:str=Form(...),row_id:int=Form(...),group_id:int=Form(...),unit_cost:str=Form(''),db:Session=Depends(get_db)):
    admin(request,db);period=parse_period(mes)
    row=db.get(ProductWriteOff,row_id)
    if not row or row.cancelled_at or not period<=row.movement_date<=month_end(period):raise HTTPException(404,'Baixa não encontrada na competência.')
    if group_id not in {g.id for g,p in group_options(db)}:raise HTTPException(400,'Categoria inválida.')
    row.closing_group_id=group_id
    if row.unit_cost is None and unit_cost.strip():
        try:
            cost=Decimal(unit_cost.strip().replace(',','.'))
            if not cost.is_finite() or cost<0 or cost>=Decimal('10000000000') or cost!=cost.quantize(Decimal('.0001')):raise ValueError()
        except (InvalidOperation,ValueError):raise HTTPException(400,'Custo inválido. Informe o custo histórico, sem separador de milhares.')
        row.unit_cost=cost
    db.commit()
    return RedirectResponse('/admin/baixas/fechamento?mes='+mes,303)


@router.post('/admin/produtos/categoria')
def catalog_category(request:Request,barcode:str=Form(...),group_id:str=Form(''),q:str=Form(''),db:Session=Depends(get_db)):
    from urllib.parse import urlencode
    admin(request,db);p=db.get(CatalogProduct,barcode)
    if not p:raise HTTPException(404,'Produto não encontrado.')
    if group_id:
        if not group_id.isdigit() or int(group_id) not in {g.id for g,parent in group_options(db)}:raise HTTPException(400,'Categoria inválida.')
        p.closing_group_id=int(group_id)
    else:p.closing_group_id=None
    db.commit()
    return RedirectResponse('/admin/produtos?'+urlencode({'q':q[:255]}),303)
