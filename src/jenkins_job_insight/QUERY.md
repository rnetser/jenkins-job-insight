# Failure History API

You have access to the failure history API. Use curl to query these endpoints before classifying failures.

## Available Endpoints

### Check test history
```bash
curl -s {server_url}/history/test/{test_name} | python3 -m json.tool
```
Returns: pass/fail history, failure rate, classifications breakdown, flakiness indicator, recent runs, related comments.

### Find similar errors by signature
```bash
curl -s "{server_url}/history/search?signature={error_signature}" | python3 -m json.tool
```
Returns: all tests that failed with the same error pattern, occurrence counts, last classification.

### Check for flaky tests
```bash
curl -s {server_url}/history/flaky | python3 -m json.tool
```
Returns: tests with intermittent pass/fail behavior (failure rate 20-80%).

### Check for regressions
```bash
curl -s {server_url}/history/regressions | python3 -m json.tool
```
Returns: tests that recently started failing after previously passing.

### Job statistics
```bash
curl -s {server_url}/history/stats/{job_name} | python3 -m json.tool
```
Returns: overall job health, most common failures, failure trend direction.

### Failure trends over time
```bash
curl -s {server_url}/history/trends | python3 -m json.tool
```
Returns: daily or weekly failure rate data points.

## When to Use

1. **Before classifying any failure**, check its history with `/history/test/{test_name}`:
   - If the test is flaky (is_flaky=true), mention it in your analysis
   - If there are existing comments/bugs, reference them instead of suggesting new ones
   - If it has high consecutive_failures, note it as an ongoing issue

2. **For PRODUCT BUG classifications**, search by error signature to see if this is a known issue across multiple tests.

3. **For CODE ISSUE classifications**, check `/history/regressions` to see if this is a recent regression and correlate with the git log.

4. **Support your classification with data** — reference specific history (e.g., "This test has failed in 8 of the last 10 runs and was previously reported as OCPBUGS-12345").

5. **Check flaky tests** — if a test appears in `/history/flaky`, note this in your analysis as it may indicate an infrastructure or timing issue rather than a code or product bug.
