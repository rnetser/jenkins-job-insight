"""HTML report generation for Jenkins job analysis results.

Generates a self-contained, dark-themed HTML report from analysis results.
All CSS is inlined so the report can be opened directly in any browser
without external dependencies.
"""

import base64
import html
import json
import re
from collections.abc import Callable

from jenkins_job_insight.models import (
    AnalysisDetail,
    AnalysisResult,
    ChildJobAnalysis,
    CodeFix,
    FailureAnalysis,
    JiraMatch,
    ProductBugReport,
)
from jenkins_job_insight.storage import count_all_failures

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#0d1117"/>
  <circle cx="13" cy="13" r="7" fill="none" stroke="#58a6ff" stroke-width="2.5"/>
  <line x1="18" y1="18" x2="26" y2="26" stroke="#58a6ff" stroke-width="2.5" stroke-linecap="round"/>
  <circle cx="13" cy="11" r="1.5" fill="#f85149"/>
  <path d="M10 15.5 Q13 18 16 15.5" fill="none" stroke="#f85149" stroke-width="1.5" stroke-linecap="round"/>
</svg>"""

FAVICON_DATA_URI = (
    "data:image/svg+xml;base64," + base64.b64encode(FAVICON_SVG.encode()).decode()
)


def _common_css() -> str:
    """Return the shared CSS rules used by both the analysis report and dashboard.

    Includes CSS custom properties, base element resets, body, container,
    sticky header, and report footer styles. Page-specific rules are added
    by each caller.

    Returns:
        A CSS string (without ``<style>`` tags) ready to embed directly.
    """
    return """\
:root {
    --bg-primary: #0d1117;
    --bg-secondary: #161b22;
    --bg-tertiary: #21262d;
    --bg-hover: #292e36;
    --border: #30363d;
    --text-primary: #e6edf3;
    --text-secondary: #8b949e;
    --text-muted: #6e7681;
    --accent-red: #f85149;
    --accent-red-bg: rgba(248, 81, 73, 0.12);
    --accent-green: #3fb950;
    --accent-blue: #58a6ff;
    --accent-blue-bg: rgba(88, 166, 255, 0.08);
    --accent-yellow: #d29922;
    --accent-orange: #f0883e;
    --accent-orange-bg: rgba(240, 136, 62, 0.12);
    --accent-purple: #bc8cff;
    --font-mono: 'SF Mono', 'Cascadia Code', 'Fira Code', 'JetBrains Mono', Consolas, monospace;
    --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
    --radius: 8px;
}
*,*::before,*::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: var(--font-sans);
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.6;
    min-height: 100vh;
}
.container { max-width: 1200px; margin: 0 auto; padding: 0 24px 60px; }

/* Header */
.sticky-header {
    position: sticky;
    top: 0;
    z-index: 100;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    margin: 0 -24px 32px;
}
.header-content { max-width: 1200px; margin: 0 auto; display: flex; flex-wrap: wrap; gap: 8px; }
.header-content h1 { font-size: 20px; font-weight: 700; flex-shrink: 0; }

/* Footer */
.report-footer {
    margin-top: 48px;
    padding: 24px 0;
    border-top: 1px solid var(--border);
    font-size: 12px;
    color: var(--text-muted);
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
}
.report-footer a { color: var(--accent-blue); text-decoration: none; }
.report-footer a:hover { text-decoration: underline; }

/* Env chips */
.env-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-left: auto; }
.env-chip {
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 6px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text-secondary);
    text-decoration: none;
}
.env-chip a { color: var(--accent-blue); text-decoration: none; }
.env-chip a:hover { text-decoration: underline; }

/* Section titles */
.section-title {
    font-size: 14px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-muted);
    margin: 32px 0 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
}

/* Responsive */
@media (max-width: 768px) {
    .header-content { flex-direction: column; align-items: flex-start; }
}"""


def _modal_css() -> str:
    """Return CSS for the confirmation modal dialog.

    Used by both the analysis report and dashboard pages.

    Returns:
        A CSS string (without ``<style>`` tags) ready to embed directly.
    """
    return """\
/* Modal popup */
.modal-overlay {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0,0,0,0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
}
.modal-dialog {
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px;
    max-width: 400px;
    width: 90%;
    text-align: center;
}
.modal-dialog h3 {
    font-size: 16px;
    color: var(--text-primary);
    margin-bottom: 8px;
}
.modal-dialog p {
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: 20px;
}
.modal-actions {
    display: flex;
    gap: 12px;
    justify-content: center;
}
.modal-btn {
    padding: 8px 20px;
    font-size: 13px;
    font-weight: 600;
    border-radius: 6px;
    cursor: pointer;
    border: 1px solid var(--border);
    transition: background 0.15s, border-color 0.15s;
}
.modal-btn-cancel {
    background: var(--bg-tertiary);
    color: var(--text-secondary);
}
.modal-btn-cancel:hover {
    background: var(--bg-hover);
}
.modal-btn-danger {
    background: rgba(248,81,73,0.12);
    color: var(--accent-red);
    border-color: var(--accent-red);
}
.modal-btn-danger:hover {
    background: rgba(248,81,73,0.25);
}"""


def _controls_css() -> str:
    """Return CSS for search, pagination, and per-page controls.

    Used by both the dashboard and history pages.

    Returns:
        A CSS string (without ``<style>`` tags) ready to embed directly.
    """
    return """\
/* Controls bar (search + per-page) */
.controls-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 20px;
    flex-wrap: wrap;
}
.search-input {
    flex: 1;
    min-width: 200px;
    padding: 10px 14px;
    font-size: 14px;
    font-family: var(--font-sans);
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-primary);
    outline: none;
    transition: border-color 0.15s;
}
.search-input::placeholder { color: var(--text-muted); }
.search-input:focus { border-color: var(--accent-blue); }
.per-page-select {
    padding: 10px 14px;
    font-size: 14px;
    font-family: var(--font-sans);
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-primary);
    cursor: pointer;
    outline: none;
}
.per-page-select:focus { border-color: var(--accent-blue); }
/* Pagination controls */
.pagination-controls {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 16px;
    margin-top: 24px;
    padding: 16px 0;
}
.pagination-btn {
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
    font-family: var(--font-sans);
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-primary);
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
}
.pagination-btn:hover:not(:disabled) {
    background: var(--bg-hover);
    border-color: var(--accent-blue);
}
.pagination-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
}
.page-info {
    font-size: 13px;
    font-family: var(--font-mono);
    color: var(--text-secondary);
}"""


def _modal_js() -> str:
    """Return JavaScript for the ``showConfirmModal`` function.

    Used by both the analysis report and dashboard pages.

    Returns:
        A JavaScript string (without ``<script>`` tags) ready to embed directly.
    """
    return """\
function showConfirmModal(title, message, onConfirm, opts) {
    opts = opts || {};
    var confirmLabel = opts.confirmLabel || 'Delete';
    var cancelLabel = opts.cancelLabel || 'Cancel';
    var confirmOnly = opts.confirmOnly || false;

    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = '<div class="modal-dialog">' +
        '<h3 id="modal-title"></h3>' +
        '<p id="modal-message"></p>' +
        '<div class="modal-actions">' +
        (confirmOnly ? '' : '<button class="modal-btn modal-btn-cancel" id="modal-cancel"></button>') +
        '<button class="modal-btn modal-btn-danger" id="modal-confirm"></button>' +
        '</div></div>';
    document.body.appendChild(overlay);
    overlay.querySelector('#modal-title').textContent = title;
    overlay.querySelector('#modal-message').textContent = message;
    overlay.querySelector('#modal-confirm').textContent = confirmLabel;

    if (!confirmOnly) {
        overlay.querySelector('#modal-cancel').textContent = cancelLabel;
        overlay.querySelector('#modal-cancel').onclick = function() { overlay.remove(); };
    }
    overlay.querySelector('#modal-confirm').onclick = function() { overlay.remove(); onConfirm(); };
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };
}"""


def _username_helper_js() -> str:
    """Return JavaScript helper for reading jji_username cookie.

    Defines a global ``getJjiUsername()`` function that safely decodes
    the jji_username cookie with try/catch. Returns empty string on
    failure or if not set.

    Returns:
        A JavaScript string (without ``<script>`` tags) ready to embed directly.
    """
    return """
function getJjiUsername() {
    try {
        return decodeURIComponent((document.cookie.match(/jji_username=([^;]+)/) || [])[1] || '');
    } catch(e) {
        return '';
    }
}
"""


def _user_badge_js() -> str:
    """Return JavaScript for the fixed top-right user badge.

    Used by the report page, dashboard, and history page.

    Returns:
        A JavaScript string (without ``<script>`` tags) ready to embed directly.
    """
    return """
(function() {
    var username = getJjiUsername();
    if (username) {
        var userBadge = document.createElement('div');
        userBadge.style.cssText = 'position:fixed;top:12px;right:24px;z-index:200;display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:4px 12px;border-radius:12px;background:rgba(188,140,255,0.15);border:1px solid var(--accent-purple);color:var(--accent-purple);font-weight:600;white-space:nowrap;';
        var escaped = document.createElement('span');
        escaped.textContent = username;
        userBadge.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>' + escaped.innerHTML;
        document.body.appendChild(userBadge);
    }
})();
"""


def _classification_colors_js() -> str:
    """Return JavaScript for the ``getClassificationStyle`` function.

    Centralizes the classification-to-color mapping so all pages use a
    single source of truth.  Call sites should invoke
    ``getClassificationStyle(cls)`` to get the CSS style string.

    Returns:
        A JavaScript string (without ``<script>`` tags) ready to embed directly.
    """
    return """\
