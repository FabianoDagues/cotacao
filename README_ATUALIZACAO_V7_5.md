# Atualização V7.5 — correção de filtros do Fluxo de Caixa

Alterações:

- Corrige o filtro **Todas as contas** quando o formulário envia `conta=` vazio.
- O saldo inicial do período agora respeita a conta selecionada:
  - Todas as contas = soma de todas as contas ativas;
  - Conta específica = saldo inicial apenas daquela conta.
- Ao clicar em **Editar** a partir do Fluxo de Caixa filtrado, a tela de edição guarda o filtro atual.
- Depois de **Salvar alterações**, o sistema retorna ao mesmo período, conta, categoria e tipo usados antes da edição.
- Os botões **Voltar ao fluxo** e **Cancelar** na edição também preservam os filtros.

Não há alteração de banco de dados nesta versão.
