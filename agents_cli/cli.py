from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from agents_cli import EFFORTS, __version__
from agents_cli.bus import (
    append_message,
    create_job,
    enqueue_inbox,
    get_or_create_conversation,
    job_dir,
    list_conversations,
    list_jobs,
    load_job,
    read_inbox,
    read_messages,
    remember_session,
    save_job,
    session_record,
)
from agents_cli.config import default_effort, default_model, load_config
from agents_cli.detect import whoami
from agents_cli.harness import build_launch, extract_text, output_is_error, probe, run_launch, which
from agents_cli.install import doctor_links, install, uninstall
from agents_cli.parse import ParseError, parse_tokens
from agents_cli.prompt import render_history, wrap_prompt


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ParseError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("erro: o agente excedeu o tempo limite", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("cancelado", file=sys.stderr)
        return 130


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agents",
        description="Bus local para Codex, Gemini, Claude e Grok conversarem entre si.",
    )
    parser.add_argument("--version", action="version", version=f"agents {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="mostra os agentes e se o harness esta no PATH")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("whoami", help="tenta detectar qual agente esta chamando")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("doctor", help="confere binarios, skill compartilhada e wrappers")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("install", help="instala a skill /delegate em todos os harnesses")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="remove junctions da skill e wrappers")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("delegate", help="envia uma mensagem e espera a resposta do harness nativo")
    p.add_argument("agent")
    p.add_argument("rest", nargs="*")
    p.add_argument("--model")
    p.add_argument("--effort", choices=list(EFFORTS))
    p.add_argument("--prompt")
    p.add_argument("--prompt-file")
    p.add_argument("--from", dest="from_agent")
    p.add_argument("--cwd")
    p.add_argument("--conversation")
    p.add_argument("--new", action="store_true", help="nova tarefa: sessao nova, sem resume")
    p.add_argument("--timeout", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--no-history", action="store_true")
    p.add_argument("--approve", choices=["auto", "prompt"], default="auto")
    p.set_defaults(func=cmd_delegate)

    p = sub.add_parser("send", help="enfileira uma mensagem sem esperar")
    p.add_argument("agent")
    p.add_argument("rest", nargs="*")
    p.add_argument("--model")
    p.add_argument("--effort", choices=list(EFFORTS))
    p.add_argument("--prompt")
    p.add_argument("--from", dest="from_agent")
    p.add_argument("--cwd")
    p.add_argument("--conversation")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("inbox", help="le mensagens pendentes")
    p.add_argument("--agent")
    p.add_argument("--consume", action="store_true")
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("reply", help="responde ao outro lado da conversa")
    p.add_argument("--conversation", required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument("--from", dest="from_agent")
    p.add_argument("--to")
    p.add_argument("--model")
    p.add_argument("--effort", choices=list(EFFORTS))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_reply)

    p = sub.add_parser("log", help="mostra o historico de uma conversa")
    p.add_argument("conversation", nargs="?")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("status", help="lista conversas e jobs recentes")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("wait", help="mostra o resultado de um job")
    p.add_argument("job")
    p.set_defaults(func=cmd_wait)

    return parser


def cmd_list(_args) -> int:
    config = load_config()
    for row in probe(config):
        mark = "ok" if row.found else "missing"
        path = row.path or "-"
        print(f"{row.agent:8} {mark:8} {path}")
    return 0


def cmd_whoami(_args) -> int:
    print(whoami())
    return 0


def cmd_doctor(_args) -> int:
    config = load_config()
    ok = True
    print("harnesses:")
    for row in probe(config):
        mark = "ok" if row.found else "MISSING"
        if not row.found:
            ok = False
        print(f"  {row.agent:8} {mark:8} {row.path or '-'}")
    print("skill /delegate:")
    for row in doctor_links():
        if row["exists"] and row["linked"]:
            mark = "ok"
        elif row["exists"]:
            mark = "present"
        else:
            mark = "MISSING"
            ok = False
        print(f"  {mark:8} {row['path']}")
    print(f"whoami: {whoami()}")
    return 0 if ok else 1


def cmd_install(_args) -> int:
    result = install()
    print(f"skill: {result['skill']}")
    for row in result["skills"]:
        print(f"  {row['action']:8} {row['path']}")
    for row in result["wrappers"]:
        print(f"  {row['action']:8} {row['path']}")
    print("abra de novo cada CLI para a skill /delegate aparecer.")
    return 0


def cmd_uninstall(_args) -> int:
    result = uninstall()
    if not result["removed"]:
        print("nada para remover")
        return 0
    for path in result["removed"]:
        print(f"removed  {path}")
    return 0


def cmd_delegate(args) -> int:
    config = load_config()
    request = _resolve_request(args, config, require_binary=not args.dry_run, fill_defaults=False)
    from_agent = args.from_agent or whoami()
    cwd = str(Path(args.cwd or os.getcwd()).resolve())
    depth = int(os.environ.get("AGENTS_DEPTH") or 0) + 1
    max_depth = int(config.get("max_depth") or 4)
    if depth > max_depth:
        print(f"erro: profundidade maxima de delegacao ({max_depth}) atingida", file=sys.stderr)
        return 4
    inherited = None if args.new else (args.conversation or os.environ.get("AGENTS_CONVERSATION"))
    conversation = get_or_create_conversation(inherited, cwd, from_agent)
    cid = conversation["id"]
    record = session_record(conversation, request.agent)
    session_id = None if (args.no_resume or args.new) else record.get("id")
    model = request.model or (record.get("model") if session_id else None) or default_model(request.agent, config)
    effort = request.effort or (record.get("effort") if session_id else None) or default_effort(request.agent, config)
    request = _Req(request.agent, model, effort, request.prompt)
    history = "" if args.no_history or session_id else render_history(read_messages(cid))
    body = history + request.prompt
    job = create_job(
        {
            "agent": request.agent,
            "model": request.model,
            "effort": request.effort,
            "from": from_agent,
            "conversation": cid,
            "cwd": cwd,
            "depth": depth,
            "status": "running" if not args.dry_run else "dry-run",
        }
    )
    wrapped = wrap_prompt(
        body=body,
        from_agent=from_agent,
        to_agent=request.agent,
        model=request.model,
        effort=request.effort,
        conversation=cid,
        job=job["id"],
        cwd=cwd,
        depth=depth,
        max_depth=max_depth,
        resume=session_id,
    )
    launch = build_launch(
        agent=request.agent,
        model=request.model,
        effort=request.effort,
        prompt=wrapped,
        cwd=cwd,
        job_dir=job_dir(job["id"]),
        config=config,
        from_agent=from_agent,
        conversation=cid,
        depth=depth,
        session_id=session_id,
        auto_approve=(args.approve == "auto") and bool(config.get("auto_approve", True)),
        allow_missing=args.dry_run,
    )
    append_message(
        cid,
        {
            "from": from_agent,
            "to": request.agent,
            "kind": "delegate",
            "job": job["id"],
            "text": request.prompt,
        },
    )
    _announce(cid, job["id"], session_id, request.model, request.effort)
    if args.dry_run:
        payload = {
            "conversation": cid,
            "job": job["id"],
            "agent": request.agent,
            "model": request.model,
            "effort": request.effort,
            "argv": launch.argv,
            "cwd": launch.cwd,
            "resume": session_id,
            "resumed": bool(session_id),
            "prompt_file": str(launch.prompt_file),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else _format_dry_run(payload))
        return 0

    timeout = args.timeout if args.timeout is not None else int(config.get("timeout_sec") or 900)
    completed = run_launch(launch, timeout)
    stdout_path = job_dir(job["id"]) / "stdout.txt"
    stderr_path = job_dir(job["id"]) / "stderr.txt"
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    text, session = extract_text(
        request.agent,
        completed.stdout or "",
        launch.last_message_file,
        stderr=completed.stderr or "",
    )
    failed = completed.returncode != 0 or output_is_error(completed.stdout or "")
    if failed:
        job["status"] = "error"
        job["returncode"] = completed.returncode or 1
        job["error"] = (text or completed.stderr or completed.stdout or "")[-4000:]
        save_job(job)
        print(job["error"], file=sys.stderr)
        return completed.returncode or 1
    remember_session(conversation, request.agent, session, request.model, request.effort)
    job["status"] = "ok"
    job["returncode"] = completed.returncode
    job["session"] = session
    job["text"] = text
    save_job(job)
    if session:
        print(f"[agents] session={session} cwd={cwd}", file=sys.stderr)
    append_message(
        cid,
        {
            "from": request.agent,
            "to": from_agent,
            "kind": "reply",
            "job": job["id"],
            "session": session,
            "text": text,
        },
    )
    if args.json:
        print(
            json.dumps(
                {
                    "conversation": cid,
                    "job": job["id"],
                    "agent": request.agent,
                    "model": request.model,
                    "effort": request.effort,
                    "session": session,
                    "resumed": bool(session_id),
                    "returncode": completed.returncode,
                    "text": text,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(text)
    return 0


def cmd_send(args) -> int:
    config = load_config()
    request = _resolve_request(args, config, require_binary=False)
    from_agent = args.from_agent or whoami()
    cwd = str(Path(args.cwd or os.getcwd()).resolve())
    conversation = get_or_create_conversation(args.conversation, cwd, from_agent)
    payload = {
        "from": from_agent,
        "to": request.agent,
        "model": request.model,
        "effort": request.effort,
        "cwd": cwd,
        "conversation": conversation["id"],
        "text": request.prompt,
    }
    append_message(conversation["id"], {**payload, "kind": "send"})
    enqueue_inbox(request.agent, payload)
    print(conversation["id"])
    return 0


def cmd_inbox(args) -> int:
    agent = args.agent or whoami()
    if agent == "unknown":
        print("erro: passe --agent", file=sys.stderr)
        return 2
    rows = read_inbox(agent, consume=args.consume)
    if not rows:
        return 0
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def cmd_reply(args) -> int:
    conversation = get_or_create_conversation(args.conversation, os.getcwd(), args.from_agent or whoami())
    from_agent = args.from_agent or whoami()
    target = args.to
    if not target:
        for name in reversed(conversation.get("participants") or []):
            if name and name not in {from_agent, "unknown"}:
                target = name
                break
    if not target:
        print("erro: nao sei para quem responder; passe --to", file=sys.stderr)
        return 2
    argv = ["delegate", target]
    if args.model:
        argv.append(args.model)
    if args.effort:
        argv.append(args.effort)
    extra = [
        "--conversation",
        conversation["id"],
        "--from",
        from_agent,
        "--prompt",
        args.prompt,
    ]
    if args.dry_run:
        extra.append("--dry-run")
    if args.json:
        extra.append("--json")
    return main(argv + extra)


def cmd_log(args) -> int:
    if not args.conversation:
        rows = list_conversations()
        if not rows:
            print("nenhuma conversa")
            return 0
        for meta in rows:
            print(f"{meta.get('id')}  cwd={meta.get('cwd')}  people={','.join(meta.get('participants') or [])}")
        return 0
    messages = read_messages(args.conversation)
    if not messages:
        print("conversa vazia")
        return 0
    for item in messages:
        speaker = item.get("from") or "?"
        print(f"[{item.get('ts','')}] {speaker}:")
        print((item.get("text") or "").rstrip())
        print()
    return 0


def cmd_status(_args) -> int:
    print("conversas:")
    convos = list_conversations()
    if not convos:
        print("  (nenhuma)")
    for meta in convos[-10:]:
        print(f"  {meta.get('id')}  {','.join(meta.get('participants') or [])}")
    print("jobs:")
    jobs = list_jobs()[:10]
    if not jobs:
        print("  (nenhum)")
    for job in jobs:
        print(f"  {job.get('id')}  {job.get('status')}  {job.get('from')} -> {job.get('agent')}")
    return 0


def cmd_wait(args) -> int:
    job = load_job(args.job)
    text = job.get("text") or ""
    print(text if text else json.dumps(job, indent=2, ensure_ascii=False))
    return 0 if job.get("status") == "ok" else 1


class _Req:
    def __init__(self, agent, model, effort, prompt):
        self.agent = agent
        self.model = model
        self.effort = effort
        self.prompt = prompt


def _resolve_request(args, config, require_binary: bool = True, fill_defaults: bool = True) -> _Req:
    parsed = parse_tokens([args.agent, *args.rest])
    prompt = args.prompt
    if getattr(args, "prompt_file", None):
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    if prompt is None:
        prompt = parsed.prompt
    if not (prompt or "").strip():
        raise ParseError("falta o prompt / a mensagem")
    agent = parsed.agent
    model = args.model or parsed.model
    effort = args.effort or parsed.effort
    if fill_defaults:
        model = model or default_model(agent, config)
        effort = effort or default_effort(agent, config)
    if require_binary and not which(agent, config):
        raise FileNotFoundError(f"harness de {agent} nao encontrado. rode: agents doctor")
    return _Req(agent, model, effort, prompt.strip())


def _announce(cid: str, job_id: str, session_id: str | None, model: str, effort: str) -> None:
    resumed = "yes" if session_id else "no"
    print(
        f"[agents] conversation={cid} job={job_id} resumed={resumed} "
        f"model={model} effort={effort}",
        file=sys.stderr,
    )


def _format_dry_run(payload: dict) -> str:
    argv = " ".join(_quote(part) for part in payload["argv"])
    return (
        f"conversation: {payload['conversation']}\n"
        f"job: {payload['job']}\n"
        f"resume: {payload['resume'] or 'new'}\n"
        f"model: {payload.get('model')}\n"
        f"effort: {payload.get('effort')}\n"
        f"cwd: {payload['cwd']}\n"
        f"cmd: {argv}\n"
        f"prompt_file: {payload['prompt_file']}\n"
    )


def _quote(part: str) -> str:
    if not part or any(ch.isspace() for ch in part) or "&" in part:
        return '"' + part.replace('"', '\\"') + '"'
    return part
