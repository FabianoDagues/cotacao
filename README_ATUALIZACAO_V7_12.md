# V7.12 — Baixa rápida por usuário e setor

Base: sistema completo V7.11. Financeiro, importação de extratos, cotações e fornecedores permanecem sem alteração de suas rotinas.

## Instalação
1. Faça backup do PostgreSQL antes do deploy. Não apague nem recrie o banco.
2. Extraia a atualização numa pasta nova e envie os arquivos de `app/` aos mesmos caminhos no GitHub. Não crie `app/app/`.
3. As tabelas `departments` e `catalog_products` e as colunas opcionais para vínculo/histórico são adicionadas automaticamente no startup. Não precisa editar SQL manualmente. `requirements.txt` da V7.11 permanece necessário (inclui `xlrd` e `openpyxl`).
4. Abra o sistema após o deploy e faça Ctrl+F5 se necessário.

## Configuração inicial
- Cadastros > Setores: crie Açougue, Padaria, Limpeza etc. conforme sua empresa.
- Cadastros > Usuários: vincule o administrador a um setor se ele também registrar baixas; cadastre colaboradores com setor, e-mail individual e senha provisória. Colaboradores precisam alterar a senha no primeiro acesso. Podem lançar e consultar apenas suas próprias baixas; editar/cancelar é responsabilidade do administrador. Fornecedores continuam com acesso separado.
- Produtos > Cadastro de produtos: baixe o modelo e importe XLS/XLSX. Colunas obrigatórias: Código de barras, Descrição, Preço de custo. Opcionais: Código interno e Unidade (padrão UN). Preserve os códigos como texto. Produtos existentes são mantidos; se marcar a opção específica, somente o custo atual é atualizado. Identificações conflitantes interrompem a importação inteira. Produtos do histórico com código compatível ou vínculo existente são integrados ao novo cadastro geral; os demais continuam na busca histórica. Se o histórico usa código interno diferente do EAN, inclua a coluna Código interno para permitir a correspondência. Descrição sozinha não é prova de identidade. O cadastro não altera as cotações anteriores.
- A baixa tem um único campo de busca. Leitor USB/Bluetooth configurado como teclado e Enter: leia o código. Também digite descrição e selecione resultado. F2 volta à busca. Data e motivo (padrão Consumo interno) permanecem após Salvar e próximo produto. O setor vem da sessão do usuário; não pode ser escolhido no formulário. O custo do cadastro é fixado no lançamento; alteração futura de custo não altera o histórico. Produtos históricos sem custo são identificados como tal; o administrador pode informar custo manualmente.
- Relatório de baixas: filtre período, setor, usuário, motivo e produto, clique Totalizar por produto e Imprimir relatório. Exclui canceladas dos totais, separa unidades diferentes e apresenta custo total geral e número de lançamentos sem custo. Baixas anteriores à V7.12 sem setor permanecem "Sem setor"; não atribuir retroativamente o setor atual.
- Baixa é um registro de consumo/perda; não integra estoque externo e não cria automaticamente despesa financeira.

## Validação
Testes locais SQLite: migração de banco V7.11 e reinício idempotente; cadastro de setores/colaboradores; importação XLSX com zeros à esquerda e deduplicação; integração de código histórico; rollback em conflito; busca sem acento e exata; troca de senha; acesso administrativo negado a colaboradores; vínculo de setor do servidor, preservação de setor e custo histórico; relatório filtrado e total após cancelamento. Sintaxe JS e Python verificadas. O formato XLS tem implementação via xlrd (dependência existente), mas não foi executado neste ambiente porque a biblioteca não estava disponível; não foi possível instalar localmente. Teste inicial com sua planilha real e scanner ainda necessário. PostgreSQL/Railway de produção não foram acessados.
