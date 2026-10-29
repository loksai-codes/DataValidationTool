"""
HTML summary report generator.
Generates an executive-grade mission-control report with dark styling,
KPI metric cards, status badges, and checks breakdown.
"""

from typing import Any, Dict, List, Optional

from migration_validator.validation.models import ValidationRunSummary, ValidationStatus


def _get_status_badge(status: ValidationStatus | str) -> str:
    st = status.value if hasattr(status, "value") else str(status)
    colors = {
        "PASSED": ("#10B981", "#064E3B", "rgba(16, 185, 129, 0.15)"),
        "FAILED": ("#EF4444", "#7F1D1D", "rgba(239, 68, 68, 0.15)"),
        "WARNING": ("#F59E0B", "#78350F", "rgba(245, 158, 11, 0.15)"),
        "INCONCLUSIVE": ("#9CA3AF", "#374151", "rgba(156, 163, 175, 0.15)"),
        "CANCELLED": ("#6B7280", "#1F2937", "rgba(107, 114, 128, 0.15)"),
        "RUNNING": ("#3B82F6", "#1D4ED8", "rgba(59, 130, 246, 0.15)"),
    }
    fg, border, bg = colors.get(st, ("#9CA3AF", "#374151", "rgba(156, 163, 175, 0.15)"))
    return f'<span style="display:inline-block; padding: 4px 10px; border-radius: 9999px; font-weight: 700; font-size: 0.75rem; letter-spacing: 0.05em; color: {fg}; background: {bg}; border: 1px solid {border};">{st}</span>'