function getClassificationStyle(cls) {
    var styles = {
        'FLAKY': 'background:rgba(210,153,34,0.15);color:var(--accent-yellow);',
        'REGRESSION': 'background:rgba(248,81,73,0.12);color:var(--accent-red);',
        'INFRASTRUCTURE': 'background:rgba(240,136,62,0.12);color:var(--accent-orange);',
        'KNOWN_BUG': 'background:rgba(188,140,255,0.12);color:var(--accent-purple);',
        'INTERMITTENT': 'background:rgba(210,153,34,0.15);color:var(--accent-yellow);',
        'PRODUCT BUG': 'background:rgba(240,136,62,0.12);color:var(--accent-orange);',
        'CODE ISSUE': 'background:rgba(88,166,255,0.08);color:var(--accent-blue);'
    };
    return styles[cls] || 'background:var(--bg-tertiary);color:var(--text-muted);';
}"""


def format_result_as_html(
    result: AnalysisResult,
    completed_at: str = "",
    *,
    github_available: bool = False,
    jira_available: bool = False,
) -> str:
    """Generate a self-contained HTML report for an analysis result.

    Produces a complete HTML document with inline CSS using a dark
    GitHub-inspired theme.  The report includes failure cards, a
    detail table, and child job sections.

    Args:
        result: The analysis result to render.
        completed_at: Optional timestamp string for when the analysis completed.
        github_available: Whether GitHub issue creation is available
            (tests_repo_url and github_token both configured).
        jira_available: Whether Jira bug creation is available
            (Jira integration enabled and configured).

    Returns:
        A complete HTML document as a string.
    """
    e = html.escape

    job_name = result.job_name or "Unknown"
    build_number = str(result.build_number) if result.build_number else ""
    provider_info = _format_provider(result.ai_provider, result.ai_model)
    jenkins_url_str = str(result.jenkins_url) if result.jenkins_url else ""
    total_failures = count_all_failures(result.model_dump())

    parts: list[str] = []

    # --- HTML HEAD ---
    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jenkins Analysis - {e(job_name)} #{e(build_number)}</title>
<link rel="icon" href="{FAVICON_DATA_URI}">
<style>
{_common_css()}
.failure-badge {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--accent-red-bg);
    color: var(--accent-red);
    font-size: 13px;
    font-weight: 700;
    padding: 4px 12px;
    border-radius: 12px;
    font-family: var(--font-mono);
}}
.status-chip {{
    font-size: 11px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 12px;
    letter-spacing: 0.3px;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}}
.regenerate-btn {{
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 6px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--accent-blue);
    cursor: pointer;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    transition: background 0.15s, border-color 0.15s;
}}
.regenerate-btn:hover {{
    background: var(--bg-hover);
    border-color: var(--accent-blue);
}}

/* Failure cards */
.failure-card {{
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 12px;
    overflow: hidden;
}}
.failure-card[open] {{ border-color: var(--accent-blue); }}
.failure-summary {{
    padding: 16px 20px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    list-style: none;
}}
.failure-summary::-webkit-details-marker {{ display: none; }}
.failure-summary::before {{
    content: "\\25B6";
    font-size: 10px;
    color: var(--text-muted);
    transition: transform 0.2s;
}}
.failure-card[open] .failure-summary::before {{ transform: rotate(90deg); }}
.failure-title {{
    flex: 1;
    font-weight: 600;
    font-size: 14px;
    font-family: var(--font-mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}
.classification-tag,
.classification-select {{
    font-weight: 600;
    border-radius: 4px;
    text-transform: uppercase;
}}
.classification-tag {{
    font-size: 11px;
    padding: 2px 8px;
}}
.classification-tag.product-bug,
.classification-select.product-bug {{
    background-color: var(--accent-orange-bg);
    color: var(--accent-orange);
}}
.classification-tag.code-issue,
.classification-select.code-issue {{
    background-color: var(--accent-blue-bg);
    color: var(--accent-blue);
}}
.classification-tag.unknown {{
    background-color: var(--bg-tertiary);
    color: var(--text-muted);
}}
.bug-id {{
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
    color: var(--accent-blue);
    background: var(--accent-blue-bg);
    padding: 2px 8px;
    border-radius: 4px;
}}
.bug-count {{
    font-size: 12px;
    color: var(--text-muted);
}}
/* Bug cards */
.bug-card {{
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 12px;
    overflow: hidden;
}}
.bug-card[open] {{ border-color: var(--accent-blue); }}
.bug-summary {{
    padding: 16px 20px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    list-style: none;
}}
.bug-summary::-webkit-details-marker {{ display: none; }}
.bug-summary::before {{
    content: "\\25B6";
    font-size: 10px;
    color: var(--text-muted);
    transition: transform 0.2s;
}}
.bug-card[open] .bug-summary::before {{ transform: rotate(90deg); }}
.bug-title {{
    flex: 1;
    font-weight: 600;
    font-size: 14px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}
.bug-body {{
    padding: 0 20px 20px;
    border-top: 1px solid var(--border);
}}
.bug-body h4 {{
    font-size: 13px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin: 16px 0 8px;
}}
.bug-tests ul {{
    list-style: none;
    padding: 0;
}}
.bug-tests li {{
    padding: 4px 0;
    font-size: 13px;
    color: var(--text-secondary);
}}
.bug-tests li::before {{
    content: "\\2192 ";
    color: var(--text-muted);
}}
.bug-tests code {{
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--text-primary);
}}
.severity-tag-inline {{
    font-size: 11px;
    font-weight: 700;
    font-family: var(--font-mono);
    padding: 2px 8px;
    border-radius: 4px;
    text-transform: uppercase;
}}
.severity-tag-inline.critical {{ background: rgba(248,81,73,0.15); color: #ff6b63; }}
.severity-tag-inline.high {{ background: rgba(240,136,62,0.15); color: var(--accent-orange); }}
.severity-tag-inline.medium {{ background: rgba(210,153,34,0.15); color: var(--accent-yellow); }}
.severity-tag-inline.low {{ background: rgba(63,185,80,0.15); color: var(--accent-green); }}
/* Jira matches */
.jira-matches {{ margin-top: 12px; }}
.jira-match-link {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    margin: 3px 4px 3px 0;
    border-radius: 4px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--accent-blue);
    font-size: 12px;
    font-family: var(--font-mono);
    text-decoration: none;
    transition: background 0.15s;
}}
.jira-match-link:hover {{ background: var(--bg-hover); text-decoration: underline; }}
.jira-match-status {{ color: var(--text-muted); font-size: 11px; }}
.failure-body {{
    padding: 0 20px 20px;
    border-top: 1px solid var(--border);
}}
.failure-body h4 {{
    font-size: 13px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin: 16px 0 8px;
}}
.analysis-pre, .error-pre {{
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 16px;
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.6;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--text-secondary);
}}
.error-pre {{
    border-left: 3px solid var(--accent-red);
    color: var(--accent-red);
}}
.detail-grid {{
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 4px 16px;
    font-size: 13px;
    margin-top: 8px;
}}
.detail-label {{ color: var(--text-muted); font-weight: 600; }}
.detail-value {{ color: var(--text-primary); font-family: var(--font-mono); font-size: 12px; }}

/* Detail table */
.table-container {{
    overflow-x: auto;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 24px;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}}
thead {{ position: sticky; top: 0; z-index: 10; }}
th {{
    background: var(--bg-tertiary);
    padding: 12px 16px;
    text-align: left;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
}}
td {{
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    color: var(--text-secondary);
    vertical-align: top;
}}
tr:hover td {{ background: var(--bg-hover); }}
td.test-name {{ font-family: var(--font-mono); font-size: 12px; color: var(--text-primary); max-width: 300px; word-break: break-all; }}
td.error-cell {{ font-family: var(--font-mono); font-size: 11px; max-width: 350px; word-break: break-word; color: var(--accent-red); }}

/* Child job sections */
.child-job {{
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 12px;
    overflow: hidden;
}}
.child-job[open] {{ border-color: var(--accent-purple); }}
.child-job-summary {{
    padding: 16px 20px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    list-style: none;
    font-weight: 600;
    font-size: 14px;
}}
.child-job-summary::-webkit-details-marker {{ display: none; }}
.child-job-summary::before {{
    content: "\\25B6";
    font-size: 10px;
    color: var(--text-muted);
    transition: transform 0.2s;
}}
.child-job[open] .child-job-summary::before {{ transform: rotate(90deg); }}
.child-job-body {{ padding: 0 20px 20px; border-top: 1px solid var(--border); }}
.child-job-meta {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 12px 0; font-size: 12px; color: var(--text-muted); }}
.child-job-meta a {{ color: var(--accent-blue); text-decoration: none; }}
.child-job-meta a:hover {{ text-decoration: underline; }}
.child-note {{ font-size: 13px; color: var(--accent-yellow); font-style: italic; margin: 8px 0; }}

/* Key takeaway */
.key-takeaway {{
    background: var(--bg-secondary);
    border: 1px solid var(--accent-yellow);
    border-left: 4px solid var(--accent-yellow);
    border-radius: var(--radius);
    padding: 20px 24px;
    margin-bottom: 24px;
}}
.key-takeaway-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
.key-takeaway-header h3 {{ font-size: 14px; color: var(--accent-yellow); }}
.key-takeaway p {{ font-size: 14px; color: var(--text-secondary); line-height: 1.7; }}

/* No failures */
.no-failures {{
    text-align: center;
    padding: 60px 20px;
    color: var(--text-muted);
    font-size: 16px;
}}
.no-failures svg {{ margin-bottom: 16px; }}

/* Reviewed toggle */
.reviewed-toggle {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 6px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text-secondary);
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
    white-space: nowrap;
}}
.reviewed-toggle:hover {{
    background: var(--bg-hover);
    border-color: var(--accent-blue);
}}
.reviewed-toggle.checked {{
    background: rgba(63, 185, 80, 0.15);
    border-color: var(--accent-green);
    color: var(--accent-green);
}}

/* Comments section */
.comments-section {{
    margin-top: 16px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
}}
.comments-header {{
    background: var(--bg-tertiary);
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.comment-item {{
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
}}
.comment-timestamp {{
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--text-muted);
    margin-bottom: 4px;
}}
.comment-text {{
    color: var(--text-secondary);
    white-space: pre-wrap;
}}
.comment-text a {{
    color: var(--accent-blue);
    text-decoration: none;
}}
.comment-text a:hover {{ text-decoration: underline; }}
.enrichment-badge {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-family: var(--font-mono);
    font-weight: 700;
    margin-left: 6px;
}}
.comment-input-row {{
    padding: 10px 16px;
    background: var(--bg-primary);
    display: flex;
    gap: 8px;
}}
.comment-input {{
    flex: 1;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 12px;
    color: var(--text-primary);
    font-size: 13px;
    font-family: var(--font-sans);
    outline: none;
    transition: border-color 0.15s;
    resize: vertical;
    min-height: 36px;
    max-height: 200px;
}}
.comment-input::placeholder {{ color: var(--text-muted); }}
.comment-input:focus {{ border-color: var(--accent-blue); }}
.comment-add-btn {{
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    color: var(--accent-blue);
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
}}
.comment-add-btn:hover {{
    background: var(--bg-hover);
    border-color: var(--accent-blue);
}}
{_modal_css()}

/* Bug creation buttons */
.bug-actions {{
    margin-top: 12px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
}}
.create-issue-btn {{
    font-size: 12px;
    padding: 6px 14px;
    border-radius: 6px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-weight: 600;
    transition: background 0.15s, border-color 0.15s;
}}
.create-issue-btn:disabled {{
    opacity: 0.5;
    cursor: not-allowed;
}}
.github-issue-btn {{
    background: rgba(63,185,80,0.12);
    border: 1px solid var(--accent-green);
    color: var(--accent-green);
}}
.github-issue-btn:hover:not(:disabled) {{
    background: rgba(63,185,80,0.25);
}}
.jira-bug-btn {{
    background: rgba(88,166,255,0.12);
    border: 1px solid var(--accent-blue);
    color: var(--accent-blue);
}}
.jira-bug-btn:hover:not(:disabled) {{
    background: rgba(88,166,255,0.25);
}}
/* Loading modal spinner */
.loading-spinner {{
    width: 32px;
    height: 32px;
    border: 3px solid var(--border);
    border-top-color: var(--accent-blue);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin: 0 auto 12px;
}}
.loading-text {{
    color: var(--text-primary);
    font-size: 14px;
    font-weight: 600;
    text-align: center;
    margin-bottom: 6px;
}}
.loading-subtext {{
    color: var(--text-muted);
    font-size: 12px;
    text-align: center;
}}
/* Custom combobox (replaces native datalist) */
.custom-combo {{
    position: relative;
    display: inline-block;
}}
.custom-combo input {{
    font-size: 12px;
    padding: 3px 22px 3px 6px;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text-primary);
    outline: none;
}}
.custom-combo input:focus {{
    border-color: var(--accent-blue);
}}
.custom-combo .combo-arrow {{
    position: absolute;
    right: 2px;
    top: 50%;
    transform: translateY(-50%);
    background: none;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 10px;
    padding: 2px 4px;
    line-height: 1;
}}
.combo-dropdown {{
    display: none;
    position: fixed;
    min-width: 100%;
    max-height: 150px;
    overflow-y: auto;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 4px;
    margin-top: 2px;
    z-index: 10000;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}}
.combo-dropdown.show {{
    display: block;
}}
.combo-dropdown .combo-option {{
    padding: 4px 8px;
    font-size: 12px;
    color: var(--text-primary);
    cursor: pointer;
}}
.combo-dropdown .combo-option:hover {{
    background: rgba(56,139,253,0.15);
    color: var(--accent-blue);
}}
/* Classification override dropdown */
.classification-select {{
    appearance: none;
    -webkit-appearance: none;
    padding: 3px 24px 3px 10px;
    font-size: 12px;
    border: 1px solid var(--border);
    cursor: pointer;
    outline: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238b949e' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 6px center;
    background-size: 10px;
    transition: border-color 0.15s;
}}
.classification-select:hover {{
    border-color: var(--accent-blue);
}}
.classification-select option {{
    background: var(--bg-secondary);
    color: var(--text-primary);
    padding: 4px 8px;
}}
/* Preview modal extensions */
.preview-modal .modal-dialog {{
    max-width: 700px;
    text-align: left;
}}
.preview-modal .modal-dialog h3 {{
    text-align: left;
}}
.preview-input {{
    width: 100%;
    padding: 8px 12px;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text-primary);
    font-size: 14px;
    margin-bottom: 12px;
    outline: none;
}}
.preview-input:focus {{
    border-color: var(--accent-blue);
}}
.preview-textarea {{
    width: 100%;
    height: 300px;
    padding: 8px 12px;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text-primary);
    font-size: 13px;
    font-family: var(--font-mono);
    resize: vertical;
    margin-bottom: 12px;
    outline: none;
}}
.preview-textarea:focus {{
    border-color: var(--accent-blue);
}}
.similar-issues-box {{
    background: rgba(240,136,62,0.08);
    border: 1px solid var(--accent-orange);
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 12px;
    font-size: 13px;
}}
.similar-issues-box h4 {{
    color: var(--accent-orange);
    font-size: 12px;
    margin-bottom: 8px;
}}
.similar-issues-box a {{
    color: var(--accent-blue);
    text-decoration: none;
    font-size: 12px;
    display: inline;
}}
.similar-issues-box a:hover {{
    text-decoration: underline;
}}
.similar-issue-status {{
    display: inline-block;
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 10px;
    margin-left: 6px;
    font-weight: 600;
    text-transform: uppercase;
    vertical-align: middle;
}}
.similar-issue-status.status-open {{
    background: rgba(63,185,80,0.15);
    color: #3fb950;
}}
.similar-issue-status.status-closed {{
    background: rgba(139,148,158,0.15);
    color: #8b949e;
}}

@keyframes spin {{
    to {{ transform: rotate(360deg); }}
}}
/* Responsive (page-specific) */
@media (max-width: 480px) {{
    .failure-summary {{ font-size: 12px; gap: 8px; }}
}}
</style>
</head>
<body>
<div class="container">
""")

    # --- STICKY HEADER ---
    job_name_html = (
        f'<a href="{e(jenkins_url_str)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;">{e(job_name)}</a>'
        if jenkins_url_str
        else e(job_name)
    )
    parts.append(f"""
<div class="sticky-header">
  <div class="header-content">
    <div id="header-line1" style="display:flex;align-items:center;gap:16px;width:100%;flex-wrap:nowrap;">
      <h1>{job_name_html}</h1>
      <a class="regenerate-btn" href="?refresh=1" title="Regenerate report from stored data" style="margin-left:auto;flex-shrink:0;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg> Regenerate</a>
    </div>
    <div style="display:flex;align-items:center;gap:8px;width:100%;flex-wrap:wrap;">
      <span class="env-chip">Build: #{e(build_number)}</span>
      <span class="env-chip">Status: {e(result.status)}</span>
      <span class="env-chip">AI: {e(provider_info)}</span>
      {f'<span class="env-chip">Analyzed: {e(completed_at)}</span>' if completed_at else ""}
      <span id="overall-comment-count" style="display:none;margin-left:auto;"></span>
    </div>
    <div id="header-line3" style="display:flex;align-items:center;gap:8px;width:100%;flex-wrap:wrap;">
      <span class="failure-badge">{total_failures} failure{"s" if total_failures != 1 else ""}</span>
      <span id="overall-review-status" class="status-chip" style="display:none"></span>
    </div>
  </div>
</div>
""")

    # --- NO FAILURES CASE ---
    if total_failures == 0 and not result.child_job_analyses:
        parts.append("""
<div class="no-failures">
  <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--accent-green)" stroke-width="2">
    <circle cx="12" cy="12" r="10"/>
    <path d="M8 12l2.5 2.5L16 9"/>
  </svg>
  <p>No failures detected in this build.</p>
</div>
""")
        _append_takeaway(parts, result.summary, e)
        _append_footer(
            parts,
            job_name,
            build_number,
            result.job_id,
            provider_info,
            jenkins_url_str,
            e,
        )
        parts.append("</div>\n</body>\n</html>")
        return "\n".join(parts)

    # --- FAILURE CARDS (grouped by root cause) ---
    groups: list[dict] = []
    if result.failures:
        groups = _group_failures(result.failures)
        parts.append('<h2 class="section-title">Root Cause Analysis</h2>')
        for group in groups:
            _render_group_card(parts, group, e, job_id=result.job_id)

    # --- CHILD JOB ANALYSES ---
    if result.child_job_analyses:
        parts.append('<h2 class="section-title">Child Job Analyses</h2>')
        _render_child_jobs(parts, result.child_job_analyses, e, job_id=result.job_id)

    # --- ALL FAILURES TABLE ---
    if result.failures:
        parts.append("""
<h2 class="section-title">All Failures</h2>
<div class="table-container">
<table>
<thead>
<tr>
  <th>#</th>
  <th>Test Name</th>
  <th>Error</th>
  <th>Classification</th>
  <th>Bug Ref</th>
</tr>
</thead>
<tbody>
""")
        # Build lookup from failure analysis key to bug_id
        analysis_to_bug: dict[str, str] = {}
        for group in groups:
            key = _grouping_key(group["failures"][0])
            analysis_to_bug[key] = group["bug_id"]

        for idx, f in enumerate(result.failures, start=1):
            cls = f.analysis.classification or "Unknown"
            cls_class = _classification_css_class(cls)
            bug_ref = analysis_to_bug.get(_grouping_key(f), "")
            parts.append(f"""<tr>
  <td>{idx}</td>
  <td class="test-name">{e(f.test_name)}</td>
  <td class="error-cell" title="{e(f.error)}">{e(f.error)}</td>
  <td><span class="classification-tag {e(cls_class)}">{e(cls)}</span></td>
  <td><span class="bug-id">{e(bug_ref)}</span></td>
</tr>
""")
        parts.append("</tbody>\n</table>\n</div>")

    # --- KEY TAKEAWAY ---
    _append_takeaway(parts, result.summary, e)

    # --- FOOTER ---
    _append_footer(
        parts, job_name, build_number, result.job_id, provider_info, jenkins_url_str, e
    )

    # --- INLINE JAVASCRIPT ---
    parts.append(f"""
<script>
const JOB_ID = "{e(result.job_id)}";
// Derive base path for API calls (works behind reverse proxies with path prefixes)
const BASE_PATH = window.location.pathname.replace(/\\/results\\/.*$/, '');
const CURRENT_AI_PROVIDER = "{e(result.ai_provider or "")}";
const CURRENT_AI_MODEL = "{e(result.ai_model or "")}";
var GITHUB_AVAILABLE = {"true" if github_available else "false"};
var JIRA_AVAILABLE = {"true" if jira_available else "false"};

var _aiConfigs = [];
(function loadAiConfigs() {{
    fetch(BASE_PATH + '/ai-configs')
        .then(function(r) {{ return r.ok ? r.json() : []; }})
        .then(function(data) {{
            _aiConfigs = data || [];
            if (typeof initAiComboboxes === 'function') initAiComboboxes();
        }})
        .catch(function() {{}});
}})();

function renderCommentBadge(badge, count) {{
    if (count <= 0) {{
        badge.style.display = 'none';
        return;
    }}
    badge.style.display = '';
    badge.style.cssText = 'display:inline-flex;align-items:center;gap:4px;font-size:13px;padding:4px 12px;border-radius:12px;background:var(--bg-tertiary);border:1px solid var(--border);color:var(--text-muted);font-family:var(--font-mono);white-space:nowrap;margin-left:auto;';
    badge.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg> ' + count;
}}

function updateCommentBadges() {{
    // Recalculate overall comment count
    var totalComments = document.querySelectorAll('.comment-item').length;
    var overallBadge = document.getElementById('overall-comment-count');
    if (overallBadge) {{
        renderCommentBadge(overallBadge, totalComments);
    }}

    // Remove all dynamically created comment badges before recreating
    document.querySelectorAll('.dynamic-comment-badge').forEach(function(el) {{ el.remove(); }});

    // Per child job comment counts — scope to direct children only
    document.querySelectorAll('.child-job-summary').forEach(function(summary) {{
        var childJobEl = summary.closest('.child-job');
        if (!childJobEl) return;
        var childJobName = childJobEl.getAttribute('data-child-job') || '';
        var childBuild = childJobEl.getAttribute('data-child-build') || '';
        // Only count comment items that belong directly to this child job, not grandchildren
        var count = Array.from(childJobEl.querySelectorAll('.comment-item')).filter(function(item) {{
            return item.closest('.child-job') === childJobEl;
        }}).length;
        // Find or create badge
        var badge = summary.querySelector('.child-comment-badge');
        if (!badge) {{
            badge = document.createElement('span');
            badge.className = 'child-comment-badge dynamic-comment-badge';
            summary.appendChild(badge);
        }}
        renderCommentBadge(badge, count);
    }});

    // Per bug card comment counts
    document.querySelectorAll('.bug-summary, .failure-summary').forEach(function(summary) {{
        var card = summary.closest('.bug-card, .failure-card');
        if (!card) return;
        var count = card.querySelectorAll('.comment-item').length;
        var badge = summary.querySelector('.card-comment-badge');
        if (!badge) {{
            badge = document.createElement('span');
            badge.className = 'card-comment-badge dynamic-comment-badge';
            summary.appendChild(badge);
        }}
        renderCommentBadge(badge, count);
    }});
}}

async function loadCommentsAndReviews() {{
    try {{
        const resp = await fetch(`${{BASE_PATH}}/results/${{JOB_ID}}/comments`);
        if (!resp.ok) return;
        const data = await resp.json();

        data.comments.forEach(c => {{
            const childJob = c.child_job_name || '';
            const childBuildNumber = c.child_build_number || 0;
            const testNames = c.test_name;
            document.querySelectorAll('.comments-section').forEach(section => {{
                const sectionTests = JSON.parse(section.dataset.testNames || '[]');
                const sectionChild = section.dataset.childJob || '';
                const sectionChildBuild = parseInt(section.dataset.childBuild || '0');
                if (sectionTests.includes(testNames) && sectionChild === childJob && (sectionChildBuild === childBuildNumber || childBuildNumber === 0)) {{
                    appendCommentToList(section, c);
                }}
            }});
        }});

        document.querySelectorAll('.comments-section').forEach(section => {{
            const count = section.querySelectorAll('.comment-item').length;
            section.querySelector('.comment-count').textContent = count;
        }});

        for (const [key, review] of Object.entries(data.reviews)) {{
            if (review.reviewed) {{
                document.querySelectorAll('.reviewed-toggle').forEach(toggle => {{
                    const testName = toggle.dataset.testName;
                    const childJob = toggle.dataset.childJob || '';
                    const toggleChildBuild = parseInt(toggle.dataset.childBuild || '0');
                    const toggleKey = childJob ? childJob + '#' + toggleChildBuild + '::' + testName : testName;
                    if (toggleKey === key) {{
                        toggle.classList.add('checked');
                        toggle.querySelector('input').checked = true;
                    }}
                }});
            }}
        }}
        updateReviewBadges();
        // Reuse updateCommentBadges() which correctly scopes by
        // child_job_name AND child_build_number via DOM .comment-item counts.
        updateCommentBadges();
    }} catch (err) {{
        console.warn('Failed to load comments:', err);
    }}
}}

function applyReviewBadge(badge, container) {{
    if (!container) return;
    var toggles = container.querySelectorAll('.reviewed-toggle');
    var total = toggles.length;
    var checked = container.querySelectorAll('.reviewed-toggle.checked').length;
    if (total === 0) return;
    badge.style.display = '';
    if (checked >= total) {{
        badge.textContent = '\u2713 Reviewed';
        badge.style.background = 'rgba(63,185,80,0.15)';
        badge.style.color = 'var(--accent-green)';
    }} else if (checked > 0) {{
        badge.textContent = checked + '/' + total;
        badge.style.background = 'rgba(210,153,34,0.15)';
        badge.style.color = 'var(--accent-yellow)';
    }} else {{
        badge.textContent = 'Needs Review';
        badge.style.background = 'rgba(248,81,73,0.12)';
        badge.style.color = 'var(--accent-red)';
    }}
}}

function updateReviewBadges() {{
    // Overall job review status
    const overallBadge = document.getElementById('overall-review-status');
    if (overallBadge) {{
        overallBadge.style.fontSize = '13px';
        overallBadge.style.padding = '4px 12px';
        overallBadge.style.fontFamily = 'var(--font-mono)';
        applyReviewBadge(overallBadge, document);
        // Adjust text for overall: use "Fully Reviewed" instead of "Reviewed"
        var allToggles = document.querySelectorAll('.reviewed-toggle');
        var totalTests = allToggles.length;
        var reviewedTests = document.querySelectorAll('.reviewed-toggle.checked').length;
        if (totalTests > 0 && reviewedTests >= totalTests) {{
            overallBadge.textContent = '\u2713 Fully Reviewed';
        }} else if (totalTests > 0 && reviewedTests > 0) {{
            overallBadge.textContent = reviewedTests + '/' + totalTests + ' Reviewed';
        }}
    }}

    // Per child job review status
    document.querySelectorAll('.child-review-status').forEach(function(badge) {{
        applyReviewBadge(badge, badge.closest('.child-job'));
    }});

    // Per bug card review status
    document.querySelectorAll('.group-review-status').forEach(function(badge) {{
        applyReviewBadge(badge, badge.closest('.bug-card') || badge.closest('.failure-card'));
    }});
}}

function appendCommentToList(section, comment) {{
    const list = section.querySelector('.comment-list');
    const item = document.createElement('div');
    item.className = 'comment-item';
    if (comment.id) item.dataset.commentId = comment.id;
    const text = autoLink(escapeHtml(comment.comment));
    var userLabel = comment.username ? '<span style="font-family:var(--font-mono);font-size:11px;color:var(--accent-purple);margin-right:6px;">' + escapeHtml(comment.username) + '</span>' : '';
    var deleteBtn = '';
    var currentUser = getJjiUsername();
    if (comment.username && comment.username === currentUser && comment.id) {{
        deleteBtn = ' <button onclick="deleteComment(this, ' + comment.id + ')" style="font-size:11px;padding:2px 8px;border-radius:4px;background:rgba(248,81,73,0.12);border:1px solid transparent;color:var(--accent-red);cursor:pointer;margin-left:8px;">Delete</button>';
    }}
    // Safe: escapeHtml sanitizes all user content, autoLink only adds <a> tags for URL patterns
    item.innerHTML = '<div class="comment-timestamp">' + (comment.created_at || '') + deleteBtn + '</div><div class="comment-text">' + userLabel + text + '</div>';  // nosec: innerHTML is safe here because escapeHtml sanitizes user input
    list.appendChild(item);
}}

{_modal_js()}

function showIssueCreatedModal(type, data) {{
    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    var dialog = document.createElement('div');
    dialog.className = 'modal-dialog';

    var iconDiv = document.createElement('div');
    iconDiv.style.marginBottom = '12px';
    iconDiv.innerHTML = '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--accent-green)" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 12l2.5 2.5L16 9"/></svg>';
    dialog.appendChild(iconDiv);

    var h3 = document.createElement('h3');
    h3.textContent = type === 'github' ? 'GitHub Issue Created' : 'Jira Bug Created';
    dialog.appendChild(h3);

    var issueUrl = data.url || '';
    var displayName = '';
    if (type === 'github' && issueUrl) {{
        var match = issueUrl.match(/github\\.com\\/([^/]+)\\/([^/]+)\\/issues\\/(\\d+)/);
        if (match) displayName = match[1] + '/' + match[2] + '#' + match[3];
        else displayName = '#' + (data.number || '');
    }} else if (type === 'jira') {{
        displayName = data.key || issueUrl;
    }}

    var p = document.createElement('p');
    p.style.marginBottom = '20px';
    var link = document.createElement('a');
    link.href = issueUrl;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = displayName;
    link.style.cssText = 'color:var(--accent-blue);text-decoration:none;font-size:16px;font-weight:600;font-family:var(--font-mono);';
    p.appendChild(link);
    dialog.appendChild(p);

    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var okBtn = document.createElement('button');
    okBtn.className = 'modal-btn modal-btn-cancel';
    okBtn.textContent = 'OK';
    okBtn.onclick = function() {{ overlay.remove(); }};
    actions.appendChild(okBtn);
    dialog.appendChild(actions);

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    overlay.onclick = function(e) {{ if (e.target === overlay) overlay.remove(); }};
}}

{_classification_colors_js()}

async function deleteComment(btn, commentId) {{
    showConfirmModal('Delete Comment', 'Are you sure you want to delete this comment?', async function() {{
        try {{
            var resp = await fetch(BASE_PATH + '/results/' + JOB_ID + '/comments/' + commentId, {{
                method: 'DELETE',
            }});
            if (resp.ok) {{
                var item = btn.closest('.comment-item');
                var section = item.closest('.comments-section');
                item.remove();
                var count = section.querySelectorAll('.comment-item').length;
                section.querySelector('.comment-count').textContent = count;
                updateCommentBadges();
            }} else {{
                var data = await resp.json();
                alert(data.detail || 'Failed to delete');
            }}
        }} catch (err) {{
            console.warn('Failed to delete comment:', err);
        }}
    }});
}}

function escapeHtml(str) {{
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}}

function autoLink(text) {{
    // GitHub PR URLs -> org/repo#number
    text = text.replace(
        /https?:\\/\\/github\\.com\\/([^\\/]+)\\/([^\\/]+)\\/pull\\/(\\d+)(?:[^\\s<]*)/g,
        '<a href="https://github.com/$1/$2/pull/$3" target="_blank" rel="noopener">$1/$2#$3</a>'
    );
    // Jira browse URLs -> TICKET-KEY (strip query params)
    text = text.replace(
        /https?:\\/\\/[^\\s<]*\\/browse\\/([A-Z][A-Z0-9]+-\\d+)(?:\\?[^\\s<]*)?/g,
        function(match, key) {{
            var cleanUrl = match.split('?')[0];
            return '<a href="' + cleanUrl + '" target="_blank" rel="noopener">' + key + '</a>';
        }}
    );
    // Other URLs -> clickable with full URL text
    text = text.replace(
        /(https?:\\/\\/[^\\s<]+)/g,
        function(match) {{
            if (match.includes('github.com') && match.includes('/pull/')) return match;
            if (match.includes('/browse/')) return match;
            return '<a href="' + match + '" target="_blank" rel="noopener">' + match + '</a>';
        }}
    );
    return text;
}}

async function toggleReviewed(label) {{
    const checkbox = label.querySelector('input');
    const testName = label.dataset.testName;
    const childJob = label.dataset.childJob || '';
    const childBuild = parseInt(label.dataset.childBuild || '0');
    const reviewed = checkbox.checked;

    try {{
        const resp = await fetch(`${{BASE_PATH}}/results/${{JOB_ID}}/reviewed`, {{
            method: 'PUT',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{test_name: testName, child_job_name: childJob, child_build_number: childBuild, reviewed: reviewed}}),
        }});
        if (resp.ok) {{
            label.classList.toggle('checked', reviewed);
            updateReviewBadges();
            // Update select-all checkbox state
            var card = label.closest('.bug-card') || label.closest('.failure-card') || label.closest('.child-job-body');
            if (card) {{
                var selectAll = card.querySelector('.select-all-toggle input[type="checkbox"]');
                if (selectAll) {{
                    var allToggles = card.querySelectorAll('.reviewed-toggle:not(.select-all-toggle) input[type="checkbox"]');
                    var allChecked = Array.from(allToggles).every(function(cb) {{ return cb.checked; }});
                    selectAll.checked = allChecked;
                    selectAll.closest('.reviewed-toggle').classList.toggle('checked', allChecked);
                }}
            }}
        }} else {{
            checkbox.checked = !reviewed;
            console.warn('Failed to toggle reviewed: server returned', resp.status);
        }}
    }} catch (err) {{
        checkbox.checked = !reviewed;
        console.warn('Failed to toggle reviewed:', err);
    }}
}}

async function toggleAllReviewed(label) {{
    var selectAllCb = label.querySelector('input[type="checkbox"]');
    var card = label.closest('.bug-card') || label.closest('.failure-card') || label.closest('.child-job-body');
    if (!card) return;
    var toggles = card.querySelectorAll('.reviewed-toggle:not(.select-all-toggle) input[type="checkbox"]');
    var newState = selectAllCb.checked;

    var promises = [];
    toggles.forEach(function(cb) {{
        if (cb.checked !== newState) {{
            cb.checked = newState;
            var lbl = cb.closest('.reviewed-toggle');
            lbl.classList.toggle('checked', newState);

            var testName = lbl.dataset.testName;
            var childJob = lbl.dataset.childJob || '';
            var childBuild = parseInt(lbl.dataset.childBuild || '0');

            promises.push(
                fetch(BASE_PATH + '/results/' + JOB_ID + '/reviewed', {{
                    method: 'PUT',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{
                        test_name: testName,
                        child_job_name: childJob,
                        child_build_number: childBuild,
                        reviewed: newState
                    }}),
                }})
            );
        }}
    }});

    await Promise.all(promises);
    label.classList.toggle('checked', newState);
    if (typeof updateReviewBadges === 'function') updateReviewBadges();
}}

async function addComment(btn) {{
    const row = btn.closest('.comment-input-row');
    const input = row.querySelector('.comment-input');
    const comment = input.value.trim();
    if (!comment) return;

    const section = btn.closest('.comments-section');
    const selector = section.querySelector('.comment-test-select');
    const testName = selector.value;
    const childJob = section.dataset.childJob || '';
    const childBuild = parseInt(section.dataset.childBuild || '0');

    try {{
        const resp = await fetch(`${{BASE_PATH}}/results/${{JOB_ID}}/comments`, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{test_name: testName, child_job_name: childJob, child_build_number: childBuild, comment: comment}}),
        }});
        if (resp.ok) {{
            const result = await resp.json();
            const now = new Date().toISOString().replace('T', ' ').substring(0, 19);
            var currentUser = getJjiUsername();
            appendCommentToList(section, {{id: result.id, comment: comment, created_at: now, test_name: testName, username: currentUser}});
            const count = section.querySelectorAll('.comment-item').length;
            section.querySelector('.comment-count').textContent = count;
            input.value = '';
            updateCommentBadges();
            await loadEnrichments();
        }}
    }} catch (err) {{
        console.warn('Failed to add comment:', err);
    }}
}}

async function loadEnrichments() {{
    try {{
        const resp = await fetch(`${{BASE_PATH}}/results/${{JOB_ID}}/enrich-comments`, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
        }});
        if (!resp.ok) return;
        const data = await resp.json();

        // Remove existing enrichment badges to avoid duplicates on re-runs
        document.querySelectorAll('.enrichment-badge').forEach(el => el.remove());

        for (const [commentId, enrichments] of Object.entries(data.enrichments)) {{
            const commentEl = document.querySelector(`.comment-item[data-comment-id="${{commentId}}"]`);
            if (!commentEl) continue;
            const textEl = commentEl.querySelector('.comment-text');
            for (const e of enrichments) {{
                const badge = document.createElement('span');
                badge.className = 'enrichment-badge';
                const statusUpper = e.status.toUpperCase();
                if (e.status === 'merged') {{
                    badge.style.background = 'rgba(63,185,80,0.15)';
                    badge.style.color = 'var(--accent-green)';
                }} else if (e.status === 'open') {{
                    badge.style.background = 'rgba(63,185,80,0.15)';
                    badge.style.color = 'var(--accent-green)';
                }} else if (e.status === 'closed') {{
                    badge.style.background = 'rgba(248,81,73,0.15)';
                    badge.style.color = 'var(--accent-red)';
                }} else {{
                    badge.style.background = 'rgba(88,166,255,0.12)';
                    badge.style.color = 'var(--accent-blue)';
                }}
                badge.textContent = statusUpper;
                textEl.appendChild(badge);
            }}
        }}
    }} catch (err) {{
        console.warn('Failed to load enrichments:', err);
    }}
}}

async function loadClassifications() {{
    try {{
        var resp = await fetch(BASE_PATH + '/history/classifications?job_id=' + encodeURIComponent(JOB_ID));
        if (!resp.ok) return;
        var data = await resp.json();
        var byKey = {{}};
        (data.classifications || []).forEach(function(c) {{
            // Normalize: empty job_name (root-job) uses '' as the key prefix,
            // matching the toggle's data-child-job which is '' for root failures.
            var scopeKey = c.job_name || '';
            var key = scopeKey + '#' + (c.child_build_number || 0) + '::' + c.test_name;
            if (!byKey[key]) byKey[key] = [];
            byKey[key].push(c);
        }});

        document.querySelectorAll('.reviewed-toggle').forEach(function(toggle) {{
            var testName = toggle.dataset.testName;
            var childJob = toggle.dataset.childJob || '';
            var childBuild = toggle.dataset.childBuild || '0';
            if (!testName) return;
            var key = childJob + '#' + childBuild + '::' + testName;
            var entries = byKey[key];
            if (!entries || entries.length === 0) return;
            // Use only the latest (first) entry per key
            var cls = entries[0];
            var badge = document.createElement('span');
            badge.className = 'classification-tag';
            badge.style.cssText = getClassificationStyle(cls.classification) + 'margin-left:6px;';
            var badgeLabel = cls.classification.replace('_', ' ');
            if (cls.classification === 'KNOWN_BUG') {{
                var jiraMatch = (cls.reason || '').match(/([A-Z][A-Z0-9]+-\\d+)/);
                if (jiraMatch) badgeLabel = 'KNOWN BUG: ' + jiraMatch[1];
            }}
            badge.textContent = badgeLabel;
            badge.title = (cls.reason || '') + (cls.references_info ? '\\nRef: ' + cls.references_info : '');
            toggle.appendChild(badge);
        }});

        // Apply classification overrides to primary classification tags.
        // When a user overrides CODE ISSUE -> PRODUCT BUG (or vice-versa),
        // the override is stored in test_classifications. On page refresh
        // the primary tag comes from result_json (stale). Update it here.
        document.querySelectorAll('.reviewed-toggle').forEach(function(toggle) {{
            var testName = toggle.dataset.testName;
            var childJob = toggle.dataset.childJob || '';
            var childBuild = toggle.dataset.childBuild || '0';
            var key = childJob + '#' + childBuild + '::' + testName;
            var entries = byKey[key];
            if (!entries || entries.length === 0) return;

            // For primary classification overrides, use only the latest (first) entry
            var cls = entries[0];
            if (cls.classification === 'CODE ISSUE' || cls.classification === 'PRODUCT BUG') {{
                var card = toggle.closest('.bug-card') || toggle.closest('.failure-card');
                if (card) {{
                    var select = card.querySelector('.classification-select');
                    if (select) {{
                        select.value = cls.classification;
                        select.className = 'classification-select ' +
                            (cls.classification === 'PRODUCT BUG' ? 'product-bug' : 'code-issue');
                        showCorrectBugButton(card, cls.classification);
                    }} else {{
                        var primaryTag = card.querySelector('.classification-tag.product-bug, .classification-tag.code-issue, .classification-tag.unknown');
                        if (primaryTag) {{
                            primaryTag.textContent = cls.classification;
                            primaryTag.className = 'classification-tag ' +
                                (cls.classification === 'PRODUCT BUG' ? 'product-bug' : 'code-issue');
                            showCorrectBugButton(card, cls.classification);
                        }}
                    }}
                }}
            }}
        }});

        // Add classification badges to bug card summaries
        document.querySelectorAll('.bug-summary, .failure-summary').forEach(function(summary) {{
            var card = summary.closest('.bug-card, .failure-card');
            if (!card) return;
            var toggles = card.querySelectorAll('.reviewed-toggle');
            var cardClassifications = {{}};
            var cardJiraKeys = {{}};
            var cardReasons = {{}};
            toggles.forEach(function(t) {{
                var tn = t.dataset.testName;
                var cj = t.dataset.childJob || '';
                var cb = t.dataset.childBuild || '0';
                var key = cj + '#' + cb + '::' + tn;
                if (tn && byKey[key] && byKey[key].length > 0) {{
                    // Use only latest classification per test for badge counts
                    var entry = byKey[key][0];
                    var cls = entry.classification;
                    cardClassifications[cls] = (cardClassifications[cls] || 0) + 1;
                    if (!cardReasons[cls]) cardReasons[cls] = [];
                    var r = entry.reason || '';
                    var ri = entry.references_info || '';
                    var tip = r + (ri ? '\\nRef: ' + ri : '');
                    if (tip && cardReasons[cls].indexOf(tip) === -1) cardReasons[cls].push(tip);
                    if (cls === 'KNOWN_BUG') {{
                        var jm = (r).match(/([A-Z][A-Z0-9]+-\\d+)/);
                        if (jm && !cardJiraKeys[jm[1]]) cardJiraKeys[jm[1]] = true;
                    }}
                }}
            }});
            for (var cls in cardClassifications) {{
                var badge = document.createElement('span');
                badge.className = 'classification-tag';
                badge.style.cssText = getClassificationStyle(cls) + 'margin-left:6px;';
                var count = cardClassifications[cls];
                var label = count > 1 ? count + ' ' + cls.replace('_', ' ') : cls.replace('_', ' ');
                if (cls === 'KNOWN_BUG') {{
                    var keys = Object.keys(cardJiraKeys);
                    if (keys.length > 0) {{
                        label = count > 1 ? count + ' KNOWN BUG: ' + keys.join(', ') : 'KNOWN BUG: ' + keys[0];
                    }}
                }}
                badge.textContent = label;
                badge.title = (cardReasons[cls] || []).join('; ');
                summary.appendChild(badge);
            }}
        }});

        // Add classification badges to child job summaries
        document.querySelectorAll('.child-job-summary').forEach(function(summary) {{
            var childCard = summary.closest('.child-job');
            if (!childCard) return;
            var toggles = childCard.querySelectorAll('.reviewed-toggle');
            var childClassifications = {{}};
            var childJiraKeys = {{}};
            var childReasons = {{}};
            toggles.forEach(function(t) {{
                var tn = t.dataset.testName;
                var cj = t.dataset.childJob || '';
                var cb = t.dataset.childBuild || '0';
                var key = cj + '#' + cb + '::' + tn;
                if (tn && byKey[key] && byKey[key].length > 0) {{
                    // Use only latest classification per test for badge counts
                    var entry = byKey[key][0];
                    var cls = entry.classification;
                    childClassifications[cls] = (childClassifications[cls] || 0) + 1;
                    if (!childReasons[cls]) childReasons[cls] = [];
                    var r = entry.reason || '';
                    var ri = entry.references_info || '';
                    var tip = r + (ri ? '\\nRef: ' + ri : '');
                    if (tip && childReasons[cls].indexOf(tip) === -1) childReasons[cls].push(tip);
                    if (cls === 'KNOWN_BUG') {{
                        var jm = (r).match(/([A-Z][A-Z0-9]+-\\d+)/);
                        if (jm && !childJiraKeys[jm[1]]) childJiraKeys[jm[1]] = true;
                    }}
                }}
            }});
            for (var cls in childClassifications) {{
                var badge = document.createElement('span');
                badge.style.cssText = 'display:inline;font-size:11px;font-weight:700;padding:3px 10px;border-radius:12px;white-space:nowrap;margin-left:6px;' + getClassificationStyle(cls);
                var count = childClassifications[cls];
                var label = count > 1 ? count + ' ' + cls.replace('_', ' ') : cls.replace('_', ' ');
                if (cls === 'KNOWN_BUG') {{
                    var keys = Object.keys(childJiraKeys);
                    if (keys.length > 0) {{
                        label = count > 1 ? count + ' KNOWN BUG: ' + keys.join(', ') : 'KNOWN BUG: ' + keys[0];
                    }}
                }}
                badge.textContent = label;
                badge.title = (childReasons[cls] || []).join('; ');
                summary.appendChild(badge);
            }}
        }});

        // Add classification summary to report header (line 3 — badges row)
        // Use only the latest (first) entry per key for header counts
        var headerClassifications = {{}};
        for (var key in byKey) {{
            if (byKey[key] && byKey[key].length > 0) {{
                var latestEntry = byKey[key][0];
                headerClassifications[latestEntry.classification] = (headerClassifications[latestEntry.classification] || 0) + 1;
            }}
        }}
        var headerLine3 = document.getElementById('header-line3');
        if (headerLine3) {{
            for (var cls in headerClassifications) {{
                var chip = document.createElement('span');
                chip.style.cssText = 'display:inline-flex;align-items:center;font-size:13px;font-weight:700;padding:4px 12px;border-radius:12px;font-family:var(--font-mono);white-space:nowrap;' + getClassificationStyle(cls);
                chip.textContent = headerClassifications[cls] + ' ' + cls.replace('_', ' ');
                headerLine3.appendChild(chip);
            }}
        }}
    }} catch (err) {{
        console.warn('Failed to load classifications:', err);
    }}
}}

// -- Bug creation: preview, create, classification override --

function showIssuePreviewModal(type, data, testName, childJob, childBuild, includeLinks, aiProvider, aiModel) {{
    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay preview-modal';

    var dialog = document.createElement('div');
    dialog.className = 'modal-dialog';

    var h3 = document.createElement('h3');
    h3.textContent = type === 'github' ? 'Create GitHub Issue' : 'Create Jira Bug';
    dialog.appendChild(h3);

    // Similar issues warning
    if (data.similar_issues && data.similar_issues.length > 0) {{
        var box = document.createElement('div');
        box.className = 'similar-issues-box';
        var boxH4 = document.createElement('h4');
        boxH4.textContent = 'Similar existing issues found (' + data.similar_issues.length + ')';
        box.appendChild(boxH4);
        data.similar_issues.forEach(function(s) {{
            var row = document.createElement('div');
            row.style.cssText = 'padding:2px 0;';
            var link = document.createElement('a');
            link.href = s.url || '#';
            link.target = '_blank';
            link.rel = 'noopener';
            link.style.cssText = 'color:var(--accent-blue);text-decoration:none;font-size:12px;';
            link.textContent = (s.key || '#' + (s.number || '')) + ': ' + (s.title || '');
            link.onmouseover = function() {{ this.style.textDecoration = 'underline'; }};
            link.onmouseout = function() {{ this.style.textDecoration = 'none'; }};
            row.appendChild(link);
            if (s.status) {{
                var badge = document.createElement('span');
                badge.className = 'similar-issue-status';
                var st = (s.status || '').toLowerCase();
                if (st === 'open' || st === 'in progress' || st === 'to do' || st === 'new' || st === 'reopened') {{
                    badge.classList.add('status-open');
                }} else {{
                    badge.classList.add('status-closed');
                }}
                badge.textContent = s.status;
                row.appendChild(badge);
            }}
            box.appendChild(row);
        }});
        dialog.appendChild(box);
    }}

    // Title input
    var titleLabel = document.createElement('label');
    titleLabel.style.cssText = 'display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;font-weight:600;';
    titleLabel.textContent = 'Title';
    dialog.appendChild(titleLabel);
    var titleInput = document.createElement('input');
    titleInput.type = 'text';
    titleInput.className = 'preview-input';
    titleInput.value = data.title || '';
    dialog.appendChild(titleInput);

    // Body textarea
    var bodyLabel = document.createElement('label');
    bodyLabel.style.cssText = 'display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;font-weight:600;';
    bodyLabel.textContent = 'Body';
    dialog.appendChild(bodyLabel);
    var bodyTextarea = document.createElement('textarea');
    bodyTextarea.className = 'preview-textarea';
    bodyTextarea.value = data.body || '';
    dialog.appendChild(bodyTextarea);

    // Attribution note
    var attrNote = document.createElement('div');
    attrNote.style.cssText = 'font-size:11px;color:var(--text-muted);margin-bottom:12px;font-style:italic;';
    var cookieUser = getJjiUsername();
    if (cookieUser) {{
        attrNote.textContent = 'A "Reported by: ' + cookieUser + ' via jenkins-job-insight" line will be added automatically.';
        dialog.appendChild(attrNote);
    }}

    // Actions
    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    actions.style.justifyContent = 'flex-end';

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn modal-btn-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = function() {{ overlay.remove(); }};
    actions.appendChild(cancelBtn);

    var createBtn = document.createElement('button');
    createBtn.className = 'modal-btn';
    createBtn.style.cssText = type === 'github'
        ? 'background:rgba(63,185,80,0.15);color:var(--accent-green);border-color:var(--accent-green);'
        : 'background:rgba(88,166,255,0.12);color:var(--accent-blue);border-color:var(--accent-blue);';
    createBtn.textContent = 'Create';
    createBtn.onclick = function() {{
        createBtn.textContent = 'Creating...';
        createBtn.disabled = true;
        submitIssue(type, titleInput.value, bodyTextarea.value, testName, childJob, childBuild, includeLinks, overlay, aiProvider, aiModel);
    }};
    actions.appendChild(createBtn);
    dialog.appendChild(actions);

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    overlay.onclick = function(ev) {{ if (ev.target === overlay) overlay.remove(); }};
}}

async function submitIssue(type, title, body, testName, childJob, childBuild, includeLinks, overlay, aiProvider, aiModel) {{
    var endpoint = type === 'github' ? '/create-github-issue' : '/create-jira-bug';
    try {{
        var payload = {{
            test_name: testName, child_job_name: childJob, child_build_number: childBuild,
            title: title, body: body, include_links: includeLinks,
        }};
        if (aiProvider) payload.ai_provider = aiProvider;
        if (aiModel) payload.ai_model = aiModel;
        var resp = await fetch(BASE_PATH + '/results/' + JOB_ID + endpoint, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(payload),
        }});
        if (!resp.ok) {{
            var errData = await resp.json().catch(function() {{ return {{}}; }});
            throw new Error(errData.detail || 'HTTP ' + resp.status);
        }}
        var data = await resp.json();
        overlay.remove();
        showIssueCreatedModal(type, data);
        // Clear existing comments before reload to avoid duplicates
        document.querySelectorAll('.comment-list').forEach(function(cl) {{ cl.innerHTML = ''; }});
        // Reload comments to show the auto-added link
        await loadCommentsAndReviews();
        updateCommentBadges();
    }} catch (err) {{
        overlay.remove();
        showConfirmModal('Error', 'Failed to create: ' + err.message, function() {{}}, {{confirmLabel: 'OK', confirmOnly: true}});
    }}
}}

function showLoadingModal(type) {{
    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay preview-modal';

    var dialog = document.createElement('div');
    dialog.className = 'modal-dialog';
    dialog.style.cssText = 'max-width:400px;text-align:center;';

    var h3 = document.createElement('h3');
    h3.style.textAlign = 'center';
    h3.textContent = type === 'github' ? 'Create GitHub Issue' : 'Create Jira Bug';
    dialog.appendChild(h3);

    var spinnerContainer = document.createElement('div');
    spinnerContainer.style.cssText = 'padding:24px 0 16px;';

    var spinner = document.createElement('div');
    spinner.className = 'loading-spinner';
    spinnerContainer.appendChild(spinner);

    var loadingText = document.createElement('div');
    loadingText.className = 'loading-text';
    loadingText.textContent = type === 'github' ? 'Generating issue...' : 'Generating bug...';
    spinnerContainer.appendChild(loadingText);

    var loadingSubtext = document.createElement('div');
    loadingSubtext.className = 'loading-subtext';
    loadingSubtext.textContent = 'AI is analyzing the failure and crafting the ' + (type === 'github' ? 'issue' : 'bug') + '.';
    spinnerContainer.appendChild(loadingSubtext);

    dialog.appendChild(spinnerContainer);

    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    actions.style.justifyContent = 'center';

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn modal-btn-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = function() {{ if (overlay._abortController) overlay._abortController.abort(); overlay.remove(); }};
    actions.appendChild(cancelBtn);

    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    overlay.onclick = function(ev) {{ if (ev.target === overlay) {{ if (overlay._abortController) overlay._abortController.abort(); overlay.remove(); }} }};
    return overlay;
}}

async function previewIssue(btn, type) {{
    var testName = btn.dataset.testName;
    var childJob = btn.dataset.childJob || '';
    var childBuild = parseInt(btn.dataset.childBuild || '0');
    var includeLinks = false;
    var aiProvider = '';
    var aiModel = '';
    var card = btn.closest('.bug-card') || btn.closest('.failure-card');
    if (card) {{
        var cb = card.querySelector('.include-links-cb');
        if (cb) includeLinks = cb.checked;
        var pEl = card.querySelector('.ai-provider-input');
        var mEl = card.querySelector('.ai-model-input');
        if (pEl) aiProvider = pEl.value;
        if (mEl) aiModel = mEl.value;
    }}
    var controller = new AbortController();
    var loadingOverlay = showLoadingModal(type);
    loadingOverlay._abortController = controller;
    var endpoint = type === 'github' ? '/preview-github-issue' : '/preview-jira-bug';
    try {{
        var payload = {{
            test_name: testName, child_job_name: childJob, child_build_number: childBuild,
            include_links: includeLinks,
        }};
        if (aiProvider) payload.ai_provider = aiProvider;
        if (aiModel) payload.ai_model = aiModel;
        var resp = await fetch(BASE_PATH + '/results/' + JOB_ID + endpoint, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(payload),
            signal: controller.signal,
        }});
        if (!resp.ok) {{ throw new Error('HTTP ' + resp.status); }}
        var data = await resp.json();
        loadingOverlay.remove();
        showIssuePreviewModal(type, data, testName, childJob, childBuild, includeLinks, aiProvider, aiModel);
    }} catch (err) {{
        if (err.name === 'AbortError') return;
        var dlg = loadingOverlay.querySelector('.modal-dialog');
        if (dlg) {{
            var spinnerContainer = dlg.querySelector('.loading-spinner');
            if (spinnerContainer) spinnerContainer.parentNode.remove();
            var errDiv = document.createElement('div');
            errDiv.style.cssText = 'padding:16px 0;text-align:center;';
            var errIcon = document.createElement('div');
            errIcon.style.cssText = 'font-size:24px;margin-bottom:8px;';
            errIcon.textContent = '\u26a0';
            errDiv.appendChild(errIcon);
            var errText = document.createElement('div');
            errText.style.cssText = 'color:var(--accent-red, #f85149);font-size:13px;';
            var label = type === 'github' ? 'issue' : 'bug';
            errText.textContent = 'Failed to generate ' + label + ' preview: ' + err.message;
            errDiv.appendChild(errText);
            dlg.querySelector('h3').insertAdjacentElement('afterend', errDiv);
        }}
    }}
}}

function overrideClassification(select) {{
    var newClassification = select.value;
    var testName = select.dataset.testName;
    var childJob = select.dataset.childJob || '';
    var childBuild = parseInt(select.dataset.childBuild || '0');

    fetch(BASE_PATH + '/results/' + JOB_ID + '/override-classification', {{
        method: 'PUT',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
            test_name: testName, child_job_name: childJob, child_build_number: childBuild,
            classification: newClassification,
        }}),
    }}).then(function(r) {{
        if (r.ok) {{
            select.className = 'classification-select ' +
                (newClassification === 'PRODUCT BUG' ? 'product-bug' : 'code-issue');
            var card = select.closest('.bug-card') || select.closest('.failure-card');
            if (card) showCorrectBugButton(card, newClassification);
        }} else {{
            r.json().then(function(data) {{
                showConfirmModal('Error', data.detail || 'Failed to override', function(){{}}, {{confirmLabel: 'OK', confirmOnly: true}});
            }}).catch(function() {{
                showConfirmModal('Error', 'Failed to override classification', function(){{}}, {{confirmLabel: 'OK', confirmOnly: true}});
            }});
        }}
    }}).catch(function(err) {{
        showConfirmModal('Error', 'Network error: ' + err.message, function(){{}}, {{confirmLabel: 'OK', confirmOnly: true}});
    }});
}}

function showCorrectBugButton(card, classification) {{
    var ghBtn = card.querySelector('.github-issue-btn');
    var jiraBtn = card.querySelector('.jira-bug-btn');
    if (ghBtn) ghBtn.style.display = (GITHUB_AVAILABLE && classification === 'CODE ISSUE') ? '' : 'none';
    if (jiraBtn) jiraBtn.style.display = (JIRA_AVAILABLE && classification === 'PRODUCT BUG') ? '' : 'none';
}}

function initBugCreationButtons() {{
    document.querySelectorAll('.bug-card, .failure-card').forEach(function(card) {{
        var select = card.querySelector('.classification-select');
        if (select) {{
            showCorrectBugButton(card, select.value);
            return;
        }}
        var tag = card.querySelector('.classification-tag');
        if (!tag) return;
        var cls = tag.textContent.trim();
        showCorrectBugButton(card, cls);
    }});
}}

function createCombobox(options, defaultValue, width, placeholder, cssClass) {{
    var wrapper = document.createElement('div');
    wrapper.className = 'custom-combo';

    var input = document.createElement('input');
    input.type = 'text';
    input.className = cssClass;
    input.value = defaultValue;
    input.placeholder = placeholder;
    input.style.width = width;

    var arrow = document.createElement('button');
    arrow.type = 'button';
    arrow.className = 'combo-arrow';
    arrow.innerHTML = '&#9660;';

    var dropdown = document.createElement('div');
    dropdown.className = 'combo-dropdown';

    options.forEach(function(opt) {{
        var item = document.createElement('div');
        item.className = 'combo-option';
        item.textContent = opt;
        item.onclick = function(e) {{
            e.stopPropagation();
            input.value = opt;
            dropdown.classList.remove('show');
            input.dispatchEvent(new Event('change'));
        }};
        dropdown.appendChild(item);
    }});

    arrow.onclick = function(e) {{
        e.stopPropagation();
        document.querySelectorAll('.combo-dropdown.show').forEach(function(d) {{
            if (d !== dropdown) d.classList.remove('show');
        }});
        positionDropdown(input, dropdown);
        dropdown.classList.toggle('show');
    }};

    input.onfocus = function() {{
        positionDropdown(input, dropdown);
        dropdown.classList.add('show');
    }};

    input.oninput = function() {{
        var val = input.value.toLowerCase();
        dropdown.querySelectorAll('.combo-option').forEach(function(opt) {{
            opt.style.display = opt.textContent.toLowerCase().indexOf(val) >= 0 ? '' : 'none';
        }});
        positionDropdown(input, dropdown);
        dropdown.classList.add('show');
    }};

    wrapper.appendChild(input);
    wrapper.appendChild(arrow);
    document.body.appendChild(dropdown);
    wrapper._dropdown = dropdown;

    return wrapper;
}}

function positionDropdown(input, dropdown) {{
    var rect = input.getBoundingClientRect();
    dropdown.style.left = rect.left + 'px';
    dropdown.style.top = (rect.bottom + 2) + 'px';
    dropdown.style.minWidth = rect.width + 'px';
}}

document.addEventListener('click', function() {{
    document.querySelectorAll('.combo-dropdown.show').forEach(function(d) {{
        d.classList.remove('show');
    }});
}});

document.addEventListener('scroll', function() {{
    document.querySelectorAll('.combo-dropdown.show').forEach(function(d) {{
        d.classList.remove('show');
    }});
}}, true);

function initAiComboboxes() {{
    if (!_aiConfigs.length) return;
    document.querySelectorAll('.ai-combos-placeholder').forEach(function(ph) {{
        if (ph.children.length > 0) return;

        var providerLabel = document.createElement('label');
        providerLabel.style.cssText = 'font-size:12px;color:var(--text-muted);margin-right:4px;';
        providerLabel.textContent = 'AI:';

        var seenProviders = [];
        _aiConfigs.forEach(function(c) {{
            if (seenProviders.indexOf(c.ai_provider) < 0) seenProviders.push(c.ai_provider);
        }});
        var providerCombo = createCombobox(seenProviders, CURRENT_AI_PROVIDER, '90px', 'provider', 'ai-provider-input');
        providerCombo.style.marginRight = '6px';

        var models = [];
        _aiConfigs.forEach(function(c) {{
            if (models.indexOf(c.ai_model) < 0) models.push(c.ai_model);
        }});
        var modelCombo = createCombobox(models, CURRENT_AI_MODEL, '170px', 'model', 'ai-model-input');
        modelCombo.style.marginRight = '8px';

        ph.appendChild(providerLabel);
        ph.appendChild(providerCombo);
        ph.appendChild(modelCombo);
    }});
}}

document.addEventListener('DOMContentLoaded', async function() {{
    document.querySelectorAll('.reviewed-toggle:not(.select-all-toggle) input[type="checkbox"]').forEach(cb => {{
        cb.addEventListener('change', function(event) {{
            event.stopPropagation();
            toggleReviewed(this.closest('.reviewed-toggle'));
        }});
    }});
    // Enter to send, Shift+Enter for new line
    document.querySelectorAll('.comment-input').forEach(textarea => {{
        textarea.addEventListener('keydown', function(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                const btn = this.closest('.comment-input-row').querySelector('.comment-add-btn');
                if (btn) btn.click();
            }}
        }});
    }});
    initBugCreationButtons();
    initAiComboboxes();
    await loadClassifications();
    await loadCommentsAndReviews();
    await loadEnrichments();
}});
</script>
<script>
{_username_helper_js()}
{_user_badge_js()}
</script>
""")

    parts.append("</div>\n</body>\n</html>")
    return "\n".join(parts)


