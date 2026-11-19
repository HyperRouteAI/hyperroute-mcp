"""Lite mode (HYPERROUTE_LITE=1): routing only on the agent's path, about 100 tokens of rules."""
import json
import os
import subprocess
import sys

_PROBE = """
import asyncio, json
from hyperroute_mcp import server, rules
async def m():
    ts = await server.mcp.list_tools()
    print(json.dumps({"tools": sorted(t.name for t in ts),
                      "tool_chars": sum(len(t.description or "") + len(json.dumps(t.input_schema)) for t in ts),
                      "rules": server._INSTRUCTIONS, "hook": rules.text("hyperroute")}))
asyncio.run(m())
"""


def _probe(lite: str) -> dict:
    env = {**os.environ, "HYPERROUTE_LITE": lite}
    out = subprocess.run([sys.executable, "-c", _PROBE], env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_lite_mode_is_three_tools_and_about_a_hundred_tokens():
    d = _probe("1")
    assert d["tools"] == ["login", "report_outcome", "run"]
    assert len(d["rules"]) // 4 <= 110 and d["hook"].startswith("HyperRoute: send each outside lookup")
    assert "mcp__hyperroute__run" in d["hook"] and "blocked" in d["rules"]
    assert (len(d["rules"]) + d["tool_chars"]) // 4 < 500


def test_full_mode_is_unchanged_by_default():
    d = _probe("")
    assert len(d["tools"]) > 3 and "hard rules" in d["rules"]
