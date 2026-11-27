"""Read the original text PDF 'Relatório de Produtos Detalhado'. No OCR."""
import bisect
import re
from collections import defaultdict, Counter
from decimal import Decimal

BOUNDARIES = (65, 455, 508, 544, 608, 651, 695, 766)
NUMBER = re.compile(r'-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2,4}')

def amount(s):
    if not NUMBER.fullmatch(s):
        raise ValueError('Valor numérico inválido no relatório: '+s[:30])
    return Decimal(s.replace('.', '').replace(',', '.'))

def read_product_pdf(raw):
    import fitz
    rows = []
    with fitz.open(stream=raw, filetype='pdf') as pdf:
        if pdf.needs_pass or not 1 <= len(pdf) <= 1500:
            raise ValueError('PDF protegido ou número de páginas fora do limite (1 a 1500).')
        for no, page in enumerate(pdf, 1):
            text = page.get_text()
            if 'Relatório de Produtos Detalhado' not in text or abs(page.rect.width-841.89)>3:
                raise ValueError(f'Página {no}: formato do relatório de produtos não reconhecido.')
            groups = defaultdict(list)
            for w in page.get_text('words'):
                groups[round(w[1],1)].append(w)
            count = 0
            for y, words in sorted(groups.items()):
                cols = [[] for _ in range(9)]
                for w in sorted(words):
                    cols[bisect.bisect_right(BOUNDARIES,w[0])].append(w[4])
                code,desc,sale,tax,cost,margin,status,barcode,ncm = [' '.join(c) for c in cols]
                is_row = code.isdigit() or status in ('Ativo','Inativo')
                if not is_row: continue
                if not (code.isdigit() and desc and len(desc)<=255 and tax and len(tax)<=40 and status in ('Ativo','Inativo') and re.fullmatch(r'\d{0,80}',barcode) and len(code)<=80 and re.fullmatch(r'\d{4}\.\d{2}\.\d{2}|\d{8}|0|',ncm)):
                    raise ValueError(f'Página {no}: linha de produto incompleta ou fora do formato (código {code}).')
                try:
                    sale,cost,margin=map(amount,(sale,cost,margin))
                    if sale<0 or cost<0 or max(abs(sale),abs(cost),abs(margin))>=Decimal('10000000000'): raise ValueError('Valor fora dos limites.')
                except ValueError as exc:
                    raise ValueError(f'Página {no}, produto {code}: {exc}') from exc
                rows.append(dict(code=code,description=desc,sale_price=sale,icms=tax,unit_cost=cost,traditional_margin=margin,source_status=status,source_barcode=barcode,ncm=ncm))
                count+=1
            # A última página pode conter somente o total geral do relatório.
            if not count and not (no==len(pdf) and 'Total Geral' in text):
                raise ValueError(f'Página {no}: nenhuma linha de produto reconhecida.')
            if count and not all(h in text for h in ('CUSTO REAL','ICMS','Situação','Barras')):
                raise ValueError(f'Página {no}: cabeçalho não reconhecido.')
            if len(rows)>100000: raise ValueError('Limite de 100 mil produtos.')
    if not rows: raise ValueError('Relatório sem produtos.')
    excluded=sum(r['description'].lstrip().casefold().startswith('zz') for r in rows)
    rows=[r for r in rows if not r['description'].lstrip().casefold().startswith('zz')]
    if len({r['code'] for r in rows})!=len(rows): raise ValueError('Código interno repetido no PDF. Confira o relatório.')
    return rows, excluded


def import_product_pdf(db, raw, update_cost=False):
    from sqlalchemy import select
    from .models import CatalogProduct, ProductBarcode
    rows, excluded = read_product_pdf(raw)
    bar_counts=Counter(r['source_barcode'] for r in rows if r['source_barcode'] and int(r['source_barcode']))
    existing={p.barcode:p for p in db.scalars(select(CatalogProduct))}
    by_code=defaultdict(list)
    for p in existing.values():
        if p.code: by_code[p.code].append(p)
    aliases={p.barcode:p for p in db.scalars(select(ProductBarcode))}
    result=dict(added=0,linked=0,skipped=0,updated=0,categorized=0,excluded=excluded,metadata=0,conflicts=0,conflict_details=[],ambiguous=sum(v for v in bar_counts.values() if v>1),missing_barcode=0,zero_cost=0,inactive=0,total=len(rows)+excluded,pdf=True)
    def conflict(r, reason):
        result['conflicts']+=1
        if len(result['conflict_details'])<30: result['conflict_details'].append(f"{r['code']} — {r['description']}: {reason}")
    for r in rows:
        code, bar, desc=r['code'],r['source_barcode'],r['description']
        usable=bool(bar and int(bar))
        result['missing_barcode']+=not usable
        result['zero_cost']+=r['unit_cost']==0
        result['inactive']+=r['source_status']=='Inativo'
        matches=by_code.get(code,[])
        if len(matches)>1:
            conflict(r,'mais de um cadastro para o código interno');continue
        p=matches[0] if matches else None
        direct=existing.get(bar) if usable else None
        # A identidade primária do relatório é o código interno, e não o EAN compartilhado.
        if not p and direct and (not direct.code or direct.code==code):
            if not direct.code and (bar_counts[bar]>1 or direct.description.strip().casefold()!=desc.strip().casefold()):
                conflict(r,'código de barras existente sem código interno confirmado');continue
            p=direct
        if p and p.code and p.code!=code:
            conflict(r,'identidade interna divergente');continue
        if usable and bar_counts[bar]==1 and direct and direct is not p:
            conflict(r,'código de barras já vinculado a outro produto');continue
        if usable and bar in aliases and aliases[bar].code and aliases[bar].code!=code and bar_counts[bar]==1:
            conflict(r,'vínculo de barras existente com outro código interno');continue
        if p:
            old_bar=p.source_barcode if p.source_barcode is not None else (p.barcode if p.barcode.isdigit() else '')
            if old_bar and int(old_bar) and usable and old_bar!=bar:
                conflict(r,'código de barras mudou; revise o vínculo');continue
            if not p.code: p.code=code;by_code[code].append(p)
            if update_cost and p.unit_cost!=r['unit_cost']:
                p.unit_cost=r['unit_cost'];result['updated']+=1
            else: result['skipped']+=1
            result['metadata']+=1
        else:
            key=bar if usable and bar_counts[bar]==1 and not direct else 'PDF:'+code
            if key in existing:
                conflict(r,'identificador interno ocupado');continue
            unit='KG' if re.search(r'\bKG\s*$',desc,re.I) else 'UN'
            p=CatalogProduct(barcode=key,code=code,description=desc,unit=unit,unit_cost=r['unit_cost'])
            db.add(p);existing[key]=p;by_code[code].append(p);result['added']+=1
        # Preserve historical units/categories and write-off costs; refresh report metadata.
        p.source_real_cost=r['unit_cost']
        for field in ('description','sale_price','icms','traditional_margin','source_status','source_barcode','ncm'):
            setattr(p,field,r[field])
    db.commit()
    return result
