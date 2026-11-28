import multiprocessing as mp
import os

from hyperroute_mcp import ledger


def _owe(home: str, n: int) -> None:
    os.environ["HYPERROUTE_HOME"] = home
    for i in range(20):
        ledger.add_run("t", f"r{n}/{i}")


def test_parallel_edits_keep_every_owed_report():
    home = os.environ["HYPERROUTE_HOME"]
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_owe, args=(home, n)) for n in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert len(ledger.load()["pending"]) == 8 * 20
