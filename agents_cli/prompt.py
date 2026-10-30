from __future__ import annotations

from agents_cli import PROTOCOL
from agents_cli.home import cli_command


HEADER = """[agents-bus protocol={protocol}]
from: {from_agent}
to: {to_agent}
model: {model}
effort: {effort}
conversation: {conversation}
job: {job}
depth: {depth}/{max_depth}
cwd: {cwd}
resume: {resume}

You are a local coding agent receiving a message from another agent on this PC.
Each agent runs in its own native harness. You share the working directory above.
Answer by doing the work. Your printed reply is the message that goes back to the sender.

To send a message to another local agent (or back to the sender), run this command
(Git Bash on Windows cannot see agents.cmd; do not gate on `command -v agents`):
  {cli} delegate <agent> [model] [effort] --conversation {conversation} --prompt "..."
  {cli} reply --conversation {conversation} --prompt "..."

Slash form inside a session that already loaded the shared skill:
  /delegate <agent> <model> <effort> <task>

Do not impersonate the other harness. Do the work with YOUR tools.
{resume_rule}
{depth_rule}
--- message ---
"""


def wrap_prompt(
    *,
    body: str,
    from_agent: str,
    to_agent: str,
    model: str,
    effort: str,
    conversation: str,
    job: str,
    cwd: str,
    depth: int,
    max_depth: int,
    resume: str | None,
) -> str:
    if depth >= max_depth:
        depth_rule = (
            f"Delegation depth is at the maximum ({max_depth}). "
            "Do the remaining work yourself. Do not call `agents delegate`."
        )
    else:
        remaining = max_depth - depth
        depth_rule = (
            f"You may delegate further ({remaining} hop(s) left). "
            "Prefer doing the work yourself when you already have the right harness. "
            f"Invoke the bus with `{cli_command()}`, not `command -v agents`."
        )
    if resume:
        resume_rule = (
            "This is a follow-up in an existing harness session. "
            "Prior turns are already in your context (and prompt cache). "
            "Continue from where you left off. Do not restart the task. "
            "Do not ask the sender to paste previous output."
        )
    else:
        resume_rule = (
            "This is a new session. Work from the message below. "
            "Do not assume prior delegated work unless it is in the message."
        )
    header = HEADER.format(
        protocol=PROTOCOL,
        from_agent=from_agent,
        to_agent=to_agent,
        model=model,
        effort=effort,
        conversation=conversation,
        job=job,
        depth=depth,
        max_depth=max_depth,
        cwd=cwd,
        resume=resume or "new",
        resume_rule=resume_rule,
        depth_rule=depth_rule,
        cli=cli_command(),
    )
    return header + body.strip() + "\n"


def render_history(messages: list[dict], limit: int = 20) -> str:
    if not messages:
        return ""
    slice_ = messages[-limit:]
    lines = ["--- earlier messages in this conversation ---"]
    for item in slice_:
        speaker = item.get("from") or item.get("agent") or "unknown"
        body = (item.get("text") or "").strip()
        if not body:
            continue
        lines.append(f"[{speaker}]")
        lines.append(body)
        lines.append("")
    lines.append("--- end of earlier messages ---")
    lines.append("")
    return "\n".join(lines)
