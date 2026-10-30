from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from agents_cli import ALIASES, EFFORTS, __version__, codex_chrome
from agents_cli.bus import (
    add_participant,
    append_message,
    bind_external_session,
    create_job,
    conversation_dir,
    enqueue_inbox,
    ensure_bus,
    ensure_conversation,
    file_lock,
    find_conversation_for_session,
    forget_session,
    job_age_sec,
    job_dir,
    job_is_stale,
    list_conversations,
    list_jobs,
    load_conversation,
    load_job,
    now_iso,
    read_inbox,
    read_messages,
    reap_job,
    remember_session,
    running_jobs,
    session_record,
    update_job,
    validate_id,
)
from agents_cli.config import default_effort, default_model, load_config
from agents_cli.detect import whoami
from agents_cli.harness import (
    build_launch,
    extract_text,
    failure_text,
    output_is_error,
    probe,
    resume_missing,
    run_launch,
    which,
)
from agents_cli.harness_sessions import (
    HarnessSession,
    find_session,
    format_when,
    list_sessions,
    matches_filter,
)
from agents_cli.home import launcher_path
from agents_cli.install import doctor_links, install, uninstall
from agents_cli.parse import ParseError, normalize_agent, parse_tokens
from agents_cli.prompt import SESSION_VALUE, render_history, wrap_prompt

LIVE = ("queued", "running")
CORRUPT_GRACE_SEC = 10.0


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == "delegate" and argv[1] in ("resume", "sessions"):
        argv = argv[1:]  # `/delegate resume ...` is `agents resume ...`
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
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2


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

    p = sub.add_parser("doctor", help="confere binarios, skill compartilhada, wrappers e codex chrome")
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
    p.add_argument("--background", action="store_true", help="dispara em segundo plano e imprime o id do job")
    p.add_argument("--job", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_delegate)

    p = sub.add_parser("sessions", help="lista as sessoes que um harness ja tem (nomeadas ou nao)")
    p.add_argument("agent")
    p.add_argument("filter", nargs="*", help="filtra por nome, id ou pasta")
    p.add_argument("--limit", type=int, default=20, help="maximo de linhas (0 = todas)")
    p.add_argument("--all", action="store_true", help="inclui sessoes arquivadas")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser("resume", help="continua uma sessao existente de um harness, pelo nome ou id")
    p.add_argument("target", nargs="+", metavar="[session] agent nome-ou-id")
    p.add_argument("--prompt", help="use --prompt=<texto> se o texto comecar com '-'")
    p.add_argument("--prompt-file")
    p.add_argument("--model")
    p.add_argument("--effort", choices=list(EFFORTS))
    p.add_argument("--from", dest="from_agent")
    p.add_argument("--cwd")
    p.add_argument("--timeout", type=int)
    p.add_argument("--json", action="store_true")
    p.add_argument("--background", action="store_true", help="dispara em segundo plano e imprime o id do job")
    p.add_argument("--approve", choices=["auto", "prompt"])
    p.set_defaults(func=cmd_resume)

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
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("reply", help="responde ao outro lado da conversa (nao use para responder a quem esta esperando)")
    p.add_argument("--conversation", required=True)
    p.add_argument("--prompt", required=True, help="use --prompt=<texto> se o texto comecar com '-'")
    p.add_argument("--from", dest="from_agent")
    p.add_argument("--to")
    p.add_argument("--model")
    p.add_argument("--effort", choices=list(EFFORTS))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_reply)

    p = sub.add_parser("log", help="mostra o historico de uma conversa")
    p.add_argument("conversation", nargs="?")
    p.add_argument("--tail", type=int, metavar="N", help="so as ultimas N entradas")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("status", help="lista conversas e jobs recentes")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("wait", help="espera um ou mais jobs terminarem e mostra o resultado")
    p.add_argument("jobs", nargs="+", metavar="job")
    p.add_argument("--timeout", type=int, help="segundos (padrao: timeout_sec da config + 60)")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--json", action="store_true")
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
    # Informational only: never changes the exit code.
    try:
        rows = codex_chrome.diagnose()
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break doctor
        rows = [{"name": "diagnose", "status": "fail", "detail": f"erro: {exc}"}]
    if rows:
        print("codex (chrome e sessoes):")
        for row in rows:
            print(f"  {str(row.get('status', '')):8} {row.get('name', '')}: {row.get('detail', '')}")
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


