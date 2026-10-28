# Sistema de Cotação — V6

Esta versão adiciona acompanhamento de envio de propostas e fechamento de pedidos por fornecedor.

## Fluxo de respostas

Quando o fornecedor salva uma cotação com pelo menos um preço preenchido, o vínculo dele com a cotação recebe uma data de envio. O administrador passa a visualizar status `Enviado` ou `Pendente`, além de uma mensagem geral indicando se todos já responderam e quais fornecedores ainda faltam.

## Fechamento por fornecedor

Na tela da cotação, cada fornecedor possui a ação **Fechamento / pedido**. Na primeira abertura, os itens em que aquele fornecedor possui o menor preço normalizado ficam pré-selecionados. O administrador pode alterar a seleção e a quantidade a pedir. A quantidade é expressa na unidade cotada pelo fornecedor (por exemplo, CX, PCT ou UN), e o sistema mostra a equivalência na unidade-base e o total estimado.

O fechamento pode ser salvo como rascunho ou marcado como pronto. A versão de impressão pode ser salva como PDF pelo navegador ou copiada como texto para envio por e-mail/WhatsApp.
