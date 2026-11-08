# Sistema de Cotação V8

Consulte **LEIA-ME-V8.md** para instalação, uso e regras do novo módulo Uso interno.

Projeto FastAPI + SQLAlchemy + Jinja2, com PostgreSQL no Railway ou SQLite local.

## Execução local

```sh
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Configure as variáveis indicadas em `.env.example` no seu ambiente antes de iniciar. O arquivo de exemplo não é carregado automaticamente. Em produção, mantenha as variáveis já configuradas no Railway.

O ZIP não contém banco, usuários reais nem credenciais de produção.
