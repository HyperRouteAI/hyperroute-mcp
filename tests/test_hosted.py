import json
import os
import subprocess
import sys
import textwrap

from hyperroute_mcp import hosted, server


class FakeAccounts:

    def __init__(self, accounts=None, down=False):
        self.accounts = accounts or {}
        self.down = down

    async def resolve(self, token):
        if self.down:
            raise RuntimeError("down")
        return self.accounts.get(token)


async def _call(gate, headers=None, path="/mcp"):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "path": path, "method": "POST",
             "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()]}
    await gate(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), body


def _app(seen):
    async def app(scope, receive, send):
        seen.append((hosted._bearer.get(), (hosted._account.get() or {}).get("id")))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    return app


async def test_no_bearer_is_refused_with_the_sign_in_pointer():
    seen = []
    gate = hosted.Gate(_app(seen), FakeAccounts(), hosted.Limiter(120, 60))
    status, headers, _ = await _call(gate)
    assert status == 401
    assert b"oauth-protected-resource/mcp" in headers[b"www-authenticate"]
    assert seen == []


async def test_unknown_bearer_is_refused():
    gate = hosted.Gate(_app([]), FakeAccounts(), hosted.Limiter(120, 60))
    status, headers, _ = await _call(gate, {"authorization": "Bearer hyr_nope"})
    assert status == 401 and b'error="invalid_token"' in headers[b"www-authenticate"]


async def test_router_down_answers_503():
    gate = hosted.Gate(_app([]), FakeAccounts(down=True), hosted.Limiter(120, 60))
    status, _, _ = await _call(gate, {"authorization": "Bearer hyr_x"})
    assert status == 503


async def test_known_bearer_rides_the_request_only():
    seen = []
    gate = hosted.Gate(_app(seen), FakeAccounts({"hyo_a": {"id": "u1", "email": "a@x"}}),
                       hosted.Limiter(120, 60))
    status, _, body = await _call(gate, {"authorization": "Bearer hyo_a"})
    assert status == 200 and body == b"ok"
    assert seen == [("hyo_a", "u1")]
    assert hosted._bearer.get() is None


async def test_other_paths_pass_through():
    gate = hosted.Gate(_app([]), FakeAccounts(), hosted.Limiter(120, 60))
    status, _, _ = await _call(gate, path="/health")
    assert status == 200


async def test_limit_is_per_account():
    accts = {"t1": {"id": "u1"}, "t2": {"id": "u2"}}
    gate = hosted.Gate(_app([]), FakeAccounts(accts), hosted.Limiter(60, 2))
    assert (await _call(gate, {"authorization": "Bearer t1"}))[0] == 200
    assert (await _call(gate, {"authorization": "Bearer t1"}))[0] == 200
    status, headers, _ = await _call(gate, {"authorization": "Bearer t1"})
    assert status == 429 and int(headers[b"retry-after"]) >= 1
    assert (await _call(gate, {"authorization": "Bearer t2"}))[0] == 200


