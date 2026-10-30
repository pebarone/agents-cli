---
name: delegate
description: >
  Send a message to another local AI agent harness (codex, gemini, claude, grok)
  and get the reply. Reuse that agent's existing session for follow-ups of the
  same task (context + prompt cache). Open a new session when the task is
  different. Use when the user runs /delegate, asks you to talk to another
  agent on this PC, wants Codex/Claude/Grok/Gemini to collaborate, or says
  "manda pro grok", "pergunta pro claude", "delega pro gemini".
---

# /delegate

You are one of several local agents. Each one runs in its own native harness.
You talk to the others through this CLI. Do not role-play the other harness.
Spawn it.

Canonical command (cmd, PowerShell, **and Git Bash**):

```text
python -u C:\Users\augus\Desktop\agents_cli\agents.py
```

Call that. Do **not** gate on `command -v agents` or `which agents`. On
Windows, Claude's Bash tool does not see `agents.cmd` (Codex/cmd does, via
PATHEXT). If `agents` works, it is only a shorthand.

```
/delegate [agent] [model] [effort] [task]
```

Agents: `codex` | `gemini` | `claude` | `grok`

If model or effort is omitted, the CLI uses that harness's default — or, on
a resumed session, the model and effort already bound to that session.

## Sessions — reuse vs new

You are the orchestrator. You decide whether this hop reuses a session.

**Reuse** when the new message is a follow-up of the same task: more of the
same work, a fix, "now do X on that", a question about the previous reply,
the same files and goal. Reuse keeps the other harness's context and prompt
cache.

**New session** when the task is different: other goal, other files, a new
user request, or you would not want the old context mixed in.

How:

1. First hop of a task — no `--conversation`:

```text
python -u C:\Users\augus\Desktop\agents_cli\agents.py delegate <agent> <model> <effort> --prompt "<task>"
```

2. Read `conversation=` from the `[agents]` line on stderr (or from `--json`).
   Keep that id for this task.
3. Follow-up of **that** task, same agent — pass the id, omit model and
   effort so the CLI resumes the same harness session with the same
   model/effort:

```text
python -u C:\Users\augus\Desktop\agents_cli\agents.py delegate <agent> --conversation <id> --prompt "<next message>"
```

4. Different task — do **not** pass `--conversation`. If you were yourself
   spawned by `agents` (`AGENTS_CONVERSATION` is set), also pass `--new` so
   you do not inherit the parent thread:

```text
python -u C:\Users\augus\Desktop\agents_cli\agents.py delegate <agent> <model> <effort> --new --prompt "<other task>"
```

Do not `--no-resume` on a follow-up. Do not paste the previous reply back
into the prompt when the session is being resumed; the other harness
already has it.

## Do this

1. Parse the slash command or the user's request into agent, optional model,
   optional effort, and the task text.
2. Decide reuse vs new (section above). Write the task to a file if it is
   long. Then run **one** of:

```text
python -u C:\Users\augus\Desktop\agents_cli\agents.py delegate <agent> <model> <effort> --prompt "<task>"
python -u C:\Users\augus\Desktop\agents_cli\agents.py delegate <agent> --conversation <id> --prompt "<follow-up>"
python -u C:\Users\augus\Desktop\agents_cli\agents.py delegate <agent> <model> <effort> --new --prompt "<other task>"
python -u C:\Users\augus\Desktop\agents_cli\agents.py delegate <agent> <model> <effort> --prompt-file <path>
```

3. The command blocks until the other harness prints its reply. Show that
   reply to the user. Quote it; do not rewrite it as if you did the work.
4. Same working directory as you. The other agent sees this repo.

Reply on the same thread:

```text
python -u C:\Users\augus\Desktop\agents_cli\agents.py reply --conversation <id> --prompt "<message>"
```

Mailbox (do not wait):

```text
python -u C:\Users\augus\Desktop\agents_cli\agents.py send <agent> --prompt "<message>"
python -u C:\Users\augus\Desktop\agents_cli\agents.py inbox --agent <you>
```

Status:

```text
python -u C:\Users\augus\Desktop\agents_cli\agents.py list
python -u C:\Users\augus\Desktop\agents_cli\agents.py doctor
python -u C:\Users\augus\Desktop\agents_cli\agents.py log
python -u C:\Users\augus\Desktop\agents_cli\agents.py log <conversation>
```

## Rules

- `gemini` launches Antigravity (`agy`) when it is installed, otherwise Gemini CLI.
- `codex`, `claude`, `grok` launch their own CLIs. Never route them through a proxy.
- Nested `/delegate` is allowed. Stop when `agents` says the depth limit was hit.
- Do not delegate to yourself.
- Do not ask the user to copy-paste between terminals. Run the python launcher.
- Do not run `install` just because `command -v agents` failed in Bash.
- Auto-approve is on for delegated runs so the other harness can work unattended.
  The user already has each CLI logged in.

## Examples

```text
/delegate grok grok-4.6 high review the diff and list bugs
/delegate claude sonnet xhigh implement the failing test
/delegate gemini gemini-3.8-flash high explore where auth tokens are stored
/delegate codex gpt-6.1-sol high apply the patch and run tests
```
