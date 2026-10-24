"""trenches-eth command entry: ``python3 . <command> [args]`` from the repo root.

Every command is a dry-run measurement or an offline replay; nothing here signs or sends.
"""
from __future__ import annotations

import runpy
import sys

COMMANDS = {
    "v2": ("eth_mev.app.screener.v2", "V2 mempool exposure screener + settlement"),
    "v3": ("eth_mev.app.screener.v3", "V3 block-level exposure funnel"),
    "arb": ("eth_mev.app.screener.arb", "cross-pool dislocation scanner"),
    "collect": ("scripts.ground_truth.collect", "record confirmed blocks + labelled sandwiches"),
    "backtest": ("scripts.finetune.backtest", "replay a recorded window through the allocator"),
    "sweep": ("scripts.finetune.sweep", "random search over the allocator knobs"),
    "report": ("scripts.finetune.report", "train/holdout finetune + markdown report"),
    "guard": ("scripts.finetune.guard_percentile", "victim guard-slack percentiles"),
}


def _usage() -> str:
    rows = "\n".join(f"  {name:<9} {desc}" for name, (_, desc) in COMMANDS.items())
    return f"usage: python3 . <command> [args]\n\ncommands:\n{rows}"


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(_usage())
        sys.exit(0 if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help") else 2)
    module, _ = COMMANDS[sys.argv[1]]
    sys.argv = [f"{sys.argv[0]} {sys.argv[1]}", *sys.argv[2:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
