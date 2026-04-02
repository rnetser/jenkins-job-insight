# Jira-Assisted Bug Triage

Jira-assisted bug triage helps you answer one practical question quickly: should this `PRODUCT BUG` become a new Jira ticket, or is Jira already tracking it? When Jira is enabled, `jenkins-job-insight` adds Jira search keywords to each `PRODUCT BUG` analysis, searches Jira for likely duplicates, and attaches the matching issues directly to the bug report before anyone files something new.

## What Gets Added
A `PRODUCT BUG` analysis carries a structured bug report, and that report includes Jira-friendly search terms:

```39:56:tests/test_bug_creation.py
def product_bug_failure() -> FailureAnalysis:
    """A PRODUCT BUG failure with a bug report."""
    return FailureAnalysis(
        test_name="tests.network.test_dns.TestDNS.test_resolve",
        error="TimeoutError: DNS resolution timed out after 30s",
        error_signature="xyz789ghi012",
        analysis=AnalysisDetail(
            classification="PRODUCT BUG",
            affected_tests=["tests.network.test_dns.TestDNS.test_resolve"],
            details="DNS resolution is failing intermittently on the internal resolver.",
            product_bug_report=ProductBugReport(
                title="DNS resolution timeout on internal resolver",
                severity="high",
                component="networking",
                description="Internal DNS resolver fails to resolve hostnames within 30s",
                evidence="TimeoutError at dns_client.py:88 - socket.timeout after 30000ms",
                jira_search_keywords=["DNS", "timeout", "resolver"],
            ),
        ),
    )
```

After post-processing, the same `product_bug_report` can also include `jira_matches`. Each match carries the Jira key, summary, status, priority, URL, and a relevance `score`. Higher scores are better matches, and the match list is sorted highest-first.

> **Note:** Jira-assisted triage only applies to failures classified as `PRODUCT BUG`. `CODE ISSUE` failures stay on the GitHub issue flow.

## Turn It On
Jira support is optional, but Jira matching now turns on only when the service has a complete Jira setup: instance URL, valid credentials, and a `JIRA_PROJECT_KEY`.

```184:206:src/jenkins_job_insight/config.py
    @property
    def jira_enabled(self) -> bool:
        """Check if Jira integration is enabled and configured with valid credentials."""
        if self.enable_jira is False:
            return False
        if not self.jira_url:
            if self.enable_jira is True:
                logger.warning("enable_jira is True but JIRA_URL is not configured")
            return False
        _, token_value = _resolve_jira_auth(self)
        if not token_value:
            if self.enable_jira is True:
                logger.warning(
                    "enable_jira is True but no Jira credentials are configured"
                )
            return False
        if not self.jira_project_key:
            if self.enable_jira is True:
                logger.warning(
                    "enable_jira is True but JIRA_PROJECT_KEY is not configured"
                )
            return False
        return True
```

In practice:
- Use `JIRA_URL` to point at your Jira instance.
- Use `JIRA_PROJECT_KEY` whenever you want Jira matching. It is required, enabled searches are always scoped to that project, and the same key is used later if you create a Jira bug from the report page.
- For Jira Cloud, set `JIRA_EMAIL` plus `JIRA_API_TOKEN`.
- For Jira Server/Data Center, set `JIRA_PAT`.
- If you are on Server/Data Center and do not have a PAT, `JIRA_API_TOKEN` can still work as a fallback only when `JIRA_EMAIL` is not set.
- Use `JIRA_MAX_RESULTS` to change how many Jira candidates are returned before relevance filtering. The default is `5`.
- Use `JIRA_SSL_VERIFY` if you need to control TLS verification. The default is `true`.

If you prefer saved CLI defaults, the example config file includes the same Jira settings:

```15:40:config.example.toml
[defaults]
# ... shared defaults above ...

# Jira
jira_url = "https://your-jira.atlassian.net"
jira_email = "you@example.com"
jira_api_token = "your-jira-token"
jira_pat = ""
jira_project_key = "PROJ"
jira_ssl_verify = true
jira_max_results = 50
enable_jira = true
```

