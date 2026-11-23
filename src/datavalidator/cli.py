"""
``dvtool`` command line interface.

Exit codes (for CI/CD gates):
    0  all tables passed (warnings allowed unless --fail-on warn)
    1  at least one table FAILED (or WARNED with --fail-on warn)
    2  at least one table could not be validated (ERROR)
    3  configuration / usage error
"""

from __future__ import annotations

import os
import sys
from importlib import resources
from typing import List, Optional

import click

from datavalidator import __version__
from datavalidator.config import DatasetRef, load_project
from datavalidator.log import setup_logging
from datavalidator.models import Status
from datavalidator.reports import REPORT_FORMATS

STATUS_COLOR = {"PASS": "green", "WARN": "yellow", "FAIL": "red", "ERROR": "magenta", "SKIPPED": "white"}


def _load(config: str):
    try:
        return load_project(config)
    except Exception as exc:  # noqa: BLE001
        click.secho(f"Configuration error: {exc}", fg="red", err=True)
        sys.exit(3)


@click.group()
@click.version_option(__version__, prog_name="dvtool")
@click.option("--log-level", default="WARNING", show_default=True, help="DEBUG, INFO, WARNING, ERROR")
def main(log_level: str) -> None:
    """Cross-cloud data migration validation (AWS <-> Alibaba Cloud and more)."""
    setup_logging(log_level)


# ------------------------------------------------------------------------------ init
@main.command()
@click.argument("path", default="dv-project.yaml")
@click.option("--force", is_flag=True, help="overwrite an existing file")
def init(path: str, force: bool) -> None:
    """Write a commented example project file (AWS -> Alibaba)."""
    if os.path.exists(path) and not force:
        click.secho(f"{path} exists (use --force)", fg="red")
        sys.exit(3)
    text = resources.files("datavalidator").joinpath("templates/aws_to_alibaba.yaml").read_text(encoding="utf-8")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    click.echo(f"Wrote {path}. Edit connections and tables, then run: dvtool test-connections -c {path}")


# ------------------------------------------------------------------------------ test-connections
@main.command("test-connections")
@click.option("-c", "--config", required=True, type=click.Path(exists=True))
@click.option("--connection", "names", multiple=True, help="only these connections")
def test_connections(config: str, names: List[str]) -> None:
    """Check connectivity and credentials for every connection."""
    from datavalidator.runner import ProjectRunner

    runner = ProjectRunner(_load(config))
    res = runner.test_connections(list(names) or None)
    runner.close()
    ok = True
    for name, r in res.items():
        mark = click.style("OK  ", fg="green") if r["ok"] else click.style("FAIL", fg="red")
        lat = f"{r['latency_ms']} ms" if r.get("latency_ms") is not None else ""
        click.echo(f"{mark} {name:<22} {r['type']:<24} {r['cloud']:<8} {lat:>10}  {'' if r['ok'] else r['message']}")
        ok &= r["ok"]
    sys.exit(0 if ok else 2)


# ------------------------------------------------------------------------------ discover
@main.command()
@click.option("-c", "--config", required=True, type=click.Path(exists=True))
@click.option("--connection", required=True)
@click.option("--schema", default=None, help="schema / database / project to list")
@click.option("--table", default=None, help="show columns of this table")
def discover(config: str, connection: str, schema: Optional[str], table: Optional[str]) -> None:
    """List schemas, tables or columns of a connection."""
    from datavalidator.runner import ProjectRunner

    runner = ProjectRunner(_load(config))
    conn = runner.connector(connection)
    try:
        if table:
            info = conn.get_table_info(DatasetRef(connection=connection, table=table, schema=schema))
            pk = conn.primary_keys(DatasetRef(connection=connection, table=table, schema=schema))
            for c in info.columns:
                flag = " [PK]" if c.name in pk else ""
                click.echo(f"{c.ordinal:>3}  {c.name:<32} {c.type.native:<32} {c.logical.value}{flag}")
        elif schema or conn.dialect.name in ("sqlite", "duckdb"):
            for t in conn.list_tables(schema):
                click.echo(t)
        else:
            for s in conn.list_schemas():
                click.echo(s)
    finally:
        runner.close()


# ------------------------------------------------------------------------------ generate-tables
@main.command("generate-tables")
@click.option("-c", "--config", required=True, type=click.Path(exists=True))
@click.option("--source", "source", required=True, help="connection[:schema]")
@click.option("--target", "target", required=True, help="connection[:schema]")
@click.option("--include", default="*", help="glob of table names to include")
@click.option("--exclude", default="", help="glob of table names to exclude")
@click.option("-o", "--output", default="-", help="file to write (default stdout)")
def generate_tables(config: str, source: str, target: str, include: str, exclude: str, output: str) -> None:
    """Generate the 'tables:' section by matching table names across two schemas.

    Primary keys are detected from the catalog where the engine exposes them.
    """
    from datavalidator.discovery import match_tables, tables_yaml
    from datavalidator.runner import ProjectRunner

    runner = ProjectRunner(_load(config))
    s_conn_name, _, s_schema = source.partition(":")
    t_conn_name, _, t_schema = target.partition(":")
    s_conn, t_conn = runner.connector(s_conn_name), runner.connector(t_conn_name)
    try:
        res = match_tables(s_conn, s_schema or None, t_conn, t_schema or None, include, exclude)
        text = tables_yaml(res)
        if output == "-":
            click.echo(text)
        else:
            with open(output, "w", encoding="utf-8") as fh:
                fh.write(text)
            click.echo(f"Wrote {len(res.entries)} table(s) to {output}; {len(res.unmatched)} unmatched.")
    finally:
        runner.close()