def _format_provider(ai_provider: str, ai_model: str) -> str:
    """Format AI provider and model for display.

    Args:
        ai_provider: AI provider name.
        ai_model: AI model name.

    Returns:
        Display string like "Claude (claude-sonnet-4-20250514)".
    """
    if not ai_provider:
        return "Unknown provider"
    if ai_model:
        return f"{ai_provider.capitalize()} ({ai_model})"
    return ai_provider.capitalize()


def _classification_css_class(classification: str) -> str:
    """Return a CSS class suffix for a classification string.

    Args:
        classification: Classification text like "PRODUCT BUG".

    Returns:
        A CSS-safe class name like "product-bug" or "code-issue".
    """
    lower = classification.lower().strip()
    if "product" in lower and "bug" in lower:
        return "product-bug"
    if "code" in lower and "issue" in lower:
        return "code-issue"
    return "unknown"


def _grouping_key(failure: FailureAnalysis) -> str:
    """Generate a grouping key for a failure.

    Uses error_signature as the primary key (matches override semantics).
    Falls back to analysis-based heuristics when signature is missing.
    """
    detail = failure.analysis

    # Primary: group by error_signature when available (matches override semantics)
    sig = failure.error_signature or ""
    if sig:
        cls = (detail.classification or "").strip().upper()
        return f"{cls}|sig:{sig}"

    # Fallback: analysis-based heuristics when signature is missing
    cls = (detail.classification or "").strip().upper()

    # For product bugs, group by classification + first 4 words of title
    if (
        isinstance(detail.product_bug_report, ProductBugReport)
        and detail.product_bug_report.title
    ):
        title = detail.product_bug_report.title.strip().lower()
        words = title.split()[:4]
        normalized_title = " ".join(words)
        return f"{cls}|title:{normalized_title}"

    # For code issues, group by classification + file path
    if isinstance(detail.code_fix, CodeFix) and detail.code_fix.file:
        return f"{cls}|file:{detail.code_fix.file.strip()}"

    # Fallback: full JSON match
    return detail.model_dump_json()


