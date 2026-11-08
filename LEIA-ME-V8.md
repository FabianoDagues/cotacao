# Sistema de Cotação V8 — Uso interno do mercado

Versão 8.0 · 08/11/2026

## O que foi acrescentado

- Menu **Uso interno** no mesmo sistema.
- Cadastro de produto com somente **ID automático, descrição, código de barras, setor e classificação (Limpeza ou Insumo)**.
- Tela para ler o código pela câmera do celular, digitar o código ou usar leitor USB/Bluetooth; depois informar a quantidade e confirmar a retirada.
- Cadastro de funcionários com login próprio. A primeira entrada exige trocar a senha provisória.
- Registro automático de funcionário, data e hora em cada retirada.
- Relatório por data inicial/final, setor e classificação: resumo diário por produto e lançamentos detalhados. Exportação CSV e impressão do resumo.
- Cancelamento administrativo com motivo, sem apagar o histórico. Cancelamentos ficam fora dos totais.

## Instalação no projeto que já está no Railway

Este ZIP contém o projeto completo com `app`, `Dockerfile` e `requirements.txt` diretamente na raiz. É uma atualização do código preparado nesta conversa, incluindo a correção da redefinição de senha. Não contém dados nem credenciais do PostgreSQL online e ainda não foi publicado por nós.

1. Faça um backup do PostgreSQL pelo procedimento já utilizado no seu projeto.
2. Extraia o ZIP no computador.
3. No repositório `FabianoDagues/cotacao`, abra a pasta que o Railway usa como raiz. Se o projeto está em `sistema_cotacao`, entre nela primeiro. Nessa pasta devem estar `Dockerfile`, `requirements.txt` e `app`.
4. Substitua os arquivos do código pelos extraídos. Envie **toda a pasta app**, incluindo `internal_use.py`, `internal_models.py`, os novos templates e os arquivos estáticos. Trocar apenas main.py não basta.
5. Mantenha as variáveis atuais do serviço: DATABASE_URL, SECRET_KEY, ADMIN_EMAIL, COOKIE_SECURE, APP_TIMEZONE, PUBLIC_BASE_URL e demais configurações. Não recrie o banco. Não troque o serviço do mercado pelo outro projeto do Railway.
6. Faça o commit e aguarde a publicação correspondente no serviço **cotacao**. Confira se o commit ativo é o que recebeu os arquivos V8.
7. Abra `/healthz` no endereço do sistema. O resultado deve ser `{"status":"ok","version":"8.0"}`. Também aparecerá **V8** junto ao nome do sistema.
8. Entre como administrador e abra **Uso interno**.

Na inicialização, o código cria somente as novas tabelas `internal_products` e `internal_withdrawals`, se ainda não existirem, e aplica as migrações anteriores necessárias. As cotações, fornecedores, respostas, pedidos e produtos de cotação existentes são mantidos. A coluna da redefinição de senha também é verificada; quem já a criou pelo Console pode instalar normalmente.

Se houve alterações no seu repositório depois dos arquivos usados nesta conversa, compare antes de substituir para preservar essas alterações adicionais. O arquivo `ARQUIVOS-ALTERADOS-V8.txt` lista o que mudou em relação à V7.1.

## Primeiro uso

### Administrador

1. Uso interno → **Produtos de uso interno** → cadastre descrição, código, setor e classificação. O ID é automático.
2. Uso interno → **Funcionários** → cadastre nome, e-mail de acesso e senha provisória. Entregue os dados de acesso a cada funcionário pelo seu canal habitual.
3. Para alterar dados ou redefinir o acesso de um funcionário, clique em **Editar / redefinir senha**. Uma nova senha provisória exige nova troca no próximo acesso. Desmarcar Acesso ativo bloqueia novas operações.
4. Uso interno → **Relatório** → escolha as datas e, se desejar, setor e classificação. O padrão é o dia atual no fuso do sistema.

### Funcionário

1. Abra o mesmo endereço de login do sistema e entre com seu e-mail e senha provisória.
2. Defina sua senha pessoal.
3. Na tela **Registrar retirada**, leia ou digite o código.
4. Confira descrição, setor e classificação. Informe a quantidade e toque em **Confirmar retirada**.
5. Aguarde o comprovante na tela. Uma nova retirada requer uma nova leitura/busca e confirmação.

O funcionário vê apenas suas últimas dez retiradas. Os cadastros, o relatório geral e os cancelamentos são restritos ao administrador. Fornecedores continuam com o acesso às cotações e não acessam o uso interno.

## Regras do cadastro e das retiradas