def generate_html_report(
    summary: ValidationRunSummary,
    source_identifier: str,
    dest_identifier: str,
    discrepancies: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Produce self-contained HTML executive report."""

    rows_html = []
    for c in summary.checks:
        rows_html.append(f"""
        <tr>
            <td style="padding: 12px; font-family: monospace; font-size: 0.85rem; color: #9CA3AF;">{c.check_id}</td>
            <td style="padding: 12px; font-weight: 600; color: #F3F4F6;">{c.check_name}</td>
            <td style="padding: 12px;">{_get_status_badge(c.status)}</td>
            <td style="padding: 12px; color: #D1D5DB; font-family: monospace; font-size: 0.85rem;">{c.source_value or '-'}</td>
            <td style="padding: 12px; color: #D1D5DB; font-family: monospace; font-size: 0.85rem;">{c.dest_value or '-'}</td>
            <td style="padding: 12px; color: #93C5FD; font-family: monospace; font-size: 0.85rem;">{c.difference or '-'}</td>
            <td style="padding: 12px; color: #9CA3AF; font-size: 0.8rem;">{c.tolerance or '-'}</td>
            <td style="padding: 12px; color: #9CA3AF; font-size: 0.8rem;">{c.duration_ms:.1f} ms</td>
        </tr>
        """)

    checks_table_body = "\n".join(rows_html)

    discrepancy_section = ""
    if discrepancies:
        disc_rows = []
        for d in discrepancies[:50]:  # Limit to 50 for preview
            disc_rows.append(f"""
            <tr>
                <td style="padding: 8px 12px; font-family: monospace; color: #F87171;">{d.get('discrepancy_type') or d.get('type')}</td>
                <td style="padding: 8px 12px; font-family: monospace; color: #E5E7EB;">{d.get('record_key') or d.get('key')}</td>
                <td style="padding: 8px 12px; color: #93C5FD;">{d.get('column_name') or d.get('column') or '-'}</td>
                <td style="padding: 8px 12px; font-family: monospace; color: #D1D5DB;">{d.get('source_val') or d.get('source_value') or '-'}</td>
                <td style="padding: 8px 12px; font-family: monospace; color: #D1D5DB;">{d.get('dest_val') or d.get('dest_value') or '-'}</td>
            </tr>
            """)
        discrepancy_section = f"""
        <div style="margin-top: 36px; background: #111827; border: 1px solid #1F2937; border-radius: 12px; padding: 24px;">
            <h3 style="margin-top: 0; color: #F3F4F6; font-size: 1.25rem;">Discrepancy Samples (First 50)</h3>
            <div style="overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 0.9rem;">
                    <thead>
                        <tr style="border-bottom: 1px solid #374151; color: #9CA3AF;">
                            <th style="padding: 8px 12px;">Type</th>
                            <th style="padding: 8px 12px;">Key</th>
                            <th style="padding: 8px 12px;">Column</th>
                            <th style="padding: 8px 12px;">Source</th>
                            <th style="padding: 8px 12px;">Destination</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(disc_rows)}
                    </tbody>
                </table>
            </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Data Migration Validation Report - {summary.run_id}</title>
    <style>
        body {{
            background-color: #0B0F19;
            color: #F3F4F6;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 40px;
        }}
        .container {{
            max-width: 1280px;
            margin: 0 auto;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #1F2937;
            padding-bottom: 24px;
            margin-bottom: 32px;
        }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }}
        .kpi-card {{
            background: #111827;
            border: 1px solid #1F2937;
            border-radius: 12px;
            padding: 20px;
        }}
        .kpi-label {{
            color: #9CA3AF;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }}
        .kpi-value {{
            font-size: 1.75rem;
            font-weight: 700;
            color: #F9FAFB;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}
        th {{
            padding: 12px;
            background: #1F2937;
            color: #9CA3AF;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        tr:nth-child(even) {{
            background-color: #0F172A;
        }}
        tr:hover {{
            background-color: #1E293B;
        }}
        .footer {{
            margin-top: 48px;
            padding-top: 24px;
            border-top: 1px solid #1F2937;
            text-align: center;
            color: #6B7280;
            font-size: 0.85rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1 style="margin: 0 0 8px 0; font-size: 1.75rem;">Data Migration Validation Report</h1>
                <div style="color: #9CA3AF; font-size: 0.9rem;">
                    Run ID: <strong style="color: #E5E7EB;">{summary.run_id}</strong> &bull;
                    Source: <strong style="color: #E5E7EB;">{source_identifier}</strong> &bull;
                    Destination: <strong style="color: #E5E7EB;">{dest_identifier}</strong>
                </div>
            </div>
            <div>
                {_get_status_badge(summary.overall_status)}
            </div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">Records Evaluated</div>
                <div class="kpi-value">{summary.total_records_evaluated:,}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Passed Checks</div>
                <div class="kpi-value" style="color: #10B981;">{summary.passed_checks} <span style="font-size: 1rem; color: #9CA3AF;">/ {summary.total_checks}</span></div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Failed Checks</div>
                <div class="kpi-value" style="color: {'#EF4444' if summary.failed_checks > 0 else '#9CA3AF'};">{summary.failed_checks}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Discrepancies</div>
                <div class="kpi-value" style="color: {'#EF4444' if summary.total_discrepancies > 0 else '#10B981'};">{summary.total_discrepancies:,}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Execution Duration</div>
                <div class="kpi-value">{summary.duration_seconds:.2f}s</div>
            </div>
        </div>

        <div style="background: #111827; border: 1px solid #1F2937; border-radius: 12px; padding: 24px;">
            <h3 style="margin-top: 0; color: #F3F4F6; font-size: 1.25rem;">Validation Checks Execution Details</h3>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Check ID</th>
                            <th>Check Rule</th>
                            <th>Status</th>
                            <th>Source Metric</th>
                            <th>Destination Metric</th>
                            <th>Difference</th>
                            <th>Tolerance</th>
                            <th>Latency</th>
                        </tr>
                    </thead>
                    <tbody>
                        {checks_table_body}
                    </tbody>
                </table>
            </div>
        </div>

        {discrepancy_section}

        <div class="footer">
            Generated autonomously by Enterprise Data Migration Validator &bull; Execution Timestamp: {summary.started_at}
        </div>
    </div>
</body>
</html>
"""
    return html
