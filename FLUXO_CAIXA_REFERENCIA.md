# Referência usada no módulo de Fluxo de Caixa

A planilha fornecida organiza o fluxo mensal em colunas diárias e contempla, entre outros:

- Venda / faturamento e compra / faturamento;
- Caixa: saldo inicial, dinheiro, cartões e Pix/outros recebimentos;
- Total de entradas;
- Pagamento de mercadorias;
- Despesas de pessoal;
- Despesas operacionais;
- Marketing;
- Despesas administrativas;
- Impostos / tributos;
- Investimentos;
- Sócios;
- Despesas financeiras;
- Perdas;
- Total de despesas e saldo do dia.

No sistema, esses dados deixam de ser uma grade mensal fixa e passam a ser lançamentos individuais vinculados a data, conta financeira e grupo de contas. Isso permite filtros, impressão, importação bancária, conciliação e saldo consolidado de vários bancos com o caixa físico.

## Importação bancária

A primeira versão contém um leitor genérico de XLS/XLSX que procura cabeçalhos equivalentes a Data, Histórico/Descrição, Valor ou Débito/Crédito, Saldo e Documento. Cada banco pode ter um formato diferente. Após receber um extrato real de cada instituição, devem ser acrescentados perfis específicos para melhorar a identificação automática de pagamentos, recebimentos e transferências.


## Perfis bancários V7.1
- Itaú: recebimentos Rede/Cabal -> Cartões recebimento; PIX recebido -> receitas; transferências da própria empresa -> transferência; boleto pago genérico permanece A classificar; Vivo -> Telefone / Internet.
- Sicoob: ALELO/Ticket/Cooper Card/NAIP/Verocheque -> Cartões recebimento; PIX recebido -> receitas; pacote de serviços -> tarifas bancárias; débito de empréstimo -> Empréstimos; PIX entre contas próprias -> transferência.
- C6 Bank: perfil pendente de extrato C6 válido.

O reconhecimento de conta própria usa FINANCE_OWN_NAME e FINANCE_OWN_TAX_ID.
