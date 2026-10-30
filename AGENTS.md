# Agents CLI

Mesa de trabalho do bus local. Fale com o usuario em portugues. Nao faca commit, push nem pull request.

Este diretorio e o codigo do `agents`. Os harnesses (Codex, Gemini/Antigravity, Claude, Grok) continuam nos binarios de cada um. Aqui so mora o protocolo, a CLI e a skill `/delegate`.

## Como funciona

`/delegate [agent] [model] [effort] [tarefa]` vira `agents delegate ...`.
A CLI dispara o harness nativo em modo print, grava a conversa em `%USERPROFILE%\.agents\bus` e devolve a resposta.

A skill compartilhada e `skills/delegate/SKILL.md`. `agents install` cria junctions dessa pasta em:

- `~/.agents/skills/delegate`
- `~/.claude/skills/delegate`
- `~/.codex/skills/delegate`
- `~/.gemini/config/skills/delegate`
- `~/.gemini/skills/delegate`
- `~/.grok/skills/delegate`

Nao copie a skill. Edite o original e reinstale se o junction quebrar.

## Regras

- Cada agente usa o proprio CLI. Nao encaminhe Grok pelo proxy do Mix, nem Claude pelo Antigravity, so porque o modelo existe la.
- `gemini` prefere `agy.exe` quando existe.
- Follow-up da mesma tarefa: `--conversation <id>`, sem repetir model/effort, para retomar a sessao do harness (contexto + cache).
- Tarefa diferente: nao passe `--conversation`. Se `AGENTS_CONVERSATION` estiver setado, passe `--new`.
- Delegacao unattended usa auto-approve do harness alvo. Nao grave token no bus.
- Profundidade maxima padrao: 4. Nao aumente sem o usuario pedir.
- Testes: `python -m unittest discover -s tests -v`

## Layout

| O que | Onde |
| --- | --- |
| CLI | `agents.py`, `agents_cli/` |
| Skill | `skills/delegate/SKILL.md` |
| Bus | `~/.agents/bus` |
| Config | `~/.agents/config.json` |
| Wrapper | `~/.local/bin/agents.cmd` |
