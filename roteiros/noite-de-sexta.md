# Noite de sexta (23/10/2026, Globo, 21h20): checklist do operador

Um comando põe a noite de pé: `scripts/noite.py`. Ele cuida de:

- captura do som do computador, gravada em arquivo;
- transcrição e quem fala;
- sessão de checagem e tela de aprovação;
- base atualizada a cada 10 min;
- publicador.

Tudo roda no notebook principal. O notebook reserva só grava (ver `roteiros/notebook-reserva.md`).

## Antes das 21h

### Na sexta de manhã ou à tarde

- [ ] **Ensaio geral audível nos dois notebooks**, na tomada: o áudio da Band tocando no navegador e capturado pelo loopback (passo a passo no fim deste arquivo).
- [ ] **Base em dia:**
  - `python -P scripts/base_checagens.py atualizar`, que puxa as checagens do último dia;
  - `python -P scripts/base_checagens.py fato-ou-fake`, que só lê o que mudou.
- [ ] **Limiar da publicação automática:** `data/avaliacao/limiar.json` sai da marcação da avaliação da Band (ticket 04, fase 2). Sem esse arquivo, ou com `"limiar": null`, tudo vai para a fila. É o esperado: o diagnóstico prevê só 5 a 8 ligações por debate.
- [ ] **Vocabulário da Globo:** o `data/vocabulario.json` é o da Band (tem "Oinegue" e "Band"). Crie `data/vocabulario_globo.json` no mesmo formato (`{"termos": [...]}`) com:
  - os nomes dos mediadores;
  - os temas do telão;
  - os nomes citados que se espera ouvir.
- [ ] **Credenciais do site:** `python -P scripts/publicador.py verifica` tem que terminar em `ok: origin/main aceita push`. Se falhar, resolva o login do git (Git Credential Manager) agora, não às 21h.
- [ ] **Chaves:** o `.env` da raiz tem `ANTHROPIC_API_KEY` e `GOOGLE_FACTCHECK_KEY`.

### Às 20h30

- [ ] **Notebook principal na tomada**, com o plano de energia em **"Melhor desempenho"**. Na bateria, a linha de falas não acompanhou o tempo real: atraso acima de 1 min (relatório do ticket 09).
- [ ] **Fechar o que pesa na CPU:** Docker, abas pesadas, outros agentes, sincronização do OneDrive. Pausar o Windows Update.
- [ ] **Notificações do Windows desligadas** ("Não perturbe"). O loopback grava **tudo** que toca no computador: nenhum outro vídeo, aviso ou música até o fim.
- [ ] **g1 tocando na saída padrão do Windows**, com o som do player no máximo e sem mudo. Confira em Configurações > Som > Saída qual é a padrão. O teste da tarde gravou de "Speakers (Realtek(R) Audio) [Loopback]".

### Às 21h00: subir a noite

```powershell
python -P scripts/noite.py ao-vivo --publicar --vocabulario data/vocabulario_globo.json --prompt "Debate presidencial na Globo, segundo turno. Lula e Flávio Bolsonaro."
```

- [ ] O terminal mostra:
  - quantas checagens a base tem;
  - se a publicação automática está ligada e com qual limiar;
  - `publicador -> origin/main`;
  - o endereço da tela.
- [ ] A tela abre em **http://127.0.0.1:8765/**. O cabeçalho mostra "linha rodando", o número de falas e a hora da última atualização da base. A primeira atualização roda logo ao começar e leva uns 5 min.
- [ ] Os quatro quadros de erro estão verdes: Transcrição e áudio, API do Claude, API do Google (base), Publicação.
- [ ] **Notebook reserva gravando** (`falas.py gravar`, ver o roteiro dele).
- [ ] Antes do 00:00, as falas ficam retidas e nada vai para o Claude. Isso é normal.

## Durante o debate

- [ ] **00:00 na abertura do mediador.** Use Enter no terminal ou o botão "Marcar 00:00 agora" na tela; os dois marcam a sessão e a linha de falas.
  - Depois de marcado, o Enter não remarca. Para corrigir, digite `remarcar` e Enter.
  - Se marcou atrasado, corrija para o horário certo:

    ```powershell
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/acao/inicio -ContentType application/json -Body '{"instante":"2026-10-23T21:20:05-03:00"}'
    ```

  - A publicação automática, se estiver ligada, só começa 10 min depois do 00:00.
- [ ] **Fila:**
  - para cada ligação, abra a página da agência e digite o selo visível;
  - aprove, recuse, ou desmarque as checagens que não servem;
  - despublicar exige motivo, que vai para as correções.
- [ ] **Quadros de erro:** vermelho = erro nos últimos 10 min; laranja = erro mais antigo. O que fazer em cada um:

