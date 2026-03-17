# Failure History API — MANDATORY INSTRUCTIONS

You MUST follow ALL steps below for EVERY test failure you analyze. These steps are NOT optional. Skipping any step is a violation of your instructions.

## Step 1: Check Test History (MANDATORY for EVERY test)

For EVERY failed test, check its history BEFORE making any classification:
```bash
curl -s "{server_url}/history/test/{test_name}?exclude_job_id={job_id}" | python3 -m json.tool
```

Examine:
- `failure_rate` — how often does this test fail?
- `consecutive_failures` — is this an ongoing issue?
- `classifications` — how was it classified before?
- `comments` — did humans leave notes about this test?
- `recent_runs` — what happened in recent builds?

## Step 2: Search for Similar Errors (MANDATORY for EVERY test)

Check if other tests fail with the same error pattern:
```bash
curl -s "{server_url}/history/search?signature={error_signature}&exclude_job_id={job_id}" | python3 -m json.tool
```

If many tests share the same error signature, this likely indicates an INFRASTRUCTURE issue — not individual test failures.

## Step 3: Check Existing Classifications (MANDATORY for EVERY test)

See if this test was previously classified:
```bash
curl -s "{server_url}/history/classifications?test_name={test_name}" | python3 -m json.tool
```

If already classified, reference the existing classification and explain if your assessment agrees or differs.

## Step 4: Check Job Statistics (MANDATORY — once per job)

Understand the overall health of this job:
```bash
curl -s "{server_url}/history/stats/{job_name}?exclude_job_id={job_id}" | python3 -m json.tool
```

## Step 5: Classify EVERY Test (MANDATORY for EVERY test — NO EXCEPTIONS)

After completing your analysis, you MUST call POST /history/classify for EVERY test you analyzed. This is NOT optional. Every test gets a classification.

```bash
curl -s -X POST "{server_url}/history/classify" \
  -H "Content-Type: application/json" \
  -d '{
    "test_name": "{test_name}",
    "classification": "REGRESSION",
    "reason": "Explain why you chose this classification",
    "job_name": "{job_name}"
  }'
```

### Valid Classifications

| Classification | When to Use |
|---|---|
| `FLAKY` | Test sometimes passes, sometimes fails. Inconsistent results across runs. |
| `REGRESSION` | Test was previously passing and recently started failing. This applies to BOTH code issues AND product bugs — a product bug can be a regression. |
| `INFRASTRUCTURE` | Failure caused by infrastructure problems (cluster not deployed, network issues, resource limits), not by the test code or the product. |
| `KNOWN_BUG` | Failure matches a known, already-reported bug. Reference the bug in the reason. |
| `INTERMITTENT` | Similar to flaky but with a known trigger (e.g., timing, resource contention). |

### Classification Rules

1. You MUST classify EVERY test. No exceptions.
2. A test can be BOTH a PRODUCT BUG and a REGRESSION — these are orthogonal:
   - PRODUCT BUG / CODE ISSUE = the TYPE of issue (what's broken)
   - FLAKY / REGRESSION / INFRASTRUCTURE / KNOWN_BUG = the PATTERN (how it manifests)
3. If many tests fail because the core infrastructure wasn't deployed, classify ALL of them as INFRASTRUCTURE — not as individual regressions.
4. Always include a clear `reason` explaining your classification.
5. Always reference historical data in your reason (e.g., "This test failed in 8 of the last 10 runs" or "First failure, was passing in all prior builds").

## Rules

- ALWAYS complete ALL 5 steps for EVERY test. No shortcuts.
- ALWAYS check history BEFORE classifying — don't classify blind.
- ALWAYS call POST /history/classify — this is how your classification is recorded.
- If many tests fail with the same infrastructure error (e.g., product not deployed), classify ALL as INFRASTRUCTURE.
- Reference existing comments, bugs, and history in your analysis.
- Your reason field should cite specific data from the history (failure rates, consecutive failures, first seen dates).
