# Noite de sexta (23/10/2026, Globo, 21h20): checklist do operador

A noite começa com **duplo clique** no atalho `iniciar\Noite do debate.cmd` (na pasta do projeto). Sem terminal:

**duplo clique → conferir a lista na tela → Começar → Marcar 00:00 → aprovar a fila → Encerrar a noite.**

Por trás, o atalho roda `scripts/noite.py`, que cuida de:

- captura do som do computador, gravada em arquivo;
- transcrição e quem fala;
- sessão de checagem e tela de aprovação;
- base atualizada a cada 10 min;
- publicador.

Tudo roda no notebook principal. O notebook reserva só grava (ver `roteiros/notebook-reserva.md`).

## Antes das 21h

### Na sexta de manhã ou à tarde

- [ ] **Ensaio geral audível nos dois notebooks**, na tomada: o áudio da Band tocando no navegador e capturado pelo loopback, com o atalho `iniciar\Ensaio.cmd` (passo a passo no fim deste arquivo).
- [ ] **Base em dia:**
  - `python -P scripts/base_checagens.py atualizar`, que puxa as checagens do último dia;
  - `python -P scripts/base_checagens.py fato-ou-fake`, que só lê o que mudou.
- [ ] **Limiar da publicação automática:** `data/avaliacao/limiar.json` sai da marcação da avaliação da Band (ticket 04, fase 2). Sem esse arquivo, ou com `"limiar": null`, tudo vai para a fila. É o esperado: o diagnóstico prevê só 5 a 8 ligações por debate.
- [ ] **Vocabulário da Globo:** o `data/vocabulario.json` é o da Band (tem "Oinegue" e "Band"). Crie `data/vocabulario_globo.json` no mesmo formato (`{"termos": [...]}`) com:
  - os nomes dos mediadores;
  - os temas do telão;
  - os nomes citados que se espera ouvir.
- [ ] **Credenciais do site:** a tela confere sozinha ("Credenciais do site" na lista, com um push simulado). Se ficar vermelho, rode `python -P scripts/publicador.py verifica` num terminal e resolva o login do git (Git Credential Manager) agora, não às 21h.
- [ ] **Chaves:** o `.env` da raiz tem `ANTHROPIC_API_KEY` e `GOOGLE_FACTCHECK_KEY`.

### Às 20h30

- [ ] **Notebook principal na tomada**, com o plano de energia em **"Melhor desempenho"**. Na bateria, a linha de falas não acompanhou o tempo real: atraso acima de 1 min (relatório do ticket 09).
- [ ] **Fechar o que pesa na CPU:** Docker, abas pesadas, outros agentes, sincronização do OneDrive. Pausar o Windows Update.
- [ ] **Notificações do Windows desligadas** ("Não perturbe"). O loopback grava **tudo** que toca no computador: nenhum outro vídeo, aviso ou música até o fim.
- [ ] **g1 tocando na saída padrão do Windows**, com o som do player no máximo e sem mudo. Confira em Configurações > Som > Saída qual é a padrão. O teste da tarde gravou de "Speakers (Realtek(R) Audio) [Loopback]".

### Às 21h00: abrir a noite

- [ ] **Duplo clique em `iniciar\Noite do debate.cmd`.** Abre uma janela preta (deixe aberta: ela mostra o que o programa faz) e, em instantes, a tela do operador no navegador, em **http://127.0.0.1:8765/**.
- [ ] A faixa **laranja** no alto diz **Site de verdade**. No ensaio ela é **roxa** e diz "Ensaio: cópia local do site". Se estiver roxa às 21h, feche e abra o atalho certo.
- [ ] A tela abre em **"Pronto para começar"**. Os modelos carregam sozinhos ("Carregando…", uns 30 s a 1 min); o botão **Começar** só libera depois. Até o clique, nada é gravado, publicado nem enviado ao Claude.
- [ ] **Confira a lista**, que a tela preenche sozinha:
  - **Som do computador:** o dispositivo capturado (a saída onde o g1 toca, com "[Loopback]") e um medidor. Dê play no g1 e veja o medidor subir;
  - **Credenciais do site:** "Site aceita envio" (push simulado, nada é enviado);
  - **Energia:** "Na tomada, em Melhor desempenho". Na bateria fica vermelho;
  - **Base de checagens:** quantas checagens e quando foi atualizada;
  - **Chave do Claude** presente;
  - **Publicação automática:** ligada (com o limiar) ou desligada (toda ligação passa por você);
  - **Aviso** laranja se faltar `data/vocabulario_globo.json`: a noite roda assim mesmo, com o vocabulário da Band.
