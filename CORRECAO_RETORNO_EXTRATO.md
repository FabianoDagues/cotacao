# Retorno ao lançamento editado do extrato

Ao abrir **Editar** na lista de lançamentos do fluxo de caixa, o sistema preserva o intervalo de datas e os filtros de conta, grupo e tipo. Após salvar, volta à mesma linha, deslocando a página até o lançamento e destacando-o em azul. Os botões **Voltar** e **Cancelar** na edição também preservam a localização.

Ao clicar em **Classificar** na própria lista, o sistema usa o mesmo comportamento. Se a edição mudar a data ou a classificação de forma que o lançamento desapareceria do filtro atual, a consulta é ajustada somente o necessário para manter o registro visível.

## Instalação

Substitua os arquivos da aplicação pelos deste ZIP e publique normalmente no Railway (ou reinicie o servidor). Não há alteração de tabelas nem migração de banco para esta correção.

## Verificação

Os testes automatizados cobrem a edição, retorno à linha, manutenção dos filtros, reclassificação, mudança de data e o fluxo de classificação rápida.
