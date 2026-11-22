# Sistema de Cotação V7.1 - Perfis bancários Itaú e Sicoob

## O que mudou
- Perfil bancário específico para Itaú.
- Perfil bancário específico para Sicoob.
- C6 Bank permanece pendente de um extrato C6 válido para calibração.
- Importador XLS/XLSX passa a capturar, quando existirem, Razão Social/Favorecido e CNPJ/CPF.
- Recebimentos de cartão identificáveis são classificados automaticamente.
- PIX recebido é classificado como receita quando não for transferência própria.
- Movimentos com a própria empresa são tratados como transferências entre contas.
- Transferências opostas em bancos diferentes, na mesma data e valor, são vinculadas automaticamente.
- Tarifa/pacote de serviços e empréstimo do Sicoob recebem classificação automática.
- Vivo no Itaú é sugerido/classificado como Telefone / Internet.
- BOLETO PAGO genérico continua A classificar para evitar classificação errada.
- Proteção contra banco divergente: se a conta escolhida for C6 e o arquivo tiver assinatura Sicoob, por exemplo, a importação é bloqueada.

## Variáveis opcionais/recomendadas
Para reconhecer transferências entre contas próprias:

FINANCE_OWN_NAME=MERCADO SANENGE
FINANCE_OWN_TAX_ID=06030826000180

A versão traz esses valores como padrão para este projeto, mas podem ser definidos no Railway.

## Arquivos alterados
- app/finance.py
- app/templates/bank_import.html
- app/templates/financial_accounts.html
- .env.example
- FLUXO_CAIXA_REFERENCIA.md

## Banco de dados
Não há alteração de tabelas nesta versão.
