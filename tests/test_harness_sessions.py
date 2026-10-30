import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from agents_cli import harness_sessions as hs
from agents_cli.harness_sessions import SessionLookupError, find_session, list_sessions
from agents_cli.parse import ParseError
from agents_cli.prompt import render_history, wrap_prompt


def uid(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-000000000000"


def make_codex_db(home: Path, rows: list[dict], number: int = 5) -> Path:
    path = home / ".codex" / f"state_{number}.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, created_at INTEGER, "
            "updated_at INTEGER, source TEXT, cwd TEXT, title TEXT, archived INTEGER DEFAULT 0, "
            "first_user_message TEXT, updated_at_ms INTEGER, thread_source TEXT, preview TEXT, name TEXT)"
        )
        for row in rows:
            data = {"archived": 0, "thread_source": "user", **row}
            conn.execute(
                f"INSERT INTO threads ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})",
                list(data.values()),
            )
        conn.commit()
    return path


def make_rollout(home: Path, sid: str, suffix: str = ".jsonl") -> Path:
    path = home / ".codex" / "sessions" / "2026" / "10" / f"rollout-2026-10-01T00-00-00-{sid}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix.endswith(".zst"):
        path.write_bytes(b"\x28\xb5\x2f\xfd")  # zstd frame magic
    else:
        path.write_text("{}\n", encoding="utf-8")
    return path


def write_claude(home: Path, sid: str, entries: list, project: str = "C--proj", mtime: float | None = None) -> Path:
    path = home / ".claude" / "projects" / project / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False) for entry in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def user_entry(text, cwd="C:\\work\\proj", **extra):
    return {"type": "user", "cwd": cwd, "message": {"role": "user", "content": text}, **extra}


def bus_prompt(text: str, history: list[dict] | None = None) -> str:
    """A prompt as the bus sends it: the real protocol header, earlier messages (optional), the message."""
    body = (render_history(history) if history else "") + text
    return wrap_prompt(
        body=body,
        from_agent="grok",
        to_agent="codex",
        model=None,
        effort=None,
        conversation="cbus0000001",
        job="jbus0000001",
        cwd="C:\\w",
        depth=1,
        max_depth=4,
        resume=None,
    )


def write_grok(home: Path, sid: str, cwd: str, **top) -> Path:
    path = home / ".grok" / "sessions" / quote(cwd, safe="") / sid / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"info": {"id": sid, "cwd": cwd}, "created_at": "2026-10-01T10:00:00Z", **top}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def make_agy(home: Path, rows: list[dict], with_conversation_db: tuple[str, ...] = ()) -> Path:
    root = home / ".gemini" / "antigravity-cli"
    (root / "conversations").mkdir(parents=True, exist_ok=True)
    db = root / "conversation_summaries.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, title TEXT, preview TEXT, "
            "last_modified_time TEXT, workspace_uris TEXT, project_id TEXT, status TEXT)"
        )
        for row in rows:
            conn.execute(
                f"INSERT INTO conversation_summaries ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                list(row.values()),
            )
        conn.commit()
    for cid in with_conversation_db:
        (root / "conversations" / f"{cid}.db").write_bytes(b"")
    return db


class HomeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        patcher = mock.patch.object(hs, "_home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)


class CodexReaderTests(HomeCase):
    def test_name_precedence_and_cleanup(self):
        make_codex_db(
            self.home,
            [
                {"id": uid(1), "name": "Nome do usuario", "title": "auto", "first_user_message": "oi", "updated_at_ms": 3000},
                {"id": uid(2), "name": "", "title": "Titulo auto", "first_user_message": "oi", "updated_at_ms": 2000},
                {"id": uid(3), "name": None, "title": "  ", "first_user_message": "primeira\n  linha\tdois", "updated_at_ms": 1000},
                {"id": uid(4), "title": "x" * 300, "updated_at_ms": 500},
            ],
        )
        by_id = {s.id: s for s in list_sessions("codex")}
        self.assertEqual(by_id[uid(1)].name, "Nome do usuario")
        self.assertEqual(by_id[uid(2)].name, "Titulo auto")
        self.assertEqual(by_id[uid(3)].name, "primeira linha dois")
        self.assertEqual(len(by_id[uid(4)].name), 120)

    def test_strips_extended_prefix_and_checks_rollout(self):
        rollout = make_rollout(self.home, uid(1))
        make_codex_db(
            self.home,
            [
                {"id": uid(1), "name": "com rollout", "cwd": "\\\\?\\C:\\work\\proj", "rollout_path": "\\\\?\\" + str(rollout)},
                {"id": uid(2), "name": "sem rollout", "cwd": "C:\\work\\two", "rollout_path": str(self.home / "nao-existe.jsonl")},
                {"id": uid(3), "name": "sem caminho"},
            ],
        )
        by_id = {s.id: s for s in list_sessions("codex")}
        self.assertEqual(by_id[uid(1)].cwd, "C:\\work\\proj")
        self.assertTrue(by_id[uid(1)].resumable)
        self.assertIsNone(by_id[uid(1)].note)
        for sid in (uid(2), uid(3)):
            self.assertFalse(by_id[sid].resumable)
            self.assertIn("rollout", by_id[sid].note)

    def test_archived_and_subagent_threads(self):
        make_codex_db(
            self.home,
            [
                {"id": uid(1), "name": "ativa"},
                {"id": uid(2), "name": "arquivada", "archived": 1},
                {"id": uid(3), "name": "subagente", "thread_source": "subagent"},
            ],
        )
        self.assertEqual([s.id for s in list_sessions("codex")], [uid(1)])
        everything = list_sessions("codex", include_archived=True)
        self.assertEqual({s.id for s in everything}, {uid(1), uid(2)})
        self.assertTrue(next(s for s in everything if s.id == uid(2)).archived)

    def test_newest_first_and_ms_or_seconds(self):
        make_codex_db(
            self.home,
            [
                {"id": uid(1), "name": "a", "updated_at_ms": 1_700_000_000_000},
                {"id": uid(2), "name": "b", "updated_at_ms": None, "updated_at": 1_800_000_000},
                {"id": uid(3), "name": "c", "updated_at_ms": 1_750_000_000_000},
            ],
        )
        rows = list_sessions("codex")
        self.assertEqual([s.id for s in rows], [uid(2), uid(3), uid(1)])
        self.assertEqual(rows[0].updated, 1_800_000_000)

    def test_picks_highest_state_db(self):
        make_codex_db(self.home, [{"id": uid(1), "name": "velho"}], number=3)
        make_codex_db(self.home, [{"id": uid(2), "name": "novo"}], number=10)
        self.assertEqual([s.id for s in list_sessions("codex")], [uid(2)])

    def test_index_fallback_without_db(self):
        make_rollout(self.home, uid(1))
        index = self.home / ".codex" / "session_index.jsonl"
        lines = [
            {"id": uid(1), "thread_name": "primeiro nome", "updated_at": "2026-10-01T10:00:00.1234567Z"},
            {"id": uid(1), "thread_name": "nome final", "updated_at": "2026-10-02T10:00:00Z"},
            {"id": uid(2), "thread_name": "sem rollout", "updated_at": "2026-10-03T10:00:00Z"},
        ]
        index.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8")
        rows = list_sessions("codex")
        self.assertEqual([s.name for s in rows], ["sem rollout", "nome final"])
        self.assertIsNone(rows[1].cwd)
        self.assertEqual([s.resumable for s in rows], [False, True])

    def test_corrupt_or_missing_store_is_empty(self):
        self.assertEqual(list_sessions("codex"), [])
        path = self.home / ".codex" / "state_5.sqlite"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"this is not a sqlite database" * 50)
        self.assertEqual(list_sessions("codex"), [])

    def test_database_is_not_modified(self):
        path = make_codex_db(self.home, [{"id": uid(1), "name": "x"}])
        before = path.read_bytes()
        list_sessions("codex")
        self.assertEqual(path.read_bytes(), before)

    def test_compressed_rollout_counts_as_resumable(self):
        # Codex compresses older rollouts to <rollout>.zst; the db may still point at the .jsonl path.
        make_rollout(self.home, uid(1), suffix=".jsonl.zst")
        jsonl = self.home / ".codex" / "sessions" / "2026" / "10" / f"rollout-2026-10-01T00-00-00-{uid(1)}.jsonl"
        make_codex_db(
            self.home,
            [
                {"id": uid(1), "name": "comprimida", "rollout_path": "\\\\?\\" + str(jsonl)},
                {"id": uid(2), "name": "sem nada", "rollout_path": str(self.home / "nao-existe.jsonl")},
            ],
        )
        by_id = {s.id: s for s in list_sessions("codex")}
        self.assertTrue(by_id[uid(1)].resumable)
        self.assertIsNone(by_id[uid(1)].note)
        self.assertFalse(by_id[uid(2)].resumable)

    def test_index_fallback_finds_compressed_rollouts(self):
        make_rollout(self.home, uid(1), suffix=".jsonl.zst")
        index = self.home / ".codex" / "session_index.jsonl"
        lines = [
            {"id": uid(1), "thread_name": "comprimida", "updated_at": "2026-10-02T10:00:00Z"},
            {"id": uid(2), "thread_name": "sem rollout", "updated_at": "2026-10-03T10:00:00Z"},
        ]
        index.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
        by_id = {s.id: s for s in list_sessions("codex")}
        self.assertTrue(by_id[uid(1)].resumable)
        self.assertFalse(by_id[uid(2)].resumable)

    def test_unsafe_ids_are_not_listed_or_found(self):
        make_codex_db(
            self.home,
            [
                {"id": uid(1), "name": "valida"},
                {"id": "-x", "name": "comeca com traco"},
                {"id": "com espaco", "name": "com espaco"},
                {"id": "quebra\nlinha", "name": "controle"},
            ],
        )
        self.assertEqual([s.id for s in list_sessions("codex")], [uid(1)])
        with self.assertRaises(SessionLookupError):
            find_session("codex", "-x")

    def test_bus_prompts_are_named_by_their_message(self):
        history_prompt = bus_prompt("Qual o status?", history=[{"from": "claude", "text": "mensagem antiga"}])
        self.assertIn("--- end of earlier messages ---", history_prompt)
        make_codex_db(
            self.home,
            [
                {"id": uid(1), "first_user_message": bus_prompt("Revise o modulo X\nem detalhes")},
                {"id": uid(2), "first_user_message": history_prompt},
                {"id": uid(3), "first_user_message": bus_prompt("")},
                {"id": uid(4), "title": bus_prompt("x" * 300)},
            ],
        )
        by_id = {s.id: s for s in list_sessions("codex")}
        self.assertEqual(by_id[uid(1)].name, "[bus] Revise o modulo X")
        self.assertEqual(by_id[uid(2)].name, "[bus] Qual o status?")
        self.assertEqual(by_id[uid(3)].name, hs.NO_TITLE)
        self.assertEqual(by_id[uid(4)].name, "[bus] " + "x" * 114)
        self.assertFalse(any("agents-bus" in s.name for s in by_id.values()))


