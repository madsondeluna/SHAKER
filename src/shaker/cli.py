"""shaker: command line entry point.

stdout carries the result (a table on a terminal, JSON when piped or with --json, tab-separated
text with --plain). Everything else goes to stderr: progress, status lines, the banner, quotes.

Exit codes: 0 success, 1 runtime failure, 2 usage error, 3 configuration error, 130 interrupted.
"""

import argparse
import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=SyntaxWarning)  # vermouth's regex literals

from . import __version__, brand, quotes, ui  # noqa: E402

EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3

TAGLINE = brand.TAGLINE

# The help screen is data, rendered as panels on a terminal and as aligned text in a pipe.
WORKFLOW = [
    ("shaker init", "write an example config.yaml"),
    ("shaker check --pin", "verify tools, pin their versions"),
    ("shaker plan", "expand the campaign, count simulations"),
    ("shaker prepare", "coarse-grain the peptides"),
    ("shaker build --replicas 10", "build 10 random boxes per condition"),
    ("shaker run --jobs 4", "simulate, 4 replicas at a time"),
]
INSPECT = [
    ("shaker status", "progress of each condition"),
    ("shaker lipids list --head PG", "browse the lipid library"),
    ("shaker lipids show POPG", "beads and template of one lipid"),
]
HELP_EXAMPLES = [
    "shaker build --condition <id> --force",
    "shaker run --until eq4 --threads 4",
    "shaker run --backend slurm-array --gres gpu:1 --replicas-per-task 4",
    "shaker status --json | jq '.[].assembled'",
]
# What to type after each stage.
NEXT = {
    "init": "shaker check --pin",
    "check": "shaker plan",
    "plan": "shaker prepare",
    "prepare": "shaker build",
    "build": "shaker run",
    "run": "shaker status",
}
HELP_OPTIONS = [
    ("-c, --config <path>", "campaign file", "./config.yaml"),
    ("    --json", "one JSON document on stdout", ""),
    ("    --plain", "tab separated text on stdout", ""),
    ("-q, --quiet", "only print errors", ""),
    ("    --no-quotes", "no quote at the end of a stage", ""),
    ("    --color <when>", "auto, always or never", "auto"),
    ("-h, --help", "show help", ""),
    ("-V, --version", "show version", ""),
]
HELP_ENVIRONMENT = "SHAKER_CONFIG  SHAKER_NO_QUOTES  SHAKER_NO_ANIMATION  NO_COLOR"
EXIT_CODES = [("0", "ok"), ("1", "failure"), ("2", "usage"), ("3", "config"),
              ("130", "interrupted")]
HELP_FOOTER = "Run `shaker <command> --help` for the options of one command."


KNOWN_FLAGS = set()  # every option string of every command, for "did you mean"


class ShakerError(Exception):
    def __init__(self, message, hint=None, code=EXIT_FAILURE, kind="failure"):
        super().__init__(message)
        self.hint, self.code, self.kind = hint, code, kind


# argument parsing ---------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    """argparse with the house error format: one line, a hint, exit 2."""

    def error(self, message):
        import re

        hint = f"run `{self.prog} --help` for usage and examples"
        unknown = re.search(r"unrecognized arguments?: (\S+)", message)
        choice = re.search(r"invalid choice: '([^']+)' \(choose from (.+)\)", message)
        if unknown:
            near = ui.did_you_mean(unknown.group(1).split("=")[0], sorted(KNOWN_FLAGS))
            if near:
                hint = f"did you mean `{near}`?"
        elif choice:
            options = [o.strip(" '") for o in choice.group(2).split(",")]
            near = ui.did_you_mean(choice.group(1), options)
            message = f"unknown command or value '{choice.group(1)}'"
            hint = f"did you mean `{near}`?" if near else f"valid: {', '.join(options)}"
        ui.err.print(f"[err]error:[/] {message}")
        ui.err.print(f"  [muted]hint:[/] {hint}")
        raise SystemExit(EXIT_USAGE)


def _formatter(prog):
    return argparse.RawDescriptionHelpFormatter(prog, width=min(ui.env.width or 80, 80),
                                                max_help_position=26)


def _common() -> argparse.ArgumentParser:
    """Options every command accepts, before or after the command name."""
    parent = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    group = parent.add_argument_group("general options")
    group.add_argument("-c", "--config", metavar="<path>",
                       help="campaign file (default: ./config.yaml, env SHAKER_CONFIG)")
    group.add_argument("--json", action="store_true", help="machine readable result on stdout")
    group.add_argument("--plain", action="store_true", help="tab separated result, no decoration")
    group.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    group.add_argument("--no-quotes", action="store_true",
                       help="no quote at the end of a stage (env SHAKER_NO_QUOTES)")
    group.add_argument("--color", choices=["auto", "always", "never"], metavar="<when>",
                       help="auto, always, never (default: auto)")
    group.add_argument("-V", "--version", action="version", version=f"shaker {__version__}")
    return parent


