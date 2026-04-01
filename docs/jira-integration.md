# Jira Integration
Jira integration helps `jenkins-job-insight` answer one practical question: is this failure already tracked? After a failure is classified as `PRODUCT BUG`, the service can search Jira for likely existing bugs, attach the most relevant matches to the analysis result, and show them in the report page as "Possible Jira Matches".

If Jira is configured, the report can also preview and create Jira bugs for `PRODUCT BUG` entries using the same server-side Jira connection.

## Configure Jira
To enable Jira integration, configure `JIRA_URL`, `JIRA_PROJECT_KEY`, and the credentials for your Jira deployment type.

**Jira Cloud**

```bash
JIRA_URL=https://your-org.atlassian.net
JIRA_EMAIL=your-email@example.com
JIRA_API_TOKEN=your-api-token
JIRA_PROJECT_KEY=MYPROJ
```

**Jira Server/DC**

```bash
JIRA_URL=https://jira.your-company.com
JIRA_PAT=your-personal-access-token
JIRA_PROJECT_KEY=MYPROJ
```

The same variables are already exposed as commented environment entries in `docker-compose.yaml` for container deployments.

`JIRA_SSL_VERIFY` defaults to `true`. `JIRA_MAX_RESULTS` defaults to `5`.

> **Note:** `JIRA_PROJECT_KEY` is now required to enable Jira integration. It also remains the project key used when creating Jira bugs from the report page.

## Authentication Modes
`jenkins-job-insight` still chooses Jira Cloud vs Jira Server/Data Center automatically, but Cloud mode now requires `JIRA_EMAIL` together with `JIRA_API_TOKEN`.

| Deployment | Required settings | Auth used | Search API |
| --- | --- | --- | --- |
| Jira Cloud | `JIRA_URL` + `JIRA_EMAIL` + `JIRA_API_TOKEN` + `JIRA_PROJECT_KEY` | Basic auth with `email:api_token` | `/rest/api/3/search/jql` |
| Jira Server/DC | `JIRA_URL` + `JIRA_PAT` + `JIRA_PROJECT_KEY` | Bearer token | `/rest/api/2/search` |

The auth resolver in `src/jenkins_job_insight/config.py` makes that distinction explicit:

```python
has_api_token = bool(
    settings.jira_api_token and settings.jira_api_token.get_secret_value()
)
has_pat = bool(settings.jira_pat and settings.jira_pat.get_secret_value())
has_email = bool(settings.jira_email)

is_cloud = has_email and has_api_token

if is_cloud:
    # Cloud: jira_api_token only (has_api_token already confirms truthiness)
    return True, settings.jira_api_token.get_secret_value()  # type: ignore[union-attr]

# Server/DC: prefer PAT, fall back to API token
if has_pat and settings.jira_pat:
    return False, settings.jira_pat.get_secret_value()
if has_api_token and settings.jira_api_token:
    return False, settings.jira_api_token.get_secret_value()
```

The selected mode then drives the search endpoint: Cloud uses `/rest/api/3/search/jql`, while Server/DC uses `/rest/api/2/search`.

> **Warning:** For Jira Cloud, `JIRA_EMAIL` without `JIRA_API_TOKEN` does not activate Cloud mode. The client falls back to Server/DC-style bearer handling instead.

> **Note:** `JIRA_PAT` + `JIRA_EMAIL` is intentionally treated as Server/DC mode. If you are integrating with Jira Cloud, use `JIRA_API_TOKEN`. For older Server/DC setups, `JIRA_API_TOKEN` still works as a bearer-token fallback when `JIRA_PAT` is not set.

## Automatic Enablement
Jira matching is resolved in a clear priority order:

```549:566:src/jenkins_job_insight/main.py
def _resolve_enable_jira(body: BaseAnalysisRequest, settings: Settings) -> bool:
    """Resolve enable_jira flag from request, env var, or auto-detection.

    Priority order:
    1. Request body field (highest)
    2. ENABLE_JIRA env var (via settings)
    3. Auto-detect from Jira credentials (lowest)
    """
    if body.enable_jira is not None:
        return body.enable_jira
    return settings.jira_enabled
```

In practice, that means:

- Request field `enable_jira` wins.
- If `enable_jira` is omitted, the service falls back to merged server settings.
- Auto-detection only succeeds when `JIRA_URL`, valid Jira credentials, and `JIRA_PROJECT_KEY` are all present.
- `ENABLE_JIRA=false` disables Jira even if the rest of the settings are valid.
- `ENABLE_JIRA=true` does not bypass missing configuration. Without a URL, credentials, or project key, Jira still stays off.

The CLI exposes the same behavior through `jji analyze --jira` and `jji analyze --no-jira`. It also supports Jira-specific per-run overrides such as `--jira-url`, `--jira-email`, `--jira-api-token`, `--jira-pat`, `--jira-project-key`, `--jira-ssl-verify` or `--no-jira-ssl-verify`, and `--jira-max-results`. If you keep CLI defaults in `~/.config/jji/config.toml`, the bundled `config.example.toml` shows the same Jira keys.

API callers can also override Jira settings per analysis with `enable_jira`, `jira_url`, `jira_email`, `jira_api_token`, `jira_pat`, `jira_project_key`, `jira_ssl_verify`, and `jira_max_results`. CLI flags, config file values, and request fields are merged over server defaults before analysis begins.

