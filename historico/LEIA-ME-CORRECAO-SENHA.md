# Correção de redefinição de senha — 28/10/2026

## Causa confirmada no log enviado

`psycopg.errors.UndefinedColumn: column password_reset_tokens.created_by_user_id does not exist`

O código já usava a coluna que registra o administrador que criou o link, mas a tabela antiga no PostgreSQL não a possuía. `create_all` não altera colunas de tabelas existentes. A correção acrescenta essa coluna na inicialização da aplicação, sem excluir ou recriar tabelas. Registros antigos mantêm NULL nesse campo; novos links registram o administrador.

## Aplicar somente a correção

Escolha APENAS UMA pasta:

- **correcao_v6**: se você ainda usa a versão anterior, sem o novo menu Produtos.
- **correcao_v7**: se já instalou a V7 com catálogo e leitor de código de barras.

1. No repositório GitHub `FabianoDagues/cotacao`, localize o arquivo `app/main.py` utilizado pelo Railway. Se o projeto está dentro de `sistema_cotacao`, o caminho é `sistema_cotacao/app/main.py`.
2. Substitua esse arquivo pelo `app/main.py` da pasta escolhida e faça o commit. Não envie a pasta correcao_v6/correcao_v7 para dentro do projeto: substitua o arquivo no caminho já existente.
3. Aguarde a publicação desse commit no Railway pelo fluxo já configurado. Somente reiniciar o código antigo não resolve.
4. A aplicação acrescentará automaticamente a coluna ao iniciar usando a DATABASE_URL atual. Não recrie o Postgres nem altere as credenciais.
5. Abra Fornecedores, clique em Redefinir senha e copie o NOVO link gerado. Confira a alteração e o login com a nova senha.

As duas correções alteram somente a migração de inicialização. A V6 não recebe o catálogo por esse caminho; a V7 mantém o catálogo. Os arquivos pressupõem as versões V6/V7 fornecidas nesta conversa. Se houver outras alterações em main.py, incorpore somente o bloco novo de migração, para preservá-las.

## Versão completa

`sistema_completo_v7_1` contém o código completo V7 com esta correção. É uma alternativa à aplicação incremental. Para manter apenas a V6, use a pasta correcao_v6 acima. Consulte também LEIA-ME-V7.md para o catálogo. O pacote não contém dados nem credenciais do banco online.

## Testes realizados

O teste de regressão cria uma tabela antiga sem created_by_user_id, reproduz o erro HTTP 500 antes da migração e verifica a recuperação após a migração. Confere preservação dos registros, aplicação repetida sem falhar, geração do link, invalidação de links anteriores, expiração, validação de senha, uso único, autorização de administrador e login com a nova senha. Também foram executados os testes existentes do catálogo.

Os testes locais usam SQLite temporário. O ramo PostgreSQL usa ADD COLUMN IF NOT EXISTS para a coluna nullable com referência a users(id) ON DELETE SET NULL. Não houve acesso ao banco de produção nem publicação no Railway nesta conversa.
