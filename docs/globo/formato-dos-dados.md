# Formato dos dados do painel de checagens (debate da Globo)

A página `docs/globo/index.html` lê `docs/globo/data/painel.json` a cada 30 s. Este arquivo descreve o formato, versão 1. O validador é `scripts/contrato_painel.py`: `valida(dados)` devolve a lista de problemas (vazia quando o arquivo está certo), e `python -P scripts/contrato_painel.py ARQUIVO.json` sai com código 1 se houver algum. Os testes ficam em `tests/test_contrato_painel.py`.

Regras gerais:

- Todo campo listado é obrigatório. Campo a mais é erro, para que um nome errado não passe despercebido.
- Horários: ISO 8601 com fuso, ex. `"2026-10-23T22:35:10-03:00"`. A página os mostra no horário de Brasília.
- Datas de checagem: `"AAAA-MM-DD"` ou `null` quando a fonte não traz data.
- Minutagem: `minutagem_s`, segundos (inteiro ou decimal, ≥ 0) contados do 00:00 marcado pelo operador na abertura do mediador.
- `quem`: `"Lula"` ou `"Flávio"`. Falas da mediação não entram.
- Exemplo completo: `docs/globo/data/exemplo.json` (fictício; a página o abre com `?exemplo`).

## Raiz

| campo | tipo | o que é |
|---|---|---|
| `versao_contrato` | `1` | versão deste formato |
| `meta` | objeto | ver abaixo |
| `ligacoes` | lista de ligação | ligações publicadas, em qualquer ordem (a página ordena pela minutagem, mais recente primeiro) |
| `sem_par` | lista de afirmação sem par | afirmações checáveis ainda sem checagem anterior |
| `correcoes` | lista de correção | ligações despublicadas |

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
| `agencias_incluidas` | lista de `{nome, via}` | agências na base e por qual canal |
| `agencias_excluidas` | lista de `{nome, motivo}` | agências fora da base e por quê |
| `modelo` | texto | modelo do Claude usado, ex. `"claude-opus-5-5"` |
| `prompts` | lista de `{arquivo, sha256}` | prompts fixos e o hash SHA-256 do conteúdo |
| `limiar_automatico` | número de 0 a 1, ou `null` | nota mínima para publicar sem aprovação; `null` = publicação automática desligada |

## Ligação

| campo | tipo | o que é |
|---|---|---|
| `id` | texto | identificador estável e único, ex. `"lig-0004"`; as correções apontam para ele |
| `minutagem_s` | segundos | início da fala |
| `quem` | `"Lula"` ou `"Flávio"` | quem disse |
| `trecho` | texto | citação literal, conferida letra a letra contra a fala |
| `reformulacao` | texto | reformulação neutra do que foi afirmado |
| `tipo` | `"numero"` ou `"perene"` | número/estatística ou fato perene; com `"numero"` a página destaca a data da checagem |
| `origem` | `"aprovada"` ou `"automatica"` | aprovada por uma pessoa ou publicada sozinha acima do limiar |
| `publicada_em` | horário | quando entrou no painel |
| `ligada_depois` | booleano | `true` quando a afirmação estava sem par e ganhou checagem numa atualização da base |
| `checagens` | lista de checagem, pelo menos 1 | todas as checagens confirmadas, mesmo que discordem |

### Checagem

| campo | tipo | o que é |
|---|---|---|
| `agencia` | texto | nome da agência como ela se apresenta |
| `data` | `"AAAA-MM-DD"` ou `null` | data da checagem |
| `rotulo` | texto | rótulo exibido, sem tradução nem conversão |
| `fonte_rotulo` | `"selo_da_pagina"` ou `"dados_da_agencia"` | `selo_da_pagina`: selo visível na página da agência, conferido pelo operador. `dados_da_agencia`: rótulo como veio nos dados (ClaimReview `textualRating` ou prefixo do título do Fato ou Fake); a página mostra "rótulo conforme dados da agência" |
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

| campo | tipo | o que é |
|---|---|---|
| `em` | horário | quando a ligação foi despublicada |
| `ligacao_id` | texto | `id` da ligação retirada |
| `motivo` | texto | por que saiu |
| `retirado` | ligação | a ligação inteira, como estava publicada |

Despublicar nunca apaga: a ligação sai de `ligacoes` e entra aqui.
