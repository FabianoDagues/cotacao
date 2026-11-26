# V7.10 — Baixa de produtos rápida

Atualização sobre a V7.9. Envie os arquivos do ZIP pequeno para a raiz do repositório, mantendo as pastas `app/`, `app/static/` e `app/templates/`. Faça commit e aguarde o deploy. O CSS e o JavaScript têm versão nova; use Ctrl+F5 se necessário.

## Utilização
- Menu Produtos > Baixa de produtos > Registrar baixa.
- Leitor USB/Bluetooth no modo teclado (HID), configurado para finalizar com Enter. Leia com o foco no campo Código de barras. Também aceita digitação e botão Localizar. F2 volta ao código, F3 abre a busca pela descrição.
- Busca por descrição ou código, com resultados em tabela. A busca tolera acentos e mais de uma palavra. Use setas e Enter ou Selecionar. Códigos são textos; zeros iniciais são preservados.
- A origem dos dados é o histórico de produtos das cotações, baixas não canceladas e vínculos explícitos de códigos. Não existe integração com um ERP externo ou cadastro completo de estoque. O custo unitário deve ser informado/conferido; não é presumido de uma cotação antiga.
- Se o código não existir, informe manualmente ou busque pela descrição. Marque a opção de vínculo ao salvar se o código de barras for diferente do código interno. O vínculo passa a ser usado nas próximas leituras. Não sobrescreve vínculos existentes incompatíveis. Caso seja necessário corrigir um vínculo errado, solicite uma atualização com gestão de vínculos; não altere registros diretamente sem backup.
- Salvar e próximo produto mantém data e motivo, limpa o produto, quantidade volta a 1 e o foco retorna ao leitor. Salvar e voltar retorna à listagem.
- A câmera de celular não foi implementada; leitores no modo teclado e digitação são suportados.

## Banco de dados
A tabela nova `product_barcodes` é criada no startup pelo mecanismo já existente de criação de tabelas. Não altera colunas antigas, não apaga registros e não exige reinstalação do PostgreSQL. Recomenda-se backup antes de atualizar. O registro de baixa permanece independente do estoque e do fluxo de caixa. Importadores bancários e demais módulos preservados.

## Validação
Testes locais com SQLite: consulta por código, zeros iniciais, pesquisa sem acento, código ambíguo, persistência de vínculo, bloqueio de vínculo conflitante, criação, edição, salvar próximo, listagem, financeiro e autorização administrativa. Interface renderizada e JS testados com respostas reais da API local e scanner simulado por teclado; largura móvel conferida. Não houve teste de scanner físico nem acesso ao PostgreSQL/Railway de produção.
