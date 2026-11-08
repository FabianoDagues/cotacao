# Sistema de Cotação — contexto V8 (08/11/2026)

Sistema: https://mercadosanenge-cotacao.up.railway.app
Repositório: FabianoDagues/cotacao. Serviço Railway cotacao no projeto ideal-communication.
Banco: PostgreSQL, não incluído neste pacote.

A versão V8 foi preparada nesta conversa, sem publicação nem acesso ao banco de produção. Usa o código V7.1 preparado anteriormente. O usuário aplicou manualmente em produção a coluna password_reset_tokens.created_by_user_id para corrigir a redefinição de senha em 28/10/2026.

Funcionalidades existentes: cotações, fornecedores, importação de planilhas, comparativo, preços normalizados, fechamento por fornecedor, impressão e Excel, senhas e links de redefinição; catálogo de cotação e leitor preparados na V7.

Novo módulo: cadastro InternalProduct (ID, descrição, código de barras, setor e classificação Limpeza/Insumo); retiradas InternalWithdrawal com identificação, quantidade e snapshots históricos; usuários employee; relatório por datas locais, setor e classificação, CSV, impressão e cancelamento administrativo com motivo.

Arquivos: app/internal_use.py, app/internal_models.py, templates internal_*.html, static/internal*.js e internal.css. main.py inclui o novo router, redireciona funcionários e expõe version 8.0 em /healthz. base.html mostra menu Uso interno e V8.

Instalação: o ZIP tem app e Dockerfile na raiz. Copiar para a raiz efetiva do serviço; pode haver pasta sistema_cotacao no repositório. Não colocar uma nova pasta externa na frente do app. Verificar /healthz depois do deploy. Não recriar o Postgres.

Leia LEIA-ME-V8.md para regras, testes e limitações. Preserve todas as funcionalidades anteriores em futuras alterações.
