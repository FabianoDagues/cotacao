# Backup do Sistema de Cotação — contexto para nova conversa

Data do backup: 27/10/2026
Versão do código: V6 (última versão consolidada desta conversa)

## Sistema online atual

- Endereço público: https://mercadosanenge-cotacao.up.railway.app
- Login: https://mercadosanenge-cotacao.up.railway.app/login
- Repositório GitHub usado na implantação: `FabianoDagues/cotacao`
- Hospedagem: Railway
- Banco: PostgreSQL no Railway

> Este ZIP contém o código-fonte e arquivos de implantação. Ele NÃO contém uma cópia dos dados vivos do PostgreSQL do Railway.

## Funcionalidades já implementadas

- Login de administrador e fornecedor.
- Cada fornecedor acessa apenas suas próprias cotações e preços.
- Cadastro de fornecedores.
- Editar, desativar/reativar e excluir fornecedor.
- Senha inicial provisória para fornecedor.
- Troca obrigatória da senha no primeiro acesso para novos fornecedores.
- Alterar senha pelo próprio fornecedor.
- Administrador pode gerar link temporário de redefinição de senha (1 hora, uso único).
- Importação de produtos via XLSX/CSV.
- Cotação com data/hora limite.
- Inclusão de fornecedores na criação da cotação.
- Inclusão de fornecedor em uma cotação já existente.
- Conversão e normalização de unidade/caixa/pacote por fator.
- Administrador pode corrigir preço, unidade e fator informados.
- Após corrigir uma linha, a tela permanece no local editado.
- Mapa comparativo com menor e maior preço normalizado.
- Exportação do comparativo para Excel.
- Fornecedor recebe confirmação após salvar/enviar a cotação.
- Acompanhamento por fornecedor: `Enviado` / `Pendente` e data/hora do envio.
- Aviso ao administrador quando todos enviaram e indicação de quem ainda falta.
- Fechamento/pedido por fornecedor.
- No fechamento, itens de menor preço daquele fornecedor vêm pré-selecionados.
- Administrador pode selecionar/desmarcar itens e alterar quantidades antes de fechar.
- Fechamento pode ficar em rascunho ou pronto.
- Pedido por fornecedor pode ser impresso/salvo em PDF pelo navegador e copiado em texto para WhatsApp/e-mail.

## Estrutura principal

- `app/main.py` — rotas e regras da aplicação
- `app/models.py` — modelos do banco
- `app/db.py` — banco e configuração
- `app/security.py` — autenticação/senhas
- `app/templates/` — telas HTML
- `app/static/style.css` — estilos
- `Dockerfile` — implantação
- `requirements.txt` — dependências
- `modelo_produtos.xlsx` / `modelo_produtos.csv` — modelos de importação

## Variáveis de ambiente usadas no Railway

Os valores secretos não estão registrados neste backup. Na implantação, conferir variáveis como:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
SECRET_KEY=<chave secreta>
ADMIN_EMAIL=<email do administrador>
ADMIN_PASSWORD=<senha inicial do administrador>
ADMIN_NAME=Administrador
COOKIE_SECURE=1
APP_TIMEZONE=America/Sao_Paulo
PUBLIC_BASE_URL=https://mercadosanenge-cotacao.up.railway.app
```

Observação: como houve um comunicado de segurança da Railway em outubro/2026, credenciais sensíveis devem estar rotacionadas conforme a recomendação da Railway.

## Observações para implantação

Historicamente o repositório `FabianoDagues/cotacao` foi criado com os arquivos dentro de uma pasta `sistema_cotacao`, e o Railway usou Root Directory `/sistema_cotacao` em parte da configuração. Este backup está organizado com o código na raiz do ZIP. Antes de um deploy, confira a estrutura real do repositório e o campo Root Directory do Railway.

## Pedido sugerido para iniciar outra conversa

Você pode anexar este ZIP e escrever:

> Este é o backup do meu Sistema de Cotação V6. Leia primeiro o arquivo `INICIAR_NOVA_CONVERSA.md` e mantenha todas as funcionalidades existentes. Quero continuar alterando esse projeto sem remover recursos já implementados. Sempre que gerar uma atualização, entregue um ZIP incremental e, se necessário, uma versão completa atualizada.

## Segurança / banco de produção

Não recrie ou apague o PostgreSQL de produção para atualizar o código. Alterações de banco devem preservar as cotações e fornecedores existentes, salvo pedido explícito do usuário.
