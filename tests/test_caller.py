import json
import sqlite3

from hyperroute_mcp import caller


def _manifest(probes, match=None):
    return {"agents": {"x": {"match": match or {"client": ["x"]}, "probes": probes}}}


def test_token_rejects_prose_and_long_values():
    assert caller.token("claude-opus-5-5") == "claude-opus-5-5"
    assert caller.token("fix my contract please") is None
    assert caller.token("a" * 65) is None
    assert caller.token(True) is None


def test_builtin_manifest_is_valid():
    assert caller.valid(caller.builtin())
    assert not caller.valid({"agents": {"x": {"probes": [{"id": "p", "kind": "exec"}]}}})


def test_jsonl_reads_latest_values(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SID", "abc")
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    rows = [{"version": "1.0", "message": {"model": "m-old"}}, {"version": "1.1", "message": {"model": "m-new"}}]
    (d / "abc.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    m = _manifest([
        {"id": "e", "kind": "env", "out": {"SID": "_session"}},
        {"id": "t", "kind": "jsonl", "path": "{env.CLAUDE_CONFIG_DIR|~/.claude}/projects/*/{_session}.jsonl",
         "out": {"version": "agent_version", "message.model": "model"}},
    ])
    got = caller.collect(m, {"name": "x"})
    assert got["model"] == "m-new" and got["agent_version"] == "1.1"
    assert got["probes_ok"] == ["e", "t"]


def test_sqlite_picks_highest_numbered_store_and_skips_missing_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    for n, model in ((2, "old"), (5, "new")):
        con = sqlite3.connect(tmp_path / f"state_{n}.sqlite")
        con.execute("CREATE TABLE threads (id TEXT, cwd TEXT, model TEXT, updated_at_ms INT)")
        con.execute("INSERT INTO threads VALUES ('t', ?, ?, 1)", (str(tmp_path), model))
        con.commit()
        con.close()
    monkeypatch.chdir(tmp_path)
    m = _manifest([{"id": "s", "kind": "sqlite", "path": "{env.CODEX_HOME|~/.codex}/state_*.sqlite",
                    "table": "threads", "where": {"cwd": "{_cwd}"}, "order": "updated_at_ms",
                    "out": {"model": "model", "no_such_column": "effort"}}])
    got = caller.collect(m, {"name": "x"})
    assert got["model"] == "new" and "effort" not in got


def test_paths_outside_agent_dirs_are_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    (tmp_path / "secret.json").write_text(json.dumps({"model": "leak"}))
    m = _manifest([{"id": "j", "kind": "json", "path": str(tmp_path / "secret.json"), "out": {"model": "model"}}])
    got = caller.collect(m, {"name": "x"})
    assert "model" not in got and got["probes_missed"] == ["j"]


async def test_sends_whole_then_ref_then_delta(monkeypatch):
    monkeypatch.delenv("HYPERROUTE_CALLER", raising=False)
    c = caller.Caller()
    c.manifest = {"agents": {}}
    c.seen("x", "1.0")
    block, sent = await c.block()
    assert "set" in block and "ref" not in block
    c.ack("0123456789abcdef", sent)
    assert (await c.block())[0] == {"ref": "0123456789abcdef"}
    c.seen("x", "1.1")
    block, _ = await c.block()
    assert block["ref"] == "0123456789abcdef"
    assert block["set"] == {"mcp_client_version": "1.1", "agent_version": "1.1"}
    c.ack(caller.UNKNOWN, sent)
    block, _ = await c.block()
    assert "set" in block and "ref" not in block


async def test_host_supplied_sends_nothing(monkeypatch):
    monkeypatch.setenv("HYPERROUTE_CALLER", "host")
    c = caller.Caller()
    c.manifest = {"agents": {}}
    assert await c.block() == (None, None)


async def test_a_server_that_never_answers_stops_being_sent_to(monkeypatch):
    monkeypatch.delenv("HYPERROUTE_CALLER", raising=False)
    c = caller.Caller()
    c.manifest = {"agents": {}}
    for _ in range(3):
        _, sent = await c.block()
        c.ack(None, sent, True)
    assert await c.block() == (None, None)


async def test_a_slow_collection_never_holds_the_call(monkeypatch):
    import time
    monkeypatch.delenv("HYPERROUTE_CALLER", raising=False)
    monkeypatch.setattr(caller, "collect", lambda m, cl: time.sleep(2) or {"agent": "x"})
    c = caller.Caller()
    c.manifest = {"agents": {}}
    t0 = time.monotonic()
    assert await c.block() == (None, None)
    assert time.monotonic() - t0 < 1


async def test_a_broken_collector_sends_nothing_and_raises_nothing(monkeypatch):
    monkeypatch.delenv("HYPERROUTE_CALLER", raising=False)
    monkeypatch.setattr(caller, "collect", lambda m, cl: 1 / 0)
    c = caller.Caller()
    c.manifest = {"agents": {}}
    assert await c.block() == (None, None)


def test_every_probe_kind_survives_garbage(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "a.json").write_text("{not json")
    (tmp_path / "state_1.sqlite").write_text("not a database")
    m = _manifest([
        {"id": "j", "kind": "json", "path": "{env.CODEX_HOME|~/.codex}/a.json", "out": {"k": "model"}},
        {"id": "s", "kind": "sqlite", "path": "{env.CODEX_HOME|~/.codex}/state_*.sqlite",
         "table": "threads", "out": {"model": "model"}},
        {"id": "l", "kind": "jsonl", "path": "{env.CODEX_HOME|~/.codex}/a.json", "out": {"k": "model"}},
    ])
    got = caller.collect(m, {"name": "x"})
    assert got["probes_missed"] == ["j", "l", "s"]
