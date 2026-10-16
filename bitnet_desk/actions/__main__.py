"""python -m bitnet_desk.actions log [-n N]   — show the action log"""
import argparse

from .. import config as cfg
from . import log


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m bitnet_desk.actions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_log = sub.add_parser("log", help="show the action log")
    p_log.add_argument("-n", type=int, default=30, help="how many entries (default 30)")
    a = ap.parse_args(argv)
    if a.cmd == "log":
        entries = log.read(a.n)
        if not entries:
            print(f"No actions logged yet ({cfg.ACTION_LOG}).")
        for e in entries:
            print(log.format_entry(e))


if __name__ == "__main__":
    main()
