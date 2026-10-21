# Lula × Flávio: o confronto livre em números

Primeiro protótipo de uma ferramenta de análise de debates. Aplicada aqui ao confronto livre (banco de tempo, 15 min para cada um) do debate presidencial do 2º turno na Band, em 18/10/2026: perguntas diretas, como cada um tratou o adversário, relógio de xadrez e ritmo, tom (classificação por IA), temas, palavras típicas, afirmações para checar e a transcrição completa, com link para o vídeo.

O painel é estático e fica em `docs/`, publicado pelo GitHub Pages.

## Como foi feito

1. **Gravação:** áudio da live no YouTube com `yt-dlp --live-from-start`.
2. **Transcrição:** `scripts/transcribe.py`, com faster-whisper na CPU e tempo por palavra. O confronto livre da Band foi transcrito com `large-v3`, passado na linha de comando (abaixo); sem o modelo na linha de comando, o script usa `large-v3-turbo`, o mesmo da checagem ao vivo. Os nomes do debate (`data/vocabulario.json`) entram no prompt inicial.
3. **Quem fala:** `scripts/diarize.py` extrai embeddings de voz ECAPA (SpeechBrain) por janela de ~3 s e agrupa as vozes. `scripts/rotular.py` dá a cada trecho a voz com mais tempo nele.
4. **Revisão de quem fala e de nomes:** `scripts/agente.py rotulos` chama o Claude (`claude-opus-5-5`, o modelo do painel da Band) com o prompt fixo `prompts/rotulos.md`. A saída validada fica em `data/bloco1_correcoes.json`.
5. **Glossário de grafia:** `data/glossario.json` traz os nomes conferidos em fonte pública, com o link de cada um. É aplicado por código depois das correções do agente.
6. **Falas:** `scripts/build_turns.py` junta os segmentos em falas e corrige a maiúscula que o Whisper põe no início de cada segmento quando a frase continua.
7. **Classificação:** `scripts/agente.py classificar`, com o prompt `prompts/classificar.md`, atribui tipo, temas e resumo a cada fala, lista as afirmações checáveis e os fios de perguntas diretas. Todo trecho citado é conferido letra a letra na transcrição. A saída fica em `data/bloco1_classificacao.json`, e cada chamada é registrada (modelo, data, hash do prompt, tokens).
8. **Análise:** `scripts/analyze.py`, com `scripts/texto.py`, gera `docs/data/dados.json` e as nuvens de palavras. O cálculo usa lemas do spaCy `pt_core_news_lg`, palavras típicas por log-odds com prior de Dirichlet informativo (Monroe, Colaresi & Quinn 2008), expressões repetidas e a "fala seguinte" de cada pergunta por regra fixa.
9. **Busca por sentido (opcional no painel):** `scripts/busca.py` calcula embeddings `intfloat/multilingual-e5-small` de trechos de até 40 palavras. No painel, o leitor ativa a busca depois de um aviso. O navegador baixa o mesmo modelo, cerca de 135 MB, via Transformers.js e compara a consulta com esses vetores, sem enviar nada a servidor.
10. **Prévia e imagens para redes:** `scripts/make_og.py` gera `docs/og.png` e `scripts/make_cards.py` gera as imagens do fio em `docs/cards/`.


```sh
python -P scripts/transcribe.py data/raw/debate_audio.m4a 16226 18162 data/bloco1_transcricao.json large-v3 --vocab
python -P scripts/diarize.py data/raw/debate_audio.m4a data/bloco1_transcricao.json data/bloco1_diar.json 3
cd data
python -P ../scripts/rotular.py bloco1_transcricao.json bloco1_diar.json bloco1_seglab_auto.json "2=L,0=F,1=M"
python -P ../scripts/agente.py rotulos bloco1_transcricao.json bloco1_seglab_auto.json bloco1_correcoes.json
python -P ../scripts/build_turns.py bloco1_transcricao.json bloco1_seglab_auto.json bloco1_correcoes.json bloco1_turnos_agente.json
python -P ../scripts/agente.py classificar bloco1_turnos_agente.json bloco1_classificacao.json
python -P ../scripts/build_turns.py bloco1_transcricao.json bloco1_seglab_auto.json bloco1_correcoes.json bloco1_final.json bloco1_classificacao.json
python -P ../scripts/analyze.py bloco1_final.json ../docs
python -P ../scripts/busca.py ../docs
python -P ../scripts/make_og.py ../docs
python -P ../scripts/make_cards.py ../docs
```

O agente lê a chave da API de `ANTHROPIC_API_KEY` ou de um arquivo `.env` na raiz, que é ignorado pelo git.

## Checagem ao vivo (debate da Globo, em construção)

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

- O tempo de fala é medido no áudio, com pausas e apartes, e não no relógio oficial. O gráfico do relógio escalona cada candidato para 15 min. A estimativa deixa 1min07 para Lula quando o tempo de Flávio acaba, e a mediação anunciou 1min08.
- Erros de transcrição e de atribuição de falas curtas são possíveis. Na dúvida, vale o vídeo original.
- A lista de afirmações registra o que foi dito, não se é verdade.