def _group_failures(failures: list[FailureAnalysis], prefix: str = "") -> list[dict]:
    """Group failures that share the same root cause.

    Uses error_signature as the primary grouping key (matches override
    semantics).  Falls back to analysis-based heuristics when the
    signature is missing.

    Args:
        failures: List of FailureAnalysis instances to group.
        prefix: String prepended to each ``bug_id`` to ensure DOM-wide
            uniqueness when the function is called for different child jobs.

    Returns:
        A list of dicts, each containing:
        - ``analysis``: the representative AnalysisDetail
        - ``failures``: list of FailureAnalysis in this group
        - ``bug_id``: a short identifier like ``"BUG-1"`` (or
          ``"<prefix>BUG-1"`` when *prefix* is provided)
    """
    if not failures:
        return []

    # First pass: group by key
    groups_map: dict[str, list[FailureAnalysis]] = {}
    order: list[str] = []
    for f in failures:
        key = _grouping_key(f)
        if key not in groups_map:
            groups_map[key] = []
            order.append(key)
        groups_map[key].append(f)

    # Build final groups
    groups: list[dict] = []
    for idx, key in enumerate(order, start=1):
        group_failures = groups_map[key]
        groups.append(
            {
                "analysis": group_failures[0].analysis,
                "failures": group_failures,
                "bug_id": f"{prefix}BUG-{idx}" if prefix else f"BUG-{idx}",
            }
        )
    return groups


