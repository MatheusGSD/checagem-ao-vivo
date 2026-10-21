# Notebook reserva: só grava o som do debate

O segundo notebook não transcreve nem checa nada. Ele grava, de forma independente, o mesmo áudio que o notebook principal captura. Se o principal falhar, essa gravação vira a entrada do modo pós-debate (`noite.py pos-debate`).

## Instalação mínima (uma vez, antes de sexta)

1. **Python 3.11 ou mais novo** para Windows, de python.org. Marque "Add python.exe to PATH". O notebook principal usa o 3.14.
2. **Pacotes** (só os do loopback; nada de torch, Whisper ou modelos):

   ```powershell
   python -m pip install --user pyaudiowpatch soxr soundfile numpy
   ```

3. **Arquivos:**
   - clone o repositório; ou
   - copie só `scripts/falas.py` e `scripts/vozes.py` para uma pasta. O `falas.py` importa o `vozes.py` no começo, e o comando `gravar` não usa mais nada.

## Teste (antes de sexta e de novo na sexta à tarde)

Toque qualquer coisa em volume baixo (um vídeo, por exemplo) e rode:

```powershell
python -P scripts/falas.py gravar --gravar teste-reserva.wav --segundos 10
```

Confira:

- O `teste-reserva.json` ao lado do WAV mostra o `dispositivo`. Ele deve ser a saída onde o g1 vai tocar, com "[Loopback]" no nome. Se não for, troque a saída padrão do Windows ou passe `--dispositivo N`.
- `segundos_gravados` deve ficar perto de 10.
- Ouça o WAV. Tem que ter o som que tocou.
- `silencio_inserido_s` alto só é normal se nada tocou. Quando nada toca, o Windows não entrega pacotes e o script preenche com silêncio para manter o relógio.

**Testado nesta máquina** (notebook principal, 20/10/2026):

- 6 s de gravação no dispositivo "Speakers (Realtek(R) Audio) [Loopback]", 48 kHz estéreo convertido para 16 kHz mono;
- um tom de 440 Hz a -40 dBFS, tocado por 1,5 s no meio, apareceu gravado a ~-44 dB RMS e com pico em 440 Hz;
- o resto foi silêncio inserido, porque nada mais tocava.

O notebook reserva em si ainda não foi testado: o dispositivo padrão dele pode ser outro.

## Na sexta

1. **Energia e sistema:**
   - notebook na tomada;
   - suspensão em "Nunca";
   - notificações do Windows desligadas ("Não perturbe");
   - nada mais tocando nele.
2. **Por volta das 21h10:** abra a transmissão do g1 nesse notebook, na saída padrão, e comece a gravar:

   ```powershell
   python -P scripts/falas.py gravar --gravar reserva-globo-2026-10-23.wav
   ```

3. **Anote a hora exata da abertura do mediador** (o 00:00). O `.json` guarda a hora em que a gravação começou (`comecou_em`). Com as duas horas dá para calcular o 00:00 em segundos da gravação.
4. **Depois das considerações finais:** Ctrl+C. O script fecha o WAV e grava `terminou_em` e `segundos_gravados` no `.json`.

**Espaço em disco:** 16 kHz mono de 16 bits dá ~115 MB por hora. Reserve 1 GB.

## Se o notebook principal falhar

1. Copie o WAV e o `.json` da reserva para o notebook principal.
2. Rode o pós-debate. `S` é o segundo da gravação em que o mediador abriu o debate: ouça o começo do WAV, ou faça (hora anotada do 00:00) − `comecou_em`.

   ```powershell
   python -P scripts/noite.py pos-debate reserva-globo-2026-10-23.wav --zero S --publicar
   ```

Antes, feche a janela preta da noite (o atalho `Noite do debate.cmd` usa a mesma pasta e a mesma porta).
Se a sessão da noite já tem falas, o pós-debate continua da última fala entregue. Sem `--pasta`, ele usa `data/sessao_globo`, a mesma pasta da noite.