- O cadastro de uso interno é específico para o consumo do mercado. O catálogo de produtos da cotação permanece disponível no menu Produtos.
- O setor é o setor de uso definido no cadastro (ex.: Padaria, Açougue, Loja); é copiado automaticamente para a retirada. Não há escolha separada de setor em cada lançamento nesta versão.
- A descrição deve identificar a embalagem ou unidade: por exemplo, `Detergente 500 ml`. Retirar dois frascos significa quantidade `2`.
- Quantidades positivas com até quatro casas decimais são aceitas. Use `1,5` ou `1.5`, sem separadores de milhares.
- Não há saldo, entrada de mercadoria ou baixa de estoque: o módulo registra consumo/retiradas.
- Código de barras é texto, preserva zeros e não pode duplicar outro produto de uso interno. EAN/UPC válidos equivalentes, com zeros à esquerda, são reconhecidos como a mesma identidade. Códigos internos também são aceitos (letras/números e `.`, `_`, `/`, `-`, até 80 caracteres, sem espaços).
- A leitura apenas localiza o produto. A retirada só é gravada após a confirmação da quantidade.
- Reenviar o mesmo formulário não cria outra retirada. Duas retiradas intencionais feitas com formulários novos são registros separados.
- Produtos desconhecidos mostram uma mensagem para solicitar cadastro ao administrador.
- Nome do produto, código, setor, classificação e nome do funcionário ficam preservados no lançamento. Editar cadastros não muda o histórico. Se a descrição ou o setor do mesmo produto mudar, o resumo mantém linhas separadas para os dados históricos diferentes.
- Funcionários e produtos não são excluídos por esta interface. Funcionários podem ser desativados; produtos podem ser editados.
- Erro de lançamento: o administrador cancela no relatório, informa o motivo e orienta o lançamento correto. O registro cancelado permanece no histórico.

## Relatório

As datas inicial e final são inclusivas e usam APP_TIMEZONE (padrão America/Sao_Paulo). Um lançamento às 23h59 fica no dia local correspondente. As datas são armazenadas em UTC no banco.

O resumo mostra data, ID, descrição, código de barras, setor, classificação e quantidade. Os detalhes mostram também quem retirou, horário e situação. O detalhamento é paginado de 50 em 50; resumo, CSV e impressão consideram todo o período filtrado. A consulta permite até 367 dias por vez.

O CSV usa UTF-8 com BOM e ponto e vírgula, com proteção contra fórmulas em campos textuais. O código de barras sai com apóstrofo inicial para preservar zeros ao abrir em planilha; importe a coluna como texto. Quantidades têm vírgula decimal.

## Câmera e compatibilidade

O leitor suporta EAN-13, EAN-8, UPC-A, Code 128, Code 39 e ITF. Precisa de HTTPS, permissão para a câmera e conexão para carregar html5-qrcode 2.3.8. Abra no navegador do celular. As imagens são processadas no navegador; não são enviadas para reconhecimento externo.

Se a câmera estiver indisponível ou a biblioteca não carregar, a digitação e o leitor USB/Bluetooth continuam disponíveis. A câmera é solicitada somente quando o usuário toca no botão e é encerrada ao concluir a leitura, cancelar ou colocar a página em segundo plano.

## Validação realizada

- Dez testes de servidor passaram, incluindo os testes existentes de catálogo e redefinição de senha e os novos testes de uso interno.
- Banco de teste com tabelas anteriores: criação das novas tabelas em startup e preservação de cotações/produtos anteriores.
- Produto, duplicidade de códigos EAN/UPC equivalentes, edição e campos obrigatórios.
- Quantidades inválidas, envio repetido, histórico preservado e restrições de acesso.
- Funcionário: cadastro, senha provisória, troca, redefinição e desativação.
- Relatório: limites de dia no fuso local, totais, filtros, CSV e cancelamentos.
- Testes de JavaScript simulam leitura, leitura repetida, câmera negada, biblioteca indisponível, cancelamento durante abertura, encerramento em segundo plano e prevenção de duplo clique.

Os testes de servidor utilizaram SQLite temporário. Não houve acesso ao PostgreSQL de produção. O navegador remoto de verificação não alcançou o servidor local; não foi possível fazer a conferência visual em navegador nesta execução. A leitura óptica deve ser conferida em um celular real após publicar.

Para repetir os testes: instale `requirements.txt` e `httpx`, execute `python -m unittest discover -s tests -v`. Os testes de interface simulada usam `node tests/test_internal_frontend.cjs`. Nenhum dado demonstrativo ou banco de testes está incluído neste ZIP.
