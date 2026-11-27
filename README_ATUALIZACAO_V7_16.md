# V7.16 — Sinal sonoro na leitura pela câmera

Base V7.15. Ao tocar em Câmera, Ativar câmera ou Tirar foto, o navegador prepara o áudio. Um bip curto de aproximadamente 140 ms é emitido quando o código é confirmado e encaminhado à busca. A leitura ao vivo continua exigindo duas detecções iguais; se houver vários códigos, o bip ocorre somente após a seleção. O som confirma a leitura, não a existência do produto nem o salvamento da baixa. Não toca na abertura, em leituras não confirmadas ou sem código. Não depende de servidor de áudio nem de arquivos externos. Se o navegador ou o dispositivo bloquear o áudio, a busca continua normalmente. Verifique o volume, modo silencioso e permissões do aparelho. A leitura por foto também usa o mesmo evento de confirmação; o áudio pode ser interrompido pelo sistema operacional ao abrir a câmera nativa.

## Atualização
Sobre V7.15, substitua apenas `app/static/barcode_camera.js` e `app/templates/product_write_off_form.html` no GitHub. O parâmetro de versão do JavaScript foi incrementado para evitar cache antigo. Se ainda estiver em V7.14 ou anterior, aplique primeiro a atualização V7.15 ou use o pacote completo V7.16. Não há alteração de banco de dados, Dockerfile ou requirements.txt em relação à V7.15.

## Validação
Sintaxe JavaScript verificada com Node.js. Teste simulado do DOM, câmera, decodificação e Web Audio: bip único após confirmação, nova leitura, ausência de código, seleção entre vários códigos, bloqueio de áudio e navegador sem Web Audio. Não houve teste físico em Android/iOS ou no Railway. A intensidade do som depende do aparelho.