class ClaudeReaderTests(HomeCase):
    def test_title_precedence_last_custom_title_wins(self):
        write_claude(
            self.home,
            uid(1),
            [
                {"type": "ai-title", "aiTitle": "Titulo da IA"},
                user_entry("primeiro prompt"),
                {"type": "custom-title", "customTitle": "Primeiro nome"},
                {"type": "ai-title", "aiTitle": "Outro titulo da IA"},
                {"type": "custom-title", "customTitle": "Nome Final"},
            ],
        )
        write_claude(self.home, uid(2), [user_entry("pergunta"), {"type": "ai-title", "aiTitle": "Titulo IA"}])
        write_claude(self.home, uid(3), [{"type": "system", "cwd": "C:\\x"}, user_entry("so o prompt")])
        by_id = {s.id: s for s in list_sessions("claude")}
        self.assertEqual(by_id[uid(1)].name, "Nome Final")
        self.assertEqual(by_id[uid(2)].name, "Titulo IA")
        self.assertEqual(by_id[uid(3)].name, "so o prompt")
        self.assertTrue(all(s.resumable for s in by_id.values()))

    def test_cwd_comes_from_entries_and_prompt_skips_tag_blocks(self):
        write_claude(
            self.home,
            uid(1),
            [
                {"type": "file-history-snapshot"},
                user_entry([{"type": "text", "text": "<command-name>/clear</command-name>"}], cwd="C:\\a b\\proj"),
                user_entry([{"type": "text", "text": "prompt   de\nverdade"}], cwd="C:\\outro"),
            ],
            project="C--mangled-dir-name",
        )
        (session,) = list_sessions("claude")
        self.assertEqual(session.cwd, "C:\\a b\\proj")
        self.assertEqual(session.name, "prompt de verdade")

    def test_ordering_ignores_non_uuid_files_and_bad_lines(self):
        write_claude(self.home, uid(1), [user_entry("antigo")], mtime=1_700_000_000)
        write_claude(self.home, uid(2), ["{broken json", user_entry("novo")], mtime=1_800_000_000)
        write_claude(self.home, "agent-abc123", [user_entry("sidechain")])
        rows = list_sessions("claude")
        self.assertEqual([s.id for s in rows], [uid(2), uid(1)])
        self.assertEqual(rows[0].updated, 1_800_000_000)

    def test_empty_session_is_not_resumable(self):
        write_claude(self.home, uid(1), [{"type": "file-history-snapshot"}])
        (session,) = list_sessions("claude")
        self.assertFalse(session.resumable)
        self.assertIsNone(session.cwd)

    def test_missing_store_is_empty(self):
        self.assertEqual(list_sessions("claude"), [])

    def test_bus_prompt_is_named_by_its_message(self):
        history = [{"from": "codex", "text": "antes"}]
        write_claude(self.home, uid(1), [user_entry(bus_prompt("primeira linha real\nsegunda", history=history))])
        write_claude(self.home, uid(2), [user_entry(bus_prompt("so isto"))])
        by_id = {s.id: s for s in list_sessions("claude")}
        self.assertEqual(by_id[uid(1)].name, "[bus] primeira linha real")
        self.assertEqual(by_id[uid(2)].name, "[bus] so isto")


