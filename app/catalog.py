"""Catálogo independente: não altera os registros históricos de cotação."""
import csv
from pathlib import Path
from decimal import Decimal, InvalidOperation
from zipfile import BadZipFile
from openpyxl.utils.exceptions import InvalidFileException
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .db import get_db
from .models import Product, Quote, QuoteItem, QuoteSupplier, PurchaseOrder

router = APIRouter()

def barcode_value(value):
    value = str(value or '').strip()
    if not value:
        return None
    if not value.isascii() or not value.isdigit() or len(value) not in (8, 12, 13, 14):
        raise ValueError('Código de barras deve ter 8, 12, 13 ou 14 dígitos. Preserve os zeros à esquerda.')
    digits = [int(c) for c in value]
    check = (10 - sum(n * (3 if i % 2 == 0 else 1) for i, n in enumerate(reversed(digits[:-1]))) % 10) % 10
    if digits[-1] != check:
        raise ValueError('Dígito verificador do código de barras inválido. Confira a embalagem.')
    # UPC-A e EAN com zeros à esquerda identificam o mesmo GTIN.
    return value.zfill(14)

def clean_product(data):
    code = str(data.get('code') or '').strip()
    description = str(data.get('description') or '').strip()
    unit = str(data.get('base_unit') or 'UN').strip().upper()
    if not code or len(code)>80 or not description or len(description)>255 or not unit or len(unit)>40:
        raise ValueError('Preencha código (até 80), produto (até 255) e unidade (até 40 caracteres).')
    return dict(code=code, description=description, base_unit=unit, barcode=barcode_value(data.get('barcode')))

def back(quote_id=0, message=''):
    from urllib.parse import urlencode
    return RedirectResponse('/admin/produtos?' + urlencode({'quote_id':quote_id, 'message':message}),303)

@router.get('/admin/produtos')
def catalog(request: Request, q: str='', quote_id: int=0, edit: int=0, db: Session=Depends(get_db)):
    from .main import require_role, templates
    user=require_role(request, db, 'admin')
    query=select(Product)
    if q.strip():
        term='%'+q.strip().replace('%','\\%').replace('_','\\_')+'%'
        conditions=[Product.description.ilike(term, escape='\\'), Product.code.ilike(term, escape='\\')]
        try:
            barcode=barcode_value(q)
            if barcode: conditions.append(Product.barcode==barcode)
        except ValueError: pass
        query=query.where(or_(*conditions))
    products=db.scalars(query.order_by(Product.description).limit(201)).all()
    quote=db.get(Quote,quote_id) if quote_id else None
    if quote_id and not quote: raise HTTPException(404)
    return templates.TemplateResponse(request,'admin_products.html',dict(user=user,products=products[:200],more=len(products)>200,q=q,quote=quote,quote_id=quote_id,editing=db.get(Product,edit) if edit else None,message=request.query_params.get('message','')))

@router.post('/admin/produtos/salvar')
async def save(request: Request, db: Session=Depends(get_db)):
    from .main import require_role
    require_role(request, db, 'admin')
    form=await request.form(); quote_id=int(form.get('quote_id') or 0)
    try:
        data=clean_product(form)
        pid=int(form.get('id') or 0)
        product=db.get(Product,pid) if pid else Product()
        if not product: raise HTTPException(404)
        for key,value in data.items(): setattr(product,key,value)
        product.active=form.get('active')=='1'
        db.add(product); db.commit()
        return back(quote_id,'Produto salvo. Busque pelo código para incluí-lo na cotação.')
    except (ValueError,IntegrityError) as exc:
        db.rollback()
        return back(quote_id,str(exc) if isinstance(exc,ValueError) else 'Código interno ou código de barras já cadastrado. Edite o produto existente.')