def _subparser(sub, name, summary, examples, common):
    return sub.add_parser(
        name, parents=[common], formatter_class=_formatter, help=summary,
        usage=f"shaker {name} [options]",
        description=f"shaker {name} - {summary}\n\nExamples:\n" + "\n".join(
            f"  {line}" for line in examples),
    )


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = _Parser(
        prog="shaker", parents=[common], formatter_class=_formatter, add_help=False,
        usage="shaker <command> [options]",
        description=f"shaker - {brand.TAGLINE}",
    )
    parser.add_argument("-h", "--help", action="store_true", default=False)
    sub = parser.add_subparsers(dest="command", parser_class=_Parser, metavar="<command>")

    p = _subparser(sub, "init", "write an example config.yaml",
                   ["shaker init", "shaker init --config campaigns/pilot/config.yaml"], common)
    p.add_argument("--force", action="store_true", help="overwrite an existing file")

    p = _subparser(sub, "check", "verify tools, versions and force-field files",
                   ["shaker check", "shaker check --pin", "shaker check --json | jq .ok"], common)
    p.add_argument("--pin", action="store_true",
                   help="record the versions found now as the campaign reference")

    p = _subparser(sub, "plan", "expand the campaign without running anything",
                   ["shaker plan", "shaker plan --ns-per-day 4000",
                    "shaker plan --json | jq .totals"], common)
    p.add_argument("--ns-per-day", type=float, metavar="<ns>",
                   help="speed of one replica; default is the speed measured by `run`")

    p = _subparser(sub, "prepare", "build ideal helices and coarse-grain them",
                   ["shaker prepare", "shaker prepare --force"], common)
    p.add_argument("--force", action="store_true", help="run martinize2 again")

    p = _subparser(sub, "build", "build the randomly mixed boxes",
                   ["shaker build", "shaker build --replicas 10",
                    "shaker build --condition <id> --force", "shaker build --dry-run"], common)
    p.add_argument("--condition", action="append", metavar="<id>",
                   help="only this condition (repeatable)")
    p.add_argument("--replicas", type=int, metavar="<n>",
                   help="only the first n replicas of each condition")
    p.add_argument("--force", action="store_true", help="rebuild boxes that already exist")
    p.add_argument("--dry-run", action="store_true", help="list what would be built")

    p = _subparser(sub, "run", "simulate the boxes: minimization, ramp, self-assembly",
                   ["shaker run", "shaker run --jobs 4 --threads 2",
                    "shaker run --until eq4 --replicas 10",
                    "shaker run --mdrun-args '-nb gpu -bonded gpu' --maxh 23.5",
                    "shaker run --backend slurm-array --gres gpu:1 --time 24:00:00 --submit"],
                   common)
    p.add_argument("--condition", action="append", metavar="<id>",
                   help="only this condition (repeatable)")
    p.add_argument("--replicas", type=int, metavar="<n>",
                   help="only the first n replicas of each condition")
    p.add_argument("--replica", action="append", type=int, metavar="<i>",
                   help="only this replica index (repeatable)")
    p.add_argument("--until", metavar="<stage>",
                   help="stop after this stage: em, eq1..eqN, assembly (default)")
    p.add_argument("--jobs", type=int, default=1, metavar="<n>",
                   help="replicas simulated at the same time (default: 1)")
    p.add_argument("--threads", type=int, metavar="<n>", help="threads per replica (mdrun -nt)")
    p.add_argument("--mdrun-args", default="", metavar="<args>",
                   help="extra arguments passed to gmx mdrun, quoted")
    p.add_argument("--maxh", type=float, metavar="<hours>",
                   help="stop cleanly after this wall time; run again to continue")
    p.add_argument("--retries", type=int, default=1, metavar="<n>",
                   help="restarts from checkpoint after a failed stage (default: 1)")
    p.add_argument("--no-check", action="store_true",
                   help="skip the environment verification")
    p.add_argument("--dry-run", action="store_true", help="list what would run")
    g = p.add_argument_group("backend")
    g.add_argument("--backend", choices=["local", "slurm-array"], default="local",
                   metavar="<name>", help="local (default) or slurm-array")
    g.add_argument("--replicas-per-task", type=int, default=1, metavar="<n>",
                   help="slurm: replicas sharing one array task")
    g.add_argument("--partition", metavar="<name>", help="slurm partition")
    g.add_argument("--time", default="24:00:00", metavar="<limit>",
                   help="slurm wall time (default: 24:00:00)")
    g.add_argument("--cpus-per-task", type=int, default=4, metavar="<n>",
                   help="slurm cpus per task (default: 4)")
    g.add_argument("--gres", metavar="<spec>", help="slurm resources, e.g. gpu:1")
    g.add_argument("--max-parallel", type=int, metavar="<n>",
                   help="slurm: array tasks running at once")
    g.add_argument("--setup", metavar="<file>",
                   help="slurm: shell file sourced before the run (modules, venv)")
    g.add_argument("--sbatch-arg", action="append", default=[], metavar="<arg>",
                   help="slurm: extra #SBATCH line content (repeatable)")
    g.add_argument("--submit", action="store_true",
                   help="slurm: call sbatch (default: only write the script)")

    _subparser(sub, "status", "show how many boxes each condition has",
               ["shaker status", "shaker status --plain"], common)

    p = _subparser(sub, "lipids", "browse the bundled lipid library",
                   ["shaker lipids list", "shaker lipids list --head PG",
                    "shaker lipids show POPG"], common)
    lipid_sub = p.add_subparsers(dest="lipid_command", parser_class=_Parser, metavar="<action>")
    q = lipid_sub.add_parser("list", parents=[common], formatter_class=_formatter,
                             help="list every lipid", usage="shaker lipids list [options]",
                             description="shaker lipids list - list every lipid\n\nExamples:\n"
                                         "  shaker lipids list\n  shaker lipids list --head PG")
    q.add_argument("--head", metavar="<group>", help="only this head group (PC, PE, PS, PA, PG)")
    q = lipid_sub.add_parser("show", parents=[common], formatter_class=_formatter,
                             help="show one lipid",
                             usage="shaker lipids show <lipid> [options]",
                             description="shaker lipids show - beads and template of one lipid"
                                         "\n\nExamples:\n  shaker lipids show POPG")
    q.add_argument("name", metavar="<lipid>")

    def collect(p):
        for action in p._actions:
            KNOWN_FLAGS.update(o for o in action.option_strings if o.startswith("--"))
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    collect(child)

    collect(parser)
    return parser


