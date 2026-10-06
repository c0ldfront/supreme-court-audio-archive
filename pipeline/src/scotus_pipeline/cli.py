from __future__ import annotations

import argparse
import json
from importlib.resources import files
from pathlib import Path

from rich.console import Console
from rich.table import Table

from scotus_pipeline.config import load_config
from scotus_pipeline.runner import PipelineRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SCOTUS pipeline.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser("validate", help="validate a TOML/YAML pipeline config")
    validate.add_argument("config", type=Path)

    plan = subcommands.add_parser("plan", help="print the executable step plan")
    plan.add_argument("config", type=Path)
    plan.add_argument("--json", action="store_true", help="emit plan as JSON")
    plan.add_argument("--case-dir", type=Path, help="override config case.case_dir")
    plan.add_argument("--docket", help="override config case.docket")
    plan.add_argument("--no-resume", action="store_true", help="ignore saved run state in the plan")

    run = subcommands.add_parser("run", help="run the pipeline")
    run.add_argument("config", type=Path)
    run.add_argument(
        "--dry-run", action="store_true", help="show planned steps without processing or writing artifacts"
    )
    run.add_argument("--case-dir", type=Path, help="override config case.case_dir")
    run.add_argument("--docket", help="override config case.docket")
    run.add_argument("--no-resume", action="store_true", help="ignore saved run state")
    run.add_argument("--only-step", help="run one step id")
    run.add_argument("--from-step", help="start from a step id")

    init = subcommands.add_parser("init-config", help="copy the example config to a new path")
    init.add_argument("output", type=Path)
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    if args.command == "init-config":
        source = files("scotus_pipeline").joinpath("templates/case.toml")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(source.read_text(encoding="utf-8"))
        console.print(f"wrote {args.output}")
        return 0

    config = load_config(args.config)
    case_dir = getattr(args, "case_dir", None)
    if case_dir and not case_dir.is_absolute():
        case_dir = case_dir.resolve()
    resume = False if getattr(args, "no_resume", False) else None
    if case_dir or getattr(args, "docket", None) or resume is not None:
        config = config.with_overrides(case_dir=case_dir, docket=args.docket, resume=resume)

    if args.command == "validate":
        console.print("[green]config valid[/green]")
        return 0

    runner = PipelineRunner(
        config, config_path=args.config, dry_run=args.command == "plan" or getattr(args, "dry_run", False)
    )
    if args.command == "plan":
        plan = runner.plan()
        if args.json:
            print(json.dumps(plan, indent=2))
            return 0
        table = Table(title="SCOTUS Pipeline Plan")
        table.add_column("Step")
        table.add_column("Enabled")
        table.add_column("Outputs")
        for step in plan:
            table.add_row(
                str(step["id"]),
                "yes" if step["enabled"] else "no",
                "\n".join(str(path) for path in step["outputs"]) or "-",
            )
        console.print(table)
        return 0

    if args.command == "run":
        return runner.run(only_step=args.only_step, from_step=args.from_step)

    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (OSError, ValueError) as exc:
        Console(stderr=True).print(f"Error: {exc}", style="bold red", markup=False)
        return 2
