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

Continuar a mesma tarefa (reusa a sessao do harness, contexto e prompt cache).
O id vem da linha `[agents] conversation=...` no stderr:

```powershell
agents delegate claude --conversation c0a1b2c3d4e5 --prompt "agora cubra o caso vazio"
agents reply --conversation c0a1b2c3d4e5 --prompt "os testes passaram"
agents log c0a1b2c3d4e5
```

Tarefa diferente — sessao nova. Se voce ja esta dentro de um hop (`AGENTS_CONVERSATION` setado), passe `--new`:

```powershell
agents delegate gemini --new --prompt "explore outro modulo"
```

Fila, sem esperar:

```powershell
agents send gemini --prompt "explore auth"
agents inbox --agent gemini
```

## Como a mensagem viaja

1. Voce (ou um agente) chama `agents delegate claude ...`
2. A CLI grava a mensagem em `~/.agents/bus/conversations/<id>/`
3. Dispara o CLI nativo em modo print (`claude -p`, `grok --prompt-file`, `agy -p`, `codex exec`)
4. Captura a resposta, guarda o session id do harness, devolve o texto
5. Recado seguinte para o mesmo agente tenta `--resume` nessa sessao

O envelope da mensagem ensina o destino a responder com `agents delegate` ou `agents reply`. Por isso a skill precisa estar instalada nos quatro.

## Padroes

| Agente | Harness | Modelo padrao | Esforco padrao |
| --- | --- | --- | --- |
| `grok` | `grok.exe` | `grok-4.6` | `high` |
| `claude` | `claude.exe` | `sonnet` | `high` |
| `gemini` | `agy.exe` (senao `gemini`) | `gemini-3.8-flash` | `high` |
| `codex` | `codex exec` | `gpt-6.1-sol` | `high` |

Aliases: `agy`/`antigravity` → gemini, `chatgpt` → codex.

Config opcional: `%USERPROFILE%\.agents\config.json`.

## Testes

```powershell
python -m unittest discover -s tests -v
```

## O que isto nao e

Nao e o Mix do Codex (proxy de modelos). Aqui cada conversa entra no app/CLI de verdade daquele agente.