- [ ] **Notebook reserva gravando** (`falas.py gravar`, ver o roteiro dele).
- [ ] **Clique Começar.** A captura, a transcrição, a base (a primeira atualização roda logo e leva uns 5 min) e o envio ao site ligam. A tela vira a mesa de sempre: a linha "O que fazer agora" diz o próximo passo e a faixa de estado tem uma luz por parte da noite (Áudio com o medidor, Análise, Site, Base e Publicação automática).
- [ ] As luzes de Áudio, Análise, Site e Base estão verdes. "Automática desligada" aparece como ajuste (círculo vazio), não como erro.
- [ ] Antes do 00:00, as falas ficam retidas e nada vai para o Claude. Isso é normal.

O atalho roda, na pasta do projeto:

```powershell
python -P scripts/iniciar.py noite
# = python -P scripts/noite.py ao-vivo --esperar-comecar --publicar --destino real --prompt "Debate presidencial na Globo, segundo turno. Lula e Flávio Bolsonaro." --vocabulario data/vocabulario_globo.json
```

Pelo terminal funciona como antes: sem `--esperar-comecar`, `noite.py ao-vivo --publicar ...` começa sozinho assim que os modelos carregam, e o Enter marca o 00:00.

## Durante o debate

- [ ] **00:00 na abertura do mediador:** o botão amarelo **Marcar 00:00** na tela (ou Enter na janela preta). Os dois marcam a sessão e a linha de falas. O botão só aparece depois do Começar.
  - Para corrigir, use "Remarcar 00:00" na tela (marca agora), ou digite `remarcar` e Enter na janela preta.
  - Se marcou atrasado, corrija para o horário certo:

    ```powershell
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/acao/inicio -ContentType application/json -Body '{"instante":"2026-10-23T21:20:05-03:00"}'
    ```

  - A publicação automática, se estiver ligada, só começa 10 min depois do 00:00.
- [ ] **Fila:**
  - para cada ligação, clique "Abrir a checagem" e digite no campo o selo que a página mostra;
  - aprove se é a mesma afirmação (mesmo sujeito, fato, número e período), sem julgar se é verdade; recuse, ou desmarque as checagens que não servem;
  - teclado na ligação marcada: A aprova, R recusa (pede confirmação), O abre a checagem, S vai ao selo, ↑ ↓ trocam de ligação; Enter no selo aprova;
  - retirar do site ("Corrigir rótulo ou retirar do site", em "No site agora") exige motivo, que vai para as correções.
- [ ] **Problemas:** uma luz vermelha diz o que fazer, e o problema aparece em texto simples acima da fila, com o detalhe técnico ao abrir. Laranja = atenção, sem ação urgente. O que fazer em cada um:

| problema | o que significa | o que fazer |
|---|---|---|
| Transcrição e áudio: "a linha de falas caiu" | O Whisper ou a captura caiu. A noite recomeça a linha sozinha em ~5 s, numa gravação nova em `data/sessao_globo/gravacoes/`, com o mesmo 00:00. | Nada, se o número de falas voltar a subir. Se cair de novo, veja a mensagem. Se for o dispositivo de áudio, confira a saída padrão. |
| Transcrição e áudio: "nenhuma fala há 180 s depois do 00:00" | Nada chega da linha. | O g1 parou, travou, está no mudo ou toca em outra saída. Volte o player. No intervalo comercial, as falas da propaganda saem como Mediação, então esse aviso não aparece. |
| API do Claude | A extração ou a confirmação de uma fala falhou (rede, sobrecarga). A fala fica sem análise, porque a sessão não tenta de novo. | Anote a minutagem. Se acontecer muito, confira a internet e o status da Anthropic. |
| API do Google (base) | Buscas da atualização falharam, ou o RSS do Fato ou Fake não respondeu. | Nada: a sessão segue com a base anterior, e a próxima atualização tenta de novo em 10 min. |
| Publicação | O push para o site falhou. O publicador tenta de novo a cada 15 s, sem perder mudanças. | Confira a internet. "Authentication failed": rode `publicador.py verifica` noutro terminal. |
| A tela diz "perdeu contato com o programa" | A janela preta fechou ou o programa travou. | Duplo clique no atalho de novo (abaixo). |