def test_limiter_refills(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(hosted.time, "monotonic", lambda: clock[0])
    lim = hosted.Limiter(120, 1)
    assert lim.take("a") == 0.0
    assert lim.take("a") > 0
    clock[0] += 0.5
    assert lim.take("a") == 0.0


def test_request_session_reads_the_request():
    s = hosted.RequestSession()
    assert not s.logged_in
    t1, t2 = hosted._bearer.set("hyo_z"), hosted._account.set({"id": "u9", "email": "z@x"})
    try:
        s.api_key = "ignored"
        assert s.api_key == "hyo_z" and s.user_id == "u9" and s.email == "z@x" and s.logged_in
    finally:
        hosted._bearer.reset(t1)
        hosted._account.reset(t2)


def test_hosted_instructions_lead_with_run_and_drop_local_pieces():
    text = server.instructions(hosted=True)
    assert "Send each via `run`" in text[:512]          # ChatGPT reads only the first ~512 characters
    for local in ("use_token", "`setup`", "Login:", "HyperRoute checks it first"):
        assert local not in text
    assert "declare_my_tool" in text and "prefer_tool" in text
    assert "HyperRoute checks it first" in server.instructions()


def test_run_description_carries_the_routing_rule():
    doc = server.run.__doc__ or ""
    assert "Every outside lookup" in doc and "needs a key = stop" in doc


def _hosted(script: str, tmp_path) -> dict:
    """Run a script against a configured hosted server in its own process: configure() changes the
    server module for good, which must not leak into the other tests."""
    env = {**os.environ, "HYPERROUTE_HOME": str(tmp_path / "home"),
           "HYPERROUTE_TOKEN_FILE": str(tmp_path / "token.json")}
    out = subprocess.run([sys.executable, "-c", textwrap.dedent(script)], capture_output=True,
                         text=True, check=True, env=env, cwd=os.path.dirname(os.path.dirname(__file__)))
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_hosted_tools_are_run_and_ranking_without_local_ones(tmp_path):
    got = _hosted("""
        import asyncio, json
        from hyperroute_mcp import hosted
        m = hosted.configure()
        print(json.dumps(sorted(t.name for t in asyncio.run(m.list_tools()))))
    """, tmp_path)
    assert {"run", "ranking", "report_outcome"} <= set(got)
    assert not set(got) & set(hosted.LOCAL_TOOLS)
    assert "recommend" not in got and "execute" not in got


def test_hosted_requests_keep_accounts_apart_and_keep_no_ledger(tmp_path):
    got = _hosted("""
        import asyncio, json, os
        import httpx
        from hyperroute_mcp import hosted, server

        class Accounts:
            async def resolve(self, token):
                return {"hyr_a": {"id": "u1"}, "hyr_b": {"id": "u2"}}.get(token)

        seen = []

        class Router:
            async def run(self, payload):
                await asyncio.sleep(0.02)
                seen.append(server._session.api_key)
                return {"run": "r_1", "text": "for " + str(server._session.user_id),
                        "items": [{"action": "run", "tool": "exa", "report": "always", "stakes": "high"}]}
            async def catalog(self):
                return {"tools": []}

        async def main():
            gate = hosted.build_app(accounts=Accounts())
            server._client = lambda: Router()
            app = gate.app
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate),
                                             base_url="http://127.0.0.1:8076") as c:
                    async def call(i, token):
                        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                                       "params": {"name": "run", "arguments": {"items": ["q"]}}},
                                         headers={"authorization": "Bearer " + token,
                                                  "accept": "application/json, text/event-stream"})
                        line = next(x for x in r.text.splitlines() if x.startswith("data:"))
                        return token, json.loads(line[5:])["result"]["content"][0]["text"]
                    replies = await asyncio.gather(*[call(i, t) for i, t in
                                                     enumerate(["hyr_a", "hyr_b", "hyr_a", "hyr_b"])])
                    replies.append(await call(9, "hyr_a"))      # after the high-stakes items landed
                    anon = await c.post("/mcp", json={})
            state = os.path.join(os.environ["HYPERROUTE_HOME"], "state")
            print(json.dumps({"replies": replies, "seen": seen, "anon": anon.status_code,
                              "state": sorted(os.listdir(state)) if os.path.isdir(state) else []}))

        asyncio.run(main())
    """, tmp_path)
    assert got["replies"] == [["hyr_a", "for u1"], ["hyr_b", "for u2"], ["hyr_a", "for u1"],
                              ["hyr_b", "for u2"], ["hyr_a", "for u1"]]   # a high-stakes item held nothing
    assert sorted(got["seen"]) == ["hyr_a", "hyr_a", "hyr_a", "hyr_b", "hyr_b"]
    assert got["anon"] == 401
    assert got["state"] == []                                        # no ledger written
