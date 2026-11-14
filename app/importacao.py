"""Atualização dos dois cadastros pela identidade estável da planilha."""
import re
from collections import Counter
from decimal import Decimal
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from fastapi import APIRouter, Depends, Request
from sqlalchemy import ForeignKey, String, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from openpyxl.utils.exceptions import InvalidFileException

from .db import Base, get_db
from .models import Product
from .internal_models import InternalProduct
from .internal_use import barcode_key, clean_text, check_csrf, csrf

router = APIRouter()


class ImportLink(Base):
    __tablename__ = 'product_import_links'
    source_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    pending_key: Mapped[str | None] = mapped_column(String(85), nullable=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id'), unique=True)
    internal_id: Mapped[int] = mapped_column(ForeignKey('internal_products.id'), unique=True)
    product: Mapped[Product] = relationship()
    internal: Mapped[InternalProduct] = relationship()


def parse_rows(raw):
    from .main import pick
    rows = []
    ids = set()
    for line, row in enumerate(raw, 2):
        if not any(str(v).strip() for v in row.values() if v is not None):
            continue
        try:
            sid = str(row.get('id') if row.get('id') is not None else '').strip()
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', sid) or sid in ids:
                raise ValueError('ID ausente, inválido ou repetido.')
            ids.add(sid)
            if 'codigo_barras' not in row:
                raise ValueError('Falta a coluna codigo_barras.')
            value = row['codigo_barras']
            bar = '' if value is None else str(value).strip()
            if re.fullmatch(r'\d+(?:[.,]\d+)?[Ee]\+12', bar):
                mantissa = re.split('[eE]', bar)[0].replace(',', '.')
                if len(mantissa.replace('.', '').lstrip('0')) < 13:
                    raise ValueError('Notação científica arredondada. Informe os 13 dígitos da embalagem.')
                number = Decimal(bar.replace(',', '.'))
                bar = format(number, 'f').partition('.')[0]
                if number != number.to_integral_value() or len(bar) != 13:
                    raise ValueError('Código científico inválido.')
            description = clean_text(pick(row, 'descricao', 'descrição', 'produto'), 'Descrição', 255)
            sector = clean_text(row['setor'], 'Setor', 100) if row.get('setor') else None
            cls = str(pick(row, 'classificacao', 'classificação') or '').strip().capitalize()
            if cls and cls not in ('Limpeza', 'Insumo'):
                raise ValueError('Classificação deve ser Limpeza ou Insumo.')
            unit = clean_text(row['unidade'], 'Unidade', 40).upper() if row.get('unidade') else None
            rows.append((sid, bar, barcode_key(bar) if bar else '', description, sector, cls, unit, line))
        except ValueError as exc:
            raise ValueError(f'Linha {line}: {exc}') from exc
    if not rows:
        raise ValueError('Planilha vazia.')
    return rows


def synchronize(db, rows):
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('LOCK TABLE product_import_links, products, internal_products IN SHARE ROW EXCLUSIVE MODE'))
    articles = db.scalars(select(Product)).all()
    by_code = {p.code: p for p in articles}
    by_gtin = {p.barcode: p for p in articles if p.barcode}
    by_key = {p.barcode_key: p for p in db.scalars(select(InternalProduct))}
    links = {link.source_id: link for link in db.scalars(select(ImportLink))}
    owners_p = {link.product_id: sid for sid, link in links.items()}
    owners_i = {link.internal_id: sid for sid, link in links.items()}
    counts = Counter(row[2] for row in rows)
    totals = Counter()
    effective_keys = set()
    for sid, bar, key, desc, sector, cls, unit, line in rows:
        link = links.get(sid)
        fallback = 'INT-REV-' + sid
        # Uma carga parcial também precisa reconhecer o código de revisão anterior.
        pending = None
        if not bar or bar == '0' or counts[key] > 1 or (link and link.pending_key == key) or (not link and 'code:'+fallback in by_key):
            pending = key
            bar = fallback
            key = barcode_key(bar)
            totals['revisao'] += 1
        if key in effective_keys:
            raise ValueError(f'Linha {line}: conflito com um código provisório. Confira o código da embalagem.')
        effective_keys.add(key)
        gtin = key[5:] if key.startswith('gtin:') else None
        code = 'IMP-' + sid
        internal = by_key.get(key)
        product = by_gtin.get(gtin) if gtin else None
        existing_code = by_code.get(code)
        if link:
            if (internal and internal.id != link.internal_id) or (product and product.id != link.product_id):
                raise ValueError(f'Linha {line}: código já vinculado a outro produto.')
            product, internal = link.product, link.internal
        else:
            if existing_code and existing_code is not product:
                raise ValueError(f'Linha {line}: o código interno {code} já está ocupado.')
            if (product and product.id in owners_p) or (internal and internal.id in owners_i):
                raise ValueError(f'Linha {line}: código já utilizado por outro ID de origem.')
        if product is None:
            product = Product(code=code, base_unit='UN', active=True)
            totals['catalogo'] += 1
        if internal is None:
            internal = InternalProduct(sector='Geral', classification='Insumo')
            totals['interno'] += 1
        if product.barcode and product.barcode != gtin:
            by_gtin.pop(product.barcode, None)
        if internal.barcode_key and internal.barcode_key != key:
            by_key.pop(internal.barcode_key, None)
        product.description = internal.description = desc
        product.barcode = gtin
        if unit: product.base_unit = unit
        if cls: internal.classification = cls
        if sector: internal.sector = sector
        internal.barcode, internal.barcode_key = bar, key
        if not link:
            link = ImportLink(source_id=sid, product=product, internal=internal)
            db.add(link)
            if product.id: owners_p[product.id] = sid
            if internal.id: owners_i[internal.id] = sid
        link.pending_key = pending
        if gtin: by_gtin[gtin] = product
        by_key[key] = internal
        by_code[product.code] = product
    db.commit()
    return f"{len(rows)} linhas sincronizadas. Novos no catálogo: {totals['catalogo']}. Novos no uso interno: {totals['interno']}. Códigos provisórios para revisão: {totals['revisao']}."


@router.api_route('/admin/importacao-produtos', methods=['GET','POST'])
async def import_page(request: Request, db: Session = Depends(get_db)):
    from .main import require_role, templates, read_product_rows
    user = require_role(request, db, 'admin')
    message = ''
    status = 200
    if request.method == 'POST':
        async with request.form() as form:
            check_csrf(request, form)
            try:
                upload = form.get('file')
                if not getattr(upload, 'filename', None):
                    raise ValueError('Selecione a planilha.')
                message = synchronize(db, parse_rows(read_product_rows(upload)))
            except (ValueError, BadZipFile, ParseError, InvalidFileException, KeyError, IntegrityError) as exc:
                db.rollback()
                status = 400
                message = (str(exc) if isinstance(exc, ValueError) else 'Arquivo inválido ou conflito entre códigos.') + ' Nenhuma alteração foi aplicada.'
    return templates.TemplateResponse(request, 'importacao.html', dict(user=user,message=message,csrf_token=csrf(request)),status_code=status)