def _render_group_card(
    parts: list[str],
    group: dict,
    e: Callable[[str], str],
    indent: str = "",
    job_id: str = "",
    child_job_name: str = "",
    child_build_number: int = 0,
) -> None:
    """Render a collapsible card for a group of failures sharing the same analysis.

    Args:
        parts: List of HTML string parts to append to.
        group: Dict with keys 'analysis' (AnalysisDetail), 'failures' (list), 'bug_id' (str).
        e: HTML escape function reference.
        indent: HTML indentation prefix for nested cards.
        job_id: The job identifier for reviewed toggle data attributes.
        child_job_name: The child job name for reviewed toggle data attributes.
        child_build_number: The child build number for reviewed toggle data attributes.
    """
    detail = group["analysis"]
    bug_id = group["bug_id"]
    failures = group["failures"]
    cls = detail.classification or "Unknown"
    cls_class = _classification_css_class(cls)
    test_count = len(failures)
    test_label = f"{test_count} test{'s' if test_count != 1 else ''}"

    # Severity from product bug report if available
    severity = ""
    if (
        isinstance(detail.product_bug_report, ProductBugReport)
        and detail.product_bug_report.severity
    ):
        severity = detail.product_bug_report.severity.lower()
    if severity not in ("critical", "high", "medium", "low"):
        severity = "unknown"

    # Card title: bug report title, or first test error
    if (
        isinstance(detail.product_bug_report, ProductBugReport)
        and detail.product_bug_report.title
    ):
        card_title = detail.product_bug_report.title
    else:
        card_title = failures[0].error or failures[0].test_name

    # DESIGN DECISION: Bug creation buttons, override button, and comments all use
    # failures[0] (the representative test).  This is correct because a bug card
    # represents ONE root cause — all tests in the group share the same analysis
    # and classification.  The override updates ALL tests with the same
    # error_signature on the backend (see storage.override_classification).
    group_test_names = e(json.dumps([f.test_name for f in failures]))
    parts.append(f"""{indent}<details class="bug-card">
{indent}  <summary class="bug-summary" data-test-names="{group_test_names}" data-child-job="{e(child_job_name)}">
{indent}    <span class="bug-id">{e(bug_id)}</span>
{indent}    <span class="bug-title">{e(card_title)}</span>
{indent}    <span class="bug-count">{e(test_label)}</span>
{indent}    <select class="classification-select {e(cls_class)}" data-test-name="{e(failures[0].test_name)}" data-child-job="{e(child_job_name)}" data-child-build="{child_build_number}" onclick="event.stopPropagation()" onchange="overrideClassification(this)">
{indent}      {'<option value="" disabled selected>' + e(cls) + "</option>" if cls not in ("CODE ISSUE", "PRODUCT BUG") else ""}
{indent}      <option value="CODE ISSUE" {"selected" if cls == "CODE ISSUE" else ""}>CODE ISSUE</option>
{indent}      <option value="PRODUCT BUG" {"selected" if cls == "PRODUCT BUG" else ""}>PRODUCT BUG</option>
{indent}    </select>
{indent}    {'<span class="severity-tag-inline ' + e(severity) + '">' + e(severity.upper()) + "</span>" if severity and severity.upper() != "UNKNOWN" else ""}
{indent}    <span class="group-review-status status-chip" style="display:none"></span>
{indent}  </summary>
{indent}  <div class="bug-body">
""")

    # AI Analysis
    if detail.details:
        parts.append(f"""{indent}    <div class="bug-analysis">
{indent}      <h4>AI Analysis</h4>
{indent}      <pre class="analysis-pre">{e(detail.details)}</pre>
{indent}    </div>
""")

    # Code Fix details (improvement over reference)
    if isinstance(detail.code_fix, CodeFix):
        fix = detail.code_fix
        parts.append(f"""{indent}    <h4>Code Fix</h4>
{indent}    <div class="detail-grid">
{indent}      <span class="detail-label">File:</span><span class="detail-value">{e(fix.file)}</span>
{indent}      <span class="detail-label">Line:</span><span class="detail-value">{e(fix.line)}</span>
{indent}      <span class="detail-label">Change:</span><span class="detail-value">{e(fix.change)}</span>
{indent}    </div>
""")

    # Product Bug Report details (improvement over reference)
    if isinstance(detail.product_bug_report, ProductBugReport):
        bug = detail.product_bug_report

        parts.append(f"""{indent}    <h4>Product Bug Report</h4>
{indent}    <div class="detail-grid">
{indent}      <span class="detail-label">Title:</span><span class="detail-value">{e(bug.title)}</span>
{indent}      <span class="detail-label">Severity:</span><span class="detail-value">{e(bug.severity)}</span>
{indent}      <span class="detail-label">Component:</span><span class="detail-value">{e(bug.component)}</span>
{indent}      <span class="detail-label">Description:</span><span class="detail-value">{e(bug.description)}</span>
{indent}      <span class="detail-label">Evidence:</span><span class="detail-value">{e(bug.evidence)}</span>
{indent}    </div>
""")
        # Jira matches
        if bug.jira_matches:
            _render_jira_matches(parts, bug.jira_matches, e, indent)

    # Artifacts evidence
    _render_artifacts_evidence(parts, detail, e, indent)

    # Affected Tests
    parts.append(f"""{indent}    <div class="bug-tests">
{indent}      <h4>Affected Tests ({test_count})</h4>
{indent}      <label class="reviewed-toggle select-all-toggle" onclick="event.stopPropagation(); toggleAllReviewed(this)" style="margin-bottom:8px;font-weight:700;">
{indent}        <input type="checkbox"> Select All
{indent}      </label>
{indent}      <ul style="padding-left:24px;">
""")
    for f in failures:
        parts.append(
            f'{indent}        <li><label class="reviewed-toggle" data-job-id="{e(job_id)}" data-test-name="{e(f.test_name)}" data-child-job="{e(child_job_name)}" data-child-build="{child_build_number}"><input type="checkbox"> <code style="font-family:var(--font-mono);font-size:12px;color:var(--text-primary)">{e(f.test_name)}</code></label></li>\n'
        )
    parts.append(f"""{indent}      </ul>
{indent}    </div>
""")

    # Error
    parts.append(f"""{indent}    <div class="bug-error">
{indent}      <h4>Error</h4>
{indent}      <pre class="error-pre">{e(failures[0].error)}</pre>
{indent}    </div>
""")

    # Comments section (populated by JavaScript)
    # Always use the first (representative) test for comments
    comment_test = failures[0].test_name
    select_html = f'{indent}        <input type="hidden" class="comment-test-select" value="{e(comment_test)}">\n'

    all_test_names = e(json.dumps([f.test_name for f in failures]))
    parts.append(f"""{indent}    <div class="comments-section" data-test-names="{all_test_names}" data-child-job="{e(child_job_name)}" data-child-build="{child_build_number}">
{indent}      <div class="comments-header">Comments (<span class="comment-count">0</span>)</div>
{indent}      <div class="comment-list"></div>
{indent}      <div class="comment-input-row" style="flex-direction:column;gap:8px;">
{select_html}{indent}        <div style="display:flex;gap:8px;">
{indent}          <textarea class="comment-input" placeholder="Add a comment (bug link, PR, notes...)" rows="1" style="flex:1;"></textarea>
{indent}          <button class="comment-add-btn" onclick="addComment(this)">Add</button>
{indent}        </div>
{indent}      </div>
{indent}    </div>
{indent}    <div class="bug-actions">
{indent}      <button class="create-issue-btn github-issue-btn" data-test-name="{e(comment_test)}" data-child-job="{e(child_job_name)}" data-child-build="{child_build_number}" onclick="previewIssue(this, 'github')" style="display:none"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg> Open GitHub Issue</button>
{indent}      <button class="create-issue-btn jira-bug-btn" data-test-name="{e(comment_test)}" data-child-job="{e(child_job_name)}" data-child-build="{child_build_number}" onclick="previewIssue(this, 'jira')" style="display:none"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M11.53 2c0 2.4 1.97 4.35 4.35 4.35h1.78v1.7c0 2.4 1.94 4.34 4.34 4.35V2.84a.84.84 0 00-.84-.84H11.53zM6.77 6.8a4.36 4.36 0 004.34 4.34h1.78v1.72a4.36 4.36 0 004.34 4.34V7.63a.84.84 0 00-.83-.83H6.77zM2 11.6c0 2.4 1.95 4.34 4.35 4.35h1.78v1.72c.01 2.39 1.95 4.33 4.35 4.33v-9.57a.84.84 0 00-.84-.83H2z"/></svg> Open Jira Bug</button>
{indent}      <span class="ai-combos-placeholder" style="display:inline-flex;align-items:center;"></span>
{indent}      <label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--text-muted);cursor:pointer;">
{indent}        <input type="checkbox" class="include-links-cb" style="width:14px;height:14px;">
{indent}        Include links
{indent}      </label>
{indent}    </div>
""")

    parts.append(f"""{indent}  </div>
{indent}</details>
""")