> **Note:** Cloud mode is selected only when `JIRA_EMAIL` and `JIRA_API_TOKEN` are both set. `JIRA_EMAIL` plus `JIRA_PAT` stays on the Server/Data Center auth path, and Server/Data Center falls back to `JIRA_API_TOKEN` only when `JIRA_EMAIL` is not set.

If you use the CLI, `jji analyze` still exposes `--jira` and `--no-jira`, and it can also forward `enable_jira` from `~/.config/jji/config.toml`:

```560:627:src/jenkins_job_insight/cli/main.py
# Start from config defaults (lowest priority), then overlay CLI flags.
extras: dict = {}
cfg = _state.get("server_config")
if cfg:
    # ...
    if cfg.enable_jira is not None:
        extras["enable_jira"] = cfg.enable_jira

# CLI flags override config (highest priority).
if provider:
    extras["ai_provider"] = provider
if model:
    extras["ai_model"] = model
if jira is not None:
    extras["enable_jira"] = jira
```

Use `jji analyze --job-name my-job --build-number 27 --jira` to force Jira on for one run, or `--no-jira` to skip it even when the selected CLI profile enables it. If you leave both flags off, `jji` uses any `enable_jira` value from its config file; otherwise the server falls back to its own default or auto-detection. The CLI also supports Jira-specific per-run overrides such as `--jira-url`, `--jira-email`, `--jira-api-token`, `--jira-pat`, `--jira-project-key`, `--jira-ssl-verify` or `--no-jira-ssl-verify`, and `--jira-max-results`.

The same toggle exists in API requests as `enable_jira`, and it still lives on the shared analysis request model, so it works for both `/analyze` and `/analyze-failures`. Send `true` to force Jira on for one request, `false` to skip it, or omit it to follow the merged server setting, which may be an explicit `ENABLE_JIRA` choice or auto-detection from complete Jira config.

> **Note:** Per-request Jira overrides such as `jira_url`, `jira_email`, `jira_api_token`, `jira_pat`, `jira_project_key`, `jira_ssl_verify`, and `jira_max_results` apply to analysis requests. Jira bug preview and creation use the server's configured Jira connection, not caller-supplied per-analysis credentials.

> **Warning:** `enable_jira` still does not bypass missing configuration. If `JIRA_URL`, Jira credentials, or `JIRA_PROJECT_KEY` are missing, Jira matching stays off.

## How Matching Works
The matching flow has two phases.

First, the AI analysis is told to generate short, specific Jira search keywords as part of every `PRODUCT BUG` report:

```223:245:src/jenkins_job_insight/analyzer.py
If PRODUCT BUG:
{
  "classification": "PRODUCT BUG",
  "affected_tests": ["test_name_1", "test_name_2"],
  "details": "Your detailed analysis of what caused this failure",
  "artifacts_evidence": "VERBATIM lines from files under build-artifacts/ that prove the product defect. Format each line as [file-path]: content. Example: [build-artifacts/logs/error.log]: 2026-03-16 ERROR NullPointerException in AuthService. Include the specific log lines showing the product failure.",
  "product_bug_report": {
    "title": "concise bug title",
    "severity": "critical/high/medium/low",
    "component": "affected component",
    "description": "what product behavior is broken",
    "evidence": "relevant log snippets",
    "jira_search_keywords": ["specific error symptom", "component + behavior", "error type"]
  }
}

jira_search_keywords rules:
- Generate 3-5 SHORT specific keywords for finding matching bugs in Jira
- Focus on the specific error symptom and broken behavior, NOT test infrastructure
- Combine component name with the specific failure (e.g. "VM start failure migration", "API timeout authentication")
- AVOID generic/broad terms alone like "timeout", "failure", "error"
- Each keyword should be specific enough to narrow Jira search results to relevant bugs
- Think: "what would someone title a Jira bug for this exact issue?"
```

Then the server turns those keywords into a Jira search that only looks for `Bug` issues and scopes the search to your configured Jira project:

