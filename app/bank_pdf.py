"""Strict text-only bank statement readers. No OCR, no execution of PDF content."""
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal
from collections import defaultdict, Counter

M = r'\d[\d.]*,\d{2}'

def norm(s):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',s) if not unicodedata.combining(c)).lower().split())

def money(s):
    return Decimal(s.replace('R$','').replace(' ','').replace('.','').replace(',','.'))

def full_date(s):
    return datetime.strptime(s, '%d/%m/%Y').date()

def short_date(s,start,end):
    for y in range(start.year-1,end.year+1):
        try: d=full_date(f'{s}/{y}')
        except ValueError: continue
        if start-timedelta(days=1)<=d<=end: return d
    raise ValueError('Data fora do período do extrato PDF.')

def row(d,desc,amount,party=None,doc=None,tax=None):
    if not desc or not amount or len(desc)>255 or len(party or '')>180:
        raise ValueError('Linha de lançamento PDF inválida ou incompleta.')
    return dict(date=d,description=desc,signed_amount=amount,balance=None,document=doc,counterparty=party,tax_id=tax,raw=['PDF',str(d),desc,str(amount),party,doc,tax])

def verify(rows,balances,start,end,opening=None):
    if not rows or not balances or any(not start<=r['date']<=end for r in rows):
        raise ValueError('PDF sem lançamentos ou saldos válidos para o período.')
    by=defaultdict(Decimal)
    for r in rows: by[r.get('accounting_date',r['date'])]+=r['signed_amount']
    last=max(balances)
    if last<max(by): raise ValueError('PDF sem saldo final após os lançamentos.')
    running=opening if opening is not None else balances[last]-sum(by.values())
    initial=running
    for d in sorted(set(by)|set(balances)):
        running+=by[d]
        if d in balances and running!=balances[d]:
            raise ValueError(f'PDF não confere com o saldo bancário em {d:%d/%m/%Y}. Nenhum lançamento gravado.')
    for d,b in balances.items():
        items=[r for r in rows if r['date']==d]
        if items: items[-1]['balance']=b
    return initial,balances[last],last

def sicoob(text):
    p=re.search(r'PERÍODO:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})',text,re.I)
    a=re.search(r'CONTA:\s*([\d.\-]+)',text,re.I)
    if not p or not a: raise ValueError('Cabeçalho Sicoob incompleto.')
    start,end=map(full_date,p.groups()); rows=[]; balances={}; opening=None; current=None
    def finish():
        if current is None: return
        d,desc,amount,details=current
        party=[]; doc=None; tax=None
        for s in details:
            if s.startswith('DOC.:'): doc=s.split(':',1)[1].strip()
            elif re.match(r'^(?:CODIGO TED|CÓDIGO TED):',s): continue
            elif re.fullmatch(r'[\d.*/ -]{8,}',s): tax=s
            elif norm(s) not in ('recebimento pix','pagamento pix','pix recebido','pix enviado'): party.append(re.sub(r'^REM\.\s*:\s*','',s))
        rows.append(row(d,desc,amount,' '.join(party) or None,doc,tax))
    body=text.split('HISTÓRICO DE MOVIMENTAÇÃO',1)[-1].split('RESUMO',1)[0]
    for line in body.splitlines():
        s=line.strip()
        m=re.fullmatch(r'(\d{2}/\d{2})\s+(.+?)\s+('+M+r')([CD*])',s)
        if m:
            finish(); current=None
            ds,desc,amt,sign=m.groups(); d=short_date(ds,start,end); val=money(amt)*(-1 if sign=='D' else 1); nd=norm(desc)
            if nd=='saldo anterior': opening=val
            elif nd.startswith('saldo bloq'): continue
            elif nd=='saldo do dia': balances[d]=val
            elif sign in 'CD': current=(d,desc,val,[])
            else: raise ValueError('Natureza de lançamento Sicoob desconhecida.')
        elif re.match(r'^\d{2}/\d{2}\s',s): raise ValueError('Linha Sicoob não reconhecida.')
        elif current and s and not s.startswith('DATA '): current[3].append(s)
    finish()
    if opening is None: raise ValueError('Saldo anterior Sicoob não encontrado.')
    return rows,a.group(1),verify(rows,balances,start,end,opening),start,end

