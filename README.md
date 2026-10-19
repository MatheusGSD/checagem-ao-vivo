# Lula × Flávio: o confronto livre em números

Painel com a análise do primeiro confronto direto (banco de tempo, 15 min para cada um) do debate presidencial do 2º turno na Band, em 18/10/2026: tempo de fala, relógio de xadrez, mapa de palavras, palavras típicas de cada candidato, temas, tipo de fala (ataque, defesa, proposta) e afirmações para checar.

O painel é estático e fica em `docs/`, publicado pelo GitHub Pages.

## Como foi feito

1. **Gravação:** áudio da live no YouTube com `yt-dlp --live-from-start`.
2. **Transcrição:** `scripts/transcribe.py`, com faster-whisper `large-v3-turbo` na CPU e tempo por palavra.
3. **Quem fala:** `scripts/diarize.py` extrai embeddings de voz ECAPA (SpeechBrain) por janela de ~3 s e agrupa as vozes.
4. **Revisão de quem fala e de nomes:** `scripts/agente.py rotulos` chama o Claude (`claude-opus-5-5`) com o prompt fixo `prompts/rotulos.md`. A saída validada fica em `data/bloco1_correcoes.json`.
5. **Glossário de grafia:** `data/glossario.json` traz os nomes conferidos em fonte pública, com o link de cada um. É aplicado por código depois das correções do agente.
6. **Falas:** `scripts/build_turns.py` junta os segmentos em falas e corrige a maiúscula que o Whisper põe no início de cada segmento quando a frase continua.
7. **Classificação:** `scripts/agente.py classificar`, com o prompt `prompts/classificar.md`, atribui tipo, temas e resumo a cada fala, lista as afirmações checáveis e os fios de perguntas diretas. Todo trecho citado é conferido letra a letra na transcrição. A saída fica em `data/bloco1_classificacao.json`, e cada chamada é registrada (modelo, data, hash do prompt, tokens).
8. **Análise:** `scripts/analyze.py`, com `scripts/texto.py`, gera `docs/data/dados.json` e as nuvens de palavras. O cálculo usa lemas do spaCy `pt_core_news_lg`, palavras típicas por log-odds com prior de Dirichlet informativo (Monroe, Colaresi & Quinn 2008), expressões repetidas e a "fala seguinte" de cada pergunta por regra fixa.
9. **Mapa:** `scripts/embeddings.py` gera embeddings de texto `intfloat/multilingual-e5-large`, rodando local, e uma projeção UMAP.
10. **Prévia para redes:** `scripts/make_og.py` gera `docs/og.png`.

As versões da revisão e da classificação feitas antes, na conversa com o Claude e sem prompt fixo, estão em `data/*_conversa.json` para comparação. No tipo de fala, as duas concordam em 90% do tempo de fala, e em 97% há pelo menos um tema em comum.

```sh
python -P scripts/transcribe.py data/raw/debate_audio.m4a 16000 18700 data/transcricao_janela.json
python -P scripts/diarize.py data/raw/debate_audio.m4a data/bloco1_transcricao.json data/bloco1_diar.json 3
cd data
python -P ../scripts/agente.py rotulos bloco1_transcricao.json bloco1_seglab_auto.json bloco1_correcoes.json
python -P ../scripts/build_turns.py bloco1_transcricao.json bloco1_seglab_auto.json bloco1_correcoes.json bloco1_turnos_agente.json
python -P ../scripts/agente.py classificar bloco1_turnos_agente.json bloco1_classificacao.json
python -P ../scripts/build_turns.py bloco1_transcricao.json bloco1_seglab_auto.json bloco1_correcoes.json bloco1_final.json bloco1_classificacao.json
python -P ../scripts/analyze.py bloco1_final.json ../docs
python -P ../scripts/embeddings.py bloco1_final.json ../docs
python -P ../scripts/make_og.py ../docs
```

O agente lê a chave da API de `ANTHROPIC_API_KEY` ou de um arquivo `.env` na raiz, que é ignorado pelo git.

## Limitações

- O tempo de fala é medido no áudio, com pausas e apartes, e não no relógio oficial. O gráfico do relógio escalona cada candidato para 15 min. A estimativa deixa 1min07 para Lula quando o tempo de Flávio acaba, e a mediação anunciou 1min08.
- Erros de transcrição e de atribuição de falas curtas são possíveis. Na dúvida, vale o vídeo original.
- A lista de afirmações registra o que foi dito, não se é verdade.