# ------------------------------------------------------------------------------ validate
@main.command()
@click.option("-c", "--config", required=True, type=click.Path(exists=True))
@click.option("-t", "--table", "tables", multiple=True, help="table name or glob (repeatable)")
@click.option("--tag", "tags", multiple=True, help="only tables with this tag")
@click.option("--parallel", type=int, default=None, help="tables validated concurrently")
@click.option("--mode", type=click.Choice(["auto", "full", "profile", "sample", "none"]), default=None,
              help="override row-comparison mode for all tables")
@click.option("--out", "out_dir", default=None, help="report directory (default from project)")
@click.option("--format", "formats", default=",".join(REPORT_FORMATS), show_default=True)
@click.option("--fail-on", type=click.Choice(["fail", "warn"]), default="fail", show_default=True)
@click.option("-q", "--quiet", is_flag=True)
def validate(config: str, tables: List[str], tags: List[str], parallel: Optional[int], mode: Optional[str],
             out_dir: Optional[str], formats: str, fail_on: str, quiet: bool) -> None:
    """Run validation and write reports."""
    from datavalidator.reports import markdown_summary, write_all
    from datavalidator.runner import ProjectRunner, select_tables

    project = _load(config)
    selected = select_tables(project, list(tables) or None, list(tags) or None)
    if not selected:
        click.secho("No tables selected.", fg="red")
        sys.exit(3)
    if mode:
        for t in selected:
            t.compare.mode = mode

    def progress(table: str, msg: str) -> None:
        if not quiet:
            click.echo(click.style(f"[{table}] ", fg="cyan") + msg, err=True)

    runner = ProjectRunner(project, progress=progress)
    try:
        run = runner.run(selected, parallel=parallel)
    finally:
        runner.close()

    paths = write_all(run, out_dir or project.output_dir, [f.strip() for f in formats.split(",") if f.strip()])
    click.echo("")
    for t in run.tables:
        click.echo(f"{click.style(t.status.value.ljust(7), fg=STATUS_COLOR[t.status.value], bold=True)} "
                   f"{t.name}  ({t.duration_s}s)")
        for c in t.checks:
            if c.status in (Status.FAIL, Status.ERROR, Status.WARN):
                click.echo(f"        {click.style(c.status.value, fg=STATUS_COLOR[c.status.value])} "
                           f"{c.name}: {c.message}")
    click.echo("")
    click.echo(f"Overall: {click.style(run.status.value, fg=STATUS_COLOR[run.status.value], bold=True)}  "
               f"run {run.run_id}  ({run.duration_s}s)")
    for k, p in paths.items():
        click.echo(f"  {k:<18} {p}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(markdown_summary(run))

    if run.status == Status.ERROR:
        sys.exit(2)
    if run.status == Status.FAIL or (fail_on == "warn" and run.status == Status.WARN):
        sys.exit(1)
    sys.exit(0)


# ------------------------------------------------------------------------------ explain
@main.command()
@click.option("-c", "--config", required=True, type=click.Path(exists=True))
@click.option("-t", "--table", required=True)
def explain(config: str, table: str) -> None:
    """Print the SQL each side will run for a table (for DBA review), without running it."""
    from datavalidator.engine import explain_table
    from datavalidator.runner import ProjectRunner, select_tables

    project = _load(config)
    sel = select_tables(project, [table])
    if not sel:
        click.secho(f"table '{table}' not found", fg="red")
        sys.exit(3)
    tc = sel[0]
    runner = ProjectRunner(project)
    try:
        ex = explain_table(tc, runner.connector(tc.source.connection), runner.connector(tc.target.connection))
        chk = ex.schema_check
        click.secho(f"-- schema check: {chk.status.value}: {chk.message}", fg="cyan")
        for side in ex.sides:
            click.secho(f"\n-- ===== {side.role.upper()} ({side.connection}, {side.dialect}) =====", fg="cyan")
            click.echo("-- table profile (one scan)")
            click.echo(side.profile_sql + ";")
            if side.bucket_sql:
                click.echo(f"\n-- bucket checksums (level {ex.bucket_digits})")
                click.echo(side.bucket_sql + ";")
                click.echo("\n-- row fetch for buckets 'a0','a1'")
                click.echo(side.fetch_sql + ";")
            if side.billing_note and not side.has_filter:
                click.secho(f"-- cost: {side.billing_note}", fg="yellow")
    finally:
        runner.close()


# ------------------------------------------------------------------------------ ui
UI_MISSING = ("The web UI needs Streamlit, which is not installed.\n"
              "Install it with:  pip install \".[ui]\"   (from the repo root)\n"
              "            or:  pip install \"data-migration-validator[ui]\"")


@main.command()
@click.option("-c", "--config", default=None, type=click.Path(exists=True, dir_okay=False),
              help="project YAML to open")
@click.option("--port", default=8501, show_default=True, type=int)
@click.option("--host", default="localhost", show_default=True, help="address to listen on")
def ui(config: Optional[str], port: int, host: str) -> None:
    """Start the web UI (needs the [ui] extra)."""
    import importlib.util
    import subprocess

    if importlib.util.find_spec("streamlit") is None:
        click.secho(UI_MISSING, fg="red", err=True)
        sys.exit(3)
    app = str(resources.files("datavalidator").joinpath("ui/app.py"))
    cmd = [sys.executable, "-m", "streamlit", "run", app, "--server.port", str(port),
           "--server.address", host, "--browser.gatherUsageStats", "false",
           "--server.enableCORS", "true", "--server.enableXsrfProtection", "true"]
    if config:
        cmd += ["--", "--config", os.path.abspath(config)]
    click.echo(f"dvtool UI on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        sys.exit(subprocess.call(cmd))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
