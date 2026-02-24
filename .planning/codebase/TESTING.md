# Testing Patterns

**Analysis Date:** 2026-02-24

## Test Framework

**Status:** No automated testing framework detected

**Testing Approach:** Manual/interactive testing only
- No pytest, unittest, vitest, or jest configuration found
- No test files (*.test.py, *_test.py, test_*.py) in codebase
- No CI/CD pipeline configuration (no .github/workflows, .gitlab-ci.yml, etc.)

**Why No Automated Tests:**
- Single-file monolithic script (`linear_jira_sync.py`) with direct API calls to Linear and Jira
- Interactive CLI workflow (prompts for credentials, team selection, issue preview)
- State-dependent operations (requires live API connectivity, valid credentials)
- Each run produces side effects (creates/updates real Jira issues)

## Test File Organization

**Location:** Not applicable — no tests exist

**Where tests would go (recommendation for future):**
- `tests/test_markdown_to_adf.py` — Markdown to ADF conversion logic (deterministic, no API calls)
- `tests/test_issue_classification.py` — `is_triage()`, `determine_issue_type()` logic
- `tests/test_date_parsing.py` — `_parse_iso_to_date()`, `resolve_due_date()` logic
- `tests/test_user_mapping.py` — User CSV loading/resolution logic
- Integration tests for API mocking (separate category)

## Testable Code Segments

**Pure Functions (Could be Unit Tested):**

1. **Markdown to ADF Conversion** (`markdown_to_adf()` at line 674):
   ```python
   def markdown_to_adf(markdown: str) -> dict:
       # Deterministic transformation: markdown string → ADF dict
       # No API calls, no side effects
       # ~75 lines of parsing logic
   ```
   - Inputs: Markdown text from Linear
   - Outputs: Atlassian Document Format (ADF) dict for Jira
   - Test cases needed: Code blocks, headings, lists, blockquotes, inline formatting

2. **Issue Classification** (`determine_issue_type()` at line 963):
   ```python
   def determine_issue_type(issue: dict) -> str:
       # Pure function: map Linear labels to Jira issue type
   ```
   - Test: "Bug" label → "Bug" type; "Feature Request" → "Story"; default → "Story"

3. **Triage Detection** (`is_triage()` at line 953):
   ```python
   def is_triage(issue: dict) -> bool:
       # Check if issue matches TRIAGE_STATE_NAMES or TRIAGE_LABEL_NAMES
   ```
   - Test: Issue with excluded state/label → True; normal issue → False

4. **Date Parsing** (`_parse_iso_to_date()` at line 976, `resolve_due_date()` at line 984):
   ```python
   def _parse_iso_to_date(s: str) -> Optional[str]:
       # ISO timestamp → YYYY-MM-DD
       try:
           dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
           return dt.strftime("%Y-%m-%d")
       except Exception:
           return None
   ```
   - Test: ISO string handling, timezone conversion, fallbacks

5. **Priority Mapping** (logic in `build_jira_fields()` line 1149):
   ```python
   linear_p = issue.get("priorityLabel") or "No priority"
   fields["priority"] = {"name": priority_map.get(linear_p, "Medium")}
   ```
   - Test: Urgent → Highest, High → High, Low → Low, unknown → Medium

6. **Story Points Mapping** (logic in `build_jira_fields()` line 1153-1154):
   ```python
   if sp_field_id and issue.get("estimate") is not None:
       fields[sp_field_id] = float(issue["estimate"])
   ```
   - Test: Numeric estimate preserved, None skipped, field ID respected

**Functions with External Dependencies (Would Need Mocking):**

- `gql()` (line 138) — GraphQL API call to Linear
  - Mocking: Mock `requests.post()` to return Linear API responses
  - Test: Handle 401, GraphQL errors, timeouts, malformed responses

- `JiraClient._request()` (line 763) — HTTP requests to Jira
  - Mocking: Mock `requests.request()` for all HTTP methods
  - Test: Auth errors (401, 403), status codes (200, 201, 204, 404), timeout handling

- User lookups: `linear_fetch_all_users()`, `JiraClient.get_all_users()`
  - Mocking: Mock paginated API responses
  - Test: Pagination loop termination, cursor handling

**Phase Functions (Would Require Integration Tests):**

- `phase_create_epics()` — Create Jira epics from Linear projects
- `phase_create_bugs()` — Create bug issues from Linear items with "Bug" label
- `phase_upload_attachments()` — Download from Linear, upload to Jira
- `phase_post_activity_comments()` — Create comment with activity history

These require:
- Mock Linear API returning realistic issue/project structures
- Mock Jira API accepting issue/attachment/comment creation
- Verification of state persistence (mapping dict updated correctly)
- Assertion on side effects (created tickets, uploaded files)

## Manual Testing Approach (Current)

**How the script is tested today:**

1. **Interactive Validation** (line 2015-2067 in `main()`):
   ```python
   linear_key = prompt_secret("Linear API key")
   viewer = linear_fetch_viewer(linear_key)
   print(f"  ✓ {viewer['name']} ({viewer['email']})")
   ```
   - User verifies credentials work at runtime

2. **Preview Before Commit** (lines 2200+):
   - Script fetches and displays all items that would be migrated
   - User reviews table of Linear issues → Jira mappings
   - User selects which items to proceed with (or can exit)

3. **Dry-run via Selection** (line 1371 `parse_selection()`):
   - Interactive prompt allows user to skip specific items
   - Safe to test on staging without migrating all issues

