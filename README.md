# Agents CLI

Bus local para Codex, Gemini, Claude e Grok conversarem entre si. Cada um continua no harness nativo. A skill `/delegate` e a mesma nos quatro.

```
/delegate [agent] [model] [effort] [tarefa]
```

## Instalar

```powershell
cd $env:USERPROFILE\Desktop\agents_cli
.\install.ps1
```

Isso:

1. Cria junctions da skill `delegate` nas pastas de skill do Grok, Claude, Codex e Gemini
2. Grava `agents.cmd` em `%USERPROFILE%\.local\bin` e em `%USERPROFILE%\.agents\bin`

Reabra cada CLI para `/delegate` aparecer.

Confira:

```powershell
python agents.py doctor
python agents.py list
```

## Uso

De qualquer agente (ou do terminal):

```powershell
agents delegate grok grok-4.6 high --prompt "revise o diff e liste bugs"
agents delegate claude sonnet xhigh --prompt "implemente o teste que falha"
agents delegate gemini gemini-3.8-flash high --prompt "onde o token de auth e gravado?"
agents delegate codex gpt-6.1-sol high --prompt "aplique o patch e rode os testes"
```

Forma slash, dentro de uma sessao que ja carregou a skill:

```
/delegate grok grok-4.6 high revise o diff
/delegate claude sonnet high implemente o teste
```

O agente que le o `/delegate` deve **rodar** `agents delegate ...`, nao fingir a resposta do outro.

Texto livre (sem `--prompt`) funciona quando a primeira palavra nao parece um modelo nem um arquivo: `delegate codex fix the bug` manda "fix the bug" com o modelo padrao. Palavras como `gpt-6.1-sol`, `sonnet` ou `x/y` viram modelo; nomes de arquivo (`README.md`, `main.py`, algo que existe na pasta atual) nao. Na duvida, prefira `--prompt`/`--prompt-file` (nesse caso o primeiro argumento solto sempre e o modelo).

Prompts longos: `--prompt-file caminho.txt` (evita problema de aspas).

Continuar a mesma tarefa (reusa a sessao do harness, contexto e prompt cache).
O id vem da linha `[agents] conversation=...` no stderr:

```powershell
agents delegate claude --conversation c0a1b2c3d4e5 --prompt "agora cubra o caso vazio"
agents log c0a1b2c3d4e5 --tail 5
```

Se a sessao do agente ja esta ocupada (job `running` na mesma conversa) ou e a propria sessao que esta rodando, a CLI nao faz resume: abre sessao nova com o historico e avisa em `[agents] aviso: ...`. Delegacoes paralelas ao mesmo agente na mesma conversa sao serializadas por um lock: so uma retoma a sessao, as outras abrem sessao nova.

Se o harness disser que a sessao salva nao existe mais (por exemplo `no rollout found` do Codex), a CLI avisa, esquece essa sessao e refaz a chamada uma vez em sessao nova com o historico.

**A resposta final do agente ja volta sozinha** para quem delegou. Quem recebe uma tarefa so imprime a resposta; nao roda `agents reply` nem `agents delegate` para o remetente (`reply` recusa com erro quando o alvo e quem esta esperando). `agents reply --conversation <id> --prompt=<texto>` serve para falar com o outro lado de fora de um hop (use `--prompt=` se o texto comeca com `-`).

Tarefa diferente — sessao nova. Se voce ja esta dentro de um hop (`AGENTS_CONVERSATION` setado), passe `--new`:

```powershell
agents delegate gemini --new --prompt "explore outro modulo"
```

Varios agentes em paralelo (fan-out): `--background` cria o job, dispara um processo separado e imprime so o id do job. `wait` espera um ou mais:

```powershell
$a = agents delegate claude --background --new --prompt "revise src/a"
$b = agents delegate grok --background --new --prompt "revise src/b"
agents wait $a $b            # blocos "=== agente job (status) ==="
agents wait $a --json        # lista de objetos
```

`wait` aceita `--timeout SEC` (padrao: `timeout_sec` da config + 60) e `--interval`. Sai com 0 se todos ok, 1 se algum falhou, 3 se o `wait` estourou o tempo. Job `running`/`queued` cujo processo morreu vira `error` ("processo do agente morreu"). Log do filho em `~/.agents/bus/jobs/<job>/agents.log`.

Fila, sem esperar:

```powershell
agents send gemini --prompt "explore auth"
agents inbox --agent gemini          # linhas "[ts] de -> para: texto"
agents inbox --agent gemini --json
```

Estado:

```powershell
agents status                        # ultimas 10 conversas e 10 jobs (idade; `stale` = processo morto)
agents log                           # lista conversas
agents log c0a1b2c3d4e5 --tail 5     # ultimas 5 mensagens (--json disponivel)
```

## Como a mensagem viaja

1. Voce (ou um agente) chama `agents delegate claude ...`
2. A CLI grava a mensagem em `~/.agents/bus/conversations/<id>/`
3. Dispara o CLI nativo em modo print (`claude -p`, `grok --prompt-file`, `agy -p`, `codex exec`)
4. Captura a resposta, guarda o session id do harness, devolve o texto
5. Recado seguinte para o mesmo agente tenta `--resume` nessa sessao

O job guarda `pid` e `started_at` e sempre termina em `ok`, `error`, `timeout` ou `cancelled`; falhas tambem viram uma mensagem `error` na conversa (e, com `--json`, um objeto JSON com `status`/`error`).

O envelope da mensagem diz ao destino que a resposta final volta sozinha e que so deve delegar para um agente DIFERENTE (`--new` se for assunto separado). Por isso a skill precisa estar instalada nos quatro.

O bus (`~/.agents/bus`) usa lock de arquivo entre processos (mensagens, inbox, meta da conversa), entao agentes em paralelo nao perdem escrita. Ids de conversa/job/inbox so aceitam `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`. Historico injetado em sessao nova: no maximo 4000 caracteres por mensagem e 24000 no total.

## Padroes

| Agente | Harness | Modelo padrao | Esforco padrao |
| --- | --- | --- | --- |
| `grok` | `grok.exe` | `grok-4.6` | `high` |
| `claude` | `claude.exe` | `sonnet` | `high` |
| `gemini` | `agy.exe` (senao `gemini`) | `gemini-3.8-flash` | `high` |
| `codex` | `codex exec` | `gpt-6.1-sol` | `high` |

Aliases: `agy`/`antigravity` → gemini, `chatgpt` → codex.

Config opcional: `%USERPROFILE%\.agents\config.json`.

`agents doctor` tambem imprime a secao `codex (chrome e sessoes):` (diagnostico somente leitura da ponte Codex + Chrome e da pasta `~/.codex/sessions`; a linha `sessoes do codex` falha quando ela e um link para um drive desmontado, o que quebra o resume). Essas linhas sao informativas e nao mudam o codigo de saida.

## Testes

```powershell
python -m unittest discover -s tests -v
```

## O que isto nao e

Nao e o Mix do Codex (proxy de modelos). Aqui cada conversa entra no app/CLI de verdade daquele agente.
