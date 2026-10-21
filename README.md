# Checagem ao vivo: Lula × Flávio no debate da Globo

Protótipo de checagem de debates em tempo real. Durante o debate do 2º turno na TV Globo, em 23/10/2026, o sistema transcreve o que os candidatos dizem, encontra as checagens que as agências já publicaram sobre a mesma afirmação e as mostra com o rótulo original e o link de cada uma. O painel não dá veredito próprio, e cada ligação passa pelo operador ou por um limiar medido antes.

A página é estática e fica em `docs/globo/`, publicada pelo GitHub Pages. O formato dos dados está em `docs/globo/formato-dos-dados.md`.

**Material de referência do debate da Band (18/10/2026):** os perfis de voz, o limiar de quem fala, a avaliação das ligações e parte das buscas (as 73 afirmações de `data/bloco1_classificacao.json`) foram medidos sobre as falas de referência do confronto livre da Band (`data/bloco1_final.json`, fora do git). `scripts/transcribe.py` e `scripts/diarize.py` ficam aqui para refazer essas medições; `scripts/agente.py` é o cliente do Claude que a sessão usa, e `prompts/classificar.md` é o critério de "afirmação checável" que `prompts/extrair_afirmacoes.md` repete. O Claude lê a chave de `ANTHROPIC_API_KEY` ou de um arquivo `.env` na raiz, que é ignorado pelo git.

## Como funciona

**Base de checagens:** `scripts/base_checagens.py` busca cada termo de `data/buscas_checagens.json` (nomes, temas, regiões, assuntos e as 73 afirmações da Band) na API oficial do Google Fact Check (`claims:search`, `languageCode=pt`), pagina até esgotar e guarda as respostas brutas. `scripts/checagens.py` transforma cada resposta num registro só com metadados: agência, URL, frase checada, quem disse, data e de onde ela veio, e rótulo como a agência publicou. Nada do texto da checagem é guardado. A base e os vetores `multilingual-e5-small` da frase checada ficam em `data/checagens/`, fora do git. A chave vem de `GOOGLE_FACTCHECK_KEY`, no ambiente ou no `.env`.

```sh
python -P scripts/base_checagens.py coletar            # ~200 buscas; grava brutos, base, vetores e relatório
python -P scripts/base_checagens.py coletar --dias 1   # só checagens do último dia (atualização)
python -P scripts/base_checagens.py reconstruir        # refaz base e vetores a partir dos brutos, sem rede
python -P scripts/base_checagens.py consultar "Bolsonaro triplicou o Bolsa Família"
python -P -m pytest -q                                 # testes
```

**Fato ou Fake (g1):** entra pelo RSS da seção e pelos sitemaps diários do g1 filtrados pela seção, desde 01/01/2025. A coleta confere o `robots.txt`, espera 1 s entre requisições e só baixa o começo de cada página, até a manchete. Guarda só a URL, o título e a data. O rótulo é o selo no começo do título (`#FATO`, `#FAKE`, `#NÃOÉBEMASSIM`). Os resumos "Veja o que é #FATO ou #FAKE…" juntam várias checagens sem um rótulo único e ficam de fora. Durante o debate, `atualizar` busca a cada 10 min só as checagens do último dia no Google e lê o RSS do Fato ou Fake. Os registros novos vão para o fim da base, e só eles ganham vetores. A sessão recebe esses registros por `Sessao.atualiza_base`.

```sh
python -P scripts/base_checagens.py fato-ou-fake       # RSS + sitemaps desde 01/01/2025 (~1 h na 1ª vez; depois só o que mudou)
python -P scripts/base_checagens.py atualizar          # durante o debate: Google (maxAgeDays=1) + RSS do Fato ou Fake
python -P scripts/base_checagens.py incorporar BRUTO.jsonl   # acrescenta à base os registros novos de brutos coletados à parte
```

**Reserva de emergência:** se a API oficial do Google cair ou a chave parar de valer, `coletar` e `atualizar` (e `noite.py`) aceitam `--explorer-emergencia`, que busca no buscador interno do Fact Check Explorer (sem chave, não documentado), com pausa entre requisições e só os metadados. Fica desligada por padrão.

