"""Leitores dos layouts PDF Itaú, Sicoob, C6 e Nubank."""
import io
import re
from datetime import date, datetime
from decimal import Decimal
from pypdf import PdfReader

MONEY = r'-?\s*(?:R\$\s*)?\d[\d.]*,\d{2}'
MONTHS = {name: i for i, name in enumerate(('JAN','FEV','MAR','ABR','MAI','JUN','JUL','AGO','SET','OUT','NOV','DEZ'), 1)}


def money(value):
    return Decimal(re.sub(r'[^\d,.-]', '', value).replace('.', '').replace(',', '.'))


def item(day, description, amount, counterparty=None, document=None):
    return dict(date=day, description=description[:255], signed_amount=amount, balance=None,
                counterparty=counterparty, document=document, tax_id=None, raw=description)


def short_date(value, start, end):
    day, month = map(int, value.split('/'))
    candidates = [date(year, month, day) for year in range(start.year-1, end.year+1)]
    return min(candidates, key=lambda d: min(abs((d-start).days), abs((d-end).days)))


def parse_pdf(content, password=''):
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(password):
            raise ValueError('Senha do PDF incorreta. Informe a senha de abertura do extrato.')
        text = '\n'.join(p.extract_text(extraction_mode='layout') or '' for p in reader.pages).replace('\xa0',' ')
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Não foi possível ler o PDF. Confira o arquivo e a senha.') from exc
    if not text.strip():
        raise ValueError('PDF sem texto legível. Envie o extrato digital emitido pelo banco.')
    if 'Nu Pagamentos' in text and 'Movimentações' in text:
        profile = 'nubank'
    elif 'SISBR' in text and 'SICOOB' in text:
        profile = 'sicoob'
    elif 'C6 Bank' in text and 'da transação' in text:
        profile = 'c6'
    elif 'Lançamentos do período:' in text and 'CNPJ/CPF' in text:
        profile = 'itau'
    else:
        raise ValueError('Modelo de PDF não reconhecido. Use um dos layouts de Itaú, C6, Nubank ou Sicoob homologados.')
    lines = [line.strip() for line in text.splitlines()]
    rows = []
    warnings = []
    opening = final = None
    balance_date = None
    if profile in ('itau','sicoob','c6'):
        period = re.search(r'(\d{2}/\d{2}/\d{4})\s+(?:até|a|-)\s+(\d{2}/\d{2}/\d{4})',text)
        if not period:
            raise ValueError('Período do PDF não reconhecido.')
        start,end = [datetime.strptime(x,'%d/%m/%Y').date() for x in period.groups()]
    if profile == 'itau':
        for line in lines:
            match = re.match(r'^\s*(\d{2}/\d{2}/\d{4})\s+(.+?)\s{2,}('+MONEY+r')\s*$',line)
            if not match:
                if rows and re.match(r'^(AT|DB)\d+', line):
                    rows[-1]['description'] += ' ' + line.split()[0]
                if re.match(r'^\s*\d{2}/\d{2}/\d{4}\s',line):
                    raise ValueError('Linha de lançamento Itaú não reconhecida.')
                continue
            day=datetime.strptime(match[1],'%d/%m/%Y').date()
            desc=match[2]; value=money(match[3])
            if desc.startswith('SALDO'):
                if 'ANTERIOR' in desc: opening=value
                if 'CONTA CORRENTE' in desc: final=value;balance_date=day
                continue
            fields=re.split(r'\s{2,}',desc)
            row=item(day,fields[0],value,fields[1] if len(fields)>1 else None)
            row['tax_id']=fields[2] if len(fields)>2 else None
            rows.append(row)
    elif profile == 'sicoob':
        for line in lines:
            if line.strip()=='RESUMO': break
            match=re.match(r'^\s*(\d{2}/\d{2})\s+(.+?)\s{2,}('+MONEY+r')([CD*])\s*$',line)
            if match:
                day=short_date(match[1],start,end);desc=match[2]
                value=money(match[3]) * (-1 if match[4]=='D' else 1)
                if desc.startswith('SALDO'):
                    if desc=='SALDO ANTERIOR': opening=value
                    if desc=='SALDO DO DIA': final=value;balance_date=day
                    continue
                rows.append(item(day,desc,value))
            elif re.match(r'^\s*\d{2}/\d{2}\s',line):
                raise ValueError('Linha de lançamento Sicoob não reconhecida.')
            elif rows and line.strip():
                extra=line.strip()
                if extra.startswith('DOC.:'): rows[-1]['document']=extra[5:].strip()
                elif not re.search(r'SICOOB|SISBR|DATA\s+HIST|EXTRATO|CONTA:|COOP.:|PERÍODO:|PLATAFORMA|SISTEMA DE',extra):
                    rows[-1]['counterparty']=((rows[-1]['counterparty'] or '')+' '+extra).strip()[:180]
    elif profile == 'c6':
        for line in lines:
            match=re.match(r'^\s*(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s{2,}(.+?)\s{2,}('+MONEY+r')\s*$',line)
            if match:
                day=short_date(match[1],start,end)
                amount=money(match[5])
                if match[3] in ('Saída PIX', 'Pagamento', 'Outros gastos') and amount>0: amount=-amount
                if match[3] not in ('Entrada PIX', 'Saída PIX', 'Entradas', 'Pagamento', 'Outros gastos'):
                    raise ValueError('Tipo de lançamento C6 não reconhecido.')
                rows.append(item(day,match[4],amount))
            elif re.match(r'^\s*\d{2}/\d{2}\s+\d{2}/\d{2}',line):
                raise ValueError('Linha de lançamento C6 não reconhecida.')
        totals=re.findall(r'Entradas:\s*('+MONEY+r')\s*•\s*Saídas:\s*('+MONEY+r')',text)
        if not totals: raise ValueError('Totais de conferência C6 ausentes.')
        if sum((money(a) for a,b in totals),Decimal(0)) != sum((r['signed_amount'] for r in rows if r['signed_amount']>0),Decimal(0)):
            raise ValueError('Entradas extraídas do C6 divergem do resumo. Importação cancelada.')
        if sum((money(b) for a,b in totals),Decimal(0)) != -sum((r['signed_amount'] for r in rows if r['signed_amount']<0),Decimal(0)):
            raise ValueError('Saídas extraídas do C6 divergem do resumo. Importação cancelada.')
        exported=re.search(r'Exportado em (\d{2}/\d{2}/\d{2})',text)
        if exported and datetime.strptime(exported[1],'%d/%m/%y').date()==end:
            for idx,line in enumerate(lines):
                if 'Saldo no dia da exportação' in line:
                    amounts=re.findall(MONEY,lines[idx+1])
                    if len(amounts)==2: final=money(amounts[-1]);balance_date=end
                    break
    else:
        day=None; sign=None
        header=text.split('Movimentações')[0]
        opening_match=re.search(r'Saldo inicial\s+('+MONEY+r')',header)
        final_match=re.findall(r'Saldo final do período\s+('+MONEY+r')',header)
        if opening_match: opening=money(opening_match[1])
        if final_match: final=money(final_match[-1])
        for line in lines:
            match=re.match(r'^\s*(\d{2}) ([A-Z]{3}) (\d{4})\s+',line)
            if match: day=date(int(match[3]),MONTHS[match[2]],int(match[1]))
            if 'Total de entradas' in line: sign=1;continue
            if 'Total de saídas' in line: sign=-1;continue
            if not day: continue
            if 'Saldo do dia' in line: sign=None;continue
            if not re.search(r'\d,\d{2}$', line): continue
            match=re.match(r'^(.+?)\s{2,}(.+?)\s{2,}('+MONEY+r')$',line)
            if match and sign is not None:
                rows.append(item(day,match[1],money(match[3])*sign,match[2][:180]))
        balance_date=max((r['date'] for r in rows),default=None)
        for label,positive in [('entradas',True),('saídas',False)]:
            expected=re.search(r'Total de '+label+r'\s+[+-]?\s*('+MONEY+r')',header)
            actual=sum((abs(r['signed_amount']) for r in rows if (r['signed_amount']>0)==positive),Decimal(0))
            if not expected or abs(money(expected[1]))!=actual:
                raise ValueError('Totais extraídos do Nubank divergem do resumo.')
    if not rows: raise ValueError('Nenhuma movimentação reconhecida no PDF.')
    net=sum((r['signed_amount'] for r in rows),Decimal(0))
    if opening is not None and final is not None and opening+net != final:
        if abs(opening + net - final) > Decimal('0.01'):
            raise ValueError('Saldo inicial e movimentações divergem do saldo final do PDF. Confira o período.')
        warnings.append(f'Saldo calculado {opening + net:.2f} difere do saldo informado {final:.2f}. Confira a diferença antes da conciliação.')
    return dict(profile=profile,rows=rows,opening=opening,final=final,balance_date=balance_date,warnings=warnings)
