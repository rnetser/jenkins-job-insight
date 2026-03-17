# Failure History API

You have access to the failure history API. Use curl to query these endpoints before classifying failures.

## IMPORTANT: Exclude Current Job

When querying history, ALWAYS add `?exclude_job_id={job_id}` to exclude the current job being analyzed. This prevents seeing stale data from previous analyses of the same job.

Example:
```bash
curl -s "{server_url}/history/test/{test_name}?exclude_job_id={job_id}" | python3 -m json.tool
```

## Available Endpoints

### Check test history
```bash
curl -s "{server_url}/history/test/{test_name}?exclude_job_id={job_id}" | python3 -m json.tool
```
Returns: pass/fail history, failure rate, classifications breakdown, recent runs, related comments.

### Find similar errors by signature
```bash
curl -s "{server_url}/history/search?signature={error_signature}&exclude_job_id={job_id}" | python3 -m json.tool
```
Returns: all tests that failed with the same error pattern, occurrence counts, last classification.

### Job statistics
```bash
curl -s "{server_url}/history/stats/{job_name}?exclude_job_id={job_id}" | python3 -m json.tool
```
Returns: overall job health, most common failures, failure trend direction.

### Failure trends over time
```bash
curl -s "{server_url}/history/trends?exclude_job_id={job_id}" | python3 -m json.tool
```
Returns: daily or weekly failure rate data points.

## When to Use

Use the raw history data to make your own judgments about flakiness, regressions, and failure patterns.

1. **Before classifying any failure**, check its history with `/history/test/{test_name}`:
   - Examine the failure_rate and consecutive_failures to judge whether the test is flaky, a regression, or a persistent issue
   - If there are existing comments/bugs, reference them instead of suggesting new ones
   - If it has high consecutive_failures, note it as an ongoing issue

2. **For PRODUCT BUG classifications**, search by error signature to see if this is a known issue across multiple tests.

3. **For CODE ISSUE classifications**, check the test history and correlate with the git log to determine if this is a recent regression.

4. **Support your classification with data** — reference specific history (e.g., "This test has failed in 8 of the last 10 runs and was previously reported as OCPBUGS-12345").

5. **Assess flakiness yourself** — if a test has an intermittent pass/fail pattern (e.g., failure rate between 20-80%), note this in your analysis as it may indicate an infrastructure or timing issue rather than a code or product bug.