def _render_artifacts_evidence(
    parts: list[str], detail: AnalysisDetail, e: Callable, indent: str
) -> None:
    """Render artifacts_evidence into the HTML parts list if present."""
    if not detail.artifacts_evidence:
        return
    parts.append(f"""{indent}    <div class="detail-grid">
{indent}      <span class="detail-label">Artifacts Evidence:</span><pre class="detail-value" style="white-space: pre-wrap; margin: 0;">{e(detail.artifacts_evidence)}</pre>
{indent}    </div>
""")


def _render_jira_matches(
    parts: list[str],
    matches: list[JiraMatch],
    e: Callable[[str], str],
    indent: str = "",
) -> None:
    """Render Jira match links.

    Args:
        parts: List of HTML string parts to append to.
        matches: List of JiraMatch objects to render.
        e: HTML escape function reference.
        indent: HTML indentation prefix.
    """
    parts.append(f"{indent}    <h4>Possible Jira Matches ({len(matches)})</h4>\n")
    parts.append(f'{indent}    <div class="jira-matches">\n')
    for match in matches:
        parts.append(
            f'{indent}      <a class="jira-match-link" href="{e(match.url)}" '
            f'target="_blank" rel="noopener">'
            f"{e(match.key)}: {e(match.summary)} "
            f'<span class="jira-match-status">[{e(match.status)}]</span>'
            f"</a>\n"
        )
    parts.append(f"{indent}    </div>\n")


def _render_child_jobs(
    parts: list[str],
    children: list[ChildJobAnalysis],
    e: Callable[[str], str],
    depth: int = 0,
    max_depth: int = 10,
    job_id: str = "",
) -> None:
    """Render child job analysis sections recursively.

    Args:
        parts: List of HTML string parts to append to.
        children: Child job analyses to render.
        e: HTML escape function reference.
        depth: Current recursion depth.
        max_depth: Maximum recursion depth for nested children.
        job_id: The job identifier for reviewed toggle data attributes.
    """
    for child in children:
        child_failures_count = len(child.failures)
        child_prefix = (
            re.sub(r"[^a-zA-Z0-9]", "_", f"{child.job_name}-{child.build_number}") + "-"
        )
        child_groups = (
            _group_failures(child.failures, prefix=child_prefix)
            if child.failures
            else []
        )
        child_groups_count = len(child_groups)

        # Show group count when grouping reduces the visible cards
        if child_groups_count and child_groups_count < child_failures_count:
            badge_text = (
                f"{child_groups_count} root cause{'s' if child_groups_count != 1 else ''}"
                f" ({child_failures_count} failure{'s' if child_failures_count != 1 else ''})"
            )
        else:
            badge_text = f"{child_failures_count} failure{'s' if child_failures_count != 1 else ''}"

        parts.append(f"""<details class="child-job">
  <summary class="child-job-summary">
    <span style="color:var(--accent-purple)">{e(child.job_name)}</span>
    <span style="color:var(--text-muted)">#{child.build_number}</span>
    <span class="failure-badge" style="font-size:11px;padding:2px 8px">{badge_text}</span>
    <span class="child-review-status status-chip" data-child-job="{e(child.job_name)}" style="display:none"></span>
  </summary>
  <div class="child-job-body">
    <div class="child-job-meta">
      <span>Build: #{child.build_number}</span>
""")
        if child.jenkins_url:
            parts.append(
                f'      <a href="{e(child.jenkins_url)}" target="_blank" rel="noopener">View in Jenkins</a>\n'
            )
        parts.append("    </div>")

        if child.note:
            parts.append(f'    <div class="child-note">{e(child.note)}</div>')

        if child.summary:
            parts.append(
                f'    <p style="font-size:13px;color:var(--text-secondary);margin:8px 0">{e(child.summary)}</p>'
            )

        if child_groups:
            for group in child_groups:
                _render_group_card(
                    parts,
                    group,
                    e,
                    indent="    ",
                    job_id=job_id,
                    child_job_name=child.job_name,
                    child_build_number=child.build_number,
                )

        # Recurse into nested children
        if child.failed_children and depth < max_depth:
            _render_child_jobs(
                parts,
                child.failed_children,
                e,
                depth=depth + 1,
                max_depth=max_depth,
                job_id=job_id,
            )

        parts.append("  </div>\n</details>\n")


def _append_takeaway(parts: list[str], summary: str, e: Callable[[str], str]) -> None:
    """Append the key takeaway callout section.

    Args:
        parts: List of HTML string parts to append to.
        summary: The analysis summary text.
        e: HTML escape function reference.
    """
    parts.append(f"""
<div class="key-takeaway">
  <div class="key-takeaway-header">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent-yellow)" stroke-width="2">
      <circle cx="12" cy="12" r="10"/>
      <line x1="12" y1="16" x2="12" y2="12"/>
      <line x1="12" y1="8" x2="12.01" y2="8"/>
    </svg>
    <h3>Key Takeaway</h3>
  </div>
  <p>{e(summary)}</p>
</div>
""")


def _append_footer(
    parts: list[str],
    job_name: str,
    build_number: str,
    job_id: str,
    provider_info: str,
    jenkins_url: str,
    e: Callable[[str], str],
) -> None:
    """Append the report footer section.

    Args:
        parts: List of HTML string parts to append to.
        job_name: Jenkins job name.
        build_number: Build number string.
        job_id: Analysis job identifier.
        provider_info: AI provider display string.
        jenkins_url: Full Jenkins build URL.
        e: HTML escape function reference.
    """
    jenkins_link = (
        f'<a href="{e(jenkins_url)}" target="_blank" rel="noopener">View in Jenkins</a>'
        if jenkins_url
        else ""
    )
    parts.append(f"""
<div class="report-footer">
  <span>{e(job_name)} #{e(build_number)} | Job ID: {e(job_id)} | Analyzed by {e(provider_info)}</span>
  {jenkins_link}
</div>
""")


def format_status_page(job_id: str, status: str, result: dict) -> str:
    """Generate a status page for a job that is still processing.

    Uses the same dark theme as the full report, with auto-refresh
    and a simple status indicator.

    Args:
        job_id: The analysis job identifier.
        status: Current job status (pending/running).
        result: The job result dict from storage.

    Returns:
        A complete HTML document as a string.
    """
    e = html.escape

    jenkins_url = result.get("jenkins_url", "")
    created_at = result.get("created_at", "")

    # Extract job_name and build_number from stored result data
    result_data = result.get("result") or {}
    job_name = result_data.get("job_name", "")
    build_number = result_data.get("build_number", "")

    status_icon = "&#9203;" if status == "running" else "&#8987;"
    status_label = "Analyzing..." if status == "running" else "Queued"
    status_detail = (
        "AI is analyzing the Jenkins build failures. This page will auto-refresh."
        if status == "running"
        else "Job is queued and waiting to start. This page will auto-refresh."
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Analysis {e(status_label)} - {
        e(job_name) + " #" + e(str(build_number)) if job_name else e(job_id)
    }</title>
<link rel="icon" href="{FAVICON_DATA_URI}">
<style>
/* Minimal standalone styles — this lightweight status page intentionally
   skips _common_css() to keep the response small and self-contained. */
:root {{
    --bg-primary: #0d1117;
    --bg-secondary: #161b22;
    --border: #30363d;
    --text-primary: #e6edf3;
    --text-secondary: #8b949e;
    --text-muted: #6e7681;
    --accent-blue: #58a6ff;
    --accent-yellow: #d29922;
    --accent-purple: #bc8cff;
    --font-mono: 'SF Mono', 'Cascadia Code', Consolas, monospace;
    --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    --radius: 8px;
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: var(--font-sans);
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.6;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
}}
.status-container {{
    max-width: 520px;
    width: 100%;
    padding: 40px;
    text-align: center;
}}
.status-icon {{
    font-size: 48px;
    margin-bottom: 20px;
    animation: pulse 2s ease-in-out infinite;
}}
@keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.5; }}
}}
.status-label {{
    font-size: 24px;
    font-weight: 700;
    margin-bottom: 8px;
    color: var(--accent-yellow);
}}
.status-detail {{
    font-size: 14px;
    color: var(--text-secondary);
    margin-bottom: 32px;
}}
.info-card {{
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px;
    text-align: left;
}}
.info-row {{
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    font-size: 13px;
    border-bottom: 1px solid var(--border);
}}
.info-row:last-child {{ border-bottom: none; }}
.info-label {{ color: var(--text-muted); font-weight: 600; }}
.info-value {{ color: var(--text-primary); font-family: var(--font-mono); font-size: 12px; }}
.info-value a {{ color: var(--accent-blue); text-decoration: none; }}
.info-value a:hover {{ text-decoration: underline; }}
.spinner {{
    display: inline-block;
    width: 16px;
    height: 16px;
    border: 2px solid var(--border);
    border-top-color: var(--accent-yellow);
    border-radius: 50%;
    animation: spin 1s linear infinite;
    vertical-align: middle;
    margin-right: 6px;
}}
@keyframes spin {{
    to {{ transform: rotate(360deg); }}
}}
.refresh-note {{
    margin-top: 20px;
    font-size: 12px;
    color: var(--text-muted);
}}
</style>
</head>
<body>
<div class="status-container">
    <div class="status-icon">{status_icon}</div>
    {
        f'<div style="font-size:16px;color:var(--text-secondary);margin-bottom:8px;font-weight:600;">{e(job_name)} #{e(str(build_number))}</div>'
        if job_name
        else ""
    }
    <div class="status-label"><span class="spinner"></span>{e(status_label)}</div>
    <div class="status-detail">{e(status_detail)}</div>
    <div class="info-card">{
        f'''<div class="info-row">
            <span class="info-label">Job</span>
            <span class="info-value">{e(job_name)} #{e(str(build_number))}</span>
        </div>'''
        if job_name
        else ""
    }
        <div class="info-row">
            <span class="info-label">Job ID</span>
            <span class="info-value">{e(job_id)}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Status</span>
            <span class="info-value">{e(status)}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Created</span>
            <span class="info-value">{e(created_at)}</span>
        </div>
        {
        ""
        if not jenkins_url
        else f'''<div class="info-row">
            <span class="info-label">Jenkins</span>
            <span class="info-value"><a href="{e(jenkins_url)}" target="_blank" rel="noopener">View Build</a></span>
        </div>'''
    }
    </div>
    <div class="refresh-note">Auto-refreshing every 10 seconds</div>