4. **Error Feedback** (throughout):
   - Script prints errors as they occur: `print(f"  Error: {exc}")`
   - User reads output and decides whether to retry, skip, or abort

**Testing signals embedded in code:**

- Diagnostic print statements (e.g., line 1178-1179):
  ```python
  print(f"  ASSIGNEE  {issue.get('identifier')...}  linear={ae}  mapped={...}")
  ```
  These help operator verify correct behavior during execution.

- Status markers: `✓` (success), `✗` (failure), `⚠` (warning)

- Detailed error messages with context (e.g., HTTP status codes, API response snippets)

## Mocking Strategy (For Future Tests)

**Mock Pattern for Linear API:**

```python
# Would use unittest.mock or pytest-mock
from unittest.mock import patch, MagicMock

@patch('requests.post')
def test_gql_success(mock_post):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"data": {"viewer": {"id": "1", "name": "Test User", "email": "test@example.com"}}}
    )
    result = gql("test-key", "query { viewer { id } }")
    assert result["viewer"]["name"] == "Test User"

@patch('requests.post')
def test_gql_auth_failure(mock_post):
    mock_post.return_value = MagicMock(status_code=401)
    with pytest.raises(Exception, match="authentication failed"):
        gql("bad-key", "query { viewer { id } }")
```

**Mock Pattern for Jira API:**

```python
@patch('requests.request')
def test_create_issue(mock_request):
    mock_request.return_value = MagicMock(
        status_code=201,
        json=lambda: {"id": "10001", "key": "DESK-123"}
    )
    jira = JiraClient("user@example.com", "token")
    result = jira.create_issue({"summary": "Test", "project": {"key": "DESK"}})
    assert result["key"] == "DESK-123"
```

**What NOT to Mock:**

- Core logic: `markdown_to_adf()`, `determine_issue_type()` — test actual functions
- Data structures: Test with real dict structures matching Linear/Jira API responses
- Transformation logic: `build_jira_fields()` should run with real test data

## Fixtures and Factories (Not Currently Used)

**Recommended Test Data:**

```python
# fixtures/linear_issue.py
LINEAR_ISSUE_FIXTURE = {
    "id": "LIN-1",
    "identifier": "DESK-123",
    "title": "Fix login button",
    "description": "The login button is not clickable on mobile.",
    "priority": 1,
    "priorityLabel": "High",
    "estimate": 5,
    "dueDate": "2026-02-28",
    "state": {"name": "In Progress"},
    "assignee": {"id": "user1", "email": "alice@linear.app"},
    "creator": {"id": "user2", "email": "bob@linear.app"},
    "labels": [{"name": "Bug"}],
    "attachments": [{"id": "att1", "url": "https://...", "fileName": "screenshot.png"}],
    "comments": [{"body": "Testing now...", "createdAt": "2026-02-24T10:00:00Z"}],
}

# fixtures/jira_user.py
JIRA_USER_FIXTURE = {
    "accountId": "627654321",
    "emailAddress": "alice@example.com",
    "displayName": "Alice",
    "active": True,
}
```

## Coverage Assessment

**Estimated Coverage Gaps:**

1. **No tests for markdown-to-ADF conversion** — Complex regex-based parser (lines 674-747)
   - Risk: Silent formatting failures in Jira descriptions
   - Impact: High (affects all migrated issue descriptions)

2. **No tests for paginated API fetching** — Pagination loops in multiple functions (lines 174-193, 822-834)
   - Risk: Infinite loops if API response is malformed; cursor not advanced correctly
   - Impact: High (script could hang)

3. **No tests for user mapping CSV logic** — `load_user_csv()`, `save_user_csv()` (lines 1912-1941)
   - Risk: Encoding issues, malformed CSV, data loss on save
   - Impact: Medium (affects user resolution)

4. **No tests for attachment upload** — Multi-step process with retries (lines 850-867, 869-916)
   - Risk: Failed uploads not detected until inspection
   - Impact: Medium (only affects attachment migration phase)

5. **No tests for issue link creation** — Relation mapping logic (line 1864 onwards)
   - Risk: Invalid link types, circular dependencies not handled
   - Impact: Low (affects only issue relationships)

6. **No integration tests** — End-to-end phase sequencing (main() line 2015 onwards)
   - Risk: Phases executed in wrong order, state not preserved between phases
   - Impact: High (entire migration could fail at runtime)

## Suggested Test Priorities

**High Priority (Test First):**
1. Markdown to ADF conversion (`markdown_to_adf()`)
2. Pagination logic for Linear API (`linear_fetch_all_users()`, similar)
3. Date parsing and due date resolution (`resolve_due_date()`)
4. Issue classification (`determine_issue_type()`, `is_triage()`)

**Medium Priority:**
1. User mapping CSV I/O
2. Jira field building with all edge cases
3. API error handling (401, 403, timeouts)

**Low Priority:**
1. Terminal formatting helpers (`_visible_len()`, `_pad_detail()`)
2. Phase execution order (integration-level, needs E2E environment)

## Run Commands (For Future)

When testing is implemented:

```bash
# Run all unit tests
pytest tests/

# Run with coverage report
pytest tests/ --cov=linear_jira_sync --cov-report=html

# Run specific test file
pytest tests/test_markdown_to_adf.py -v

# Watch mode (using pytest-watch)
ptw tests/

# Run tests with detailed output
pytest tests/ -vv -s
```

---

*Testing analysis: 2026-02-24*