@router.post('/admin/produtos/base')
def seed(request: Request, db: Session=Depends(get_db)):
    from .main import require_role
    require_role(request,db,'admin'); count=0
    with (Path(__file__).parent/'data/base_produtos.csv').open(encoding='utf-8-sig') as f:
        for row in csv.DictReader(f,delimiter=';'):
            if not db.scalar(select(Product.id).where(Product.code==row['code'])):
                db.add(Product(**clean_product(row))); count+=1
    try: db.commit()
    except IntegrityError:
        db.rollback(); return back(message='A base já foi carregada por outra sessão. Atualize a página.')
    return back(message=f'{count} produtos-base incluídos. Vincule os códigos reais das embalagens ao editar.')

@router.post('/admin/produtos/importar')
async def import_catalog(request: Request, db: Session=Depends(get_db)):
    from .main import require_role, read_product_rows, pick
    require_role(request,db,'admin'); form=await request.form()
    try:
        upload=form.get('file')
        if not upload or not upload.filename: raise ValueError('Selecione CSV ou XLSX.')
        rows=read_product_rows(upload)
        if not rows: raise ValueError('Arquivo vazio.')
        count=0
        for index,row in enumerate(rows,2):
            try:
                data=clean_product(dict(code=pick(row,'codigo','código','code'),description=pick(row,'produto','descricao','description'),base_unit=pick(row,'unidade','base_unit'),barcode=pick(row,'codigo_barras','ean','gtin','barcode')))
            except ValueError as exc: raise ValueError(f'Linha {index}: {exc}')
            existing=db.scalar(select(Product).where(Product.code==data['code']))
            if existing: continue # nunca sobrescrever catálogo existente silenciosamente
            db.add(Product(**data)); db.flush(); count+=1
        db.commit(); return back(message=f'{count} produtos importados. Códigos internos existentes foram mantidos.')
    except (ValueError,IntegrityError,InvalidOperation,BadZipFile,InvalidFileException) as exc:
        db.rollback(); return back(message=str(exc) if isinstance(exc,ValueError) else 'Importação cancelada: código de barras duplicado ou valor inválido. Nenhum produto foi importado.')

    finally:
        await form.close()

@router.post('/admin/cotacoes/{quote_id}/produtos')
async def add_product(quote_id: int, request: Request, db: Session=Depends(get_db)):
    from .main import require_role, parse_decimal, now_local
    require_role(request,db,'admin'); form=await request.form()
    quote=db.scalar(select(Quote).where(Quote.id==quote_id).with_for_update())
    if not quote: raise HTTPException(404)
    try:
        if quote.deadline < now_local(): raise ValueError('Reabra o prazo antes de incluir produtos.')
        if db.scalar(select(PurchaseOrder.id).where(PurchaseOrder.quote_id==quote_id)):
            raise ValueError('Esta cotação já possui pedido. Crie uma nova cotação para acrescentar produtos.')
        product=db.get(Product,int(form.get('product_id') or 0))
        if not product or not product.active: raise ValueError('Selecione um produto ativo.')
        qty=parse_decimal(form.get('quantity'))
        if not qty.is_finite() or qty<=0 or qty>=Decimal('10000000000') or qty.as_tuple().exponent < -4:
            raise ValueError('Quantidade deve ser positiva, menor que 10 bilhões e ter até 4 casas decimais.')
        if db.scalar(select(QuoteItem.id).where(QuoteItem.quote_id==quote_id,QuoteItem.code==product.code)):
            raise ValueError('Este produto já está na cotação. A leitura repetida não altera a quantidade.')
        db.add(QuoteItem(quote_id=quote_id,code=product.code,description=product.description,base_unit=product.base_unit,requested_qty=qty))
        for link in db.scalars(select(QuoteSupplier).where(QuoteSupplier.quote_id==quote_id)):
            link.submitted_at=None
        db.commit(); return back(quote_id,f'{product.description}: {qty} {product.base_unit} incluído. Fornecedores devem revisar e reenviar a cotação.')
    except (ValueError,InvalidOperation) as error:
        db.rollback(); return back(quote_id,str(error) if isinstance(error,ValueError) else 'Quantidade inválida.')