</div>
<script>
{_username_helper_js()}
{_user_badge_js()}
</script>
</body>
</html>"""


def generate_dashboard_html(
    jobs: list[dict], base_url: str = "", limit: int = 500
) -> str:
    """Generate a self-contained HTML dashboard page listing analysis jobs.

    Produces a complete HTML document with inline CSS using the same dark
    GitHub-inspired theme as the analysis reports. Each job is rendered as
    a clickable card linking to its HTML report.

    Args:
        jobs: List of dicts from list_results_for_dashboard(). Each dict has
            job_id, jenkins_url, status, created_at, and optionally job_name,
            build_number, failure_count.
        base_url: External base URL for constructing absolute report links.
        limit: The server-side cap that was used to load jobs. Shown in the UI
            so the user can adjust it.

    Returns:
        A complete HTML document as a string.
    """
    e = html.escape
    total_jobs = len(jobs)

    parts: list[str] = []

    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jenkins Job Insight - Dashboard</title>
<link rel="icon" href="{FAVICON_DATA_URI}">
<style>
{_common_css()}
.jobs-badge {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(88, 166, 255, 0.12);
    color: var(--accent-blue);
    font-size: 13px;
    font-weight: 700;
    padding: 4px 12px;
    border-radius: 12px;
    font-family: var(--font-mono);
}}

/* Dashboard cards */
.dashboard-card {{
    display: flex;
    align-items: flex-start;
    gap: 16px;
    flex-wrap: wrap;
    padding: 16px 20px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 8px;
    color: inherit;
    transition: background 0.15s, border-color 0.15s;
}}
.dashboard-card:hover {{
    background: var(--bg-hover);
    border-color: var(--accent-blue);
}}
.card-link {{
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 16px;
    text-decoration: none;
    color: inherit;
}}
.card-main {{
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
}}
.card-job-name {{
    font-weight: 600;
    font-size: 14px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 400px;
}}
.card-build-chip {{
    font-size: 12px;
    font-family: var(--font-mono);
    padding: 2px 8px;
    border-radius: 4px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text-secondary);
    white-space: nowrap;
}}
.status-chip {{
    font-size: 11px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 12px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    white-space: nowrap;
}}
.status-chip.completed {{ background: rgba(63, 185, 80, 0.15); color: var(--accent-green); }}
.status-chip.failed {{ background: rgba(248, 81, 73, 0.15); color: var(--accent-red); }}
.status-chip.running {{ background: rgba(210, 153, 34, 0.15); color: var(--accent-yellow); }}
.status-chip.pending {{ background: rgba(88, 166, 255, 0.12); color: var(--accent-blue); }}
.failure-count-badge {{
    font-size: 12px;
    font-weight: 700;
    font-family: var(--font-mono);
    padding: 2px 8px;
    border-radius: 4px;
    background: rgba(248, 81, 73, 0.12);
    color: var(--accent-red);
    white-space: nowrap;
}}
/* Result indicators */
.card-result-icon {{
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    border-radius: 50%;
}}
.card-result-icon.passed {{
    background: rgba(63, 185, 80, 0.15);
    color: var(--accent-green);
}}
.card-result-icon.has-failures {{
    background: rgba(248, 81, 73, 0.15);
    color: var(--accent-red);
}}
.dashboard-card.result-passed {{
    border-left: 3px solid var(--accent-green);
}}
.dashboard-card.result-failures {{
    border-left: 3px solid var(--accent-red);
}}
.passed-badge {{
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    background: rgba(63, 185, 80, 0.12);
    color: var(--accent-green);
    white-space: nowrap;
}}
.child-jobs-badge {{
    font-size: 11px;
    font-weight: 700;
    font-family: var(--font-mono);
    padding: 2px 8px;
    border-radius: 4px;
    background: rgba(188, 140, 255, 0.12);
    color: var(--accent-purple);
    white-space: nowrap;
}}
.card-meta {{
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
    white-space: nowrap;
}}
.card-timestamp {{
    font-size: 12px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    white-space: nowrap;
}}
.card-jenkins-icon {{
    color: var(--text-muted);
    flex-shrink: 0;
}}
.card-job-id {{
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 120px;
}}

{_controls_css()}
.limit-control {{
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
}}
.limit-label {{
    font-size: 13px;
    color: var(--text-secondary);
    white-space: nowrap;
}}
.limit-input {{
    width: 80px;
    padding: 10px 10px;
    font-size: 14px;
    font-family: var(--font-mono);
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-primary);
    outline: none;
    transition: border-color 0.15s;
}}
.limit-input:focus {{ border-color: var(--accent-blue); }}
.limit-btn {{
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 600;
    font-family: var(--font-sans);
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-primary);
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
    white-space: nowrap;
}}
.limit-btn:hover {{
    background: var(--bg-hover);
    border-color: var(--accent-blue);
}}

/* Empty state */
.empty-state {{
    text-align: center;
    padding: 80px 20px;
    color: var(--text-muted);
}}
.empty-state svg {{ margin-bottom: 20px; }}
.empty-state p {{
    font-size: 16px;
    margin-top: 8px;
}}

{_modal_css()}

/* Responsive (page-specific) */
@media (max-width: 768px) {{
    .card-link {{ flex-direction: column; align-items: flex-start; gap: 10px; }}
    .card-meta {{ width: 100%; justify-content: space-between; }}
    .card-job-name {{ max-width: 100%; }}
    .controls-bar {{ flex-direction: column; }}
    .search-input {{ min-width: 100%; }}
}}
@media (max-width: 480px) {{
    .card-main {{ font-size: 12px; gap: 6px; }}
    .card-job-id {{ max-width: 80px; }}
}}
</style>
</head>
<body>
<div class="container">
""")

    # --- STICKY HEADER ---
    limit_note = f" (showing last {limit})" if total_jobs >= limit else ""
    parts.append(f"""
<div class="sticky-header">
  <div class="header-content">
    <h1>Jenkins Job Insight</h1>
    <span id="jobs-badge" class="jobs-badge">{total_jobs} job{"s" if total_jobs != 1 else ""}{e(limit_note)}</span>
    <a class="env-chip" href="{e(base_url)}/history" style="margin-left:auto;text-decoration:none;color:var(--accent-blue);">History</a>
  </div>
</div>
""")

    # --- EMPTY STATE ---
    if total_jobs == 0:
        parts.append(f"""
<div class="controls-bar">
  <div class="limit-control">
    <span class="limit-label">Load last</span>
    <input type="number" id="limit-input" class="limit-input" min="1" value="{limit}">
    <button id="limit-btn" class="limit-btn" onclick="window.location.href='{e(base_url)}/dashboard?limit='+document.getElementById('limit-input').value">Load</button>
  </div>
</div>
<div class="empty-state">
  <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
    <rect x="3" y="3" width="18" height="18" rx="2"/>
    <line x1="9" y1="9" x2="15" y2="9"/>
    <line x1="9" y1="13" x2="15" y2="13"/>
    <line x1="9" y1="17" x2="12" y2="17"/>
  </svg>
  <p>No analysis results yet</p>
</div>
""")
    else:
        # --- CONTROLS BAR ---
        parts.append(f"""
<div class="controls-bar">
  <input type="text" id="search-input" class="search-input" placeholder="Search jobs by name, ID, or status...">
  <select id="per-page-select" class="per-page-select">
    <option value="10" selected>10 per page</option>
    <option value="50">50 per page</option>
    <option value="100">100 per page</option>
  </select>
  <div class="limit-control">
    <span class="limit-label">Load last</span>
    <input type="number" id="limit-input" class="limit-input" min="1" value="{limit}">
    <button id="limit-btn" class="limit-btn" onclick="window.location.href='{e(base_url)}/dashboard?limit='+document.getElementById('limit-input').value">Load</button>
  </div>
</div>
""")

        # --- JOB CARDS ---
        parts.append('<div id="job-cards">')
        for job in jobs:
            _render_dashboard_card(parts, job, base_url, e)
        parts.append("</div>")

        # --- PAGINATION CONTROLS ---
        parts.append("""
<div class="pagination-controls">
  <button id="prev-btn" class="pagination-btn" disabled>Previous</button>
  <span id="page-info" class="page-info">Page 1 of 1</span>
  <button id="next-btn" class="pagination-btn" disabled>Next</button>
</div>
""")

    # --- FOOTER ---
    parts.append("""
<div class="report-footer">
  <span>Jenkins Job Insight Dashboard</span>
</div>
""")

    # --- JAVASCRIPT (only when there are jobs) ---
    if total_jobs > 0:
        parts.append(f"""
<script>
(function() {{
  var currentPage = 1;
  var perPage = 10;
  var serverLimit = {limit};
  var allCards = Array.from(document.querySelectorAll('#job-cards .dashboard-card'));
  var totalAll = allCards.length;
  var filteredCards = allCards.slice();

  var searchInput = document.getElementById('search-input');
  var perPageSelect = document.getElementById('per-page-select');
  var prevBtn = document.getElementById('prev-btn');
  var nextBtn = document.getElementById('next-btn');
  var pageInfo = document.getElementById('page-info');
  var jobsBadge = document.getElementById('jobs-badge');

  function getCardText(card) {{
    var text = card.textContent.toLowerCase();
    var titles = card.querySelectorAll('[title]');
    for (var i = 0; i < titles.length; i++) {{
      text += ' ' + titles[i].getAttribute('title').toLowerCase();
    }}
    var href = card.getAttribute('href');
    if (href) {{
      text += ' ' + href.toLowerCase();
    }}
    return text;
  }}

  function applyFilter() {{
    var query = searchInput.value.toLowerCase().trim();
    if (query === '') {{
      filteredCards = allCards.slice();
    }} else {{
      filteredCards = allCards.filter(function(card) {{
        return getCardText(card).indexOf(query) !== -1;
      }});
    }}
    currentPage = 1;
    render();
  }}

  function render() {{
    var totalFiltered = filteredCards.length;
    var totalPages = Math.max(1, Math.ceil(totalFiltered / perPage));
    if (currentPage > totalPages) currentPage = totalPages;
    var start = (currentPage - 1) * perPage;
    var end = start + perPage;

    // Hide all cards first
    for (var i = 0; i < allCards.length; i++) {{
      allCards[i].style.display = 'none';
    }}
    // Show only the filtered cards for the current page
    for (var j = 0; j < filteredCards.length; j++) {{
      if (j >= start && j < end) {{
        filteredCards[j].style.display = '';
      }}
    }}

    // Update page info
    pageInfo.textContent = 'Page ' + currentPage + ' of ' + totalPages;

    // Update buttons
    prevBtn.disabled = (currentPage <= 1);
    nextBtn.disabled = (currentPage >= totalPages);

    // Update badge
    var suffix = (totalAll !== 1 ? 's' : '');
    if (totalFiltered === totalAll) {{
      var limitNote = (totalAll >= serverLimit) ? ' (showing last ' + serverLimit + ')' : '';
      jobsBadge.textContent = totalAll + ' job' + suffix + limitNote;
    }} else {{
      jobsBadge.textContent = totalFiltered + ' of ' + totalAll + ' job' + suffix;
    }}
  }}

  searchInput.addEventListener('input', applyFilter);

  perPageSelect.addEventListener('change', function() {{
    perPage = parseInt(perPageSelect.value, 10);
    currentPage = 1;
    render();
  }});

  prevBtn.addEventListener('click', function() {{
    if (currentPage > 1) {{
      currentPage--;
      render();
    }}
  }});

  nextBtn.addEventListener('click', function() {{
    var totalPages = Math.max(1, Math.ceil(filteredCards.length / perPage));
    if (currentPage < totalPages) {{
      currentPage++;
      render();
    }}
  }});

  // After a card is deleted, rebuild the cached card arrays and re-render
  // so pagination counts, search state, and badge totals stay accurate.
  window.addEventListener('dashboard-card-deleted', function() {{
    allCards = Array.from(document.querySelectorAll('#job-cards .dashboard-card'));
    totalAll = allCards.length;
    applyFilter();
  }});

  // Initial render
  render();
}})();
</script>
""")

    # --- USERNAME DISPLAY (always, regardless of job count) ---
    parts.append(f"""
<script>
{_username_helper_js()}
{_user_badge_js()}
</script>
""")

    # --- CLASSIFICATION BADGES (global header summary + per-card by job_id) ---
    parts.append(f"\n<script>\n{_classification_colors_js()}\n</script>")
    parts.append("""
<script>
(function() {
    var BASE = window.location.pathname.replace(/\\/dashboard.*$/, '');
    fetch(BASE + '/history/classifications').then(function(r) { return r.json(); }).then(function(data) {
        var counts = {};
        var byJobId = {};
        var jiraKeysByJobId = {};
        (data.classifications || []).forEach(function(c) {
            counts[c.classification] = (counts[c.classification] || 0) + 1;
            var jid = c.job_id || '';
            if (jid) {
                if (!byJobId[jid]) byJobId[jid] = {};
                byJobId[jid][c.classification] = (byJobId[jid][c.classification] || 0) + 1;
                if (c.classification === 'KNOWN_BUG') {
                    var jm = (c.reason || '').match(/([A-Z][A-Z0-9]+-\\d+)/);
                    if (jm) {
                        if (!jiraKeysByJobId[jid]) jiraKeysByJobId[jid] = {};
                        jiraKeysByJobId[jid][jm[1]] = true;
                    }
                }
            }
        });

        // Per-card classification badges (matched by job_id)
        document.querySelectorAll('.classification-job-badges').forEach(function(span) {
            var cardJobId = span.dataset.jobId;
            if (!cardJobId || !byJobId[cardJobId]) return;
            var html = '';
            for (var cls in byJobId[cardJobId]) {
                var count = byJobId[cardJobId][cls];
                var color = getClassificationStyle(cls);
                var label = count + ' ' + cls.replace('_', ' ');
                if (cls === 'KNOWN_BUG' && jiraKeysByJobId[cardJobId]) {
                    var keys = Object.keys(jiraKeysByJobId[cardJobId]);
                    if (keys.length > 0) {
                        label = count + ' KNOWN BUG: ' + keys.join(', ');
                    }
                }
                html += '<span style="display:inline;font-size:11px;font-weight:700;padding:3px 10px;border-radius:12px;' + color + 'white-space:nowrap;margin-right:4px;">' + label + '</span>';
            }
            span.style.display = '';
            span.innerHTML = html;
            var sec = span.closest('.card-secondary');
            if (sec) sec.style.display = 'flex';
        });
    }).catch(function() {});
})();
</script>
""")

    # --- DELETE JOB JS ---
    parts.append("\n<script>")
    parts.append(_modal_js())
    parts.append("""
function deleteJob(btn, jobId) {
    showConfirmModal('Delete Analysis', 'Are you sure you want to delete this analysis? All comments, reviews, and history data will be permanently removed.', async function() {
        var BASE = window.location.pathname.replace(/\\/dashboard.*$/, '');
        try {
            var resp = await fetch(BASE + '/results/' + jobId, { method: 'DELETE' });
            if (resp.ok) {
                var card = btn.closest('.dashboard-card');
                card.style.transition = 'opacity 0.3s';
                card.style.opacity = '0';
                setTimeout(function() {
                    card.remove();
                    // Dispatch a custom event so the pagination controller can
                    // rebuild its cached card lists and re-render.
                    window.dispatchEvent(new CustomEvent('dashboard-card-deleted'));
                }, 300);
            } else {
                var errData = await resp.json().catch(function() { return {}; });
                showConfirmModal('Delete Failed', errData.detail || 'Failed to delete analysis. HTTP ' + resp.status, function() {}, {confirmLabel: 'OK', confirmOnly: true});
            }
        } catch (err) {
            console.warn('Failed to delete job:', err);
            showConfirmModal('Delete Failed', 'Failed to delete analysis. Please try again.', function() {}, {confirmLabel: 'OK', confirmOnly: true});
        }
    });
}
</script>
""")

    parts.append("</div>\n</body>\n</html>")
    return "\n".join(parts)


