# Project Coding Principles

## Data Integrity

- Never truncate data arbitrarily (no `[:100]` or `[:2000]` slicing)
- Preserve full information; let consumers handle their own limits

## No Dead Code

- Use everything you create: imports, variables, clones, instantiations
- Remove unused code rather than leaving it dormant

## Smart Context Management

- Prefer structured data (test reports, APIs) over raw logs
- When raw data is necessary, extract relevant content (errors, failures, warnings) instead of full dumps

## Parallel Execution

- Run independent, stateless operations in parallel
- Handle failures gracefully: one failure should not crash all parallel tasks
- Capture exceptions and continue processing

## File Handling

- Preserve user edits when modifying files
- Add missing elements rather than replacing entire content
- Never overwrite user customizations

## Communication

- Explain data flow through the system, not just variable locations
- Show how components connect and interact
