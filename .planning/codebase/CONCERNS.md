# Codebase Concerns

**Analysis Date:** 2026-02-24

## Tech Debt

**Monolithic script structure:**
- Issue: The entire application is a single 2523-line Python file with no modular organization
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py`
- Impact: Difficult to test individual components, hard to maintain, no code reuse across projects, single point of failure
- Fix approach: Refactor into packages: `linear_client/`, `jira_client/`, `conversion/`, `models/` with clear separation of concerns

**Hardcoded configuration in code:**
- Issue: Jira URL and team mappings must be edited directly in source code (lines 44, 54-61)
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 44, 54-61)
- Impact: Breaks workflow for multiple Jira instances, requires code changes for each deployment, no environment-based config
- Fix approach: Move to `.env` file or CLI parameters, use ConfigParser or pydantic for management

**String-based query building:**
- Issue: GraphQL and API queries constructed via string concatenation (lines 180, 269-277, 348-356)
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 180, 269-277, 348-356)
- Impact: Vulnerable to injection if user input reaches query construction, difficult to trace data flow, hard to refactor
- Fix approach: Use GraphQL libraries like `gql` or typed query builders

## Known Bugs

**Missing pagination cursor in Linear user fetch:**
- Symptoms: If a workspace has >250 Linear users, only the first page is returned but no warning is issued
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 174-193)
- Trigger: Run migration on workspace with 251+ Linear users
- Workaround: If unmatched users appear, manually add them to `user_mapping.csv`
- Severity: Medium — manifests silently, impacting user assignment accuracy

**Markdown to ADF conversion incomplete:**
- Symptoms: Complex Markdown features (nested lists, tables, strikethrough combinations) may not render correctly in Jira
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 674-747)
- Cause: Custom regex-based parser doesn't handle all Markdown syntax or ADF requirements
- Workaround: None — content renders but may not match original formatting
- Severity: Low — mostly cosmetic, readability not affected

**Image download silent failure:**
- Symptoms: Images fail to download from Linear but are silently omitted from Jira description
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 576-609)
- Trigger: Network timeouts, auth header issues, or deleted Linear files
- Workaround: Manual re-upload or use remote links
- Severity: Medium — data loss occurs without clear feedback to user

## Security Considerations

**Secrets in memory without cleanup:**
- Risk: API keys (Linear and Jira) stored as plain strings in memory; no secure deletion on exit
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 2035-2060)
- Current mitigation: Passwords entered via `getpass` module (not echoed to terminal)
- Recommendations:
  - Implement `secrets` module for key storage
  - Zero memory buffers after use (use `ctypes.memmove` or similar)
  - Avoid printing API tokens in logs/debug output

**No rate limiting on API calls:**
- Risk: Rapid successive API calls could trigger rate limits or be misinterpreted as attack
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 138-162, 280-297, 374-427)
- Current mitigation: None — relies on API provider not blocking
- Recommendations:
  - Add exponential backoff retry logic
  - Implement token bucket or sliding window rate limiter
  - Track and respect `X-RateLimit-*` response headers

**Credentials not validated against whitelist:**
- Risk: Jira/Linear URLs hardcoded but not validated; could point to attacker-controlled server
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (line 44)
- Current mitigation: JIRA_URL is set at code time (not user input)
- Recommendations: Validate URLs against known domains or use explicit allowlisting

**CSV mapping file permissions:**
- Risk: `linear_jira_mapping.json` and `user_mapping.csv` contain issue IDs and email mappings without encryption
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 79-80, 1446-1460, 1912-1939)
- Impact: Sensitive mapping data left on disk unencrypted
- Recommendations: Encrypt mapping files or store in secure location, set restrictive file permissions (0600)

## Performance Bottlenecks

**Sequential image downloads and uploads:**
- Problem: For each issue, images are downloaded one-by-one, then uploaded one-by-one (lines 546-609)
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 546-609, 1683-1690)
- Cause: No concurrency or batching; I/O bound operations block execution
- Improvement path: Use `concurrent.futures.ThreadPoolExecutor` or `asyncio` for parallel downloads/uploads; batch operations

**No batching in enrichment queries:**
- Problem: Enrichment fetches up to 8 issues per batch (line 375), but retries fall back to single-issue queries on failure (line 404-416)
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 374-427)
- Cause: Risk-averse query sizing; batch too small to efficiently use API
- Improvement path: Start with batch_size=20, increase batch_size dynamically based on success rate

**Excessive field lookups on field errors:**
- Problem: On "data was not an array" error, the script fetches all Jira fields (lines 1580) which requires an extra API call per issue
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 1557-1612)
- Cause: Fields are fetched once globally, but error handling re-fetches
- Improvement path: Cache field list at migration start, pass to `_try_create_issue()`

**Linear and Jira user list fetched every run:**
- Problem: All Linear users (paginated) and all Jira users (paginated) fetched even if mapping file has recent entries (lines 2164-2167, 2331-2337)
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 2164-2167, 2331-2337)
- Cause: No caching or incremental fetch strategy
- Improvement path: Cache user lists locally with TTL, fetch only new users since last run

## Fragile Areas

**Main() function:**
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 2015-2523)
- Why fragile: Single 509-line function with multiple nested levels (up to 5), complex state management, many early exits
- Safe modification: Break into smaller functions for each "Step" (1-5); extract phase logic into separate functions
- Test coverage: No tests exist; error paths untested

**Enrichment retry logic:**
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 374-427)
- Why fragile: Nested try-except blocks with silent fallback to history-only; if history fetch also fails, continues without logging full error state
- Safe modification: Add detailed logging at each fallback; return success/failure status tuple; collect errors for final report
- Test coverage: Untested against network failures

**Markdown to ADF conversion:**
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 674-747)
- Why fragile: Complex regex patterns, multiple nested state machines for code blocks/lists/quotes, no unit tests
- Safe modification: Add comprehensive test suite before any changes; consider using established library (e.g., `python-markdown` + `pandoc`)
- Test coverage: No tests; edge cases (nested code blocks, mixed list types) likely broken

**User mapping CSV I/O:**
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 1912-1939)
- Why fragile: CSV parsing assumes exactly 2 columns; writes via temp file but no rollback on exception; no validation of email format
- Safe modification: Use `csv.DictReader`/`csv.DictWriter` for robustness; validate emails before write; add try-finally for cleanup
- Test coverage: Untested against malformed CSV

## Scaling Limits

**Linear pagination: query complexity budget:**
- Current capacity: 8 issues per enrichment batch (history, comments, attachments, relations)
- Limit: Queries >4000 complexity units rejected; teams with >5000 issues or issues with >100 comments may fail
- Scaling path: Implement adaptive batching (start at 8, halve on error); fetch history separately from comments/attachments/relations

**Jira field lookup explosion:**
- Current capacity: Detects story points + epic name fields by substring matching (lines 1101-1109)
- Limit: Projects with custom fields that match keywords will cause false positives; no caching of field detection results
- Scaling path: Cache field detection results in mapping file; add exact field ID configuration option

**Memory usage with large attachment downloads:**
- Current capacity: Entire attachment files loaded into memory (line 1804)
- Limit: Issues with >100 MB total attachments could cause OOM on low-memory systems
- Scaling path: Stream attachments to disk then re-read for upload; implement size checks and skip oversize files

**Linear GraphQL query size:**
- Current capacity: Issues queried with ~30 scalar fields per issue (lines 216-242)
- Limit: Linear API limits query complexity; if field list grows further, queries fail silently and fall back to safe field set
- Scaling path: Document query complexity budget; monitor API responses for warning headers

## Dependencies at Risk

**requests library dependency:**
- Risk: `requests >= 2.28.0` is the only external dependency; if a critical security issue found in requests, project blocked
- Impact: No protection against HTTP/session management flaws; all API communication uses requests
- Migration plan: Pin to specific version (e.g., `2.32.0`); monitor GitHub security advisories; consider `httpx` as alternative if requests abandoned

**No async/await framework:**
- Risk: All I/O is synchronous; if Linear or Jira API becomes slow, migration hangs with no timeout (60s timeout set but only at request level)
- Impact: Large migrations with network issues may take hours; no progress persistence
- Migration plan: Evaluate `aiohttp` or `httpx` with async support; implement checkpoint system to resume from failures

## Missing Critical Features

**No incremental/resume capability:**
- Problem: If migration fails halfway (e.g., network loss), must restart from beginning; no checkpoint or state recovery
- Blocks: Long-running migrations, unreliable networks, automated scheduling
- Fix approach: Save migration state after each issue; implement resume logic to skip already-migrated items

**No dry-run mode:**
- Problem: Must perform full migration to Jira to validate mappings and data conversion
- Blocks: Confidence testing, schema validation
- Fix approach: Add `--dry-run` flag; output what would be created without actual API calls

**No migration rollback:**
- Problem: If data looks wrong after migration, no way to undo; must manually delete from Jira
- Blocks: Safe migrations, testing in production
- Fix approach: Store created Jira keys in migration log; add `--rollback` command to bulk-delete migrated issues

**No field mapping customization:**
- Problem: Priority, issue type, and other field mappings are hardcoded (lines 64-97)
- Blocks: Custom projects with different field schemas
- Fix approach: Move mappings to config file; allow CLI override; validate mappings against target project schema

**No conflict detection:**
- Problem: If Linear issue already migrated to Jira but mapping is lost, creates duplicate instead of updating
- Blocks: Multi-run migrations, team re-migrations
- Fix approach: Query Jira for issues with "linear-{id}" label before creating; offer update-in-place option

## Test Coverage Gaps

**No unit tests:**
- What's not tested: GraphQL query building, markdown conversion, field detection, user mapping logic, all utility functions
- Files: All functions in `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py`
- Risk: Regression on refactoring, silent data corruption, API incompatibility
- Priority: High — critical path functions untested

**No integration tests:**
- What's not tested: Full Linear→Jira data flow, image upload/download round-trip, attachment handling, comment consolidation
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (phases 1400-1906)
- Risk: End-to-end failures discovered only during production migration
- Priority: High — catches real-world API interaction issues

**No error scenario tests:**
- What's not tested: Network timeouts, API rate limiting, malformed responses, permission denied errors, field type mismatches
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (error handling lines 143-162, 402-416, 1567-1612)
- Risk: Silent failures or incorrect error recovery; user confusion about what failed
- Priority: Medium — impacts production reliability

**No data validation tests:**
- What's not tested: Email format validation, CSV parsing edge cases, user input validation (selection parsing), date parsing
- Files: `C:/own/dev/South/LinearJiraSync/linear_jira_sync.py` (lines 1371-1394, 1912-1939, 976-1016)
- Risk: Invalid data migrated to Jira, crashes on edge case inputs
- Priority: Medium — data quality impact

---

*Concerns audit: 2026-02-24*