> **Note:** Per-request Jira overrides apply to analysis requests. Jira bug preview and creation use the server's configured Jira connection, not caller-supplied per-analysis credentials.

## Project Scoping
`JIRA_PROJECT_KEY` is now required for Jira integration. The older "search across every visible project" behavior no longer applies to enabled Jira matching, because the integration stays off until a project key is configured.

When Jira search does run, the query is still prefixed with the project clause before the normal Bug + summary search:

```python
jql = f"issuetype = Bug AND ({text_clauses})"
if self._project_key:
    jql = f'project = "{self._project_key}" AND {jql}'

jql += " ORDER BY updated DESC"
```

That makes `JIRA_PROJECT_KEY` do three jobs:

- It is part of the enablement check.
- It keeps duplicate search focused on a single Jira project.
- It supplies the project key used later when creating Jira bugs from the report page.

> **Tip:** On busy Jira deployments, choosing the right `JIRA_PROJECT_KEY` is more useful than simply increasing `JIRA_MAX_RESULTS`.

## Search Behavior
Jira search only runs for failures that are already classified as `PRODUCT BUG`. `CODE ISSUE` failures are ignored, and `PRODUCT BUG` reports without `jira_search_keywords` are skipped.

The AI analysis prompt explicitly tells the model to produce short, specific Jira search terms:

```168:190:src/jenkins_job_insight/analyzer.py
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

The matching flow is:

1. The analysis step produces `jira_search_keywords` for each `PRODUCT BUG`.
2. Failures with the same keyword set are deduplicated, so Jira is searched once for that shared set.
3. Unique keyword sets are searched in parallel.
4. Each search looks for Jira issues where `issuetype = Bug` and the issue `summary` matches one or more keywords.
5. Jira returns candidate issues with `summary`, `description`, `status`, `priority`, and a browsable issue URL.

The JQL is built like this:

```125:139:src/jenkins_job_insight/jira.py
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
```

A few details are worth knowing:

- Search is `summary`-based, not full-description JQL search.
- Jira descriptions are still fetched and used later as context for AI filtering.
- JQL-reserved characters are stripped from keywords before they are sent to Jira.
- Raw Jira candidates are requested in `updated DESC` order.
- Jira Cloud descriptions returned in Atlassian Document Format are flattened to plain text before filtering.

> **Tip:** Good matches depend heavily on good `jira_search_keywords`. Specific symptom phrases such as `"API timeout authentication"` are far more useful than broad words like `"timeout"`.

## AI-Based Relevance Filtering
The first Jira search is intentionally broad. After candidates come back, `jenkins-job-insight` can send the new bug and the Jira candidates to the configured AI provider for a second pass.

The filtering prompt in `src/jenkins_job_insight/jira.py` asks the model to judge whether a candidate is really the same bug or a closely related one:

```250:272:src/jenkins_job_insight/jira.py
prompt = f"""You are evaluating whether existing Jira bug tickets match a newly discovered bug.

NEW BUG:
Title: {bug_title}
Description: {bug_description}

JIRA CANDIDATES:
{chr(10).join(candidate_lines)}

For each candidate, determine if it describes the SAME bug or a closely related issue
(including regressions of previously fixed bugs).

A match means the Jira ticket describes essentially the same broken behavior,
not just that it mentions similar components or technologies.

Respond with ONLY a JSON array. For each candidate include:
- "key": the Jira issue key
- "relevant": true or false
- "score": relevance score 0.0 to 1.0 (1.0 = exact same bug, 0.5+ = likely related)

Example: [{"key": "PROJ-123", "relevant": true, "score": 0.9}, {"key": "PROJ-456", "relevant": false, "score": 0.1}]

Respond with ONLY the JSON array, no other text."""
```

What happens next:

- Only candidates marked `relevant: true` are kept.
- Remaining matches are sorted by `score` from highest to lowest.
- There is no hard score cutoff in code. The `relevant` flag controls inclusion, and the `score` is used for ranking.
- Stored matches include `key`, `summary`, `status`, `priority`, `url`, and `score`.

> **Warning:** If no `ai_provider` and `ai_model` are available for this filtering step, the service does not semantically filter the Jira results. It returns all search candidates as `jira_matches` with a score of `0.0`.

> **Note:** Jira lookup and AI filtering are best-effort. If Jira is unreachable, keywords are missing, or AI output cannot be parsed, the overall analysis still completes. The result simply comes back with no matches or with less-filtered matches.

## What You Will See
When Jira matching succeeds, the API result stores matches in `product_bug_report.jira_matches`. In the report page, those matches are rendered as "Possible Jira Matches" under the `PRODUCT BUG` entry.

That gives end users a simple workflow:

1. Run an analysis with Jira configured or explicitly enabled.
2. Open each `PRODUCT BUG` section and review any suggested Jira matches.
3. Reuse an existing Jira bug when the behavior is already tracked.
4. If nothing matches, use the report's Jira bug flow to preview and create a new ticket.

> **Note:** Jira bug preview does its own best-effort duplicate check using words from the generated Jira title and returns up to five `similar_issues`. This reuses the same Jira connection settings but does not block preview if search fails.
