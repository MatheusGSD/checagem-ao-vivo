# Formato dos dados do painel de checagens (debate da Globo)

A página `docs/globo/index.html` lê `docs/globo/data/painel.json` a cada 30 s. Este arquivo descreve o formato, versão 1. O validador é `scripts/contrato_painel.py`: `valida(dados)` devolve a lista de problemas (vazia quando o arquivo está certo), e `python -P scripts/contrato_painel.py ARQUIVO.json` sai com código 1 se houver algum. Os testes ficam em `tests/test_contrato_painel.py`.

Regras gerais:

- Todo campo listado é obrigatório. Campo a mais é erro, para que um nome errado não passe despercebido.
- Horários: ISO 8601 com fuso, ex. `"2026-10-23T22:35:10-03:00"`. A página os mostra no horário de Brasília.
- Datas de checagem: `"AAAA-MM-DD"` ou `null` quando a fonte não traz data.
- Minutagem: `minutagem_s`, segundos (inteiro ou decimal, ≥ 0) contados do 00:00 marcado pelo operador na abertura do mediador. Uma fala que já estava em andamento quando o 00:00 foi marcado começou antes dele: aparece com `minutagem_s` 0.
- `quem`: `"Lula"` ou `"Flávio"`. Falas da mediação não entram.
- Exemplo completo: `tests/fixtures/exemplo.json`, com dados fictícios, fora do site publicado. Para ver a página com ele sem publicar nada, sirva a raiz do repositório (`python -m http.server 8000`) e abra `http://localhost:8000/docs/globo/?dados=../../tests/fixtures/exemplo.json`. O parâmetro `?dados=` só aceita um caminho relativo para um `.json` do mesmo site; qualquer outra coisa cai em `data/painel.json`.

## Raiz

| campo | tipo | o que é |
|---|---|---|
| `versao_contrato` | `1` | versão deste formato |
| `meta` | objeto | ver abaixo |
| `ligacoes` | lista de ligação | ligações publicadas, em qualquer ordem (a página ordena pela minutagem, mais recente primeiro) |
| `sem_par` | lista de afirmação sem par | afirmações checáveis ainda sem checagem anterior |
| `correcoes` | lista de correção | ligações despublicadas e rótulos corrigidos, em qualquer ordem (a página ordena pelo horário, mais recente primeiro) |

## `meta`

| campo | tipo | o que é |
|---|---|---|
| `titulo` | texto | título do painel |
| `debate` | texto | linha de identificação, ex. `"Debate do 2º turno · TV Globo · 23/10/2026"` |
| `inicio_debate` | horário ou `null` | o 00:00; `null` antes de o operador marcar |
| `atualizado_em` | horário | última gravação do arquivo |
| `exemplo` | booleano | `true` só em dados fictícios; a página mostra um aviso |
| `encerrado` | booleano | `true` depois do debate; a página para de buscar atualizações |
| `fontes` | lista de `{nome, url}` | canais de coleta da base |
| `agencias_incluidas` | lista de `{nome, via}` | agências na base, pelo nome exibido, e por qual canal |
| `agencias_excluidas` | lista de `{nome, motivo}` | agências fora da base e por quê |
| `modelo` | texto | modelo do Claude pedido nas duas etapas, ex. `"claude-sonnet-5-5"` (configurável por `CHECAGEM_MODELO`) |
| `modelos_que_responderam` | lista de texto | modelos que de fato responderam às chamadas, sem repetição e em ordem alfabética (a API pode refazer uma chamada recusada num modelo de reserva); vazia antes da primeira resposta |
| `prompts` | lista de `{arquivo, sha256}` | prompts fixos e o hash SHA-256 do conteúdo |
| `limiar_automatico` | número de 0 a 1, ou `null` | nota mínima para publicar sem aprovação; `null` = publicação automática desligada |

As duas listas de agências saem de `agencias_do_painel(registros)` em `scripts/agencias.py`, a partir da política versionada em `data/agencias.json` (agências excluídas com o motivo e nome exibido de cada agência, que unifica grafias como "Agência Publica" e "Agência Pública"; o rótulo nunca muda).

## Ligação

