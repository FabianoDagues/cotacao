# Sistema de Cotação — MVP

Sistema web para cotações com acesso separado por fornecedor.

## O que esta versão faz
- Login de administrador e fornecedores.
- Cadastro de fornecedores com usuário e senha próprios.
- Criação de cotação com data/hora limite.
- Importação de produtos via `.xlsx` ou `.csv`.
- Convite de fornecedores específicos para cada cotação.
- Fornecedor vê somente cotações destinadas a ele e somente os próprios preços.
- Cotação de unidade, caixa, pacote etc. com fator de conversão para unidade-base.
- Bloqueio de alterações depois do prazo.
- Administrador pode alterar/reabrir o prazo.
- Administrador pode corrigir unidade, fator e preço informado em caso de erro de preenchimento.
- Mapa comparativo com menor e maior preço normalizado por produto.
- Exportação do comparativo para Excel.

## Regra de conversão
Se o item está em `UN` e o fornecedor vende `CX` com 20 unidades:
- Unidade cotada: `CX`
- Fator: `20`
- Preço: `180,00`
- Preço normalizado: `180 / 20 = 9,00 por UN`

Se o item está em `KG` e o fornecedor vende pacote de 500 g:
- Unidade cotada: `PCT`
- Fator: `0,5`
- Preço do pacote: `8,00`
- Preço normalizado: `8 / 0,5 = 16,00 por KG`

## Planilha de importação
Use a primeira linha com estas colunas:

`Código | Produto | Quantidade | Unidade`

Também são aceitos alguns nomes equivalentes, como `Descricao`, `Qtd`, `Cod` e `SKU`.

## Executar rapidamente para teste
Requer Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abra `http://localhost:8000`.

Por padrão, para teste local:
- usuário: `admin@empresa.local`
- senha: `Admin123!`

Troque essas credenciais em produção.

## Executar com Docker + PostgreSQL
Edite as senhas de `docker-compose.yml` e rode:

```bash
docker compose up -d --build
```

O sistema ficará na porta `8000` do servidor.

## Para publicar online 24h
Use uma VPS/servidor ou plataforma que aceite Docker e PostgreSQL. Em produção:
1. Use domínio próprio, por exemplo `cotacao.suaempresa.com.br`.
2. Coloque um proxy HTTPS (Caddy, Nginx ou serviço da plataforma) na frente do app.
3. Defina `COOKIE_SECURE=1` quando estiver em HTTPS.
4. Troque `SECRET_KEY`, senha do PostgreSQL e senha do administrador.
5. Configure `APP_TIMEZONE` para o fuso da empresa (o padrão é `America/Sao_Paulo`).
6. Faça backup automático do banco PostgreSQL.
7. Não exponha a porta do PostgreSQL à internet.

## Próximas melhorias recomendadas antes de uso crítico
- Recuperação/troca de senha pelo próprio fornecedor.
- Envio de convite e lembrete por e-mail.
- Registro de auditoria de alterações.
- CSRF explícito nos formulários.
- Perfis de múltiplos administradores/compradores.
- Anexos de proposta e documentação.
- Seleção de vencedor por item e geração de pedido de compra.
- Frete, impostos, condição de pagamento e valor mínimo.
- Migrações de banco com Alembic.
- Testes automatizados adicionais e monitoramento.
