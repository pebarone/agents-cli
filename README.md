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

Texto livre (sem `--prompt`) funciona quando a primeira palavra nao parece um modelo nem um arquivo: `delegate codex fix the bug` manda "fix the bug" com o modelo padrao. Palavras como `gpt-6.1-sol`, `sonnet` ou `x/y` viram modelo; palavras so com letras e hifen (`corrija-o`), numero puro (`3`) e nomes de arquivo (`README.md`, `main.py`, algo que existe na pasta do `--cwd` ou na atual) nao. Na duvida, prefira `--prompt`/`--prompt-file` (nesse caso o primeiro argumento solto sempre e o modelo).

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

## Retomar uma sessao que voce ja tem (por nome)

Sessoes criadas fora do bus (nomeadas no app do Codex, com `/rename` no Claude, etc.) podem ser continuadas pelo nome ou pelo id:

```powershell
agents sessions codex                       # lista, mais recentes primeiro (--limit 20, --all inclui arquivadas, --json)
agents sessions codex fiscal                # filtra por nome, id ou pasta (sem acento, sem maiusculas)
agents resume codex "Revisao Fiscal" --prompt "continue de onde parou"
agents resume session claude "Nome da Sessao" --prompt-file pedido.txt
```

Forma slash: `/delegate resume [session] <agente> "<nome>" [mensagem]` vira `agents resume <agente> "<nome>" --prompt "<mensagem>"` (`agents delegate resume ...` tambem funciona). A mensagem entra so por `--prompt` ou `--prompt-file`: palavras soltas depois do agente fazem parte do nome (`agents resume codex revisao fiscal` procura "revisao fiscal").

- Cada harness le o proprio armazenamento, so para leitura: Codex (`~/.codex/state_*.sqlite`), Claude (`~/.claude/projects`), Grok (`~/.grok/sessions`) e Antigravity (`~/.gemini/antigravity-cli`). O Gemini CLI (`gemini.cmd`) nao retoma por id: use o `agy`.
- O nome casa, nesta ordem, por: id exato; nome exato (sem acento, sem maiusculas); prefixo unico do id, so para texto com cara de id hexadecimal (6+ caracteres); e trecho do nome. Nome repetido, ou trecho que casa com mais de uma sessao, vale a unica que for retomavel. Se ainda sobrar mais de uma, a CLI lista ate 10 candidatas (id, data, nome, pasta) e pede o id: `agents resume codex 01a18cd4 --prompt "..."`.
- Sem `--prompt`, `resume` so liga a sessao a uma conversa do bus (imprime o id da conversa; as proximas vezes reusa a mesma conversa). Siga com `agents delegate <agente> --conversation <id> --prompt "..."`.
- A sessao roda na pasta em que foi criada (`--cwd` troca). Modelo e esforco continuam os da sessao, a menos que voce passe `--model`/`--effort`.
- A sessao do usuario nunca e trocada por uma nova: se o harness nao conseguir retoma-la, o job falha com `a sessao "<nome>" (<id>) nao pode ser retomada pelo <agente>: <motivo>`; se ela estiver ocupada por outro job, tambem falha. Sessoes marcadas `[nao retomavel: ...]` nao podem ser continuadas. No Codex, sessoes criadas enquanto `~/.codex/sessions` nao podia ser gravado nao tem rollout neste PC e aparecem como `nao retomavel`.
- Nao use a mesma sessao ao mesmo tempo no app/TUI do Codex (ou no Claude/Grok interativo) e pelo bus.
- Sessoes criadas pelo bus aparecem em `agents sessions` como `[bus] <primeira linha da mensagem>`, sem o cabecalho do protocolo.

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
