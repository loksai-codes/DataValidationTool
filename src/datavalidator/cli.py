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
import yaml

from datavalidator import __version__
from datavalidator.config import DatasetRef, load_project
from datavalidator.log import setup_logging
from datavalidator.models import Status

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
    import fnmatch

    from datavalidator.runner import ProjectRunner

    runner = ProjectRunner(_load(config))
    s_conn_name, _, s_schema = source.partition(":")
    t_conn_name, _, t_schema = target.partition(":")
    s_conn, t_conn = runner.connector(s_conn_name), runner.connector(t_conn_name)
    try:
        s_tables = s_conn.list_tables(s_schema or None)
        t_tables = {t.lower(): t for t in t_conn.list_tables(t_schema or None)}
        entries, unmatched, no_pk = [], [], []
        for st in s_tables:
            if not fnmatch.fnmatch(st, include) or (exclude and fnmatch.fnmatch(st, exclude)):
                continue
            tt = t_tables.get(st.lower())
            if not tt:
                unmatched.append(st)
                continue
            s_ref = {"connection": s_conn_name, "table": f"{s_schema}.{st}" if s_schema else st}
            t_ref = {"connection": t_conn_name, "table": f"{t_schema}.{tt}" if t_schema else tt}
            entry = {"name": st, "source": s_ref, "target": t_ref}
            pk = s_conn.primary_keys(DatasetRef(**s_ref)) or t_conn.primary_keys(DatasetRef(**t_ref))
            entry["keys"] = pk
            if not pk:
                no_pk.append(st)
            entries.append(entry)
        text = yaml.safe_dump({"tables": entries}, sort_keys=False, allow_unicode=True)
        if no_pk:
            text += ("\n# No primary key found (set 'keys' to enable row-level comparison):\n"
                     + "".join(f"#   - {u}\n" for u in no_pk))
        if unmatched:
            text += "\n# Source tables with no matching target table:\n" + "".join(f"#   - {u}\n" for u in unmatched)
        if output == "-":
            click.echo(text)
        else:
            with open(output, "w", encoding="utf-8") as fh:
                fh.write(text)
            click.echo(f"Wrote {len(entries)} table(s) to {output}; {len(unmatched)} unmatched.")
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
@click.option("--format", "formats", default="json,html,csv,junit,md", show_default=True)
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
    from datavalidator.engine import TableValidator
    from datavalidator.runner import ProjectRunner, select_tables

    project = _load(config)
    sel = select_tables(project, [table])
    if not sel:
        click.secho(f"table '{table}' not found", fg="red")
        sys.exit(3)
    tc = sel[0]
    runner = ProjectRunner(project)
    try:
        v = TableValidator(tc, runner.connector(tc.source.connection), runner.connector(tc.target.connection))
        s_info = v.src.conn.get_table_info(tc.source)
        t_info = v.tgt.conn.get_table_info(tc.target)
        chk = v._map_columns(s_info, t_info)
        click.secho(f"-- schema check: {chk.status.value}: {chk.message}", fg="cyan")
        for side in (v.src, v.tgt):
            click.secho(f"\n-- ===== {side.role.upper()} ({side.conn.name}, {side.d.name}) =====", fg="cyan")
            click.echo("-- table profile (one scan)")
            click.echo(v._profile_sql(side) + ";")
            if v.src.key_cols:
                click.echo(f"\n-- bucket checksums (level {tc.compare.initial_bucket_hex_digits})")
                click.echo(v._bucket_sql(side, tc.compare.initial_bucket_hex_digits, None) + ";")
                click.echo("\n-- row fetch for buckets 'a0','a1'")
                click.echo(v._fetch_sql(side, ["a0", "a1"]) + ";")
    finally:
        runner.close()


if __name__ == "__main__":  # pragma: no cover
    main()