**Sessão de checagem:** `scripts/sessao.py` recebe as falas fechadas (quem, início em segundos desde o 00:00, texto). Para cada fala de candidato, o Claude (pela função `chama` de `scripts/agente.py`) extrai as afirmações checáveis com o prompt fixo `prompts/extrair_afirmacoes.md`, que usa o mesmo critério do `classificar.md`. Cada trecho é conferido letra a letra contra a fala, e o que não bate é descartado e registrado. As falas da mediação não passam pela extração. A base é consultada por sentido: até 8 checagens candidatas por afirmação, e só de 2025 em diante quando a afirmação é um número. Depois o Claude confirma, com `prompts/confirmar_ligacao.md`, quais candidatas tratam da mesma afirmação, com nota de 0 a 1 e motivo. As ligações confirmadas vão para a fila do operador. Sem nenhuma confirmada, a afirmação fica "sem par". O estado é exportado no formato de `docs/globo/formato-dos-dados.md`. Toda mudança de estado é um evento, que leva junto a resposta do Claude. A cada atualização da base, as afirmações sem par são conferidas de novo, só contra os registros novos, e as que ganham par seguem o caminho de qualquer ligação. Essa é a ligação tardia.

O modelo das duas etapas é configurável: `claude-sonnet-5-5` por padrão, ou o que estiver em `CHECAGEM_MODELO`. O painel mostra o modelo pedido e os que de fato responderam (a API pode refazer uma chamada recusada num modelo de reserva). O limiar da publicação automática sai da avaliação da Band (`scripts/avaliacao_band.py`) e só vale para o modelo com que foi medido: com outro modelo, a publicação automática fica desligada e a tela diz por quê. Uma chamada ao Claude que falha é tentada de novo três vezes, com espera; o que falhar em todas fica no painel de erros e é refeito quando a sessão é reaberta, sem repetir o que já deu certo. Na fila, o operador aprova (com o selo visível na página da agência), recusa ou edita o trecho e a reformulação antes de aprovar; o trecho editado também tem de estar letra a letra na fala.

```sh
python -P scripts/sessao.py band    # repassa as 150 falas da Band; grava data/sessao_band/{painel,fila}.json, eventos.jsonl e agente/
```

**A noite num comando só:** `scripts/noite.py` liga as partes:

- a linha de falas (`scripts/falas.py`), sobre o loopback do som do Windows ou sobre um arquivo;
- a sessão;
- a tela de aprovação (`scripts/aprovacao.py`), com um quadro de erros por etapa: transcrição e áudio, API do Claude, API do Google e publicação;
- a atualização da base a cada 10 min;
- o publicador (`--publicar`).

A transcrição ao vivo usa faster-whisper `large-v3-turbo` int8 na CPU; quem fala sai dos perfis de voz de `scripts/vozes.py` (o cálculo dos perfis e a medição do limiar ficam em `scripts/vozes_medir.py`). O 00:00 é marcado uma vez, por Enter ou pelo botão da tela, e vale para a sessão e para as falas. Se a linha de falas cai, ela recomeça sozinha. Se o processo cai, o mesmo comando retoma a sessão pelo registro, sem chamar o Claude de novo para o que já foi feito e sem republicar. O modo pós-debate roda a mesma sessão sobre as gravações da noite e gera o painel completo. O passo a passo da noite está em `roteiros/`.

```sh
python -P scripts/noite.py ao-vivo --publicar --vocabulario data/vocabulario_globo.json   # a noite; Enter marca o 00:00
python -P scripts/noite.py arquivo AUDIO --zero S [--inicio S --fim S] [--ritmo 0]         # ensaio sobre um arquivo
python -P scripts/noite.py pos-debate [GRAVACAO.wav ...] --publicar                        # painel completo depois do debate
```

**Sem terminal:** os atalhos de `iniciar/` (duplo clique) rodam `scripts/iniciar.py`. `Noite do debate.cmd` publica no site de verdade; `Ensaio.cmd` e `Ensaio do zero.cmd` publicam numa cópia local do site, servida em `http://127.0.0.1:8766/globo/`. Os dois abrem a tela em "Pronto para começar" (`noite.py --esperar-comecar`): os modelos carregam em segundo plano, a tela confere sozinha som, site, credenciais, base, energia e chave do Claude (`scripts/preparo.py`), e nada é capturado nem enviado antes do botão Começar (ou Retomar). O botão "Encerrar a noite" faz a saída limpa.

## Limitações

- O painel não julga a frase nova: mostra checagens anteriores sobre a mesma afirmação, e a agência pode ter checado uma versão um pouco diferente.
- Transcrição, atribuição de quem fala e casamento com a base são automáticos e sujeitos a erro. Na dúvida, vale a fala original e a checagem da agência.
- Só entram agências que publicam dados estruturados (ClaimReview) ou, no caso do Fato ou Fake, RSS e sitemap. As excluídas e o motivo aparecem no método da página.