# --- delegate ---------------------------------------------------------------


def _plan_session(args, conversation: dict, request, config: dict, exclude_job: str | None):
    """Decide resume vs fresh session. Returns (session_id, model, effort, warning)."""
    cid = conversation["id"]
    record = session_record(conversation, request.agent)
    session_id = None if (args.no_resume or args.new) else record.get("id")
    external = bool(session_id and record.get("external"))
    warning = None
    if session_id:
        busy = running_jobs(cid, request.agent, exclude=exclude_job)
        me = (os.environ.get("AGENTS_SELF") or "").strip().lower()
        me = ALIASES.get(me, me)
        if external and busy:
            # A fresh session would drop the context the user asked to continue.
            raise ParseError(f'a sessao "{_session_label(record)}" ja esta em uso por outro job ({busy[0].get("id")})')
        if external and me == request.agent and cid == os.environ.get("AGENTS_CONVERSATION"):
            raise ParseError(
                f'a sessao "{_session_label(record)}" e a que esta rodando agora nesta conversa; '
                "nao da para retoma-la de dentro dela"
            )
        if busy:
            warning = (
                f"sessao de {request.agent} ocupada pelo job {busy[0].get('id')}; "
                "iniciando sessao nova com historico"
            )
        elif me == request.agent and cid == os.environ.get("AGENTS_CONVERSATION"):
            warning = (
                f"{request.agent} e a sessao que esta rodando agora nesta conversa; "
                "iniciando sessao nova com historico"
            )
        if warning:
            session_id = None
            record = {}
    model = request.model or (record.get("model") if session_id else None)
    effort = request.effort or (record.get("effort") if session_id else None)
    if not external:  # an external session keeps the model/effort it already has
        model = model or default_model(request.agent, config)
        effort = effort or default_effort(request.agent, config)
    return session_id, model, effort, warning


def _session_label(record: dict) -> str:
    return str(record.get("name") or record.get("id") or "?")


class _ClaimLost(Exception):
    """A background child found its job already reaped or claimed."""


class _DepthError(RuntimeError):
    pass


def _plan_and_claim(args, conversation: dict, request, config: dict, child_job: str | None, fields: dict | None):
    """Plan the session and, when `fields` is given, mark the job `running` (pid = us)
    under one per-conversation lock, so parallel delegates cannot resume the same session.
    Returns (session_id, model, effort, warning, job_or_None)."""
    cid = conversation["id"]
    with file_lock(conversation_dir(cid) / "sessions"):
        fresh = load_conversation(cid) or conversation
        session_id, model, effort, warning = _plan_session(args, fresh, request, config, child_job)
        job = None
        if fields is not None:
            claimed = {
                **fields,
                "agent": request.agent,
                "model": model,
                "effort": effort,
                "status": "running",
                "pid": os.getpid(),
                "started_at": now_iso(),
            }
            if child_job:
                job = update_job(child_job, claimed, only_if_status=("queued",))
                if job is None:
                    raise _ClaimLost(child_job)
            else:
                job = create_job(claimed)
    return session_id, model, effort, warning, job


