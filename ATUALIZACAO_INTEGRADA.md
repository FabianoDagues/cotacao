# Cotação, fluxo de caixa e uso interno

Esta versão reúne o fluxo de caixa do arquivo enviado e as funcionalidades mais recentes desta conversa.

Inclui contas financeiras, grupos, lançamentos, transferências, importação bancária, impressão, catálogo de cotação, importação unificada, funcionários, retiradas, quantidade com botões, cadastro de destinos e relatório por destino.

## Atualizar
1. Extraia este ZIP.
2. Atualize os arquivos na pasta do repositório usada pelo serviço Railway. O Dockerfile, requirements.txt e a pasta app devem ficar juntos nessa pasta.
3. Publique a atualização usando o mesmo banco e as variáveis existentes do serviço.
4. Confira os menus Fluxo de caixa, Produtos, Importar produtos e Uso interno.

As tabelas adicionais são criadas na inicialização. O arquivo .env.example serve como referência. As variáveis FINANCE_OWN_NAME e FINANCE_OWN_TAX_ID são utilizadas para reconhecer transferências entre contas próprias.

## Verificação
Testes locais das rotas financeiras, criação de lançamento, processamento e repetição de importação com dados simulados, permissões e atualização de banco SQLite criado pelos modelos do arquivo enviado. Testes existentes de cotação, recuperação de senha, uso interno e interface aprovados.

A publicação e a verificação no PostgreSQL do Railway dependem da instalação no serviço. Este pacote contém o código-fonte. Os dados operacionais ficam no banco configurado no serviço.