def c6(text):
    p=re.search(r'\(\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})\s*\)',text)
    a=re.search(r'Conta:\s*([\d.\-]+)',text); totals=re.search(r'Entradas:\s*R\$\s*('+M+r').*?Saídas:\s*R\$\s*('+M+r')',text)
    if not p or not a or not totals: raise ValueError('Cabeçalho/totais C6 incompletos.')
    start,end=map(full_date,p.groups()); rows=[]; balances={}
    for line in text.splitlines():
        s=line.strip()
        m=re.fullmatch(r'(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(Entrada PIX|Entradas|Saída PIX|Saídas|Devolução PIX|Pagamento(?: de boleto)?)\s+(.+?)\s+(-?R\$\s*-?'+M+r')',s,re.I)
        if m:
            ds,accounting,kind,desc,amt=m.groups(); val=money(amt); d=short_date(ds,start,end)
            accounting_date=short_date(accounting,start,end)
            if (norm(kind).startswith('entrada') and val<0) or (norm(kind).startswith('saida') and val>0): raise ValueError('Sinal C6 não confere.')
            item=row(d,desc,val); item['accounting_date']=accounting_date; rows.append(item)
        elif re.match(r'^\d{2}/\d{2}\s',s): raise ValueError('Linha C6 não reconhecida. Use XLS/XLSX.')
        else:
            b=re.fullmatch(r'Saldo(?: contábil)? do dia\s+(\d{2}/\d{2}/\d{2,4})\s+(-?R\$\s*-?'+M+r')',s,re.I)
            if b: balances[datetime.strptime(b[1],'%d/%m/%y' if len(b[1])==8 else '%d/%m/%Y').date()]=money(b[2])
    if sum(r['signed_amount'] for r in rows if r['signed_amount']>0)!=money(totals[1]) or -sum(r['signed_amount'] for r in rows if r['signed_amount']<0)!=money(totals[2]): raise ValueError('Entradas/saídas C6 não conferem com o extrato.')
    result=verify(rows,balances,start,end)
    export_date=re.search(r'Exportado em (\d{2}/\d{2}/\d{2,4})',text)
    export_balance=re.search(r'Saldo no dia da exportação[^\n]*\n[^\n]*?R\$\s*'+M+r'\s+(-?R\$\s*-?'+M+r')',text)
    if export_date and export_balance:
        ds=export_date[1]; exported=datetime.strptime(ds,'%d/%m/%y' if len(ds)==8 else '%d/%m/%Y').date()
        if exported==result[2] and money(export_balance[1])!=result[1]:
            raise ValueError('Saldo C6 do dia da exportação não confere com o saldo contábil.')
    return rows,a.group(1),result,start,end

def itau(doc,text):
    p=re.search(r'Lançamentos do período:\s*(\d{2}/\d{2}/\d{4})\s+até\s+(\d{2}/\d{2}/\d{4})',text)
    a=re.search(r'Agência\s+\d+\s+Conta\s+([\d.\-]+)',text)
    if not p or not a: raise ValueError('Cabeçalho Itaú incompleto.')
    start,end=map(full_date,p.groups()); rows=[]; balances={}; opening=None; carry=[]
    for page in doc:
        # coordinates relative to original A4 model; date anchors center each printed row
        words=page.get_text('words'); scale=page.rect.width/595.2
        words=[(w[0]/scale,w[1]/scale,w[4]) for w in words]
        anchors=sorted([(y,t) for x,y,t in words if x<85 and re.fullmatch(r'\d{2}/\d{2}/\d{4}',t)])
        anchors=[(y,t) for y,t in anchors if any(450<=x<570 and abs(yy-y)<2 and re.fullmatch(r'-?'+M,v) for x,yy,v in words)]
        if not anchors: raise ValueError('Página Itaú sem lançamentos reconhecíveis.')
        buckets=[[] for _ in anchors]; trailing=[]
        for x,y,t in words:
            if not 85<=x<570: continue
            j=min(range(len(anchors)),key=lambda i:abs(anchors[i][0]-y))
            if abs(anchors[j][0]-y)<=6.3: buckets[j].append((x,y,t))
            elif 225<=x<362 and 6.3<y-anchors[-1][0]<=14: trailing.append((x,y,t))
        for i,((y,ds),bucket) in enumerate(zip(anchors,buckets)):
            def field(lo,hi): return ' '.join(t for x,yy,t in sorted(bucket,key=lambda w:(round(w[1],1),w[0])) if lo<=x<hi)
            desc=field(85,225); party=field(225,362); tax=field(362,450); amt=field(450,515); bal=field(515,570)
            if i==0 and carry: party=' '.join(t for x,yy,t in sorted(carry,key=lambda w:(w[1],w[0])))+' '+party
            d=full_date(ds); nd=norm(desc)
            if nd.startswith('saldo'):
                if amt or not re.fullmatch(r'-?'+M,bal): raise ValueError('Saldo Itaú não reconhecido.')
                if nd=='saldo anterior': opening=money(bal)
                elif nd in ('saldo total disponivel dia','saldo em conta corrente'): balances[d]=money(bal)
                else: raise ValueError('Tipo de saldo Itaú desconhecido.')
            else:
                if bal or not re.fullmatch(r'-?'+M,amt): raise ValueError('Valor Itaú não reconhecido.')
                rows.append(row(d,desc,money(amt),party.strip() or None,None,tax or None))
        carry=trailing
    if opening is None: raise ValueError('Saldo anterior Itaú não encontrado.')
    return rows,a.group(1),verify(rows,balances,start,end,opening),start,end

