# Implantação no Railway

Este pacote está preparado para Railway + PostgreSQL.

## Arquitetura
- Serviço `cotacao`: aplicação FastAPI construída pelo `Dockerfile`.
- Serviço `Postgres`: banco de dados gerenciado no mesmo projeto Railway.
- HTTPS: fornecido pelo domínio público do Railway.

## Variáveis da aplicação
No serviço da aplicação, defina:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
SECRET_KEY=<uma chave longa e aleatória>
ADMIN_EMAIL=<seu e-mail de administrador>
ADMIN_PASSWORD=<uma senha forte>
ADMIN_NAME=Administrador
COOKIE_SECURE=1
APP_TIMEZONE=America/Sao_Paulo
```

Se o seu serviço PostgreSQL tiver outro nome, substitua `Postgres` pelo nome exato exibido no Railway.

## Passos no painel Railway
1. Coloque estes arquivos em um repositório GitHub.
2. No Railway, crie/abra um projeto.
3. `+ New` > `Database` > `PostgreSQL`.
4. `+ New` > `GitHub Repo` e selecione o repositório do sistema.
5. Abra o serviço da aplicação > `Variables` e crie as variáveis acima.
6. Confirme que o deploy terminou sem erros nos logs.
7. Em `Settings` > `Networking` > `Public Networking`, clique em `Generate Domain`.
8. Acesse o domínio gerado e faça login com `ADMIN_EMAIL` e `ADMIN_PASSWORD`.

## Observações
- Não habilite acesso público ao Postgres; a aplicação usa a rede interna do Railway.
- O Dockerfile usa a variável `PORT` fornecida pelo Railway e usa 8000 apenas como fallback local.
- A aplicação aceita `postgresql://`/`postgres://` do Railway e converte automaticamente para o driver `psycopg` incluído no projeto.
- A rota `/healthz` retorna `{"status": "ok"}` e pode ser usada em healthchecks.
- O administrador inicial é criado no primeiro startup apenas se o e-mail configurado ainda não existir.
