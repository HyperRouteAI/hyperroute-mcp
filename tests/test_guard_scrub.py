from hyperroute_mcp import hooks


def test_guard_lookup_never_carries_credentials():
    cases = {
        'curl -H "Authorization: Bearer sk-abc123456789012345" https://a.io/q?city=Paris&api_key=S3CR3T&lang=en':
            ("sk-abc", "S3CR3T"),
        "curl -u ivan:hunter2 https://user:pw@api.example.com/data": ("hunter2", "user:pw"),
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnop python run.py": ("sk-proj",),
        "wget 'https://s3.example.com/f?X-Amz-Signature=abcdef12&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE'":
            ("abcdef12", "AKIAIOSFODNN7EXAMPLE"),
        "gh api -H 'X-Api-Key: zzz999' repos": ("zzz999",),
    }
    for command, secrets in cases.items():
        sent = hooks._lookup({"tool_name": "Bash", "tool_input": {"command": command}})
        assert not any(s in sent for s in secrets), sent
    url = hooks._lookup({"tool_name": "WebFetch", "tool_input": {"url": "https://x.io/a?token=abc123&q=react"}})
    assert "abc123" not in url and "q=react" in url


def test_guard_lookup_keeps_what_the_call_is_for():
    sent = hooks._lookup({"tool_name": "Bash", "tool_input": {
        "command": 'curl "https://api.frankfurter.app/latest?from=GBP&to=PLN"'}})
    assert "frankfurter.app/latest?from=GBP&to=PLN" in sent
    assert hooks._lookup({"tool_name": "WebSearch", "tool_input": {"query": "latest react version"}}) == \
        "latest react version"
