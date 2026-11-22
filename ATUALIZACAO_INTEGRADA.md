# Cotação, fluxo de caixa e uso interno

Esta versão reúne o fluxo de caixa do arquivo enviado e as funcionalidades mais recentes desta conversa.

Inclui contas financeiras, grupos, lançamentos, transferências, importação bancária, impressão, catálogo de cotação, importação unificada, funcionários, retiradas, quantidade com botões, cadastro de destinos e relatório por destino.

## Atualizar
1. Extraia este ZIP.
2. Atualize os arquivos na pasta do repositório usada pelo serviço Railway. O Dockerfile, requirements.txt e a pasta app devem ficar juntos nessa pasta.
3. Publique a atualização usando o mesmo banco e as variáveis existentes do serviço.
4. Confira os menus Fluxo de caixa, Produtos, Importar produtos e Uso interno.

As tabelas adicionais são criadas na inicialização. O arquivo .env.example serve como referência. As variáveis FINANCE_OWN_NAME e FINANCE_OWN_TAX_ID são utilizadas para reconhecer transferências entre contas próprias.

## Verificação
Testes locais das rotas financeiras, criação de lançamento, processamento e repetição de importação com dados simulados, permissões e atualização de banco SQLite criado pelos modelos do arquivo enviado. Testes existentes de cotação, recuperação de senha, uso interno e interface aprovados.

A publicação e a verificação no PostgreSQL do Railway dependem da instalação no serviço. Este pacote contém o código-fonte. Os dados operacionais ficam no banco configurado no serviço.


## Recebimentos diários e relatório sintético

Em Fluxo de caixa, abra Recebimentos diários. Selecione data e conta, carregue o dia e informe os totais recebidos de crédito, débito, voucher e Pix. Salvar novamente substitui os totais daquela conta/data. Zero remove o total da modalidade. Os valores representam recebimentos na conta selecionada.

Os grupos de receita são separados por modalidade. As contas financeiras identificam o banco ou caixa onde o dinheiro está. Energia, Água e Telefone também possuem grupos individuais para classificar despesas.

Abra Relatório sintético, escolha o período e opcionalmente uma conta. O relatório apresenta totais por grupo/subgrupo, entradas, saídas e resultado, e pode ser impresso ou salvo em PDF pelo navegador. Transferências entre contas são mostradas separadamente. Lançamentos antigos continuam totalizados nos grupos que possuem. Para obter as modalidades específicas nos históricos, edite a classificação dos lançamentos cuja modalidade não foi identificada.

A importação de extratos e o lançamento diário conferem conflitos de recebimentos na mesma conta e data para evitar duplicidade. Para substituir um fechamento manual pelo extrato, zere os totais manuais desse dia antes da importação.

## PDFs bancários

Importação de PDF digital nos formatos dos exemplos enviados de Itaú, Sicoob, C6 e Nubank. Informe a senha no campo próprio quando o documento estiver protegido. A senha não é gravada no banco nem no código.

O sistema usa o saldo inicial cadastrado e os movimentos para calcular o saldo. Na primeira importação de uma conta com saldo inicial zero, o saldo inicial identificado no extrato é utilizado, ou é calculado a partir do saldo final e da soma dos movimentos. Quando não há saldo no documento, revise o saldo inicial na tela Contas financeiras.

Conferência dos exemplos reais em ambiente de teste:
- Itaú: 251 movimentos; saldo calculado R$ 1.519,13; informado R$ 1.519,14. O aviso da diferença de R$ 0,01 é exibido após importar.
- C6: 793 movimentos; saldo calculado R$ 1.644,05.
- Nubank: 5 movimentos; saldo calculado R$ 490,00.
- Sicoob: 48 movimentos; saldo calculado R$ 2.126,89. Os lançamentos futuros e o limite de crédito são excluídos da importação.
- Repetir o mesmo arquivo foi testado sem duplicação.
PDFs digitalizados sem texto e layouts diferentes apresentam aviso e exigem adaptação.

Instale o requirements.txt atualizado para habilitar a leitura e a abertura de PDFs protegidos. A validação foi local com SQLite; publicação e verificação no PostgreSQL do Railway continuam pendentes.

## Plano de contas em níveis
Em Fluxo de caixa → Grupos de contas, informe o nome e a natureza e escolha Dentro de qual grupo. Deixe Nenhum para criar um grupo principal. Escolher um subgrupo permite criar mais um nível.

Exemplo: Despesas operacionais → Logística → Transporte / Combustível / Manutenção de veículos.
Despesas de pessoal inclui Folha de pagamento, Diárias, FGTS e INSS.

Os lançamentos e filtros exibem o caminho completo. Filtrar um grupo inclui suas subcontas. No relatório sintético, cada grupo superior soma todos os seus descendentes, e o total geral conta cada lançamento uma vez. Os lançamentos históricos mantêm sua classificação original e podem ser reclassificados pela edição do lançamento.
