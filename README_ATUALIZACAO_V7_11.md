# V7.11 — Conferência de extratos sobrepostos C6

Base: sistema completo V7.10. Altera somente `app/finance.py` no código de produção.

- PIX de mesma descrição e valor em datas distintas não são duplicatas só por proximidade. Para PDF/Excel, verifica a data contábil conhecida antes de bloquear por diferença de datas.
- Prioriza correspondências exatas antes das compatíveis; mantém a contagem de ocorrências legítimas no mesmo dia e preserva chaves anteriores.
- Divergências reais, movimentos anteriores ausentes e possíveis duplicatas manuais continuam bloqueando toda a importação. O aviso informa datas, valores e motivos (até cinco divergências).
- A validação de banco, conta, senha, arquivo repetido, saldo do PDF, classificação aprendida e conciliação permanece. Nenhuma alteração de banco de dados.

## Instalação
1. Faça backup do banco de produção.
2. Extraia a atualização em uma pasta nova e substitua `app/finance.py` no GitHub, no caminho existente.
3. Aguarde o deploy e abra Importar extrato. Use Conferir sem gravar antes de Importar.
4. Se ainda bloquear, envie a nova mensagem e os dois extratos (o já importado e o que falhou) para comparação. Não exclua lançamentos para tentar forçar a importação.

## Validação
Teste local SQLite: PDF C6 de 24/11/2026, 35 movimentos; conferência sem gravar, importação, reexportação com hash diferente sem duplicar e saldo final igual ao PDF. Testes adicionais simulados: sobreposição, mesmo valor no mesmo dia, repetição em datas distintas, PDF/Excel, prioridade de correspondência exata, divergência de processamento e movimentos ausentes. Não foi possível verificar o extrato que apresentou o erro em 25/11 nem o banco do Railway. Nenhum extrato, senha de PDF ou base de teste foi incluído.
