import multiprocessing as mp
import os

from hyperroute_mcp import ledger


def _spend(home: str, n: int) -> None:
    os.environ["HYPERROUTE_HOME"] = home
    for i in range(20):
        ledger.grant_pass(f"lookup{n}x{i} word{n}x{i}")
        ledger.take_pass(f"other{n}x{i}")                   # no match: still a load and a save


def test_parallel_hooks_keep_the_caller_and_every_pass():
    # An agent fires its outside calls at once; each hook edits the shared state. Unlocked, a
    # half-written file read as empty dropped the caller ref, owed reports and passes.
    ledger.set_caller("ref-1")
    home = os.environ["HYPERROUTE_HOME"]
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_spend, args=(home, n)) for n in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    s = ledger.load()
    assert s.get("caller") == "ref-1"
    assert len(s["passes"]) == 8 * 20
