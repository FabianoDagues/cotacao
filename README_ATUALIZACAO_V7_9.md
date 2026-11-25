# V7.9 — Menu lateral, compras e baixa de produtos

Base: Sistema de Cotação V7.8. O código financeiro (finance.py e bank_pdf.py) e as dependências foram mantidos sem alterações.

## Atualização
1. Faça um backup do banco PostgreSQL e confirme que o repositório está na V7.8.
2. Extraia o ZIP em uma pasta nova. No GitHub, envie os arquivos da pasta app mantendo a estrutura app/templates e app/static. Os arquivos README são informativos.
3. Após o deploy do Railway, atualize a página (Ctrl+F5) e entre novamente se necessário.
4. O startup cria a tabela nova product_write_offs usando create_all. Não é necessário apagar, recriar ou zerar o banco. Não há alterações destrutivas no esquema existente.

## Menu
Compras: Cotações, Nova cotação, Pedidos/fechamento, Fornecedores.
Produtos: Baixa de produtos.
Financeiro: Fluxo de caixa, Importar extrato, Bancos e caixa, Plano de contas.
Menu recolhível no desktop, gaveta no celular, indicação de página selecionada. Fornecedores mantêm menu restrito às próprias cotações e alteração de senha.

## Baixas de produtos
Não foi encontrada a implementação anterior nos arquivos históricos de cotação disponíveis. Esta rotina foi recriada como registro independente de perdas/saídas: data, código, descrição, unidade, quantidade, custo opcional, motivo, observação. Há sugestões de itens cotados, edição, cancelamento com histórico, filtros e impressão. Não altera quantidades das cotações, não movimenta estoque de outro sistema nem cria despesa automática. Dados de uma eventual implementação antiga não são migrados sem identificação do esquema original.

## Acesso
As verificações de papel e usuário ativo foram preservadas. Em navegação HTML, sessão ausente/inativa em rota protegida redireciona ao login; acesso indevido mantém 403 e exibe página com opção de retorno. As respostas JSON para chamadas de API foram mantidas. O motivo do 403 específico em produção não foi reproduzido ou confirmado sem os respectivos endereços/logs. O fluxo de recuperação por token da V6 substitui a antiga redefinição direta de senha da V2.

## Testes
Banco SQLite de teste: login admin/fornecedor, abertura das telas de cotações, fornecedores, fechamento, exportação, financeiro (incluindo todas as contas e impressão com período), contas, categorias e importação. Testados fechamento e impressão de pedido, cadastro/edição/cancelamento de baixas, validação de quantidade negativa, custo e permissões de fornecedor. Layout e alternância do menu verificados em Chromium com HTML renderizado em 1440 e 390 px. Não executado no banco de produção Railway. A importação bancária não foi retestada nesta revisão, pois os arquivos de lógica permanecem idênticos à V7.8.
