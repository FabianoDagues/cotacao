# Atualização V7.6 — Transferências no saldo

Correção do Fluxo de Caixa:

- Transferências internas continuam fora de Receitas, Despesas e Resultado.
- Transferência recebida aumenta o saldo da conta destino.
- Transferência enviada reduz o saldo da conta origem.
- Saldo final do período agora considera receitas, despesas e transferências.
- Resumo diário agora considera transferências no saldo acumulado.
- Tela passa a exibir separadamente Receitas, Despesas, Transferências recebidas e Transferências enviadas.
- Relatório de impressão segue a mesma lógica.

Exemplo:
- Caixa físico recebe transferência: + R$ 135,00
- Despesa do caixa: - R$ 509,00
- Resultado: - R$ 509,00
- Movimento do saldo: - R$ 374,00

Não há alteração de banco de dados.
