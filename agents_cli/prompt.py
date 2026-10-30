from __future__ import annotations

from agents_cli import PROTOCOL
from agents_cli.home import cli_command


SESSION_VALUE = "(da sessao)"  # model/effort left to the resumed session

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
Answer by doing the work. Your final printed answer is delivered back to the sender
automatically. Do NOT run `agents reply` or `agents delegate` to answer the sender;
just print the answer.

Use the bus only to reach a DIFFERENT agent for a sub-task (Git Bash on Windows
cannot see agents.cmd; do not gate on `command -v agents`):
  {cli} delegate <agent> [model] [effort] --prompt "..."
- Sub-task unrelated to this conversation: add --new.
- Long text: write it to a file and use --prompt-file <path>.
- Fan out: add --background (prints a job id), then `{cli} wait <job> [<job> ...]`.

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
    model: str | None,
    effort: str | None,
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
        model=model or SESSION_VALUE,
        effort=effort or SESSION_VALUE,
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


TRUNCATED = "[... truncado ...]"


def _clip(body: str, max_chars: int) -> str:
    if max_chars <= 0 or len(body) <= max_chars:
        return body
    head = max_chars * 2 // 3
    tail = max_chars - head
    return f"{body[:head].rstrip()}\n{TRUNCATED}\n{body[-tail:].lstrip()}"


def render_history(
    messages: list[dict],
    limit: int = 20,
    max_message_chars: int = 4000,
    max_total_chars: int = 24000,
) -> str:
    """Earlier messages for a session that is not resumed. Each message is capped
    at `max_message_chars`; the block keeps the most recent messages that fit in
    `max_total_chars`."""
    if not messages:
        return ""
    blocks = []
    for item in messages[-limit:]:
        speaker = item.get("from") or item.get("agent") or "unknown"
        body = (item.get("text") or "").strip()
        if not body:
            continue
        blocks.append(f"[{speaker}]\n{_clip(body, max_message_chars)}\n")
    if not blocks:
        return ""
    kept: list[str] = []
    used = 0
    for block in reversed(blocks):
        if kept and max_total_chars > 0 and used + len(block) > max_total_chars:
            break
        kept.append(block)
        used += len(block)
    kept.reverse()
    lines = ["--- earlier messages in this conversation ---"]
    if len(kept) < len(blocks):
        lines.append(TRUNCATED)
    lines.extend(kept)
    lines.append("--- end of earlier messages ---")
    lines.append("")
    return "\n".join(lines)