def _render_dashboard_card(
    parts: list[str],
    job: dict,
    base_url: str,
    e: Callable[[str], str],
) -> None:
    """Render a single dashboard job card as a clickable link.

    Args:
        parts: List of HTML string parts to append to.
        job: Job dict from list_results_for_dashboard().
        base_url: External base URL for constructing report links.
        e: HTML escape function reference.
    """
    job_id = job.get("job_id", "")
    status = job.get("status", "unknown")
    created_at = job.get("created_at", "")
    jenkins_url = job.get("jenkins_url", "")
    job_name = job.get("job_name", "") or "Direct Analysis"
    build_number = job.get("build_number", "")
    failure_count = job.get("failure_count")

    status_class = (
        status if status in ("completed", "failed", "running", "pending") else "pending"
    )
    report_href = f"{base_url}/results/{job_id}.html"

    # Truncated job_id for display (first 8 chars)
    short_id = job_id[:8] if len(job_id) > 8 else job_id

    # Determine result class for the card border
    result_class = ""
    if status == "completed" and failure_count is not None:
        if failure_count > 0:
            result_class = " result-failures"
        else:
            result_class = " result-passed"

    # Use a <div> container with an <a> link inside (not wrapping the button)
    # to avoid nesting interactive elements, which breaks keyboard/screen-reader accessibility.
    # The outer <div> is the flex container; <a class="card-link"> takes flex:1 for the
    # clickable area, and <div class="card-meta"> sits outside the link with the delete button.
    parts.append(
        f'<div class="dashboard-card{result_class}" data-job-id="{e(job_id)}">'
    )
    parts.append(
        f'  <a class="card-link" href="{e(report_href)}" target="_blank" rel="noopener">'
    )

    # Result icon for completed jobs with known failure count
    if status == "completed" and failure_count is not None:
        if failure_count > 0:
            parts.append(
                '    <span class="card-result-icon has-failures">'
                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"'
                ' stroke="currentColor" stroke-width="3">'
                '<line x1="18" y1="6" x2="6" y2="18"/>'
                '<line x1="6" y1="6" x2="18" y2="18"/>'
                "</svg></span>"
            )
        else:
            parts.append(
                '    <span class="card-result-icon passed">'
                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"'
                ' stroke="currentColor" stroke-width="3">'
                '<polyline points="20 6 9 17 4 12"/>'
                "</svg></span>"
            )

    parts.append('    <div class="card-main">')
    parts.append(f'      <span class="card-job-name">{e(job_name)}</span>')

    if build_number:
        parts.append(
            f'      <span class="card-build-chip">#{e(str(build_number))}</span>'
        )

    parts.append(
        f'      <span class="status-chip {e(status_class)}">{e(status)}</span>'
    )

    if failure_count is not None and failure_count > 0:
        parts.append(
            f'      <span class="failure-count-badge">'
            f"{failure_count} failure{'s' if failure_count != 1 else ''}"
            f"</span>"
        )
    elif status == "completed" and failure_count is not None:
        parts.append('      <span class="passed-badge">passed</span>')

    child_job_count = job.get("child_job_count")
    if child_job_count is not None and child_job_count > 0:
        parts.append(
            f'      <span class="child-jobs-badge">'
            f"{child_job_count} child job{'s' if child_job_count != 1 else ''}"
            f"</span>"
        )

    # Review status chip (only for cards with failures)
    reviewed_count = job.get("reviewed_count", 0)
    comment_count = job.get("comment_count", 0)
    if failure_count is not None and failure_count > 0:
        if reviewed_count >= failure_count:
            parts.append(
                '      <span class="status-chip" '
                'style="background: rgba(63,185,80,0.15); color: var(--accent-green)">'
                "\u2713 Fully Reviewed</span>"
            )
        elif reviewed_count > 0:
            parts.append(
                '      <span class="status-chip" '
                'style="background: rgba(210,153,34,0.15); color: var(--accent-yellow)">'
                f"{reviewed_count}/{failure_count} Reviewed</span>"
            )
        else:
            parts.append(
                '      <span class="status-chip" '
                'style="background: rgba(248,81,73,0.12); color: var(--accent-red)">'
                "Needs Review</span>"
            )

    parts.append("    </div>")
    parts.append("  </a>")

    # card-meta sits outside the <a> link so interactive elements (delete button) are
    # not nested inside a link.  It stays on the right side of the card via flex layout.
    parts.append('  <div class="card-meta">')
    parts.append(
        f'    <span class="card-job-id" title="{e(job_id)}">{e(short_id)}</span>'
    )
    parts.append(f'    <span class="card-timestamp">{e(created_at)}</span>')

    # Delete button inline in card-meta (no absolute positioning)
    parts.append(
        f'    <button class="delete-job-btn" data-job-id="{e(job_id)}"'
        f' onclick="event.stopPropagation(); deleteJob(this, &#39;{e(job_id)}&#39;)"'
        ' style="background:none;border:1px solid transparent;border-radius:4px;color:var(--text-muted);'
        'cursor:pointer;padding:4px 6px;transition:all 0.15s;display:inline-flex;align-items:center;"'
        ' onmouseover="this.style.color=&#39;var(--accent-red)&#39;;this.style.borderColor=&#39;var(--accent-red)&#39;;'
        'this.style.background=&#39;rgba(248,81,73,0.12)&#39;"'
        ' onmouseout="this.style.color=&#39;var(--text-muted)&#39;;this.style.borderColor=&#39;transparent&#39;;'
        'this.style.background=&#39;none&#39;"'
        ' title="Delete this analysis">'
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'
        '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>'
    )

    if jenkins_url:
        parts.append(
            '    <span class="card-jenkins-icon" title="Jenkins build available">'
            '\n      <svg width="14" height="14" viewBox="0 0 24 24" fill="none"'
            ' stroke="currentColor" stroke-width="2">'
            '\n        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
            '\n        <polyline points="15 3 21 3 21 9"/>'
            '\n        <line x1="10" y1="14" x2="21" y2="3"/>'
            "\n      </svg>"
            "\n    </span>"
        )

    parts.append("  </div>")

    # Secondary row: classification badges (left) + comment badge (right-aligned)
    # Placed as direct child of .dashboard-card so it spans full card width.
    comment_badge = ""
    if comment_count > 0:
        comment_badge = (
            f'<span class="card-build-chip"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
            f'<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg> {comment_count}</span>'
        )
    parts.append(
        f'  <div class="card-secondary" style="display:flex;width:100%;align-items:center;gap:6px;padding-top:4px;">'
        f'<span class="classification-job-badges" data-job-name="{e(job_name)}" data-job-id="{e(job_id)}" style="display:none"></span>'
        f'<span style="flex:1"></span>'
        f"{comment_badge}"
        f"</div>"
    )

    parts.append("</div>")


def generate_register_html() -> str:
    """Generate the user registration page HTML.

    Returns:
        A complete HTML document as a string with the registration form.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jenkins Job Insight - Register</title>
<link rel="icon" href="{FAVICON_DATA_URI}">
<style>
{_common_css()}
.register-container {{
    max-width: 400px;
    margin: 100px auto;
    padding: 40px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    text-align: center;
}}
.register-container h2 {{
    font-size: 20px;
    margin-bottom: 8px;
    color: var(--text-primary);
}}
.register-container p {{
    font-size: 14px;
    color: var(--text-secondary);
    margin-bottom: 24px;
}}
.register-input {{
    width: 100%;
    padding: 12px 16px;
    font-size: 16px;
    font-family: var(--font-sans);
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text-primary);
    outline: none;
    margin-bottom: 16px;
    transition: border-color 0.15s;
}}
.register-input::placeholder {{ color: var(--text-muted); }}
.register-input:focus {{ border-color: var(--accent-blue); }}
.register-btn {{
    width: 100%;
    padding: 12px;
    font-size: 14px;
    font-weight: 600;
    font-family: var(--font-sans);
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--accent-blue);
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
}}
.register-btn:hover {{
    background: var(--bg-hover);
    border-color: var(--accent-blue);
}}
</style>
</head>
<body>
<div class="register-container">
    <h2>Welcome to Jenkins Job Insight</h2>
    <p>Enter your name to get started</p>
    <form method="POST" action="/register">
        <input class="register-input" type="text" name="username" placeholder="Your name" required autofocus>
        <button class="register-btn" type="submit">Continue</button>
    </form>
</div>
</body>
</html>"""


def generate_history_html(base_url: str = "") -> str:
    """Generate a self-contained HTML page for failure history exploration.

    The page uses inline JavaScript to fetch paginated failure data from
    the ``/history/failures`` API endpoint with search and classification
    filtering.  A trends section is shown below the main table when
    multi-day data is available.  All dynamic content is escaped via
    ``escapeHtml()`` before DOM insertion.

    Args:
        base_url: External base URL for constructing API request URLs.

    Returns:
        A complete HTML document as a string.
    """
    e = html.escape
    api = e(base_url)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jenkins Job Insight - Failure History</title>
<link rel="icon" href="{FAVICON_DATA_URI}">
<style>
{_common_css()}
.env-chip:hover {{ border-color: var(--accent-blue); color: var(--accent-blue); }}

{_controls_css()}

/* Table */
.table-container {{
    overflow-x: auto;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 24px;
}}
.table-container table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}}
.table-container th {{
    text-align: left;
    padding: 10px 14px;
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
}}
.table-container td {{
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    color: var(--text-primary);
    vertical-align: top;
}}
.table-container tr:last-child td {{ border-bottom: none; }}
.table-container tr:hover td {{ background: var(--bg-hover); }}

/* Classification tags */
.classification-tag {{
    display: inline-block;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    white-space: nowrap;
}}
.classification-tag.product-bug {{
    background: var(--accent-orange-bg);
    color: var(--accent-orange);
}}
.classification-tag.code-issue {{
    background: var(--accent-blue-bg);
    color: var(--accent-blue);
}}
.classification-tag.known-bug {{
    background: rgba(188, 140, 255, 0.12);
    color: var(--accent-purple);
}}
.classification-tag.regression {{
    background: var(--accent-red-bg);
    color: var(--accent-red);
}}
.classification-tag.flaky {{
    background: rgba(210, 153, 34, 0.15);
    color: var(--accent-yellow);
}}
.classification-tag.infrastructure {{
    background: var(--accent-orange-bg);
    color: var(--accent-orange);
}}
.classification-tag.intermittent {{
    background: rgba(210, 153, 34, 0.15);
    color: var(--accent-yellow);
}}
.classification-tag.unknown {{
    background: var(--bg-tertiary);
    color: var(--text-muted);
}}

/* Test name column */
.test-name {{
    max-width: 400px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono);
    font-size: 12px;
}}

.empty-msg {{
    text-align: center;
    padding: 32px 20px;
    color: var(--text-muted);
    font-size: 14px;
}}
.mono {{ font-family: var(--font-mono); font-size: 12px; }}

/* Responsive */
@media (max-width: 768px) {{
    .controls-bar {{ flex-direction: column; }}
    .search-input {{ min-width: 100%; }}
    .test-name {{ max-width: 200px; }}
}}
</style>
</head>
<body>
<div class="container">

<div class="sticky-header">
  <div class="header-content">
    <h1>Failure History</h1>
    <div class="env-chips">
      <a class="env-chip" href="{api}/dashboard">Dashboard</a>
    </div>
  </div>
</div>

<!-- Controls -->
<div class="controls-bar">
    <input class="search-input" placeholder="Search by test name, job, or error..." id="search-input">
    <select class="per-page-select" id="classification-filter">
        <option value="">All Classifications</option>
        <option value="PRODUCT BUG">PRODUCT BUG</option>
        <option value="CODE ISSUE">CODE ISSUE</option>
        <option value="KNOWN_BUG">KNOWN_BUG</option>
        <option value="REGRESSION">REGRESSION</option>
        <option value="FLAKY">FLAKY</option>
        <option value="INFRASTRUCTURE">INFRASTRUCTURE</option>
        <option value="INTERMITTENT">INTERMITTENT</option>
    </select>
    <select class="per-page-select" id="per-page-select">
        <option value="25">25 per page</option>
        <option value="50" selected>50 per page</option>
        <option value="100">100 per page</option>
    </select>
</div>

<!-- Failures table -->
<div class="table-container">
<table>
<thead>
<tr><th>Test Name</th><th>Job</th><th>Build</th><th>Classification</th><th>Child Job</th><th>Date</th></tr>
</thead>
<tbody id="failures-tbody">
<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:20px;">Loading failures...</td></tr>
</tbody>
</table>
</div>

<div class="pagination-controls">
    <button class="pagination-btn" id="prev-btn" disabled>Previous</button>
    <span class="page-info" id="page-info"></span>
    <button class="pagination-btn" id="next-btn">Next</button>
</div>

<!-- Trends (shown only when multi-day data exists) -->
<div id="trends-wrapper" style="display:none;">
  <h2 class="section-title">Failure Trends (Last 30 Days)</h2>
  <div id="trends-section">
    <div class="empty-msg">Loading trends...</div>
  </div>
</div>

<div class="report-footer">
  <span>Jenkins Job Insight - Failure History</span>
</div>

</div>

<script>
(function() {{
  var BASE = window.location.pathname.replace(/\\/history$/, '');

  var currentPage = 1;
  var perPage = 50;
  var totalItems = 0;
  var currentSearch = '';
  var currentClassification = '';
  var searchTimer = null;

  function escapeHtml(s) {{
    if (s == null) return '';
    var el = document.createElement('div');
    el.textContent = String(s);
    return el.innerHTML;
  }}

  function fetchJson(url) {{
    return fetch(url).then(function(r) {{
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }});
  }}

  function classificationClass(c) {{
    switch(c) {{
        case 'PRODUCT BUG': return 'product-bug';
        case 'CODE ISSUE': return 'code-issue';
        case 'KNOWN_BUG': return 'known-bug';
        case 'REGRESSION': return 'regression';
        case 'FLAKY': return 'flaky';
        case 'INFRASTRUCTURE': return 'infrastructure';
        case 'INTERMITTENT': return 'intermittent';
        default: return 'unknown';
    }}
  }}

  function loadFailures() {{
    var offset = (currentPage - 1) * perPage;
    var url = BASE + '/history/failures?limit=' + perPage + '&offset=' + offset;
    if (currentSearch) url += '&search=' + encodeURIComponent(currentSearch);
    if (currentClassification) url += '&classification=' + encodeURIComponent(currentClassification);

    fetchJson(url).then(function(data) {{
      totalItems = Number(data.total || 0);
      renderTable(Array.isArray(data.failures) ? data.failures : []);
      renderPagination();
    }}).catch(function(err) {{
      document.getElementById('failures-tbody').innerHTML =
        '<tr><td colspan="6" style="text-align:center;color:var(--accent-red);padding:20px;">Failed to load: ' + escapeHtml(err.message) + '</td></tr>';
    }});
  }}

  function renderTable(failures) {{
    var tbody = document.getElementById('failures-tbody');
    if (failures.length === 0) {{
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:20px;">No failures found</td></tr>';
      return;
    }}
    tbody.innerHTML = failures.map(function(f) {{
      return '<tr>' +
        '<td class="test-name"><a href="' + BASE + '/history/test/' + encodeURIComponent(f.test_name) + '" style="color:var(--accent-blue);text-decoration:none;" title="' + escapeHtml(f.test_name) + '">' + escapeHtml(f.test_name) + '</a></td>' +
        '<td>' + escapeHtml(f.job_name) + '</td>' +
        '<td>' + f.build_number + '</td>' +
        '<td><span class="classification-tag ' + classificationClass(f.classification) + '">' + escapeHtml(f.classification) + '</span></td>' +
        '<td>' + escapeHtml(f.child_job_name || '-') + '</td>' +
        '<td style="font-family:var(--font-mono);font-size:12px;color:var(--text-muted);white-space:nowrap;">' + escapeHtml(f.analyzed_at || '') + '</td>' +
        '</tr>';
    }}).join('');
  }}

  function renderPagination() {{
    var totalPages = Math.max(1, Math.ceil(totalItems / perPage));
    document.getElementById('page-info').textContent = 'Page ' + currentPage + ' of ' + totalPages + ' (' + totalItems + ' failures)';
    document.getElementById('prev-btn').disabled = currentPage <= 1;
    document.getElementById('next-btn').disabled = currentPage >= totalPages;
  }}

  /* ---- Event listeners ---- */
  var searchInput = document.getElementById('search-input');
  searchInput.addEventListener('keyup', function() {{
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function() {{
      currentSearch = searchInput.value.trim();
      currentPage = 1;
      loadFailures();
    }}, 300);
  }});

  document.getElementById('classification-filter').addEventListener('change', function() {{
    currentClassification = this.value;
    currentPage = 1;
    loadFailures();
  }});

  document.getElementById('per-page-select').addEventListener('change', function() {{
    perPage = parseInt(this.value, 10);
    currentPage = 1;
    loadFailures();
  }});

  document.getElementById('prev-btn').addEventListener('click', function() {{
    if (currentPage > 1) {{
      currentPage--;
      loadFailures();
    }}
  }});

  document.getElementById('next-btn').addEventListener('click', function() {{
    var totalPages = Math.max(1, Math.ceil(totalItems / perPage));
    if (currentPage < totalPages) {{
      currentPage++;
      loadFailures();
    }}
  }});

  /* ---- Trends (only show when multi-day data exists) ---- */
  fetchJson(BASE + '/history/trends?period=daily&days=30')
    .then(function(data) {{
      var items = data.data || data.periods || [];
      if (items.length <= 1) {{
        // Single day or no data: keep trends hidden
        return;
      }}
      document.getElementById('trends-wrapper').style.display = 'block';
      var section = document.getElementById('trends-section');
      var h = '<div class="table-container"><table>';
      h += '<tr><th>Period</th><th>Total Failures</th><th>Unique Tests</th><th>Builds Analyzed</th></tr>';
      for (var i = 0; i < items.length; i++) {{
        var t = items[i];
        h += '<tr>';
        h += '<td class="mono">' + escapeHtml(t.date || t.period || '') + '</td>';
        h += '<td class="mono">' + (t.failures != null ? t.failures : (t.total_failures != null ? t.total_failures : 0)) + '</td>';
        h += '<td class="mono">' + (t.unique_tests != null ? t.unique_tests : '-') + '</td>';
        h += '<td class="mono">' + (t.total_tests != null ? t.total_tests : '-') + '</td>';
        h += '</tr>';
      }}
      h += '</table></div>';
      section.innerHTML = h;
    }})
    .catch(function(err) {{
      // Silently ignore trend load failures; the section stays hidden
    }});

  /* ---- Initial load ---- */
  loadFailures();
}})();
</script>
<script>
{_username_helper_js()}
{_user_badge_js()}
</script>
</body>
</html>"""