```120:163:src/jenkins_job_insight/jira.py
# Build JQL: summary ~ "kw1" OR summary ~ "kw2" ...
text_clauses = " OR ".join(
    f'summary ~ "{_sanitize_jql_keyword(kw)}"' for kw in keywords
)
jql = f"issuetype = Bug AND ({text_clauses})"
if self._project_key:
    jql = f'project = "{self._project_key}" AND {jql}'

jql += " ORDER BY updated DESC"

params = {
    "jql": jql,
    "maxResults": self._max_results,
    "fields": "summary,description,status,priority",
}

# ... Jira response parsing ...

desc = fields.get("description") or ""
# Cloud API v3 returns description as ADF (Atlassian Document Format)
if isinstance(desc, dict):
    desc = _extract_text_from_adf(desc)
```

A few details matter here:
- Because Jira matching now requires `JIRA_PROJECT_KEY`, enabled searches are scoped to that project.
- The initial Jira query searches issue summaries, not full-text descriptions.
- The returned candidates still include description, status, priority, and browse URL for review.
- JQL-reserved characters are stripped from keywords before they are sent to Jira.
- Jira Cloud descriptions returned as Atlassian Document Format are flattened to plain text before later relevance filtering.
- The search is ordered by `updated DESC`, so fresher bug reports are preferred.

If several failures point to the same root cause, `jenkins-job-insight` avoids duplicate Jira work by deduplicating on keyword sets and reusing the same search result across all of those reports:

```387:456:src/jenkins_job_insight/jira.py
# Deduplicate by keyword set — same keywords = one Jira search
keyword_to_reports: dict[tuple[str, ...], list[ProductBugReport]] = {}
for report in reports:
    if not report.jira_search_keywords:
        continue
    key = tuple(sorted(report.jira_search_keywords))
    keyword_to_reports.setdefault(key, []).append(report)

# Search Jira for each unique keyword set in parallel
async def _search_safe(keywords: list[str]) -> list[dict]:
    try:
        return await client.search(keywords)
    except Exception:
        logger.exception("Jira search failed for keywords: %s", keywords)
        return []

tasks = [_search_safe(list(kw_tuple)) for kw_tuple in keyword_to_reports]
search_results = await asyncio.gather(*tasks)

# AI relevance filtering for each keyword set
for kw_tuple, candidates in zip(keyword_to_reports, search_results):
    if not candidates:
        continue

    representative = keyword_to_reports[kw_tuple][0]

    if ai_provider and ai_model:
        matches = await _filter_matches_with_ai(
            bug_title=representative.title,
            bug_description=representative.description,
            candidates=candidates,
            ai_provider=ai_provider,
            ai_model=ai_model,
            ai_cli_timeout=settings.ai_cli_timeout,
        )
    else:
        matches = [
            JiraMatch(
                key=c["key"],
                summary=c["summary"],
                status=c["status"],
                priority=c["priority"],
                url=c["url"],
                score=0.0,
            )
            for c in candidates
        ]

    # Attach matches to all reports sharing the same keyword set
    for report in keyword_to_reports[kw_tuple]:
        report.jira_matches = matches
```

This gives you five useful outcomes:
- Repeated failures with the same root cause do not trigger repeated Jira searches.
- Related failures get the same attached Jira matches, which makes team triage more consistent.
- When AI relevance filtering succeeds, only candidates marked relevant are kept and then sorted highest-first by `score`.
- If no AI provider and model are available for that filtering step, the raw Jira candidates are still attached as `jira_matches` with `score` set to `0.0`.
- Jira lookup and AI filtering are both best-effort. If Jira is unreachable or the filtering step fails, the overall analysis still completes, and that keyword set may simply end up with no attached matches.

> **Note:** If a `PRODUCT BUG` report has no `jira_search_keywords`, Jira lookup is skipped for that failure.

## Where to Look During Triage
You do not need to inspect logs by hand to find the Jira data. `jenkins-job-insight` surfaces it in the places reviewers already use:
- In the result JSON, under `analysis.product_bug_report.jira_matches`.
- In the React report page at `/results/{jobId}`, where each `PRODUCT BUG` card renders a `Matching Jira Issues` list.
- In the Jira bug creation dialog on that same page, where preview-time duplicate detection is shown as `similar_issues` before you submit a new bug.
- In enriched JUnit XML, where `src/jenkins_job_insight/xml_enrichment.py` writes `ai_jira_match_*` testcase properties. The same formatter also adds a readable Jira-match summary to `system-out`.