class GrokReaderTests(HomeCase):
    def test_titles_cwd_and_ordering(self):
        write_grok(self.home, uid(1), "C:\\work\\um", generated_title="Titulo gerado", session_summary="resumo",
                   last_active_at="2026-10-02T10:00:00Z")
        write_grok(self.home, uid(2), "C:\\work\\dois", session_summary="so o resumo\nem linhas",
                   updated_at="2026-10-03T10:00:00.5Z")
        write_grok(self.home, uid(3), "C:\\work\\tres")
        rows = list_sessions("grok")
        self.assertEqual([s.id for s in rows], [uid(2), uid(1), uid(3)])
        self.assertEqual([s.name for s in rows], ["so o resumo em linhas", "Titulo gerado", hs.NO_TITLE])
        self.assertEqual(rows[1].cwd, "C:\\work\\um")
        self.assertTrue(all(s.resumable for s in rows))

    def test_corrupt_summary_is_skipped(self):
        write_grok(self.home, uid(1), "C:\\work\\um", generated_title="ok")
        bad = self.home / ".grok" / "sessions" / "x" / uid(2) / "summary.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{not json", encoding="utf-8")
        self.assertEqual([s.id for s in list_sessions("grok")], [uid(1)])

    def test_missing_store_is_empty(self):
        self.assertEqual(list_sessions("grok"), [])


