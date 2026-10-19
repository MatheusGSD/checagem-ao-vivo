# Lula × Flávio: o confronto livre em números

Painel com a análise do primeiro confronto direto (banco de tempo, 15 min para cada um) do debate presidencial do 2º turno na Band, em 18/10/2026: tempo de fala, relógio de xadrez, mapa de palavras, palavras típicas de cada candidato, temas, tipo de fala (ataque, defesa, proposta) e afirmações para checar.

O painel é estático e fica em `docs/`, publicado pelo GitHub Pages.

## Como foi feito

1. **Gravação:** áudio da live no YouTube com `yt-dlp --live-from-start`.
2. **Transcrição:** `scripts/transcribe.py`, com faster-whisper `large-v3-turbo` na CPU e tempo por palavra.
3. **Quem fala:** `scripts/diarize.py` extrai embeddings de voz ECAPA (SpeechBrain) por janela de ~3 s e agrupa as vozes. Trocas rápidas e falas da mediação foram corrigidas por IA (Claude), lendo a transcrição, em `data/bloco1_correcoes.json`, que também normaliza nomes que o Whisper errou (Vorcaro, Milei, Luchsinger…).
4. **Turnos e classificação:** `scripts/build_turns.py` junta tudo em turnos. A classificação de cada turno (tipo, temas, resumo) e a lista de afirmações estão em `data/bloco1_classificacao.json`. Foram feitas por IA (Claude) lendo a transcrição e ainda não passaram por revisão humana.
5. **Análise:** `scripts/analyze.py` gera `docs/data/dados.json` e as nuvens de palavras com spaCy `pt_core_news_lg`. O cálculo usa lemas, palavras distintivas por log-odds com prior de Dirichlet informativo (Monroe, Colaresi & Quinn 2008) e expressões repetidas.
6. **Prévia para redes:** `scripts/make_og.py` gera `docs/og.png`.

```sh
python -P scripts/transcribe.py data/raw/debate_audio.m4a 16000 18700 data/transcricao_janela.json
python -P scripts/diarize.py data/raw/debate_audio.m4a data/bloco1_transcricao.json data/bloco1_diar.json 3
python -P scripts/build_turns.py data/bloco1_transcricao.json data/bloco1_seglab_auto.json \
    data/bloco1_correcoes.json data/bloco1_final.json data/bloco1_classificacao.json
python -P scripts/analyze.py data/bloco1_final.json docs
python -P scripts/make_og.py docs
```

## Limitações

- O tempo de fala é medido no áudio, com pausas e apartes, e não no relógio oficial. O gráfico do relógio escalona cada candidato para 15 min. A estimativa deixa 1min07 para Lula quando o tempo de Flávio acaba, e a mediação anunciou 1min08.
- Erros de transcrição e de atribuição de falas curtas são possíveis. Na dúvida, vale o vídeo original.
- A lista de afirmações registra o que foi dito, não se é verdade.