def cmd_delegate(args) -> int:
    config = load_config()
    child_job = args.job
    if child_job:
        validate_id(child_job, "id de job")
    try:
        prepared = _prepare_delegate(args, config)
    except _ClaimLost:
        print(f"erro: job {child_job} ja foi assumido ou encerrado; nada a fazer", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - a background child must finalize its job
        if not child_job:
            raise
        try:
            job = load_job(child_job)
        except FileNotFoundError:
            # No meta for this id: do not create a ghost job just to record the error.
            print(f"erro: {exc}; o job {child_job} nao existe, nada a finalizar", file=sys.stderr)
            return 2
        except (OSError, RuntimeError, ValueError):
            job = {}
        job = {**job, "id": child_job}
        code = 4 if isinstance(exc, _DepthError) else 2
        return _fail(
            args,
            job,
            args.from_agent or "unknown",
            "error",
            code,
            str(exc),
            f"erro: {exc}",
            only_if_status=("queued",),
        )
    if isinstance(prepared, int):
        return prepared
    return _run_delegate(args, config, *prepared)


def _prepare_delegate(args, config: dict):
    """Everything up to the job claim. Returns an exit code or the state for _run_delegate."""
    request = _resolve_request(args, config, require_binary=not args.dry_run, fill_defaults=False)
    from_agent = args.from_agent or whoami()
    cwd = str(Path(args.cwd or os.getcwd()).resolve())
    depth = int(os.environ.get("AGENTS_DEPTH") or 0) + 1
    max_depth = int(config.get("max_depth") or 4)
    child_job = args.job
    if depth > max_depth:
        message = f"profundidade maxima de delegacao ({max_depth}) atingida"
        if child_job:
            raise _DepthError(message)
        print(f"erro: {message}", file=sys.stderr)
        return 4
    if child_job:
        if not args.conversation:
            raise ParseError("--job exige --conversation")
        inherited = args.conversation
    else:
        inherited = None if args.new else (args.conversation or os.environ.get("AGENTS_CONVERSATION"))
    conversation, created = ensure_conversation(inherited, cwd, from_agent)
    cid = conversation["id"]
    if created and inherited:
        print(f"[agents] aviso: conversa {cid} nao existia; criada agora", file=sys.stderr)
    elif not created and from_agent and from_agent not in (conversation.get("participants") or []):
        conversation = add_participant(cid, from_agent) or conversation

    timeout = args.timeout if args.timeout is not None else int(config.get("timeout_sec") or 900)
    background_parent = bool(args.background and not child_job and not args.dry_run)
    base_fields = {"from": from_agent, "conversation": cid, "cwd": cwd, "depth": depth}
    claim = None if (args.dry_run or background_parent) else base_fields
    session_id, model, effort, warning, job = _plan_and_claim(args, conversation, request, config, child_job, claim)
    request = _Req(request.agent, model, effort, request.prompt)

    if background_parent:
        return _delegate_background(args, request, cid, from_agent, cwd, depth, timeout)

    if warning:
        print(f"[agents] aviso: {warning}", file=sys.stderr)
    if job is None:  # dry run
        job = create_job(
            {
                **base_fields,
                "agent": request.agent,
                "model": request.model,
                "effort": request.effort,
                "status": "dry-run",
            }
        )
    return request, conversation, job, session_id, from_agent, cwd, depth, max_depth, timeout


def _run_delegate(args, config, request, conversation, job, session_id, from_agent, cwd, depth, max_depth, timeout) -> int:
    cid = conversation["id"]
    child_job = args.job
    jid = job["id"]

    def build(sid: str | None):
        messages = [m for m in read_messages(cid) if m.get("job") != jid]
        history = "" if args.no_history or sid else render_history(messages)
        wrapped = wrap_prompt(
            body=history + request.prompt,
            from_agent=from_agent,
            to_agent=request.agent,
            model=request.model,
            effort=request.effort,
            conversation=cid,
            job=jid,
            cwd=cwd,
            depth=depth,
            max_depth=max_depth,
            resume=sid,
        )
        return build_launch(
            agent=request.agent,
            model=request.model,
            effort=request.effort,
            prompt=wrapped,
            cwd=cwd,
            job_dir=job_dir(jid),
            config=config,
            from_agent=from_agent,
            conversation=cid,
            depth=depth,
            session_id=sid,
            auto_approve=(args.approve == "auto") and bool(config.get("auto_approve", True)),
            allow_missing=args.dry_run,
        )

    try:
        launch = build(session_id)
        if not child_job:
            append_message(
                cid,
                {
                    "from": from_agent,
                    "to": request.agent,
                    "kind": "delegate",
                    "job": jid,
                    "text": request.prompt,
                },
            )
        _announce(cid, jid, session_id, request.model, request.effort)
        if args.dry_run:
            payload = {
                "conversation": cid,
                "job": jid,
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

        bound = session_record(load_conversation(cid) or conversation, request.agent)
        external = bool(session_id and bound.get("external") and bound.get("id") == session_id)
        retried = False
        while True:
            completed = run_launch(launch, timeout)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            (job_dir(jid) / "stdout.txt").write_text(stdout, encoding="utf-8")
            (job_dir(jid) / "stderr.txt").write_text(stderr, encoding="utf-8")
            text, session = extract_text(request.agent, stdout, launch.last_message_file, stderr=stderr)
            failed = completed.returncode != 0 or output_is_error(stdout, agent=request.agent)
            # agy answers a missing conversation by silently opening a new one (exit 0),
            # so a resumed run that came back on another session also counts as lost.
            switched = not failed and bool(session) and session != session_id
            if not failed and not switched:
                break
            if session_id and not retried and resume_missing(request.agent, stdout, stderr, text):
                if external:
                    # Never swap the user's own session for a fresh one: report and keep the record.
                    reason = _short_reason(failure_text(request.agent, stdout, stderr) or stderr or text)
                    message = (
                        f'a sessao "{_session_label(bound)}" ({session_id}) '
                        f"nao pode ser retomada pelo {request.agent}: {reason}"
                    )
                    return _fail(args, job, from_agent, "error", completed.returncode or 1, message, f"erro: {message}")
                retried = True
                _keep_first_attempt(jid)
                print(
                    f"[agents] aviso: sessao {session_id} do {request.agent} nao existe mais; "
                    "abrindo sessao nova com historico",
                    file=sys.stderr,
                )
                forget_session(cid, request.agent, session_id)
                update_job(jid, {"resume_failed": session_id})
                session_id = None
                launch = build(None)
                _announce(cid, jid, None, request.model, request.effort)
                continue
            if not failed:
                if switched and external:
                    # The user's own session is never replaced by the one the harness answered in.
                    message = (
                        f'a sessao "{_session_label(bound)}" ({session_id}) nao pode ser retomada '
                        f"pelo {request.agent}: o harness respondeu em outra sessao ({session})"
                    )
                    return _fail(args, job, from_agent, "error", 1, message, f"erro: {message}")
                break
            detail =(failure_text(request.agent, stdout, stderr) or text or stderr or stdout)[-4000:]
            return _fail(args, job, from_agent, "error", completed.returncode or 1, detail, detail)
        try:
            remember_session(conversation, request.agent, session, request.model, request.effort)
        except (OSError, RuntimeError) as exc:
            print(f"[agents] aviso: nao consegui gravar a sessao: {exc}", file=sys.stderr)
        update_job(
            jid,
            {
                "status": "ok",
                "returncode": completed.returncode,
                "session": session,
                "text": text,
                "finished_at": now_iso(),
            },
        )
    except subprocess.TimeoutExpired:
        msg = "o agente excedeu o tempo limite"
        return _fail(args, job, from_agent, "timeout", 3, msg, f"erro: {msg}")
    except KeyboardInterrupt:
        return _fail(args, job, from_agent, "cancelled", 130, "cancelado", "cancelado")
    except (ValueError, RuntimeError, OSError) as exc:
        return _fail(args, job, from_agent, "error", 2, str(exc), f"erro: {exc}")
    except Exception as exc:
        _fail(args, job, from_agent, "error", 1, f"{type(exc).__name__}: {exc}", None)
        raise

    if session:
        print(f"[agents] session={session} cwd={cwd}", file=sys.stderr)
    try:
        append_message(
            cid,
            {
                "from": request.agent,
                "to": from_agent,
                "kind": "reply",
                "job": jid,
                "session": session,
                "text": text,
            },
        )
    except (OSError, RuntimeError) as exc:
        print(f"[agents] aviso: nao gravei a resposta na conversa: {exc}", file=sys.stderr)
    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "conversation": cid,
                    "job": jid,
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



def _short_reason(text: str | None) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return "o harness nao encontrou a sessao"


def _keep_first_attempt(jid: str) -> None:
    """Move the failed first attempt's output aside so the retry writes its own."""
    folder = job_dir(jid)
    for name in ("stdout", "stderr"):
        try:
            (folder / f"{name}.txt").replace(folder / f"{name}.attempt1.txt")
        except OSError as exc:
            print(f"[agents] aviso: nao consegui guardar {name} da 1a tentativa: {exc}", file=sys.stderr)


def _fail(
    args,
    job: dict,
    from_agent: str,
    status: str,
    returncode: int,
    message: str,
    stderr_text,
    only_if_status: tuple[str, ...] | None = None,
) -> int:
    """Finish a job as failed: job meta, conversation message, stderr, optional JSON.
    With `only_if_status`, a job already in another state is left untouched."""
    jid = job["id"]
    cid = job.get("conversation")
    message = message or status
    try:
        updated = update_job(
            jid,
            {"status": status, "returncode": returncode, "error": message[-4000:], "finished_at": now_iso()},
            only_if_status=only_if_status,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[agents] aviso: nao atualizei o job {jid}: {exc}", file=sys.stderr)
        updated = {}
    if updated is None:
        # Another process claimed or finished this job; its conversation is not ours to write.
        if stderr_text:
            print(stderr_text, file=sys.stderr)
        return returncode
    try:
        append_message(
            cid,
            {
                "from": job.get("agent") or "unknown",
                "to": from_agent,
                "kind": "error",
                "job": jid,
                "text": message[-500:],
            },
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[agents] aviso: nao gravei o erro na conversa: {exc}", file=sys.stderr)
    if stderr_text:
        print(stderr_text, file=sys.stderr)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "status": status,
                    "error": message[-4000:],
                    "conversation": cid,
                    "job": jid,
                    "returncode": returncode,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    return returncode


def _delegate_background(args, request, cid: str, from_agent: str, cwd: str, depth: int, timeout: int) -> int:
    job = create_job(
        {
            "agent": request.agent,
            "model": request.model,
            "effort": request.effort,
            "from": from_agent,
            "conversation": cid,
            "cwd": cwd,
            "depth": depth,
            "status": "queued",
            "background": True,
        }
    )
    jid = job["id"]
    folder = job_dir(jid)
    prompt_file = folder / "request.txt"
    prompt_file.write_text(request.prompt, encoding="utf-8")
    append_message(
        cid,
        {"from": from_agent, "to": request.agent, "kind": "delegate", "job": jid, "text": request.prompt},
    )
    argv = background_argv(args, request, cid, jid, from_agent, cwd, timeout, prompt_file)
    try:
        launcher_pid = spawn_detached(argv, folder / "agents.log", cwd)
    except OSError as exc:
        _fail(args, job, from_agent, "error", 2, f"nao consegui iniciar o processo em segundo plano: {exc}", None)
        raise
    if isinstance(launcher_pid, int) and launcher_pid > 0:
        try:
            update_job(jid, {"launcher_pid": launcher_pid})
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[agents] aviso: nao gravei o pid do processo em segundo plano: {exc}", file=sys.stderr)
    print(f"[agents] conversation={cid} job={jid} background=yes", file=sys.stderr)
    if args.json:
        print(
            json.dumps(
                {
                    "status": "queued",
                    "conversation": cid,
                    "job": jid,
                    "agent": request.agent,
                    "model": request.model,
                    "effort": request.effort,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(jid)
    return 0


def background_argv(
    args, request, cid: str, jid: str, from_agent: str, cwd: str, timeout: int, prompt_file: Path
) -> list[str]:
    argv = [
        sys.executable,
        "-u",
        str(launcher_path()),
        "delegate",
        request.agent,
        "--job",
        jid,
        "--conversation",
        cid,
        "--from",
        from_agent,
        "--cwd",
        cwd,
        "--timeout",
        str(timeout),
        "--approve",
        args.approve,
        "--prompt-file",
        str(prompt_file),
    ]
    if request.model:
        argv += ["--model", request.model]
    if request.effort:
        argv += ["--effort", request.effort]
    for flag, on in (("--new", args.new), ("--no-resume", args.no_resume), ("--no-history", args.no_history)):
        if on:
            argv.append(flag)
    return argv


def spawn_detached(argv: list[str], log_path: Path, cwd: str | None = None) -> int:
    """Start `argv` fully detached from this process; output goes to `log_path`."""
    log = open(log_path, "ab")
    try:
        kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log, "cwd": cwd, "close_fds": True}
        if os.name == "nt":
            # CREATE_NO_WINDOW (not DETACHED_PROCESS): no console window pops up for the child
            # or for the harness it starts.
            base = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
            breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
            try:
                proc = subprocess.Popen(argv, creationflags=base | breakaway, **kwargs)
            except OSError:
                # The parent job object may forbid breakaway.
                proc = subprocess.Popen(argv, creationflags=base, **kwargs)
        else:
            proc = subprocess.Popen(argv, start_new_session=True, **kwargs)
    finally:
        log.close()
    return proc.pid


# --- sessions / resume ------------------------------------------------------


def _session_view(item: HarnessSession) -> dict:
    return {
        "agent": item.agent,
        "id": item.id,
        "name": item.name,
        "cwd": item.cwd,
        "updated": item.updated,
        "updated_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(item.updated)) if item.updated else None,
        "resumable": item.resumable,
        "note": item.note,
        "archived": item.archived,
    }


def _session_row(item: HarnessSession) -> str:
    row = f"{format_when(item.updated)}  {item.id[:8]}  {item.name}  {item.cwd or '-'}"
    if not item.resumable:
        row += f" [nao retomavel: {item.note or 'sem detalhe'}]"
    if item.archived:
        row += " [arquivada]"
    return row


def cmd_sessions(args) -> int:
    config = load_config()
    agent = normalize_agent(args.agent)
    query = " ".join(args.filter).strip()
    rows = [
        item
        for item in list_sessions(agent, include_archived=args.all, config=config)
        if matches_filter(item, query)
    ]
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]
    if args.json:
        print(json.dumps([_session_view(item) for item in rows], indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("nenhuma sessao")
        return 0
    for item in rows:
        print(_session_row(item))
    return 0


SESSION_WORDS = ("session", "sessao", "sessão", "sessions")


def cmd_resume(args) -> int:
    config = load_config()
    tokens = list(args.target)
    if tokens and tokens[0].lower() in SESSION_WORDS:
        tokens = tokens[1:]
    if len(tokens) < 2:
        raise ParseError("uso: agents resume [session] <agent> <nome-ou-id> [--prompt TEXTO]")
    agent = normalize_agent(tokens[0])
    query = " ".join(tokens[1:]).strip()
    if args.prompt is not None and args.prompt_file:
        raise ParseError("use --prompt ou --prompt-file, nao os dois")
    has_prompt = bool((args.prompt or "").strip()) or bool(args.prompt_file)

    session = find_session(agent, query, config=config)
    if not session.resumable:
        raise ParseError(f'a sessao "{session.name}" nao pode ser retomada: {session.note or "sem detalhe"}')
    cwd = args.cwd or session.cwd
    if not cwd:
        raise ParseError(f'a sessao "{session.name}" nao registrou a pasta; passe --cwd')
    if not Path(cwd).is_dir():
        if args.cwd:
            raise ParseError(f"a pasta nao existe: {cwd}")
        raise ParseError(f"a pasta da sessao nao existe mais: {cwd}; passe --cwd")
    cwd = str(Path(cwd).resolve())
    from_agent = args.from_agent or whoami()

    with file_lock(ensure_bus() / "conversations" / "resume"):
        existing = find_conversation_for_session(agent, session.id)
        if existing:
            cid = existing["id"]
        else:
            cid = ensure_conversation(None, cwd, from_agent)[0]["id"]
        bind_external_session(cid, agent, session.id, session.name)
    print(
        f'[agents] conversation={cid} sessao={session.id} nome="{session.name}" cwd={cwd}',
        file=sys.stderr,
    )

    if not has_prompt:
        if args.json:
            payload = {
                "conversation": cid,
                "agent": agent,
                "session": session.id,
                "name": session.name,
                "cwd": cwd,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(cid)
        print(f'continue com: agents delegate {agent} --conversation {cid} --prompt "..."', file=sys.stderr)
        return 0

    argv = ["delegate", agent, "--conversation", cid, "--cwd", cwd, "--from", from_agent]
    if args.prompt_file:
        argv += ["--prompt-file", args.prompt_file]
    else:
        argv.append(f"--prompt={args.prompt}")
    for flag, value in (
        ("--model", args.model),
        ("--effort", args.effort),
        ("--timeout", args.timeout),
        ("--approve", args.approve),
    ):
        if value is not None:
            argv += [flag, str(value)]
    for flag, on in (("--json", args.json), ("--background", args.background)):
        if on:
            argv.append(flag)
    return main(argv)


# --- send / inbox / reply ---------------------------------------------------


def cmd_send(args) -> int:
    config = load_config()
    request = _resolve_request(args, config, require_binary=False)
    from_agent = args.from_agent or whoami()
    cwd = str(Path(args.cwd or os.getcwd()).resolve())
    conversation, created = ensure_conversation(args.conversation, cwd, from_agent)
    if not created and from_agent and from_agent not in (conversation.get("participants") or []):
        add_participant(conversation["id"], from_agent)
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
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    for row in rows:
        origin = row.get("from") or "?"
        print(f"[{row.get('ts', '')}] {origin} -> {row.get('to') or agent}: {(row.get('text') or '').rstrip()}")
        if row.get("conversation"):
            print(f"    (conversation={row['conversation']})")
    return 0


def cmd_reply(args) -> int:
    cid = validate_id(args.conversation, "id de conversa")
    conversation = load_conversation(cid)
    if conversation is None:
        print(f"erro: conversa nao encontrada: {cid}", file=sys.stderr)
        return 2
    from_agent = args.from_agent or whoami()
    target = normalize_agent(args.to) if args.to else None
    if not target:
        for name in reversed(conversation.get("participants") or []):
            if name and name not in {from_agent, "unknown"}:
                target = name
                break
    if not target:
        print("erro: nao sei para quem responder; passe --to", file=sys.stderr)
        return 2
    waiting = (os.environ.get("AGENTS_FROM") or "").strip().lower()
    waiting = ALIASES.get(waiting, waiting)
    if waiting == target and cid == os.environ.get("AGENTS_CONVERSATION"):
        print(
            f"erro: sua resposta final ja volta para {target} automaticamente; so imprima a resposta",
            file=sys.stderr,
        )
        return 2
    argv = ["delegate", target]
    if args.model:
        argv += ["--model", args.model]
    if args.effort:
        argv += ["--effort", args.effort]
    argv += ["--conversation", cid, "--from", from_agent, f"--prompt={args.prompt}"]
    if args.dry_run:
        argv.append("--dry-run")
    if args.json:
        argv.append("--json")
    return main(argv)


# --- log / status / wait ----------------------------------------------------


def cmd_log(args) -> int:
    tail = args.tail if args.tail and args.tail > 0 else None
    if not args.conversation:
        rows = list_conversations()
        if tail:
            rows = rows[-tail:]
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
            return 0
        if not rows:
            print("nenhuma conversa")
            return 0
        for meta in rows:
            print(f"{meta.get('id')}  cwd={meta.get('cwd')}  people={','.join(meta.get('participants') or [])}")
        return 0
    messages = read_messages(args.conversation)
    if tail:
        messages = messages[-tail:]
    if args.json:
        print(json.dumps(messages, indent=2, ensure_ascii=False))
        return 0
    if not messages:
        print("conversa vazia")
        return 0
    for item in messages:
        speaker = item.get("from") or "?"
        print(f"[{item.get('ts','')}] {speaker}:")
        print((item.get("text") or "").rstrip())
        print()
    return 0


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def cmd_status(_args) -> int:
    print("conversas:")
    convos = list_conversations()
    if not convos:
        print("  (nenhuma)")
    for meta in convos[-10:]:
        people = ",".join(meta.get("participants") or [])
        print(f"  {meta.get('id')}  {meta.get('created_at') or '-'}  {people}")
    print("jobs:")
    jobs = list_jobs()[:10]
    if not jobs:
        print("  (nenhum)")
    for job in jobs:
        status = "stale" if job_is_stale(job) else job.get("status")
        age = _fmt_age(job_age_sec(job))
        print(f"  {job.get('id')}  {str(status):8} {age:>4}  {job.get('from')} -> {job.get('agent')}")
    return 0


def _job_view(job: dict) -> dict:
    return {
        "job": job.get("id"),
        "conversation": job.get("conversation"),
        "agent": job.get("agent"),
        "model": job.get("model"),
        "status": job.get("status"),
        "returncode": job.get("returncode"),
        "text": job.get("text"),
        "error": job.get("error"),
    }


def cmd_wait(args) -> int:
    config = load_config()
    for jid in args.jobs:
        validate_id(jid, "id de job")
        load_job(jid)  # FileNotFoundError if unknown
    if args.timeout is not None:
        limit = args.timeout
    else:
        base = int(config.get("timeout_sec") or 0)
        limit = base + 60 if base else None
    deadline = None if limit is None else time.monotonic() + limit
    interval = max(0.05, args.interval)
    final: dict[str, dict] = {}
    latest: dict[str, dict] = {}
    unreadable_since: dict[str, float] = {}
    timed_out = False
    while True:
        for jid in args.jobs:
            if jid in final:
                continue
            try:
                job = load_job(jid)
                missing = False
            except FileNotFoundError:
                job, missing = {}, True
            if not job:
                # Unreadable meta may be a transient write; if it persists (or the file is
                # gone) the job is reported as corrupt instead of waited on forever.
                first = unreadable_since.setdefault(jid, time.monotonic())
                if missing or time.monotonic() - first > CORRUPT_GRACE_SEC:
                    final[jid] = _corrupt_job(jid)
                continue
            unreadable_since.pop(jid, None)
            latest[jid] = job
            if job.get("status") in LIVE:
                job = reap_job(job)
                latest[jid] = job
            if job.get("status") not in LIVE:
                final[jid] = job
        if len(final) == len(args.jobs):
            break
        if deadline is not None and time.monotonic() >= deadline:
            for jid in args.jobs:
                if jid not in final and jid not in latest:
                    final[jid] = _corrupt_job(jid)  # never readable, even once
            timed_out = len(final) != len(args.jobs)
            break
        remaining = interval if deadline is None else min(interval, max(0.0, deadline - time.monotonic()))
        time.sleep(remaining)

    results = [final.get(jid) or latest.get(jid) or _corrupt_job(jid) for jid in args.jobs]
    if args.json:
        print(json.dumps([_job_view(job) for job in results], indent=2, ensure_ascii=False))
    elif len(results) == 1:
        _print_job_result(results[0], header=False)
    else:
        for job in results:
            print(f"=== {job.get('agent')} {job.get('id')} ({job.get('status')}) ===")
            _print_job_result(job, header=True)
            print()
    if timed_out:
        if not args.json:
            print("erro: tempo de espera esgotado", file=sys.stderr)
        return 3
    return 0 if all(job.get("status") == "ok" for job in results) else 1


def _corrupt_job(jid: str) -> dict:
    return {
        "id": jid,
        "status": "corrupt",
        "returncode": 1,
        "error": "meta do job ilegivel ou ausente",
    }


def _print_job_result(job: dict, header: bool) -> None:
    status = job.get("status")
    if status == "ok":
        print(job.get("text") or "")
        return
    if status in LIVE:
        line = f"ainda {status}"
    else:
        line = job.get("error") or f"terminou com status {status}"
    if header:
        print(f"erro: {line}")
    else:
        print(f"erro: job {job.get('id')} ({status}): {line}", file=sys.stderr)


# --- helpers ----------------------------------------------------------------


class _Req:
    def __init__(self, agent, model, effort, prompt):
        self.agent = agent
        self.model = model
        self.effort = effort
        self.prompt = prompt


def _resolve_request(args, config, require_binary: bool = True, fill_defaults: bool = True) -> _Req:
    prompt = args.prompt
    prompt_file = getattr(args, "prompt_file", None)
    if prompt_file:
        prompt = Path(prompt_file).read_text(encoding="utf-8")
    known = [
        conf.get("model")
        for conf in (config.get("defaults") or {}).values()
        if isinstance(conf, dict) and conf.get("model")
    ]
    parsed = parse_tokens(
        [args.agent, *args.rest],
        prompt_given=bool(prompt),
        known_models=known,
        cwd=getattr(args, "cwd", None),
    )
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


def _announce(cid: str, job_id: str, session_id: str | None, model: str | None, effort: str | None) -> None:
    resumed = "yes" if session_id else "no"
    print(
        f"[agents] conversation={cid} job={job_id} resumed={resumed} "
        f"model={model or SESSION_VALUE} effort={effort or SESSION_VALUE}",
        file=sys.stderr,
    )


def _format_dry_run(payload: dict) -> str:
    argv = " ".join(_quote(part) for part in payload["argv"])
    return (
        f"conversation: {payload['conversation']}\n"
        f"job: {payload['job']}\n"
        f"resume: {payload['resume'] or 'new'}\n"
        f"model: {payload.get('model') or SESSION_VALUE}\n"
        f"effort: {payload.get('effort') or SESSION_VALUE}\n"
        f"cwd: {payload['cwd']}\n"
        f"cmd: {argv}\n"
        f"prompt_file: {payload['prompt_file']}\n"
    )


def _quote(part: str) -> str:
    if not part or any(ch.isspace() for ch in part) or "&" in part:
        return '"' + part.replace('"', '\\"') + '"'
    return part