# output helpers -----------------------------------------------------------------------------

def _table(rows, columns, title=None):
    from rich.box import ASCII, ROUNDED
    from rich.table import Table

    table = Table(title=title, title_style="accent", title_justify="left",
                  box=ROUNDED if ui.env.unicode else ASCII, border_style="border",
                  header_style="muted", pad_edge=True, expand=False)
    for key, label, justify in columns:
        table.add_column(label, justify=justify, no_wrap=key in ("condition", "id", "name"))
    for row in rows:
        table.add_row(*["" if row.get(key) is None else str(row.get(key)) for key, _, _ in columns])
    return table


def _emit(args, data, rows, columns, title=None, table_columns=None):
    """Result to stdout: JSON, tab separated text, or a table.

    `columns` is the full set, used by --plain. `table_columns` is the subset that fits a
    terminal; the long condition id already carries peptide, membrane, copies and oligomer.
    """
    if args.json:
        ui.emit(data, as_json=True)
    elif args.plain:
        lines = ["\t".join(label for _, label, _ in columns)]
        lines += ["\t".join("" if r.get(k) is None else str(r.get(k)) for k, _, _ in columns)
                  for r in rows]
        sys.stdout.write("\n".join(lines) + "\n")
    else:
        ui.emit(data, renderable=_table(rows, table_columns or columns, title))


def _type_out(text):
    """Typing effect on an animated terminal; one print everywhere else."""
    from rich.markup import escape

    width = max(ui.env.width or 80, 20) - 1
    if not ui.env.animate or len(text) > width:
        ui.err.print(f"[muted][i]{escape(text)}[/i][/muted]", highlight=False)
        return
    delay = min(0.014, 0.7 / max(len(text), 1))
    for char in text:
        ui.err.print(f"[muted][i]{escape(char)}[/i][/muted]", end="", highlight=False)
        time.sleep(delay)
    ui.err.print()