| campo | tipo | o que é |
|---|---|---|
| `id` | texto | identificador estável e único, ex. `"lig-0004"`; as correções apontam para ele |
| `minutagem_s` | segundos | início da fala |
| `quem` | `"Lula"` ou `"Flávio"` | quem disse |
| `trecho` | texto | citação literal, conferida letra a letra contra a fala |
| `reformulacao` | texto | reformulação neutra do que foi afirmado |
| `tipo` | `"numero"` ou `"perene"` | número/estatística ou fato perene; com `"numero"` a página destaca a data da checagem |
| `origem` | `"aprovada"` ou `"automatica"` | como a ligação entrou no painel: aprovada por uma pessoa ou publicada sozinha acima do limiar. Cada checagem tem a sua `origem`: numa ligação publicada sozinha, as checagens que o operador aprovou depois são `"aprovada"` |
| `publicada_em` | horário | quando entrou no painel |
| `ligada_depois` | booleano | `true` quando a afirmação estava sem par e ganhou checagem numa atualização da base |
| `checagens` | lista de checagem, pelo menos 1 | todas as checagens confirmadas, mesmo que discordem |

### Checagem

| campo | tipo | o que é |
|---|---|---|
| `agencia` | texto | nome exibido da agência (`agencia` do registro da base, já com as grafias unificadas por `data/agencias.json`) |
| `data` | `"AAAA-MM-DD"` ou `null` | data da checagem |
| `rotulo` | texto | rótulo exibido, sem tradução nem conversão |
| `fonte_rotulo` | `"selo_da_pagina"` ou `"dados_da_agencia"` | `selo_da_pagina`: selo visível na página da agência, conferido pelo operador. `dados_da_agencia`: rótulo como veio nos dados (ClaimReview `textualRating` ou prefixo do título do Fato ou Fake); a página mostra "rótulo conforme dados da agência" |
| `origem` | `"aprovada"` ou `"automatica"` | como esta checagem entrou na ligação: aprovada por uma pessoa ou publicada sozinha acima do limiar; quando as checagens de uma ligação diferem, a página mostra a origem de cada uma |
| `url` | texto | link para a checagem |

## Afirmação sem par

| campo | tipo |
|---|---|
| `minutagem_s` | segundos |
| `quem` | `"Lula"` ou `"Flávio"` |
| `trecho` | texto literal |
| `reformulacao` | texto |

Quando ganha ligação, a afirmação sai de `sem_par` e entra em `ligacoes` com `ligada_depois: true`.

## Correção

Duas formas, pelo campo `tipo`. Cada uma tem só os seus campos.

### Despublicação (`"tipo": "despublicacao"`)

| campo | tipo | o que é |
|---|---|---|
| `tipo` | `"despublicacao"` | |
| `em` | horário | quando a ligação foi despublicada |
| `ligacao_id` | texto | `id` da ligação retirada |
| `motivo` | texto | por que saiu |
| `retirado` | ligação | a ligação inteira, como estava publicada |

Despublicar nunca apaga: a ligação sai de `ligacoes` e entra aqui.

### Rótulo corrigido (`"tipo": "rotulo"`)

| campo | tipo | o que é |
|---|---|---|
| `tipo` | `"rotulo"` | |
| `em` | horário | quando o rótulo foi trocado |
| `ligacao_id` | texto | `id` da ligação, que continua em `ligacoes` (até ser despublicada, se for) |
| `minutagem_s` | segundos | da ligação |
| `quem` | `"Lula"` ou `"Flávio"` | da ligação |
| `agencia` | texto | agência da checagem cujo rótulo mudou |
| `url` | texto | link da checagem (o mesmo `url` da checagem na ligação) |
| `rotulo_anterior` | texto | rótulo exibido até a troca |
| `fonte_rotulo_anterior` | `"selo_da_pagina"` ou `"dados_da_agencia"` | de onde vinha o rótulo anterior |
| `rotulo` | texto | rótulo exibido a partir de agora |
| `fonte_rotulo` | `"selo_da_pagina"` | o operador conferiu o selo visível na página da agência |

Corrigir o rótulo troca o rótulo da checagem na ligação e deixa a troca registrada aqui; trocas seguidas na mesma checagem geram uma entrada cada.
