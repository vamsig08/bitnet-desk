"""python -m bitnet_desk.actions log [-n N]   — show the action log
python -m bitnet_desk.actions eval [--write]  — run the action eval (fast path + real model)"""
import argparse
import importlib
import os
import subprocess
import sys
import tempfile
import time
from datetime import date

from .. import config as cfg
from . import log


def run_eval(write):
    from .. import llm, server
    from . import evalkit, router

    tmp = tempfile.mkdtemp(prefix="bitnet-eval-")
    os.environ.update(evalkit.fake_mac(tmp))                 # resolve against the fake Mac only
    os.environ["BITNET_ACTION_LOG"] = os.path.join(tmp, "actions.jsonl")
    importlib.reload(cfg)
    llm._total_slots.cache_clear()
    cases = evalkit.load_cases()
    proc = None
    if not server.server_up():
        print("Starting the model server for the eval…")
        proc = server.start_server(os.path.join(tmp, "server.log"))
        t0 = time.time()
        while not server.server_up():
            if proc.poll() is not None or time.time() - t0 > 180:
                sys.exit("The model server didn't start — see " + os.path.join(tmp, "server.log"))
            time.sleep(0.5)
    try:
        slots = llm.server_slots()
        print(f"Server slots: {slots}" + ("" if slots >= 2 else " (router shares the chat slot)"))
        fast = evalkit.score_fast(cases)
        verb = evalkit.score_verb_check(cases)
        print(fast.report())
        print("Model path:")
        rows = evalkit.run_model(cases)
    finally:
        if proc:
            proc.terminate()
            proc.wait(15)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            cwd=cfg.HERE).stdout.strip() or "?"
    header = (f"## {date.today()} — commit {commit}, {cfg.MODEL.name}, {cfg.THREADS} threads, "
              f"{len(cases)} cases, threshold {router.THRESHOLD}")
    report = "\n\n".join([header, evalkit.model_report(rows, fast, verb, router.THRESHOLD),
                           evalkit.pipeline_report(cases, rows)])
    print("\n" + report)
    if write:
        path = cfg.HERE / "docs" / "evals.md"
        old = path.read_text(encoding="utf-8") if path.exists() else (
            "# Evals\n\nAction eval: `python -m bitnet_desk.actions eval --write` (cases in "
            "`evals/actions.jsonl`, design in `docs/actions-design.md` §8). Newest first.\n\n")
        head, _, rest = old.partition("\n## ")
        path.write_text(head.rstrip("\n") + "\n\n" + report + "\n" + ("\n## " + rest if rest else ""),
                        encoding="utf-8")
        print(f"\nWritten to {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m bitnet_desk.actions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_log = sub.add_parser("log", help="show the action log")
    p_log.add_argument("-n", type=int, default=30, help="how many entries (default 30)")
    p_eval = sub.add_parser("eval", help="run the action eval against the real model")
    p_eval.add_argument("--write", action="store_true", help="add the results to docs/evals.md")
    a = ap.parse_args(argv)
    if a.cmd == "log":
        entries = log.read(a.n)
        if not entries:
            print(f"No actions logged yet ({cfg.ACTION_LOG}).")
        for e in entries:
            print(log.format_entry(e))
    elif a.cmd == "eval":
        run_eval(a.write)


if __name__ == "__main__":
    main()