class AgyReaderTests(HomeCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("agents_cli.harness.which", return_value="C:\\agy\\bin\\agy.exe")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rows_workspace_and_resumable(self):
        make_agy(
            self.home,
            [
                {"conversation_id": "conv-a", "title": "Titulo A", "preview": "p", "workspace_uris": '["file:///c%3A/Users/x%20y/proj"]',
                 "last_modified_time": "2026-10-02 10:00:00.1234567+00:00"},
                {"conversation_id": "conv-b", "title": "", "preview": "so preview", "workspace_uris": "[]",
                 "last_modified_time": "2026-10-01 10:00:00+00:00"},
                {"conversation_id": "conv-c", "title": "Sem data", "workspace_uris": "nao e json",
                 "last_modified_time": "0001-01-01 00:00:00+00:00"},
            ],
            with_conversation_db=("conv-a",),
        )
        rows = list_sessions("gemini")
        self.assertEqual([s.id for s in rows], ["conv-a", "conv-b", "conv-c"])
        a, b, c = rows
        self.assertEqual(a.name, "Titulo A")
        self.assertEqual(a.cwd, "C:\\Users\\x y\\proj")
        self.assertTrue(a.resumable)
        self.assertEqual(b.name, "so preview")
        self.assertIsNone(b.cwd)
        self.assertFalse(b.resumable)
        self.assertIn("agy", b.note)
        self.assertIsNone(c.cwd)
        self.assertEqual(c.updated, 0.0)

    def test_missing_or_corrupt_db_is_empty(self):
        self.assertEqual(list_sessions("gemini"), [])
        root = self.home / ".gemini" / "antigravity-cli"
        root.mkdir(parents=True)
        (root / "conversation_summaries.db").write_bytes(b"garbage" * 100)
        self.assertEqual(list_sessions("gemini"), [])

    def test_gemini_cli_cannot_resume_by_id(self):
        with mock.patch("agents_cli.harness.which", return_value="C:\\npm\\gemini.CMD"):
            with self.assertRaises(ParseError) as ctx:
                list_sessions("gemini")
            self.assertIn("use o agy", str(ctx.exception))
            with self.assertRaises(ParseError):
                find_session("gemini", "qualquer coisa")

    def test_gemini_not_installed(self):
        with mock.patch("agents_cli.harness.which", return_value=None):
            with self.assertRaises(ParseError):
                list_sessions("gemini")


class FindSessionTests(HomeCase):
    def setUp(self):
        super().setUp()
        self.rows = [
            {"id": uid(1), "name": "Revisão Fiscal", "cwd": "C:\\w\\um", "updated_at_ms": 5000},
            {"id": uid(2), "name": "Plano de Migração do Banco", "cwd": "C:\\w\\dois", "updated_at_ms": 4000},
            {"id": uid(3), "name": "Plano de Testes", "cwd": "C:\\w\\tres", "updated_at_ms": 3000},
            {"id": "abcdef12-0000-4000-8000-000000000000", "name": "Duplicada", "updated_at_ms": 2000},
            {"id": "abcdef34-0000-4000-8000-000000000000", "name": "Duplicada", "updated_at_ms": 1000},
        ]
        for row in self.rows:
            sid = row["id"]
            row["rollout_path"] = str(make_rollout(self.home, sid))
        make_codex_db(self.home, self.rows)

    def test_exact_id_is_case_insensitive(self):
        self.assertEqual(find_session("codex", uid(1).upper()).id, uid(1))

    def test_unique_id_prefix(self):
        self.assertEqual(find_session("codex", uid(2)[:8]).id, uid(2))
        self.assertEqual(find_session("codex", "00000003").id, uid(3))

    def test_ambiguous_id_prefix(self):
        with self.assertRaises(SessionLookupError):
            find_session("codex", "abcdef")

    def test_short_prefix_is_a_name_not_an_id(self):
        with self.assertRaises(SessionLookupError):
            find_session("codex", "0000")

    def test_exact_name_ignores_case_and_accents(self):
        self.assertEqual(find_session("codex", "revisao fiscal").id, uid(1))
        self.assertEqual(find_session("codex", "  REVISÃO   fiscal ").id, uid(1))

    def test_unique_substring(self):
        self.assertEqual(find_session("codex", "migracao").id, uid(2))

    def test_ambiguous_lists_candidates_and_hint(self):
        with self.assertRaises(SessionLookupError) as ctx:
            find_session("codex", "plano")
        message = str(ctx.exception)
        self.assertIsInstance(ctx.exception, ParseError)
        self.assertIn("passe o id", message)
        self.assertIn(f"  {uid(2)[:8]}  ", message)
        self.assertIn("Plano de Testes  (C:\\w\\tres)", message)
        self.assertRegex(message, r"  00000002  \d{4}-\d{2}-\d{2} \d{2}:\d{2}  Plano de Migração do Banco  \(C:\\w\\dois\)")

    def test_ambiguity_message_caps_candidates(self):
        make_codex_db(
            self.home,
            [{"id": uid(100 + n), "name": f"Tarefa {n}", "updated_at_ms": n} for n in range(15)],
            number=9,
        )
        with self.assertRaises(SessionLookupError) as ctx:
            find_session("codex", "tarefa")
        lines = str(ctx.exception).splitlines()
        self.assertEqual(len([l for l in lines if l.startswith("  0000")]), 10)
        self.assertIn("e mais 5", lines[-1])

    def test_duplicate_names_pick_the_only_resumable(self):
        make_codex_db(
            self.home,
            [
                {"id": uid(1), "name": "Mesmo Nome", "rollout_path": str(make_rollout(self.home, uid(1))), "updated_at_ms": 1},
                {"id": uid(2), "name": "Mesmo Nome", "rollout_path": None, "updated_at_ms": 9},
            ],
            number=9,
        )
        self.assertEqual(find_session("codex", "mesmo nome").id, uid(1))

    def test_duplicate_names_both_resumable_is_ambiguous(self):
        with self.assertRaises(SessionLookupError):
            find_session("codex", "Duplicada")

    def test_not_found_has_hint(self):
        with self.assertRaises(SessionLookupError) as ctx:
            find_session("codex", "inexistente")
        self.assertIn("agents sessions codex", str(ctx.exception))

    def test_archived_sessions_are_findable_by_default(self):
        make_codex_db(self.home, [{"id": uid(7), "name": "Velha", "archived": 1}], number=9)
        self.assertEqual(find_session("codex", "velha").id, uid(7))
        with self.assertRaises(SessionLookupError):
            find_session("codex", "velha", include_archived=False)

    def test_name_wins_over_an_id_prefix_spelled_the_same(self):
        # "facade" is hex, so it could also be an id prefix; the exact name is tried first.
        by_name, by_prefix = uid(8), "facade00-0000-4000-8000-000000000000"
        make_codex_db(
            self.home,
            [
                {"id": by_name, "name": "Facade", "rollout_path": str(make_rollout(self.home, by_name)), "updated_at_ms": 1},
                {"id": by_prefix, "name": "Outra", "rollout_path": str(make_rollout(self.home, by_prefix)), "updated_at_ms": 2},
            ],
            number=9,
        )
        self.assertEqual(find_session("codex", "facade").id, by_name)
        self.assertEqual(find_session("codex", "facade0").id, by_prefix)

    def test_substring_picks_the_only_resumable_match(self):
        make_codex_db(
            self.home,
            [
                {"id": uid(1), "name": "Plano Alfa", "updated_at_ms": 3},
                {"id": uid(2), "name": "Plano Beta", "rollout_path": str(make_rollout(self.home, uid(2))), "updated_at_ms": 2},
                {"id": uid(3), "name": "Plano Gama", "updated_at_ms": 1},
            ],
            number=9,
        )
        self.assertEqual(find_session("codex", "plano").id, uid(2))

    def test_empty_query(self):
        with self.assertRaises(ParseError):
            find_session("codex", "   ")

    def test_normalize(self):
        self.assertEqual(hs.normalize("  Sessão   ÀÇÃO\tX "), "sessao acao x")


if __name__ == "__main__":
    unittest.main()