def _finish(args, stage, summary):
    """Closing line of a stage, then a quote on a terminal."""
    if args.quiet:
        return
    ui.ok(f"{brand.DONE.get(stage, 'done')}: {summary}")
    if stage in NEXT and not args.json:
        ui.err.print(f"  [muted]next:[/] [accent]{NEXT[stage]}[/]", highlight=False)
    if args.json or args.no_quotes or not quotes.enabled() or not ui.env.stderr_tty:
        return
    picked = quotes.pick(stage)
    if picked:
        text, author = picked
        ui.err.print()
        _type_out(f'  "{text}"' + (f"  {author}" if author else ""))


def _splash():
    """Bare `shaker` on a terminal: the glass beside the name, then the help."""
    ui.banner("SHAKER", mark=brand.glass(ui.env.unicode), rule=False,
              mark_colours=brand.glass_colours(ui.theme["accent2"], ui.theme["teal"]))
    ui.err.print()
    _print_help()


def _help_plain() -> str:
    lines = [f"shaker - {TAGLINE}", "", "Usage", "  shaker <command> [options]", "", "Workflow"]
    lines += [f"  {i}  {name:<30}{text}" for i, (name, text) in enumerate(WORKFLOW, start=1)]
    lines += ["", "Inspect"]
    lines += [f"     {name:<30}{text}" for name, text in INSPECT]
    lines += ["", "Examples"] + [f"  {example}" for example in HELP_EXAMPLES]
    lines += ["", "Options"]
    for flag, text, default in HELP_OPTIONS:
        lines.append(f"  {flag:<21}{text}" + (f"  (default: {default})" if default else ""))
    lines += ["", "Environment", f"  {HELP_ENVIRONMENT}", "", "Exit codes",
              "  " + "   ".join(f"{code} {meaning}" for code, meaning in EXIT_CODES),
              "", HELP_FOOTER]
    return "\n".join(lines) + "\n"


def _print_help():
    """Help on stdout: panels on a terminal, aligned plain text anywhere else."""
    if not ui.env.stdout_tty:
        sys.stdout.write(_help_plain())
        return
    from rich.table import Table
    from rich.text import Text

    def grid(*widths):
        table = Table.grid(padding=(0, 2))
        for width in widths:
            table.add_column(no_wrap=True, min_width=width)
        return table

    out = ui.console
    out.print(Text(TAGLINE, style="muted"))
    out.print()
    out.print(Text.assemble(("usage  ", "muted"), "shaker ", ("<command>", "accent2"),
                            (" [options]", "muted")))

    steps = grid(1, 28, 0)
    for i, (name, text) in enumerate(WORKFLOW, start=1):
        steps.add_row(Text(str(i), style="dim"), Text(name, style="accent"),
                      Text(text, style="muted"))
    out.print(ui.panel(steps, "workflow"))

    inspect = grid(1, 28, 0)
    for name, text in INSPECT:
        inspect.add_row(Text(ui.G["info"], style="dim"), Text(name, style="accent"),
                        Text(text, style="muted"))
    out.print(ui.panel(inspect, "inspect"))

    examples = grid(1, 0)
    for example in HELP_EXAMPLES:
        examples.add_row(Text(ui.G["pointer"], style="accent2"), Text(example, style="fg"))
    out.print(ui.panel(examples, "examples"))

    options = grid(19, 0, 0)
    for flag, text, default in HELP_OPTIONS:
        options.add_row(Text(flag, style="accent2"), Text(text, style="fg"),
                        Text(default, style="dim"))
    out.print(ui.panel(options, "options"))

    out.print(Text.assemble(("env    ", "muted"), (HELP_ENVIRONMENT, "dim")))
    codes = Text.assemble(("exit   ", "muted"))
    for code, meaning in EXIT_CODES:
        codes.append(code, style="accent2")
        codes.append(f" {meaning}   ", style="dim")
    out.print(codes)
    out.print(Text(HELP_FOOTER, style="muted"))


# commands -----------------------------------------------------------------------------------

def _config_path(args) -> Path:
    return Path(args.config or os.environ.get("SHAKER_CONFIG") or "config.yaml")


def _load(args, library=None):
    from .config import ConfigError, load_campaign

    path = _config_path(args)
    try:
        return load_campaign(path, library)
    except ConfigError as exc:
        hint = "run `shaker init` to create one, or pass --config <path>" \
            if "not found" in str(exc) else f"edit {path}"
        raise ShakerError(str(exc), hint, EXIT_CONFIG, "config") from None


