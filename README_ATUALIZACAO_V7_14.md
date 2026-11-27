# V7.14 — Cadastro de produtos via PDF

Atualização da V7.13. Acesse Produtos > Cadastro de produtos e selecione o PDF original do Relatório de Produtos Detalhado. O suporte a XLS/XLSX continua disponível. A leitura de PDF utiliza a dependência PyMuPDF já instalada na V7.13.

- Todas as nove colunas do relatório são gravadas: código interno, descrição, preço de venda, ICMS, custo real do PDF, margem tradicional, situação, código de barras e NCM.
- Descrições que começam com duas ou mais letras Z (sem distinção de caixa) são ignoradas, sem apagar registros históricos ou cadastros existentes. A regra também vale para Excel.
- A identidade dos produtos do PDF é o código interno. Código de barras compartilhado exige seleção na busca da baixa; código zero ou ausente é preservado como informação de origem e o produto é buscado por código interno ou descrição. Identificadores técnicos PDF:... são usados somente internamente quando necessários.
- Produtos existentes recebem os dados do PDF quando identificados sem conflito. O custo operacional existente só muda se a caixa de atualização de custo estiver marcada. O custo real do relatório é gravado separadamente. Categorias de baixa, unidades já cadastradas e custos das baixas históricas são preservados. Novos produtos usam KG se a descrição termina em KG, UN nos demais casos; confira a unidade. Categoria de baixa não está no PDF e fica sem vínculo nos novos produtos.
- Inativos não iniciados por ZZ também são importados com a situação original; são sinalizados na busca. Produtos de custo zero precisam de revisão antes de fechar as baixas.
- Conflitos com cadastros existentes não são sobrescritos; a tela informa a quantidade e os primeiros 30 casos. PDFs de formato desconhecido, protegidos, digitalizados ou com linhas não reconhecidas são rejeitados.
- Teste local no PDF de 414 páginas: 12.385 registros, 5.403 excluídos por ZZ, 6.982 elegíveis; 79 sem barras válidas, 10 compartilhando cinco códigos, 52 com custo zero, 19 inativos elegíveis. Reimportação em SQLite: zero produtos novos. Teste de migração de V7.13 com produto existente, pesquisa por código compartilhado e tela administrativa realizados localmente. Não foi testado no PostgreSQL de produção.

## Instalação
Faça backup do PostgreSQL. Extraia a atualização em pasta separada, envie a pasta app e o README para a raiz do repositório GitHub, preservando os demais arquivos. Aguarde o deploy bem-sucedido do Railway. As novas colunas opcionais são criadas no startup, sem excluir registros. Não é necessário alterar requirements.txt. O PDF de produtos não acompanha os ZIPs; importe o original pela tela.