```323:337:frontend/src/pages/report/FailureCard.tsx
                  {analysis.product_bug_report?.jira_matches?.length > 0 && (
                    <div className="mt-2">
                      <p className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-1">Matching Jira Issues</p>
                      <ul className="space-y-1">
                        {analysis.product_bug_report.jira_matches.map((m) => (
                          <li key={m.key} className="flex items-center gap-2 text-xs">
                            <a href={m.url} target="_blank" rel="noopener noreferrer" className="text-text-link hover:underline">
                              {m.key}: {m.summary}
                            </a>
                            {m.status && <Badge variant="outline" className="text-[10px]">{m.status}</Badge>}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
```

```143:163:frontend/src/pages/report/BugCreationDialog.tsx
            {similar.length > 0 && (
              <div className="rounded-md border border-signal-orange/30 bg-glow-orange p-3">
                <div className="flex items-center gap-2 text-sm font-medium text-signal-orange">
                  <AlertTriangle className="h-4 w-4" />
                  {similar.length} similar {similar.length === 1 ? 'issue' : 'issues'} found
                </div>
                <ul className="mt-2 space-y-1">
                  {similar.map((s) => (
                    <li key={s.url || s.key} className="text-xs">
                      <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-text-link hover:underline">
                        {s.key || `#${s.number}`}: {s.title}
                      </a>
                      {s.status && (
                        <Badge variant="outline" className="ml-2 text-[10px]">
                          {s.status}
                        </Badge>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
```

That means downstream tools can reuse the Jira triage result from JSON or enriched XML without scraping the web UI.

## Before Filing a New Jira Bug
The intended flow is simple:
1. Review the `PRODUCT BUG` analysis.
2. Check the attached `jira_matches`.
3. Reuse an existing Jira issue if one already describes the same defect.
4. Only preview and create a new Jira bug when those attached matches do not fit.

The preview step adds another safety net. After `jenkins-job-insight` generates the Jira summary and description, it performs one more best-effort duplicate search using the generated title and returns up to five hits as `similar_issues`:

```1873:1891:src/jenkins_job_insight/main.py
# Duplicate detection (best-effort: failures must not break preview)
similar: list[dict] = []
if settings.jira_enabled:
    try:
        similar = await search_jira_duplicates(
            title=content["title"],
            settings=settings,
        )
    except Exception:
        logger.warning(
            "Jira duplicate search failed for job_id=%s",
            job_id,
            exc_info=True,
        )

return {
    "title": content["title"],
    "body": content["body"],
    "similar_issues": similar,
}
```

That second check is separate from `jira_matches`:
- `jira_matches` are attached during analysis, based on the `PRODUCT BUG` report and its search keywords.
- `similar_issues` are generated later, during preview, based on the final Jira title that would be filed.

When you do create a Jira bug, `src/jenkins_job_insight/main.py` only allows that action for failures currently classified as `PRODUCT BUG`, and it writes the created Jira URL back to the failure as a comment so later triagers can see that the bug was already filed.

> **Warning:** `jenkins-job-insight` refuses to create a Jira bug for a failure currently classified as `CODE ISSUE`. That is deliberate, and it keeps product bugs and code bugs on their intended trackers.

## Practical Advice
- Treat `jira_search_keywords` as a strong starting point, not an automatic verdict.
- Prefer specific component-and-symptom keywords over broad words like “error” or “timeout”.
- Check both attached `jira_matches` and preview-time `similar_issues` before opening a new ticket.
- If you already export enriched XML downstream, the `ai_jira_match_*` properties are the cleanest machine-readable source of truth.

> **Tip:** If many tests fail because of the same product defect, `jenkins-job-insight` reuses one Jira search across all failures that share the same keyword set. That keeps triage fast and keeps the attached matches consistent across related failures.


## Related Pages

- [Jira Integration](jira-integration.html)
- [Analyze Raw Failures and JUnit XML](direct-failure-analysis.html)
- [Analyze Jenkins Jobs](analyze-jenkins-jobs.html)
- [HTML Reports and Dashboard](html-reports-and-dashboard.html)
- [Results, Reports, and Dashboard Endpoints](api-results-and-dashboard.html)