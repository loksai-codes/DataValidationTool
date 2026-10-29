"""Self-contained HTML report exporter with modern styling."""
from __future__ import annotations

import html
from pathlib import Path

from data_validator.reporting.models import ValidationReportData


def export_html_report(
    report_data: ValidationReportData, output_dir: str | Path = "reports"
) -> Path:
    """Generate and write a rich, self-contained HTML report."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{report_data.summary.run_id}_report.html"
    filepath = out_dir / filename

    s = report_data.summary
    is_success = (s.total_discrepancies == 0 and s.status == "Completed")
    status_color = "#10b981" if is_success else "#f59e0b" if s.total_discrepancies > 0 else "#ef4444"
    status_bg = "#ecfdf5" if is_success else "#fffbeb" if s.total_discrepancies > 0 else "#fef2f2"

    # Format checks table
    checks_rows = ""
    for chk in report_data.checks:
        c_status = chk.get("status", "")
        c_badge = (
            f"<span class='badge badge-success'>{c_status}</span>"
            if c_status == "Passed"
            else f"<span class='badge badge-danger'>{c_status}</span>"
        )
        checks_rows += f"""
        <tr>
            <td><strong>{html.escape(chk.get('check_name', ''))}</strong></td>
            <td>{c_badge}</td>
            <td>{html.escape(chk.get('summary', '') or '-')}</td>
            <td><small>{chk.get('executed_at', '')[:19]}</small></td>
        </tr>
        """

    # Format discrepancies table
    disc_rows = ""
    for disc in report_data.discrepancies[:100]:
        disc_type = disc.get("type", "")
        type_badge = f"<span class='badge badge-warning'>{html.escape(disc_type)}</span>"
        disc_rows += f"""
        <tr>
            <td><code>{html.escape(str(disc.get('partition_id', '')))}</code></td>
            <td>{type_badge}</td>
            <td><code>{html.escape(str(disc.get('key', '')))}</code></td>
            <td>{html.escape(str(disc.get('column', '')) or '-')}</td>
            <td class='val-source'>{html.escape(str(disc.get('source_value', '')) or '-')}</td>
            <td class='val-target'>{html.escape(str(disc.get('target_value', '')) or '-')}</td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Validation Report — {html.escape(s.job_name)}</title>
    <style>
        :root {{
            --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            --bg: #f8fafc;
            --card-bg: #ffffff;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --border: #e2e8f0;
            --primary: #3b82f6;
        }}
        body {{
            font-family: var(--font);
            background: var(--bg);
            color: var(--text-main);
            margin: 0;
            padding: 2rem;
            line-height: 1.5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            background: var(--card-bg);
            border-radius: 12px;
            padding: 1.5rem 2rem;
            border: 1px solid var(--border);
            margin-bottom: 1.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .header-title h1 {{
            margin: 0;
            font-size: 1.5rem;
            color: var(--text-main);
        }}
        .header-title p {{
            margin: 0.25rem 0 0 0;
            color: var(--text-muted);
            font-size: 0.9rem;
        }}
        .status-pill {{
            padding: 0.5rem 1.25rem;
            border-radius: 9999px;
            font-weight: 600;
            font-size: 0.95rem;
            background: {status_bg};
            color: {status_color};
            border: 1px solid {status_color};
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1rem;
            margin-bottom: 1.5rem;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 1.25rem;
        }}
        .card-label {{
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 0.25rem;
        }}
        .card-value {{
            font-size: 1.6rem;
            font-weight: 700;
            color: var(--text-main);
        }}
        .section {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
        }}
        .section-title {{
            font-size: 1.15rem;
            font-weight: 600;
            margin-top: 0;
            margin-bottom: 1rem;
            border-bottom: 1px solid var(--border);
            padding-bottom: 0.5rem;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
        }}
        th, td {{
            text-align: left;
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--border);
        }}
        th {{
            background: #f1f5f9;
            color: var(--text-muted);
            font-weight: 600;
        }}
        code {{
            background: #f1f5f9;
            padding: 0.2rem 0.4rem;
            border-radius: 4px;
            font-size: 0.85em;
        }}
        .val-source {{
            color: #b91c1c;
            background: #fef2f2;
            font-family: monospace;
        }}
        .val-target {{
            color: #15803d;
            background: #f0fdf4;
            font-family: monospace;
        }}
        .badge {{
            display: inline-block;
            padding: 0.2rem 0.6rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
        .badge-success {{ background: #dcfce7; color: #15803d; }}
        .badge-danger {{ background: #fee2e2; color: #b91c1c; }}
        .badge-warning {{ background: #fef3c7; color: #b45309; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-title">
                <h1>Validation Report: {html.escape(s.job_name)}</h1>
                <p>Run ID: <code>{html.escape(s.run_id)}</code> &bull; Source: <code>{html.escape(s.source_table)}</code> &bull; Target: <code>{html.escape(s.target_table)}</code></p>
            </div>
            <div class="status-pill">{html.escape(s.status)}</div>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-label">Source Rows</div>
                <div class="card-value">{s.rows_source:,}</div>
            </div>
            <div class="card">
                <div class="card-label">Target Rows</div>
                <div class="card-value">{s.rows_target:,}</div>
            </div>
            <div class="card">
                <div class="card-label">Rows Compared</div>
                <div class="card-value">{s.rows_compared:,}</div>
            </div>
            <div class="card">
                <div class="card-label">Total Discrepancies</div>
                <div class="card-value" style="color: {status_color}">{s.total_discrepancies:,}</div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-label">Missing in Target</div>
                <div class="card-value">{s.missing_count:,}</div>
            </div>
            <div class="card">
                <div class="card-label">Extra in Target</div>
                <div class="card-value">{s.extra_count:,}</div>
            </div>
            <div class="card">
                <div class="card-label">Changed Records</div>
                <div class="card-value">{s.changed_count:,}</div>
            </div>
            <div class="card">
                <div class="card-label">Duplicate / Null Keys</div>
                <div class="card-value">{s.duplicate_key_count + s.null_key_count:,}</div>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">Validation Checks Overview</h2>
            <table>
                <thead>
                    <tr>
                        <th>Check Name</th>
                        <th>Status</th>
                        <th>Summary</th>
                        <th>Executed At</th>
                    </tr>
                </thead>
                <tbody>
                    {checks_rows}
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2 class="section-title">Detected Discrepancies Sample ({len(report_data.discrepancies)} shown)</h2>
            <table>
                <thead>
                    <tr>
                        <th>Partition</th>
                        <th>Type</th>
                        <th>Unique Key</th>
                        <th>Column</th>
                        <th>Source Value</th>
                        <th>Target Value</th>
                    </tr>
                </thead>
                <tbody>
                    {disc_rows or "<tr><td colspan='6' style='text-align:center;color:#64748b;'>No discrepancies detected. Data is 100% matched!</td></tr>"}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    return filepath