def read_pdf(content,password=''):
    import pymupdf as fitz
    if len(content)>12*1024*1024 or not content.startswith(b'%PDF-'): raise ValueError('PDF inválido ou superior a 12 MB.')
    try:
        with fitz.open(stream=content,filetype='pdf') as doc:
            if doc.needs_pass and not doc.authenticate(password or ''): raise ValueError('PDF protegido: informe a senha correta.')
            if not 1<=doc.page_count<=100: raise ValueError('PDF deve ter entre 1 e 100 páginas.')
            texts=[]; readable_pages=[]
            for page in doc:
                t=page.get_text(sort=True)
                if not t.strip():
                    # Only skip genuinely blank pages: no embedded images, annotations,
                    # visible marks or non-white drawing paths. Never skip a scan.
                    blank=(page.rect.width<=1200 and page.rect.height<=1600
                        and not page.get_image_info() and not list(page.annots() or [])
                        and not list(page.widgets() or [])
                        and all(d.get('fill')==(1.0,1.0,1.0) and d.get('color') is None for d in page.get_drawings()))
                    if blank:
                        pix=page.get_pixmap(matrix=fitz.Matrix(2,2),colorspace=fitz.csGRAY,alpha=False)
                        blank=min(pix.samples)==255
                    if not blank: raise ValueError(f'PDF digitalizado ou conteúdo sem texto na página {page.number+1}. Use o extrato original do banco ou XLS/XLSX.')
                    continue
                texts.append(t); readable_pages.append(page)
            if not texts: raise ValueError('PDF sem páginas com texto. Use o extrato original do banco ou XLS/XLSX.')
            text='\n'.join(texts); n=norm(text)
            if len(text)>2_000_000: raise ValueError('PDF muito extenso.')
            if 'sisbr' in n and 'sicoob' in n: profile='sicoob'; result=sicoob(text)
            elif 'lancamentos do periodo' in n and 'razao social' in n and 'cnpj/cpf' in n: profile='itau'; result=itau(readable_pages,text)
            elif 'c6 bank' in n and 'data' in n and 'contabil' in n: profile='c6'; result=c6(text)
            else: raise ValueError('Modelo PDF não reconhecido. Use XLS/XLSX.')
            agency_pattern=r'COOP\.:\s*([\d.\-]+)' if profile=='sicoob' else r'Agência:?\s*([\d.\-]+)'
            agency=re.search(agency_pattern,text,re.I)
            if not agency: raise ValueError('Agência/cooperativa não identificada no PDF.')
            agency=agency.group(1)
    except ValueError: raise
    except Exception: raise ValueError('Não foi possível ler o PDF com segurança. Confira o arquivo ou use XLS/XLSX.') from None
    rows,account,(opening,closing,last),start,end=result
    return dict(profile=profile,rows=rows,account=account,agency=agency,opening=opening,closing=closing,last=last,start=start,end=end)