| quadro | o que significa | o que fazer |
|---|---|---|
| Transcrição e áudio: "a linha de falas caiu" | O Whisper ou a captura caiu. A noite recomeça a linha sozinha em ~5 s, numa gravação nova em `data/sessao_globo/gravacoes/`, com o mesmo 00:00. | Nada, se o número de falas voltar a subir. Se cair de novo, veja a mensagem. Se for o dispositivo de áudio, confira a saída padrão. |
| Transcrição e áudio: "nenhuma fala há 180 s depois do 00:00" | Nada chega da linha. | O g1 parou, travou, está no mudo ou toca em outra saída. Volte o player. No intervalo comercial, as falas da propaganda saem como Mediação, então esse aviso não aparece. |
| API do Claude | A extração ou a confirmação de uma fala falhou (rede, sobrecarga). A fala fica sem análise, porque a sessão não tenta de novo. | Anote a minutagem. Se acontecer muito, confira a internet e o status da Anthropic. |
| API do Google (base) | Buscas da atualização falharam, ou o RSS do Fato ou Fake não respondeu. | Nada: a sessão segue com a base anterior, e a próxima atualização tenta de novo em 10 min. |
| Publicação | O push para o site falhou. O publicador tenta de novo a cada 15 s, sem perder mudanças. | Confira a internet. "Authentication failed": rode `publicador.py verifica` noutro terminal. |

- [ ] **Se o processo travar ou fechar:** rode **o mesmo comando** de novo. Ele:
  - reconstrói a sessão pelo registro (`data/sessao_globo/eventos.jsonl`);
  - termina as análises que ficaram pela metade;
  - segue com o mesmo 00:00, numa gravação nova;
  - não chama o Claude de novo para o que já está no registro e não republica.

  Fica sem análise ao vivo só o trecho em que o processo esteve fora. Ele está na gravação do notebook reserva.
- [ ] **CPU:** se o atraso crescer (as falas aparecem um bom tempo depois de ditas), feche o que puder. Não abra nada pesado.

## Depois do debate

- [ ] **Ctrl+C** no terminal da noite, depois das considerações finais. A saída limpa:
  - para o áudio e fecha o WAV;
  - espera as análises em curso;
  - grava `data/sessao_globo/painel.json`;
  - faz o último envio.

  Espere a linha `encerrada às ...`.
- [ ] **Fila pendente:** a fila só existe com o processo rodando. Para aprovar com calma depois, abra só a tela e o publicador sobre a mesma pasta, sem áudio. Nada é refeito.

  ```powershell
  python -P scripts/aprovacao.py --publicar
  ```
- [ ] **Ctrl+C no notebook reserva.**
- [ ] **Guardar as gravações:**
  - `data/sessao_globo/gravacoes/*.wav` e os `.json` ao lado;
  - o WAV da reserva.

  Copie tudo para um segundo disco ou para a nuvem.
- [ ] **Se o ao vivo falhou no meio,** gere o painel completo sobre a gravação da noite. O pós-debate continua da última fala entregue e marca o painel como encerrado:

  ```powershell
  python -P scripts/noite.py pos-debate --publicar --vocabulario data/vocabulario_globo.json --prompt "Debate presidencial na Globo, segundo turno. Lula e Flávio Bolsonaro."
  ```

  Sem arquivo, ele usa todas as gravações de `data/sessao_globo/gravacoes/`, cada uma com o 00:00 do seu `.json`. Para usar a gravação da reserva, veja `roteiros/notebook-reserva.md`.
- [ ] **Trocar a chave do Google** (projeto `checagem-debate`) e revisar as chaves do Gemini sem restrição.
- [ ] Mais tarde: ajustar as minutagens à íntegra do g1, se o link dela aceitar minutagem.

## Ensaio geral audível (sexta de manhã)

No notebook principal, na tomada, com "Melhor desempenho" e a reserva gravando em paralelo:

1. Abra o vídeo da Band no navegador, no ponto 4:19:41 (15581 s da gravação, a abertura do Escriba) ou no confronto livre.
2. Suba a noite numa pasta de ensaio, sem `--publicar` (nada vai para o site).

   ```powershell
   python -P scripts/noite.py ao-vivo --pasta data/ensaio/sessao_audivel --sem-base
   ```

   Atenção: o ensaio chama o Claude de verdade. Pelo ensaio seco, são ~8 chamadas por minuto de confronto livre (~13 mil tokens de entrada por minuto).
3. Dê play e aperte Enter na abertura do mediador.
4. Deixe tocar uns 20 min, com um trecho de confronto livre.
5. No meio, feche o terminal e rode o mesmo comando de novo, para conferir a retomada com o loopback.
6. Ctrl+C. Meça e registre em `data/ensaio/relatorio.md`:
   - **atraso fim da fala → entrega:** está no terminal como `(+N s)` e em `falas.jsonl`, campo `atraso_s`;
   - **acerto de texto e de quem fala:** `scripts/falas_medir.py` contra o Escriba, como no ticket 09;
   - **Mediação pelo som do computador:** confira o limiar 0,51 com a voz dos mediadores, a ressalva do ticket 08;
   - **reserva:** a gravação do notebook reserva tem o mesmo áudio.
