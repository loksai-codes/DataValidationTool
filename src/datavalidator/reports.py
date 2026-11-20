"""Report writers: JSON, HTML, CSV, JUnit XML, Markdown — plus run history."""

from __future__ import annotations

import csv
import json
import os
from typing import Dict, List
from xml.sax.saxutils import escape, quoteattr

from jinja2 import Environment, select_autoescape

from datavalidator.models import RunResult, Status


def write_json(run: RunResult, path: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(run.model_dump_json(indent=2))
    return path


def write_csv(run: RunResult, out_dir: str) -> List[str]:
    checks_path = os.path.join(out_dir, "checks.csv")
    with open(checks_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["table", "check", "name", "status", "source_value", "target_value", "message", "duration_s"])
        for t in run.tables:
            for c in t.checks:
                w.writerow([t.name, c.check, c.name, c.status.value, _s(c.source_value), _s(c.target_value),
                            c.message, c.duration_s])
    disc_path = os.path.join(out_dir, "discrepancies.csv")
    with open(disc_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["table", "kind", "key", "column", "source_value", "target_value"])
        for t in run.tables:
            for d in t.discrepancies:
                w.writerow([t.name, d.kind, d.key, d.column or "", d.source_value, d.target_value])
    return [checks_path, disc_path]


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return str(v)


def write_junit(run: RunResult, path: str) -> str:
    """JUnit XML so CI systems (Jenkins, GitLab, GitHub Actions, Azure DevOps) show per-check results."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    total = sum(len(t.checks) for t in run.tables)
    fails = sum(1 for t in run.tables for c in t.checks if c.status == Status.FAIL)
    errs = sum(1 for t in run.tables for c in t.checks if c.status == Status.ERROR)
    lines.append(f'<testsuites name={quoteattr("datavalidator " + run.project)} tests="{total}" '
                 f'failures="{fails}" errors="{errs}" time="{run.duration_s}">')
    for t in run.tables:
        tf = sum(1 for c in t.checks if c.status == Status.FAIL)
        te = sum(1 for c in t.checks if c.status == Status.ERROR)
        lines.append(f'  <testsuite name={quoteattr(t.name)} tests="{len(t.checks)}" failures="{tf}" '
                     f'errors="{te}" time="{t.duration_s}">')
        for c in t.checks:
            lines.append(f'    <testcase classname={quoteattr(t.name)} name={quoteattr(c.name)} time="{c.duration_s}">')
            if c.status == Status.FAIL:
                lines.append(f'      <failure message={quoteattr(c.message[:500])}>{escape(c.message)}</failure>')
            elif c.status == Status.ERROR:
                lines.append(f'      <error message={quoteattr(c.message[:500])}>{escape(c.message)}</error>')
            elif c.status == Status.SKIPPED:
                lines.append(f'      <skipped message={quoteattr(c.message[:500])}/>')
            elif c.status == Status.WARN:
                lines.append(f'      <system-out>{escape("WARNING: " + c.message)}</system-out>')
            lines.append("    </testcase>")
        lines.append("  </testsuite>")
    lines.append("</testsuites>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def markdown_summary(run: RunResult) -> str:
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "ERROR": "🛑", "SKIPPED": "⏭️"}
    out = [f"## Migration validation — {run.project}",
           f"**Overall: {icon[run.status.value]} {run.status.value}** · run `{run.run_id}` · "
           f"{len(run.tables)} table(s) · {run.duration_s}s", "",
           "| Table | Status | Source rows | Target rows | Failed checks |", "|---|---|---:|---:|---|"]
    for t in run.tables:
        failed = ", ".join(c.check for c in t.checks if c.status in (Status.FAIL, Status.ERROR)) or "—"
        out.append(f"| {t.name} | {icon[t.status.value]} {t.status.value} | {t.stats.get('source_rows', '')} | "
                   f"{t.stats.get('target_rows', '')} | {failed} |")
    return "\n".join(out) + "\n"


_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Migration validation · {{ run.project }}</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e4e4df;--pass:#1f7a4d;--warn:#a66300;--fail:#b3261e;--err:#6d1b7b;--skip:#7a7a74}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
main{max-width:1200px;margin:0 auto;padding:32px 24px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}
.mute{color:var(--mute)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.kpi b{display:block;font-size:22px}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;color:#fff}
.PASS{background:var(--pass)}.WARN{background:var(--warn)}.FAIL{background:var(--fail)}.ERROR{background:var(--err)}.SKIPPED{background:var(--skip)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:8px 0}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{background:#efefea;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:0}code{font:12px ui-monospace,Menlo,Consolas,monospace;background:#efefea;padding:1px 4px;border-radius:4px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:12px 0}
summary{cursor:pointer;font-weight:600}.small{font-size:12px}td.num{text-align:right;font-variant-numeric:tabular-nums}
</style></head><body><main>
<h1>Migration validation — {{ run.project }}</h1>
<div class="mute">Run <code>{{ run.run_id }}</code> · started {{ run.started_at }} · {{ run.duration_s }}s · datavalidator {{ run.tool_version }}</div>
<div class="grid">
<div class="kpi">Overall<b><span class="badge {{ run.status.value }}">{{ run.status.value }}</span></b></div>
<div class="kpi">Tables<b>{{ run.tables|length }}</b></div>
{% for k in ['PASS','WARN','FAIL','ERROR'] %}<div class="kpi">{{ k }}<b>{{ counts[k] }}</b></div>{% endfor %}
</div>
<h2>Connections</h2>
<table><tr><th>Name</th><th>Type</th><th>Cloud</th></tr>
{% for n, c in run.connections.items() %}<tr><td><code>{{ n }}</code></td><td>{{ c.type }}</td><td>{{ c.cloud }}</td></tr>{% endfor %}</table>
<h2>Tables</h2>
<table><tr><th>Table</th><th>Status</th><th>Source</th><th>Target</th><th class="num">Source rows</th><th class="num">Target rows</th><th class="num">Seconds</th></tr>
{% for t in run.tables %}<tr><td><a href="#t-{{ loop.index }}">{{ t.name }}</a></td><td><span class="badge {{ t.status.value }}">{{ t.status.value }}</span></td>
<td class="small">{{ t.source }}</td><td class="small">{{ t.target }}</td><td class="num">{{ '{:,}'.format(t.stats.source_rows) if t.stats.source_rows is defined else '' }}</td>
<td class="num">{{ '{:,}'.format(t.stats.target_rows) if t.stats.target_rows is defined else '' }}</td><td class="num">{{ t.duration_s }}</td></tr>{% endfor %}</table>
{% for t in run.tables %}
<details id="t-{{ loop.index }}" {% if t.status.value in ['FAIL','ERROR'] %}open{% endif %}>
<summary><span class="badge {{ t.status.value }}">{{ t.status.value }}</span> {{ t.name }}</summary>
<p class="small mute">{{ t.source }} → {{ t.target }} · keys: {{ (t.stats.get('keys') or [])|join(', ') or 'none' }} ({{ t.stats.get('key_source','') }}) · rows transferred: {{ t.stats.get('rows_fetched_source',0) + t.stats.get('rows_fetched_target',0) }}</p>
<table><tr><th>Check</th><th>Status</th><th>Source</th><th>Target</th><th>Result</th></tr>
{% for c in t.checks %}<tr><td>{{ c.name }}</td><td><span class="badge {{ c.status.value }}">{{ c.status.value }}</span></td>
<td class="small">{{ fmt(c.source_value) }}</td><td class="small">{{ fmt(c.target_value) }}</td><td>{{ c.message }}</td></tr>{% endfor %}</table>
{% for c in t.checks %}{% if c.details.get('mismatches') %}
<p class="small"><b>{{ c.name }} — differences</b></p>
<table>{% set rows = c.details['mismatches'] %}<tr>{% for k in rows[0].keys() %}<th>{{ k }}</th>{% endfor %}</tr>
{% for r in rows[:200] %}<tr>{% for v in r.values() %}<td class="small">{{ v }}</td>{% endfor %}</tr>{% endfor %}</table>
{% endif %}{% if c.check == 'data' and c.details.get('column_mismatches') %}
<p class="small"><b>Mismatching rows per column</b></p><table><tr><th>Column</th><th class="num">Rows</th></tr>
{% for k, v in c.details['column_mismatches'].items() %}<tr><td>{{ k }}</td><td class="num">{{ '{:,}'.format(v) }}</td></tr>{% endfor %}</table>
{% endif %}{% if c.check == 'schema' and c.details.get('columns') %}
<details><summary class="small">Column mapping ({{ c.details['columns']|length }})</summary><table><tr><th>Source</th><th>Type</th><th>Target</th><th>Type</th><th>Compared as</th><th>Compatibility</th></tr>
{% for r in c.details['columns'] %}<tr><td>{{ r.source }}</td><td class="small">{{ r.source_type }}</td><td>{{ r.target }}</td><td class="small">{{ r.target_type }}</td><td class="small">{{ r.compare_as }}</td><td class="small">{{ r.compatibility }} {{ r.note }}</td></tr>{% endfor %}</table></details>
{% endif %}{% endfor %}
{% if t.discrepancies %}<p class="small"><b>Sample discrepancies ({{ t.discrepancies|length }})</b></p>
<table><tr><th>Kind</th><th>Key</th><th>Column</th><th>Source</th><th>Target</th></tr>
{% for d in t.discrepancies[:300] %}<tr><td class="small">{{ d.kind }}</td><td><code>{{ d.key }}</code></td><td>{{ d.column or '' }}</td><td class="small">{{ d.source_value if d.source_value is not none else 'NULL' }}</td><td class="small">{{ d.target_value if d.target_value is not none else 'NULL' }}</td></tr>{% endfor %}</table>{% endif %}
{% if t.error %}<p><b>Error:</b> <code>{{ t.error }}</code></p>{% endif %}
</details>{% endfor %}
</main></body></html>"""


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):
        return ", ".join(f"{k}: {x}" for k, x in v.items())
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def write_html(run: RunResult, path: str) -> str:
    env = Environment(autoescape=select_autoescape(default=True))
    html = env.from_string(_HTML).render(run=run, counts=run.counts, fmt=_fmt)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


def write_all(run: RunResult, out_dir: str, formats: List[str]) -> Dict[str, str]:
    run_dir = os.path.join(out_dir, run.run_id)
    os.makedirs(run_dir, exist_ok=True)
    out: Dict[str, str] = {}
    if "json" in formats:
        out["json"] = write_json(run, os.path.join(run_dir, "results.json"))
    if "html" in formats:
        out["html"] = write_html(run, os.path.join(run_dir, "report.html"))
    if "csv" in formats:
        paths = write_csv(run, run_dir)
        out["csv"] = paths[0]
        out["csv_discrepancies"] = paths[1]
    if "junit" in formats:
        out["junit"] = write_junit(run, os.path.join(run_dir, "junit.xml"))
    if "md" in formats:
        p = os.path.join(run_dir, "summary.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(markdown_summary(run))
        out["md"] = p
    # append-only run history for trend tracking across migration waves
    with open(os.path.join(out_dir, "history.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run_id": run.run_id, "project": run.project, "started_at": run.started_at,
                             "status": run.status.value, "duration_s": run.duration_s,
                             "tables": {t.name: t.status.value for t in run.tables}}) + "\n")
    return out
