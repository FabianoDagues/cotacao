# Cotação V7.7 — importação de extratos PDF

Atualização sobre a V7.6. Mantém o layout, os saldos com transferências, filtros, classificação aprendida e as tabelas existentes.

Arquivos a copiar para o repositório na raiz (substituir arquivos existentes e adicionar o novo módulo):
- `requirements.txt` (obrigatório, inclui PyMuPDF)
- `app/finance.py`
- `app/bank_pdf.py` (novo)
- `app/templates/bank_import.html`

No Railway, aguarde o deploy do novo commit. Não exclua ou recrie o PostgreSQL. Antes de atualizar um sistema financeiro em produção, faça backup do banco de dados.

## Uso
1. Em Contas, confira o banco cadastrado e preencha o número da conta com dígito. Se a agência/cooperativa estiver preenchida, precisa corresponder ao extrato.
2. Em Importar extrato, selecione a conta e o PDF original do banco (C6, Itaú ou Sicoob). XLS/XLSX continuam disponíveis.
3. Informe a senha somente se o PDF estiver protegido. Não informe senha do internet banking: use apenas a senha de abertura do arquivo. A senha não é gravada pelo módulo.
4. Clique em **Conferir sem gravar**. Veja o número de movimentos, possíveis duplicados e saldos inicial/final do PDF. Se estiver correto, selecione novamente o arquivo, informe a senha se necessária e clique em Importar extrato.

## Proteções e limites
- PDF de até 12 MB e 100 páginas; leitura local no servidor, sem OCR, sem executar JavaScript do PDF e sem guardar o arquivo integral no banco. Os dados dos movimentos são registrados normalmente para auditoria. A biblioteca de leitura deve ser mantida atualizada.
- Reconhecimento pelo conteúdo, não pelo nome; exige banco e número da conta correspondentes. Conferência de saldo anterior, movimentos e saldos diários nos modelos Itaú/Sicoob; no C6 confere totais de crédito/débito e saldos por data contábil. Registra movimentos pela data de lançamento, guarda a data contábil no histórico bruto e calcula o saldo inicial pela movimentação e saldo final do extrato quando o PDF não apresenta saldo anterior. Limite de cheque especial não entra no saldo.
- Arquivo idêntico bloqueado; comparação individual um a um, inclusive ocorrências repetidas dentro do mesmo arquivo. Sobreposição PDF/planilha com dados divergentes ou ambíguos é recusada (não há como garantir identidade se o banco omite identificadores); use períodos sem sobreposição ou o formato anterior e confira manualmente. Lançamentos antigos não são apagados. Não corrige automaticamente diferenças históricas de saldo; informa divergência e preserva o saldo inicial cadastrado, salvo primeira importação sem saldo inicial.
- PDF digitalizado, modelo desconhecido, senha incorreta, valor/linha incompatível ou saldo que não fecha: rejeição sem gravar. Formatos novos dos bancos podem necessitar calibração. Não é verificação criptográfica de autenticidade do extrato: use arquivos obtidos diretamente do banco.
- Para C6 com datas contábil e lançamento diferentes, saldo diário do sistema por data de lançamento pode divergir do saldo contábil do banco, mas a conferência do PDF usa a data contábil. Períodos com movimentos contabilizados fora do intervalo podem ser recusados.

## Validação local
Testados os três PDFs originais fornecidos (51 movimentos Sicoob, 87 Itaú, 1072 C6, incluindo cinco ocorrências repetidas legítimas). Os saldos finais calculados conferiram em banco SQLite de teste; conferência de reimportação identificou os movimentos já importados. Senha incorreta e PDF inválido foram recusados. Não foi testado no PostgreSQL de produção, nem com todos os formatos possíveis dos bancos. Não inclui extratos reais ou senhas neste ZIP.