- [ ] **Se o programa travar ou a janela preta fechar:** duplo clique no mesmo atalho. A tela abre em **"Pronto para retomar"**, com a fila, o placar e o 00:00 de antes já à vista. Clique **Retomar** (um clique; não precisa marcar o 00:00 de novo). Ele:
  - reconstrói a sessão pelo registro (`data/sessao_globo/eventos.jsonl`);
  - termina as análises que ficaram pela metade;
  - segue com o mesmo 00:00, numa gravação nova;
  - não chama o Claude de novo para o que já está no registro e não republica.

  Fica sem análise ao vivo só o trecho em que o processo esteve fora. Ele está na gravação do notebook reserva.
- [ ] **CPU:** se o atraso crescer (as falas aparecem um bom tempo depois de ditas), feche o que puder. Não abra nada pesado.

## Depois do debate

- [ ] **Encerrar a noite**, depois das considerações finais: o botão no canto da banda da tela. Ele pede confirmação na própria tela (e diz quantas ligações ainda estão na fila) e faz a saída limpa:
  - para o áudio e fecha o WAV (as gravações ficam guardadas);
  - espera as análises em curso;
  - grava `data/sessao_globo/painel.json`;
  - faz o último envio.

  Espere a tela dizer **"Noite encerrada. Já pode fechar a janela do programa"** e conferir "Último envio ao site: feito". Só então feche a janela preta. (Ctrl+C na janela preta faz o mesmo.)
- [ ] **Fila pendente:** depois de encerrar, a tela não aprova mais. Para aprovar com calma depois, feche a janela preta e abra só a tela e o publicador sobre a mesma pasta, sem áudio. Nada é refeito.

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
2. **Duplo clique em `iniciar\Ensaio.cmd`.** Nada vai para o GitHub:
   - a faixa no alto é **roxa** ("Ensaio: cópia local do site");
   - o ensaio publica numa cópia descartável do site, montada sozinha em `%TEMP%\checagem-ao-vivo-ensaio`; a página do leitor dessa cópia fica em **http://127.0.0.1:8766/globo/** (o link está na faixa roxa);
   - a sessão fica em `data/ensaio/sessao_tela`, separada da noite. Abrir de novo retoma o ensaio; **`iniciar\Ensaio do zero.cmd`** guarda o ensaio anterior ao lado (`sessao_tela-AAAAMMDD-HHMMSS`) e recomeça, com a cópia do site refeita.

   Atenção: o ensaio chama o Claude de verdade depois do 00:00. Pelo ensaio seco, são ~8 chamadas por minuto de confronto livre (~13 mil tokens de entrada por minuto). A base também atualiza a cada 10 min, como na noite.
3. Confira a lista, dê play e veja o medidor de som subir. Clique **Começar** e, na abertura do mediador, **Marcar 00:00**.
4. Deixe tocar uns 20 min, com um trecho de confronto livre. Aprove uma ligação e veja-a na página do leitor da cópia.
5. No meio, feche a janela preta e dê duplo clique em `Ensaio.cmd` de novo: a tela deve abrir em "Pronto para retomar". Clique Retomar.
6. **Encerrar a noite** na tela. Meça e registre em `data/ensaio/relatorio.md`:
   - **atraso fim da fala → entrega:** está na janela preta como `(+N s)` e em `falas.jsonl`, campo `atraso_s`;
   - **acerto de texto e de quem fala:** `scripts/falas_medir.py` contra o Escriba, como no ticket 09;
   - **Mediação pelo som do computador:** confira o limiar 0,51 com a voz dos mediadores, a ressalva do ticket 08;
   - **reserva:** a gravação do notebook reserva tem o mesmo áudio.

Pelo terminal, o ensaio é `python -P scripts/iniciar.py ensaio [--do-zero] [--porta 8765] [--porta-site 8766]`.
