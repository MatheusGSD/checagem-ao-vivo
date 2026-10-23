# Outro PC: rodar a checagem completa

Para deixar um segundo computador pronto para rodar o pipeline inteiro (ensaio ou noite). Para um notebook que só grava o som, veja `roteiros/notebook-reserva.md`.

Este roteiro serve de prompt. No outro PC, depois do clone, peça ao Claude Code: "Leia `roteiros/outro-pc.md` e deixe este PC pronto, conferindo cada passo."

## 1. O que o git não traz

Os itens abaixo ficam fora do git de propósito (`.gitignore`). Copie-os do notebook principal para os mesmos caminhos no outro PC:

| Caminho | O que é | Sem ele |
|---|---|---|
| `.env` | `ANTHROPIC_API_KEY` e `GOOGLE_FACTCHECK_KEY` | o Claude não roda |
| `data/vozes/` | perfis de voz de Lula e Flávio (`perfis.json`) | `noite.py` não sobe (`Reconhecedor.carregar`) |
| `data/checagens/` (~26 MB) | base de checagens e vetores e5 | nenhuma ligação com checagens; refazer com `scripts/base_checagens.py` leva tempo e usa a chave do Google |
| `data/vocabulario_globo.json` | termos da Globo para o Whisper | roda com o vocabulário da Band e mostra um aviso |

O restante de `data/` (áudio e transcrições da Band, avaliações, sessões antigas) só serve para refazer medições. Não precisa copiar.

## 2. Instalação

1. **Python 3.14** para Windows, de python.org. Marque "Add python.exe to PATH". É a versão do notebook principal.
2. **Pacotes.** O torch roda em CPU (`device="cpu"` em `falas.py` e `vozes.py`), então a versão CPU basta:

   ```powershell
   python -m pip install --user anthropic numpy scikit-learn faster-whisper speechbrain torch torchaudio sentence-transformers pyaudiowpatch soxr soundfile pytest
   ```

   Versões do notebook principal, caso alguma mais nova quebre: anthropic 1.15.0, faster-whisper 1.2.1, speechbrain 1.1.2, sentence-transformers 5.3.0, numpy 2.3.5, scikit-learn 1.8.0, PyAudioWPatch 0.2.12.9.
3. **Git com push para `MatheusGSD/checagem-ao-vivo`.** Só é necessário para o atalho da noite. O ensaio não publica no GitHub. Para conferir, rode `python -P scripts/publicador.py verifica`.

## 3. Conferência

1. **Testes:** `python -m pytest -q` na raiz. Todos devem passar.
2. **Modelos:** na primeira execução são baixados `large-v3-turbo` (Whisper, ~1,6 GB), `speechbrain/spkrec-ecapa-voxceleb` e `intfloat/multilingual-e5-small`. Faça isso com internet boa, antes da noite.
3. **Loopback:** toque um vídeo e rode o teste de 10 s de `roteiros/notebook-reserva.md`. O dispositivo deve ser a saída onde o g1 vai tocar, com "[Loopback]" no nome.
4. **Ensaio:** dê duplo clique em `iniciar/Ensaio.cmd`. A tela do operador deve abrir em "Pronto para começar", sem aviso de vocabulário. Dê Começar com um vídeo tocando e confira se aparecem falas, afirmações e a página do leitor em `http://127.0.0.1:8766/globo/`.

## Atenção na noite

**Só um PC roda `Noite do debate.cmd`.** Os dois publicariam `docs/globo/data/painel.json` na `main` e os pushes brigariam. O ensaio pode rodar em qualquer um, porque publica numa cópia local.
