# Sistema de Cotação — Financeiro V7.2

Atualização do módulo de Fluxo de Caixa.

## Novidades

- Botão destacado **Nova transferência** para lançamento manual entre bancos/caixa.
- Lançamentos importados podem ser convertidos manualmente em **transferência entre contas** quando o reconhecimento automático falhar.
- Quando o extrato da conta oposta chegar depois, a contrapartida criada manualmente é conciliada em vez de duplicada.
- Plano de contas em dois níveis: **Grupo principal + Subgrupo**.
- Despesas de pessoal foram detalhadas em subgrupos separados, incluindo **Colaboradores extras**, **Pró-labore** e **Diárias**.
- Filtros do fluxo por **data inicial/final, conta, grupo, subgrupo e tipo**.
- Atalhos de período: Hoje, Últimos 7 dias e Mês atual.
- Proteção contra duplicidade em dois níveis: arquivo inteiro e lançamentos repetidos em extratos sobrepostos.
- **Memória de classificação** por conta bancária + descrição do extrato.
- Tela de importação mostra as classificações aprendidas e permite excluir uma regra incorreta.
- Resumo e impressão exibem Grupo e Subgrupo separadamente.

## Banco de dados

A atualização cria automaticamente a nova tabela `cash_classification_rules` no próximo deploy. As tabelas e dados existentes são preservados.
