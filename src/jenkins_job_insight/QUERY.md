# Failure History API

You have access to the failure history API. Follow these steps IN ORDER before classifying any failure.

## Step 1: Check Test History

For EVERY failed test, check its history first:
```bash
curl -s "{server_url}/history/test/{test_name}?exclude_job_id={job_id}" | python3 -m json.tool
```
Look at: failure_rate, consecutive_failures, classifications, comments.

## Step 2: Search for Similar Errors

Check if other tests fail with the same error pattern:
```bash
curl -s "{server_url}/history/search?signature={error_signature}&exclude_job_id={job_id}" | python3 -m json.tool
```
If many tests share the same signature, this may be an infrastructure issue, not individual test failures.

## Step 3: Check Existing Classifications

See if this test has already been classified:
```bash
curl -s "{server_url}/history/classifications?test_name={test_name}" | python3 -m json.tool
```
If already classified, reference the existing classification in your analysis.

## Step 4: Check Job Statistics

Understand the overall health of this job:
```bash
curl -s "{server_url}/history/stats/{job_name}?exclude_job_id={job_id}" | python3 -m json.tool
```

## Step 5: Classify the Test

After analysis, if you determine a test is FLAKY, a REGRESSION, INFRASTRUCTURE issue, or a KNOWN_BUG, report it:
```bash
curl -s -X POST "{server_url}/history/classify" -H "Content-Type: application/json" -d '{
  "test_name": "{test_name}",
  "classification": "FLAKY",
  "reason": "Test passes intermittently - 6 of 10 runs pass, timing-dependent assertion",
  "job_name": "{job_name}"
}'
```

Valid classifications: FLAKY, REGRESSION, INFRASTRUCTURE, KNOWN_BUG, INTERMITTENT

## Rules

- ALWAYS check history before classifying -- don't ignore existing data
- If many tests fail with the same infrastructure error, classify as INFRASTRUCTURE not individual regressions
- Reference existing comments and bugs from history in your analysis
- Only classify as REGRESSION if the test was consistently passing before and recently started failing
- Classify as FLAKY only if the test genuinely passes sometimes and fails other times
