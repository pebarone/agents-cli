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
already has it. If that session is busy (a job still running in the same
conversation) or is your own, the CLI starts a fresh session with history and
prints `[agents] aviso: ...` on stderr.

## Your output is the reply

When another agent delegated to you, your final printed answer goes back to
it automatically. Just print the answer. Do **not** run `agents reply` or
`agents delegate` to answer the sender (`reply` refuses with exit 2). Delegate
only to a **different** agent for a sub-task, with `--new` if it is unrelated
to this conversation.

## Fan out (background + wait)

```text
python -u C:\Users\augus\Desktop\agents_cli\agents.py delegate <agent> --background --new --prompt "<task>"   # prints a job id
python -u C:\Users\augus\Desktop\agents_cli\agents.py wait <job> [<job> ...] [--timeout SEC] [--json]
```

Start several, then `wait` on all. One job prints its text; several print
`=== agent job (status) ===` blocks. Exit 0 only if all are ok, 1 if one
failed, 3 if the wait timed out. A job whose process died becomes `error`.

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

Long prompts: write them to a file and pass `--prompt-file <path>` (no quoting
problems). A free-text task such as `delegate codex fix the bug` works when the
first word does not look like a model (`sonnet`, `gpt-6.1-sol`...) or a file
(`README.md`, or a path that exists here). Prefer `--prompt` / `--prompt-file`:
with them the first loose argument is always the model.

Talk to the other side of a thread from outside a hop (not to answer the agent
waiting on you); use `--prompt=<text>` if the text starts with `-`:

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
python -u C:\Users\augus\Desktop\agents_cli\agents.py status
python -u C:\Users\augus\Desktop\agents_cli\agents.py log
python -u C:\Users\augus\Desktop\agents_cli\agents.py log <conversation> --tail 5
```

`status` shows the latest 10 conversations and jobs (`stale` = the process
died). `doctor` also prints a `codex (chrome e sessoes):` section (read-only
diagnosis of Codex driving Chrome and of `~/.codex/sessions`; informational,
never changes the exit code).

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
