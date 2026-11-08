# Atualização V7 — Catálogo e leitor de código de barras

## Instalação no sistema existente

Este pacote atualiza o código V6. Não contém os dados do PostgreSQL e não foi publicado no Railway nesta conversa.

1. Faça backup do banco PostgreSQL no Railway antes de atualizar.
2. Copie o conteúdo de `sistema_completo` para a raiz do projeto existente (onde estão Dockerfile, requirements.txt e app). Alternativamente, sobreponha somente os arquivos de `atualizacao_incremental` sobre a V6 original.
3. Mantenha DATABASE_URL, SECRET_KEY e as demais variáveis atuais. Não recrie o banco ou o serviço. Verifique Root Directory: se o repositório usa `sistema_cotacao`, aplique os arquivos dentro dessa pasta.
4. Publique pelo fluxo habitual GitHub → Railway. Na inicialização, SQLAlchemy cria somente a nova tabela products; as tabelas e registros anteriores permanecem.
5. Abra Produtos como administrador e clique em **Carregar base inicial de 80 produtos**. O botão pode ser usado novamente sem duplicar os códigos BASE.
6. Confira no celular: cadastro, câmera, lançamento e visualização pelo fornecedor.

## Como usar

- Produtos: cadastrar, buscar, editar e desativar. Desativar não remove itens de cotações anteriores.
- Na base inicial, a unidade UN significa uma embalagem da descrição (ex.: 1 pacote de 5 kg), não o peso em kg. Ajuste a unidade e o nome antes de usar conforme sua forma de compra.
- A base contém 80 produtos genéricos de mercearia, bebidas, limpeza e higiene. Não é uma base comercial de GTIN verificados. Não existe consulta externa automática nesta versão.
- Para usar um produto real, edite sua descrição com marca e embalagem e leia ou digite o código impresso. Para várias marcas/embalagens, crie cadastros separados com códigos internos diferentes.
- São aceitos EAN-8, UPC-A, EAN-13 e GTIN-14 válidos. GTIN é normalizado com zeros à esquerda até 14 dígitos para evitar duplicidade UPC/EAN. UPC-E não é aceito.
- Nova cotação: escolha fornecedores e prazo; a planilha agora é opcional. Na cotação, clique em **Adicionar produtos / Ler código de barras**.
- Escaneie ou busque, confira o produto, informe a quantidade e clique em **Adicionar à cotação**. Escanear não lança sem confirmação.
- Código desconhecido: cadastre o produto na mesma tela ou pesquise pelo nome e edite o cadastro para vincular o código. Depois de salvar, faça a busca novamente.
- O mesmo código interno não entra duas vezes na mesma cotação; nova leitura não soma quantidade silenciosamente.
- Inclusão é bloqueada se o prazo venceu ou já existe pedido, inclusive rascunho. Reabra o prazo ou crie outra cotação conforme a mensagem.
- Ao incluir item, o status dos fornecedores volta a Pendente; os preços anteriores são preservados. Eles precisam revisar e reenviar. Não há notificação externa automática.
- Alterar o catálogo não altera o nome/unidade dos itens já lançados nas cotações.

## Importação de catálogo

CSV UTF-8 (vírgula ou ponto e vírgula) ou XLSX. Colunas: `codigo`, `produto`, `unidade`, `codigo_barras` (opcional). Códigos devem ser células de texto, inclusive GTIN, para preservar zeros. Códigos internos existentes são ignorados, sem sobrescrever. Um erro de validação ou duplicidade de GTIN cancela toda a importação.

A importação antiga de planilha na criação de cotação continua funcionando; ela não alimenta automaticamente o catálogo. A base distribuída está em `app/data/base_produtos.csv`, também aceita na importação de catálogo.

## Câmera

A câmera exige HTTPS, permissão do navegador e internet para carregar o leitor html5-qrcode 2.3.8 do unpkg. Em caso de bloqueio, carregamento indisponível ou código ilegível, digite os números. Abra no navegador do celular, evitando o navegador interno de aplicativos. A leitura é processada no navegador; não há envio de fotos a um serviço de reconhecimento.

Documentação do leitor: https://scanapp.org/html5-qrcode-docs/docs/intro

## Verificação técnica

Testes automatizados: `pip install -r requirements.txt httpx` e `python -m unittest discover -s tests -v`. Usam SQLite temporário separado. Incluem autorização, base idempotente, validação de GTIN, busca, catálogo, criação sem planilha, lançamento, duplicidade, quantidades inválidas, preservação de histórico/preços, status após reinício, bloqueio com pedido, importação atômica e importação V6.

O banco PostgreSQL de produção não foi acessado. A leitura óptica e as permissões precisam ser conferidas em um celular real após implantação; os testes de servidor não verificam o hardware.
