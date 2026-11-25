# Cotação V7.8 — correção dos PDFs C6 e Sicoob

Atualização da V7.7, sem alteração de tabelas. O importador aceita páginas realmente em branco, sem imagens, anotações ou marcas visíveis, e reconhece o novo rótulo C6 "Saldo contábil do dia". Continua sem OCR, com conferência de totais, saldos, conta/banco e duplicidade. O saldo C6 na data de exportação também é comparado quando identificável e correspondente ao último dia contabilizado. Não altera os lançamentos ou classificações já existentes.

## Instalação
Extraia o ZIP de atualização em uma pasta nova e envie o conteúdo para a raiz do repositório. Substitua `app/bank_pdf.py`, `app/finance.py`, `app/templates/bank_import.html` e `requirements.txt` (os três últimos incluem a V7.7 para compatibilidade com a V7.6). Faça o deploy no Railway. Não apague o PostgreSQL. Recomenda-se backup do banco antes da atualização.

## Conferência
Use Importar extrato, selecione a conta, o arquivo original e, se necessário, somente a senha de abertura do PDF. Clique em Conferir sem gravar; depois selecione o arquivo novamente e efetive a importação se estiver tudo certo. Extratos digitalizados ou com conteúdo sem texto continuam bloqueados. Não se trata de verificação criptográfica de autenticidade do extrato.

## Testes locais
Extratos de 24/11/2026: C6 35 movimentos (saldo inicial inferido 132,07, entradas 680,40, saídas 619,15, saldo final 193,32); Sicoob 2 movimentos (saldo inicial 0,00, entradas 158,61, saldo final 158,61). Testados pela rota real com SQLite local: prévia sem gravação, gravação, saldo calculado, arquivo repetido e identificação individual de duplicidades; senha errada e página com conteúdo gráfico sem texto rejeitadas. Regressão de leitura nos três PDFs anteriores: C6 1072, Itaú 87, Sicoob 51 movimentos. Não testado no PostgreSQL do Railway. Diferenças históricas no saldo cadastrado não são corrigidas automaticamente. Não há PDFs bancários ou senhas neste pacote.