def cmd_init(args):
    from .config import EXAMPLE

    path = _config_path(args)
    if path.exists() and not args.force:
        raise ShakerError(f"{path} already exists", "pass --force to overwrite it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE)
    if args.json:
        ui.emit({"written": str(path)}, as_json=True)
    else:
        ui.ok(f"wrote {path}")
        ui.err.print(f"  [muted]next:[/] [accent]{NEXT['init']}[/]", highlight=False)
    return 0


def cmd_check(args):
    from . import check

    path = _config_path(args)
    root = path.resolve().parent if path.exists() else Path.cwd()
    if args.pin:
        with ui.status("pinning the environment"):
            lock = check.pin(root)
        if not args.quiet:
            ui.ok(f"wrote {lock.name}")
    with ui.status("checking the environment"):
        checks = check.run_checks(root)
    failures = check.blocking(checks)
    glyph = {"ok": ui.G["ok"], "warn": ui.G["warn"], "fail": ui.G["err"]}
    rows = [{**c, "mark": glyph[c["status"]]} for c in checks]
    _emit(args, {"ok": not failures, "checks": checks}, rows,
          [("status", "status", "left"), ("name", "item", "left"), ("detail", "detail", "left")],
          "environment")
    if failures:
        raise ShakerError(
            f"{len(failures)} check(s) failed: {', '.join(c['name'] for c in failures)}",
            "fix the items marked fail, or re-pin with `shaker check --pin` if the change is intended",
        )
    _finish(args, "check", f"{len(checks)} items, no failures")
    return 0


def cmd_plan(args):
    from . import campaign as ops
    from .lipids import LipidLibrary

    library = LipidLibrary.load()
    campaign = _load(args, library)
    data = ops.plan(campaign, library, args.ns_per_day)
    totals = data["totals"]
    for row in data["conditions"]:
        row["progress"] = f"{row['built']}/{row['replicas']}"
    _emit(args, data, data["conditions"], [
        ("id", "condition", "left"), ("peptide", "peptide", "left"),
        ("membrane", "membrane", "left"), ("lipids", "lipids", "left"),
        ("copies", "copies", "right"), ("oligomer_size", "oligomer", "right"),
        ("peptide_to_lipid", "P:L", "right"), ("beads", "beads", "right"),
        ("box_nm", "box nm", "right"), ("built", "built", "right"),
        ("replicas", "replicas", "right")], "campaign plan", table_columns=[
        ("id", "condition", "left"), ("lipids", "lipids", "left"),
        ("peptide_to_lipid", "P:L", "right"), ("beads", "beads", "right"),
        ("box_nm", "box nm", "right"), ("progress", "built", "right")])
    if not args.quiet and not args.json:
        ui.info(f"{totals['conditions']} conditions x {totals['replicas_per_condition']} replicas "
                f"= {totals['simulations']} simulations, "
                f"{totals['simulated_microseconds']} microseconds in total")
        if totals["compute_days"] is not None:
            ui.info(f"about {totals['compute_days']} days of one replica slot at "
                    f"{totals['ns_per_day']:g} ns/day ({totals['ns_per_day_source']})")
        else:
            ui.info("time not estimated: run a pilot, or pass --ns-per-day")
        if totals["trajectory_gb"] is not None:
            ui.info(f"about {totals['trajectory_gb']} GB of trajectories, from a measured frame")
        if any(not row["beads_exact"] for row in data["conditions"]):
            ui.warn("bead counts are approximate until `shaker prepare` has run")
    _finish(args, "plan", f"{totals['simulations']} simulations")
    return 0


def cmd_prepare(args):
    from . import campaign as ops
    from .peptide.martinize import PrepareError

    campaign = _load(args)
    definitions = campaign.used_peptides()
    if not definitions:
        raise ShakerError("no condition uses a peptide", "add a peptide under conditions.peptide")
    rows = []
    try:
        with ui.progress() as bar:
            task = bar.add_task("coarse-graining", total=len(definitions))
            rows = ops.prepare(campaign, force=args.force, on_done=lambda _: bar.advance(task))
    except PrepareError as exc:
        raise ShakerError(str(exc), "read the martinize2.log named above") from None
    for row in rows:
        if row["warnings"] and not args.quiet:
            ui.warn(f"{row['peptide']}: {row['warnings']}")
    _emit(args, rows, rows, [
        ("peptide", "peptide", "left"), ("length", "length", "right"),
        ("net_charge", "charge", "right"), ("hydrophobic_moment", "moment", "right"),
        ("fraction_hydrophobic", "hydrophobic", "right"), ("cg_beads", "beads", "right"),
        ("reused", "reused", "left")], "peptides")
    _finish(args, "prepare", f"{len(rows)} peptide(s), table in results/peptides.csv")
    return 0


def cmd_build(args):
    from . import campaign as ops
    from .config import ConfigError
    from .lipids import LipidLibrary

    library = LipidLibrary.load()
    campaign = _load(args, library)
    if args.replicas is not None and args.replicas < 1:
        raise ShakerError("--replicas must be at least 1", code=EXIT_USAGE, kind="usage")
    try:
        targets = ops.build_targets(campaign, args.condition, args.replicas)
    except ConfigError as exc:
        raise ShakerError(str(exc), "run `shaker plan` to list the condition ids",
                          EXIT_USAGE, "usage") from None

    columns = [("condition", "condition", "left"), ("replica", "replica", "right"),
               ("status", "status", "left"), ("beads", "beads", "right"),
               ("box_nm", "box nm", "right"), ("message", "message", "left")]
    if args.dry_run:
        rows = []
        for condition, index in targets:
            exists = (ops.replica_dir(campaign, condition, index) / "manifest.json").exists()
            rows.append({"condition": condition.id, "replica": index,
                         "status": "would rebuild" if exists and args.force
                         else "would skip" if exists else "would build"})
        _emit(args, rows, rows, columns[:3], "dry run")
        return 0

    outcomes = []
    with ui.progress() as bar:
        task = bar.add_task("shaking", total=len(targets))
        outcomes = ops.build(campaign, targets, library, force=args.force,
                             on_done=lambda _: bar.advance(task))
    counts = {s: sum(o["status"] == s for o in outcomes) for s in ("built", "skipped", "failed")}
    summary = [{"condition": c.id, **{s: sum(o["status"] == s and o["condition"] == c.id
                                              for o in outcomes) for s in counts}}
               for c in campaign.conditions if any(o["condition"] == c.id for o in outcomes)]
    _emit(args, {"counts": counts, "replicas": outcomes}, summary,
          [("condition", "condition", "left"), ("built", "built", "right"),
           ("skipped", "skipped", "right"), ("failed", "failed", "right")], "boxes")
    if counts["failed"]:
        first = next(o for o in outcomes if o["status"] == "failed")
        raise ShakerError(
            f"{counts['failed']} of {len(outcomes)} boxes failed "
            f"(first: {first['condition']} rep_{first['replica']:03d}: {first['message']})",
            "every failure is recorded in events.jsonl",
        )
    _finish(args, "build", f"{counts['built']} built, {counts['skipped']} already there")
    return 0


def cmd_run(args):
    import shlex
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from . import campaign as ops
    from . import check
    from .config import ConfigError
    from .engine import slurm, stage_names
    from .engine.gromacs import EngineError, gmx_executable

    campaign = _load(args)
    stages = stage_names(campaign.simulation)
    usage = dict(code=EXIT_USAGE, kind="usage")
    if args.until and args.until not in stages:
        raise ShakerError(f"unknown stage '{args.until}'", f"valid: {', '.join(stages)}", **usage)
    for name in ("jobs", "replicas_per_task"):
        if getattr(args, name) < 1:
            raise ShakerError(f"--{name.replace('_', '-')} must be at least 1", **usage)
    if args.replicas is not None and args.replicas < 1:
        raise ShakerError("--replicas must be at least 1", **usage)
    try:
        mdrun_args = shlex.split(args.mdrun_args)
        selected = ops.run_targets(campaign, args.condition, args.replicas, args.replica,
                                   args.until)
    except (ConfigError, ValueError) as exc:
        raise ShakerError(str(exc), "run `shaker status` to list the condition ids",
                          **usage) from None
    todo, target = selected["todo"], selected["target"]
    columns = [("condition", "condition", "left"), ("replica", "replica", "right"),
               ("status", "status", "left"), ("last_stage", "last stage", "left"),
               ("ns_per_day", "ns/day", "right"), ("reason", "reason", "left")]

    if selected["unbuilt"] and not args.quiet:
        ui.warn(f"{len(selected['unbuilt'])} selected replica(s) have no box yet: "
                "run `shaker build` first")
    if args.dry_run:
        rows = [{"condition": c.id, "replica": i, "status": f"would run to {target}"}
                for c, i in todo]
        rows += [{"condition": c.id, "replica": i, "status": "done"} for c, i in selected["done"]]
        _emit(args, rows, rows, columns[:3], "dry run")
        return 0
    if not todo:
        if args.json:
            ui.emit({"counts": {"done": 0, "incomplete": 0, "failed": 0},
                     "already_done": len(selected["done"]), "replicas": []}, as_json=True)
        elif not args.quiet:
            ui.info(f"nothing to run: {len(selected['done'])} replica(s) already at {target}")
        return 0

    if not args.no_check:
        failures = check.blocking(check.run_checks(campaign.root))
        if failures:
            raise ShakerError(
                "environment differs from the pinned one: "
                + ", ".join(f"{c['name']} ({c['detail']})" for c in failures),
                "run `shaker check`, or pass --no-check to run anyway")

    if args.backend == "slurm-array":
        options = []
        if args.until:
            options += ["--until", args.until]
        if args.threads:
            options += ["--threads", str(args.threads)]
        if args.maxh:
            options += ["--maxh", str(args.maxh)]
        if args.mdrun_args:
            options += ["--mdrun-args", args.mdrun_args]
        options += ["--retries", str(args.retries)]
        try:
            script, n_tasks = slurm.write_array(
                campaign.root, campaign.path, [(c.id, i) for c, i in todo],
                replicas_per_task=args.replicas_per_task, run_options=options,
                partition=args.partition, time_limit=args.time,
                cpus_per_task=args.cpus_per_task, gres=args.gres,
                max_parallel=args.max_parallel, setup=args.setup, sbatch_args=args.sbatch_arg)
            answer = slurm.submit(script) if args.submit else None
        except slurm.SlurmError as exc:
            raise ShakerError(str(exc)) from None
        relative = script.relative_to(campaign.root)
        data = {"script": str(script), "tasks": n_tasks, "replicas": len(todo),
                "submitted": bool(answer), "sbatch": answer}
        if args.json:
            ui.emit(data, as_json=True)
        else:
            sys.stdout.write(f"{relative}\n")
        if not args.quiet:
            ui.ok(f"{n_tasks} array task(s) for {len(todo)} replica(s) in {relative}")
            if answer:
                ui.info(answer)
            else:
                ui.err.print(f"  [muted]next:[/] [accent]sbatch {relative}[/]", highlight=False)
        return 0

    try:
        gmx_executable()
    except EngineError as exc:
        raise ShakerError(str(exc), "install GROMACS or set SHAKER_GMX") from None
    options = dict(until=args.until, threads=args.threads, mdrun_args=mdrun_args,
                   maxh=args.maxh, retries=args.retries)
    outcomes = []
    with ui.progress() as bar:
        task = bar.add_task(f"stirring to {target}", total=len(todo))
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(ops.run_one, campaign, c, i, **options) for c, i in todo]
            for future in as_completed(futures):
                outcomes.append(future.result())
                bar.advance(task)
    outcomes.sort(key=lambda o: (o["condition"], o["replica"]))
    counts = {s: sum(o["status"] == s for o in outcomes) for s in ("done", "incomplete", "failed")}
    _emit(args, {"counts": counts, "target": target, "replicas": outcomes}, outcomes, columns,
          f"simulations to {target}", table_columns=[
              ("condition", "condition", "left"), ("replica", "rep", "right"),
              ("status", "status", "left"), ("last_stage", "last stage", "left"),
              ("ns_per_day", "ns/day", "right")])
    for outcome in outcomes:
        if outcome.get("warning") and not args.quiet:
            ui.warn(f"{outcome['condition']} rep_{outcome['replica']:03d}: {outcome['warning']}")
    if counts["failed"]:
        first = next(o for o in outcomes if o["status"] == "failed")
        raise ShakerError(
            f"{counts['failed']} of {len(outcomes)} replicas failed "
            f"(first: {first['condition']} rep_{first['replica']:03d}: {first['reason']})",
            "each replica keeps its own events.jsonl and <stage>_mdrun.log")
    if counts["incomplete"] and not args.quiet:
        ui.info(f"{counts['incomplete']} replica(s) stopped at the wall-time limit: "
                "run `shaker run` again to continue")
    _finish(args, "run", f"{counts['done']} replica(s) at {target}")
    return 0


def cmd_status(args):
    from . import campaign as ops

    campaign = _load(args)
    rows = ops.status(campaign)
    full = [("condition", "condition", "left"), ("peptide", "peptide", "left"),
            ("membrane", "membrane", "left"), ("copies", "copies", "right"),
            ("oligomer_size", "oligomer", "right"), ("replicas", "replicas", "right"),
            ("built", "built", "right"), ("minimized", "minimized", "right"),
            ("equilibrated", "equilibrated", "right"), ("assembled", "assembled", "right"),
            ("failed", "failed", "right"), ("ns_per_day", "ns/day", "right")]
    _emit(args, rows, rows, full, "campaign status", table_columns=[
        ("condition", "condition", "left"), ("replicas", "of", "right"),
        ("built", "built", "right"), ("equilibrated", "equil", "right"),
        ("assembled", "assem", "right"), ("failed", "fail", "right"),
        ("ns_per_day", "ns/day", "right")])
    return 0


def cmd_lipids(args):
    from .lipids import LipidLibrary
    from .lipids.registry import HEAD_GROUPS

    library = LipidLibrary.load()
    action = getattr(args, "lipid_command", None)
    if action == "show":
        if args.name not in library:
            near = ui.did_you_mean(args.name, library.names())
            raise ShakerError(f"lipid '{args.name}' is not in the library",
                              f"did you mean `{near}`?" if near else "run `shaker lipids list`")
        lipid = library.get(args.name)
        rows = [{"bead": atom.name, "type": atom.type, "charge": atom.charge,
                 "x": round(float(xyz[0]), 3), "y": round(float(xyz[1]), 3),
                 "z": round(float(xyz[2]), 3)}
                for atom, xyz in zip(lipid.molecule.atoms, lipid.template)]
        data = {"name": lipid.name, "head": lipid.head, "charge": lipid.charge,
                "source": lipid.source, "beads": rows}
        _emit(args, data, rows, [("bead", "bead", "left"), ("type", "type", "left"),
                                 ("charge", "charge", "right"), ("x", "x nm", "right"),
                                 ("y", "y nm", "right"), ("z", "z nm", "right")],
              f"{lipid.name}  {HEAD_GROUPS.get(lipid.head, lipid.head)}  charge {lipid.charge:+g}")
        return 0
    if action != "list":
        raise ShakerError("missing action for `shaker lipids`", "use `shaker lipids list` or "
                          "`shaker lipids show <lipid>`", EXIT_USAGE, "usage")
    head = getattr(args, "head", None)
    if head and head.upper() not in HEAD_GROUPS:
        raise ShakerError(f"unknown head group '{head}'",
                          f"valid: {', '.join(HEAD_GROUPS)}", EXIT_USAGE, "usage")
    rows = [{"name": l.name, "head": l.head, "charge": l.charge, "beads": l.n_beads,
             "bead_names": " ".join(l.bead_names)}
            for l in (library.get(n) for n in library.names())
            if not head or l.head == head.upper()]
    _emit(args, rows, rows, [("name", "name", "left"), ("head", "head", "left"),
                             ("charge", "charge", "right"), ("beads", "beads", "right"),
                             ("bead_names", "bead names", "left")], "lipid library")
    _finish(args, "lipids", f"{len(rows)} lipids")
    return 0


COMMAND_TABLE = {"init": cmd_init, "check": cmd_check, "plan": cmd_plan, "prepare": cmd_prepare,
                 "build": cmd_build, "run": cmd_run, "status": cmd_status, "lipids": cmd_lipids}


def main(argv=None) -> int:
    ui.install_signal_handlers()
    parser = build_parser()
    args = parser.parse_args(argv)
    for name, default in (("config", None), ("json", False), ("plain", False), ("quiet", False),
                          ("no_quotes", False), ("color", "auto")):
        if not hasattr(args, name):
            setattr(args, name, default)
    ui.apply_color_flag(args.color)
    if args.quiet:
        ui.err.quiet = True

    if args.command is None:
        if args.json:
            ui.emit({"name": "shaker", "version": __version__,
                     "commands": sorted(COMMAND_TABLE)}, as_json=True)
            return 0
        if getattr(args, "help", False) or not (ui.env.stderr_tty and ui.env.stdout_tty):
            _print_help()
        else:
            _splash()
        return 0

    try:
        return COMMAND_TABLE[args.command](args)
    except ShakerError as exc:
        if args.json:
            ui.emit({"error": {"code": exc.kind, "message": str(exc), "hint": exc.hint}},
                    as_json=True)
        ui.err.quiet = False
        ui.err.print(f"[err]error:[/] {exc}", highlight=False)
        if exc.hint:
            ui.err.print(f"  [muted]hint:[/] {exc.hint}", highlight=False)
        return exc.code
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except BrokenPipeError:
            pass
        return 0
    except Exception as exc:  # anything unplanned: one line, traceback only on request
        if os.environ.get("SHAKER_DEBUG"):
            raise
        if args.json:
            ui.emit({"error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}",
                               "hint": None}}, as_json=True)
        ui.err.quiet = False
        ui.err.print(f"[err]error:[/] unexpected {type(exc).__name__}: {exc}", highlight=False)
        ui.err.print("  [muted]hint:[/] set SHAKER_DEBUG=1 to see the traceback", highlight=False)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
