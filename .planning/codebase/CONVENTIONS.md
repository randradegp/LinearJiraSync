# Coding Conventions

**Analysis Date:** 2026-02-24

## Naming Patterns

**Files:**
- Single monolithic file: `linear_jira_sync.py`
- Configuration files: `linear_jira_mapping.json`, `user_mapping.csv`

**Functions:**
- Public functions use `snake_case`: `linear_fetch_viewer()`, `build_jira_fields()`, `main()`
- Private/internal functions prefixed with `_`: `_nodes()`, `_fmt_date()`, `_build_issue_query()`, `_paginate_issues()`
- Helper functions for specific domains: `_visible_len()`, `_pad_detail()`, `_truncate_ansi()` (string helpers); `_inline_marks()`, `_paragraph()`, `_list_item()` (markdown/ADF helpers)
- Fetch/query functions: `linear_fetch_*()` for Linear API, `jira.get_*()`, `jira.create_*()` for Jira client methods
- Phase functions: `phase_create_epics()`, `phase_create_bugs()`, `phase_upload_attachments()` — each corresponds to one migration step

**Variables:**
- `snake_case` throughout: `api_key`, `team_id`, `issue_type`, `mapped_teams`, `user_cache`
- Short loop counters: `i`, `n` (line indexing in markdown parser); `start`, `end` (pagination)
- Constants in `UPPERCASE`: `JIRA_URL`, `LINEAR_API_URL`, `TEAM_SPACE_MAP`, `LABEL_ISSUE_TYPE_MAP`, `DEFAULT_PRIORITY_MAP`
- Intermediate dict comprehensions: `v_up`, `v_low` (uppercase/lowercase versions for comparison); `ae` (email); `aid` (account ID); `rid` (reporter ID); `sp_field_id` (story points field)
- Configuration/metadata: `W` (width for terminal formatting)

**Types:**
- Use Python type hints in function signatures: `def prompt(message: str, default: str = None) -> str:`
- Dict types: `dict` (no nested hints) or `Optional[dict]` when nullable
- List types: `list` (no generic parameters) — e.g., `def linear_fetch_teams(api_key: str) -> list:`
- Optional types: `Optional[str]`, `Optional[dict]`, `Optional[bytes]` from `typing`
- No dataclass or TypedDict usage — all data passed as plain `dict` objects

## Code Style

**Formatting:**
- No explicit formatter configured (no `.prettierrc`, `black` config, or linting)
- Line length: Variable, functions sometimes exceed 100 characters
- Indentation: 4 spaces (Python standard)
- String style: Double quotes for most strings; no f-string consistency rule (both `f"..."` and regular strings used)
- Multi-line strings: Triple-quoted strings for GraphQL queries and docstrings

**Comments and Sections:**
- Section dividers: Heavy decorative lines using `═` and `─` characters
  ```
  # ═════════════════════════════════════════════════════════════════════════════
  #  CONFIGURATION  —  the only section you need to edit
  # ═════════════════════════════════════════════════════════════════════════════
  ```
- Subsection dividers use lighter lines:
  ```
  # ─────────────────────────────────────────────────────────────────────────────
  # Linear GraphQL client
  # ─────────────────────────────────────────────────────────────────────────────
  ```
- Inline step comments in `main()`: `# ── Step 1: Linear credentials ────` (mix of em-dashes and hyphens)

**Linting:**
- No linter configured (no `.eslintrc`, `pylint`, or `flake8` config detected)
- Type hints used but not enforced by strict linter

## Import Organization

**Order:**
1. Python built-in modules: `sys`, `json`, `re`, `base64`, `getpass`, `os`, `io`, `mimetypes`, `csv`
2. Standard library with submodules: `from datetime import datetime, timedelta, timezone` and `from typing import Optional`
3. Third-party: `import requests`

**File structure:**
- Imports at top (lines 26-38)
- Configuration section follows (lines 40-102)
- Utilities section starts line 104
- No module aliases (no `import X as Y` pattern used)
- No wildcard imports

## Error Handling

**Strategy:**
- Broad exception catching: `except Exception:` is common pattern (not granular error types)
- Specific handling for HTTP errors: `requests.exceptions.ConnectionError`, `requests.exceptions.Timeout` are caught separately
- HTTP status codes checked explicitly: `if resp.status_code == 401:`, `if resp.status_code in (200, 201):`

**Patterns:**
In `gql()` function (lines 138-162):
```python
try:
    resp = requests.post(LINEAR_API_URL, json=payload, headers=headers, timeout=60)
except requests.exceptions.ConnectionError:
    raise Exception("Connection error reaching Linear API.")
except requests.exceptions.Timeout:
    raise Exception("Linear API request timed out.")
if resp.status_code == 401:
    raise Exception("Linear authentication failed — check your API key.")
# Parse body first so GraphQL errors surface as readable messages
try:
    body = resp.json()
except Exception:
    resp.raise_for_status()
    raise Exception(f"Linear API {resp.status_code}: {resp.text[:300]}")
```

In `JiraClient._request()` (lines 763-789):
```python
try:
    resp = requests.request(method, url, headers=headers, json=json_body, params=params, timeout=60)
except requests.exceptions.ConnectionError:
    raise Exception(f"Connection error: {url}")
except requests.exceptions.Timeout:
    raise Exception(f"Timeout: {url}")
if resp.status_code == 401:
    raise Exception("Jira authentication failed (401).")
if resp.status_code == 403:
    raise Exception(f"Jira permission denied (403): {method} {path}")
```

**Error propagation:**
- Inner functions raise `Exception` with human-readable messages
- Callers catch and print with context marker (e.g., `print(f"  Error: {exc}")`): see line 2044, 2064, 2148
- Some errors allow graceful degradation (e.g., user lookup returns `None` on failure, see line 1557-1569)

## Logging

**Framework:** `print()` — no logging library used

**Patterns:**
- Status output to stdout with visual markers:
  - `✓` for success
  - `✗` or `⚠` for warnings
  - `ASSIGNEE`, `WARN` labels for diagnostic output
- Formatted output at milestone steps: section headers with unicode box characters, progress messages, formatted tables

Examples from lines 2017-2020:
```python
print()
print("╔" + "═" * (W - 2) + "╗")
print("║" + "  LINEAR → JIRA UNIFIED SYNC".center(W - 2) + "║")
print("╚" + "═" * (W - 2) + "╝")
```

From lines 1178-1179 (diagnostic logging):
```python
print(f"  ASSIGNEE  {issue.get('identifier') or issue.get('title','?')!r:<20}"
      f"  linear={ae or '(none)'}  mapped={'YES → '+aid[:8]+'…' if aid else 'NO'}")
```

## Comments

**When to Comment:**
- Section dividers: Mandatory before each functional area
- Complex logic: Applied to markdown-to-ADF parser (line 674 onwards)
- API quirks: Explained in docstrings for tricky methods like `get_media_uuid_for_attachment()` (lines 869-876)
- Implementation notes: Inline comments explain workarounds, e.g., line 1137-1138: "Description is NOT set here — it is applied after creation via update_issue so that inline images are uploaded to Jira first (no Linear URLs ever sent)."

**Docstrings:**
- Module-level docstring (lines 2-24): Comprehensive explanation of purpose, field mapping, usage
- Function docstrings: One-liners for simple functions (e.g., line 175: `"""Fetch all Linear users with pagination (max 250 per page)."""`)
- Method docstrings: Multi-line for complex methods (e.g., lines 851-854 for `upload_attachment()`, lines 869-876 for `get_media_uuid_for_attachment()`)
- Docstring format: Plain text, not Google/Sphinx style

Example (lines 811):
```python
def issue_exists(self, key: str) -> bool:
    """Return True if the Jira issue key exists (False on 404 or any error)."""
```

## Function Design

**Size:**
- Small utility functions: 3-5 lines (`prompt()`, `_nodes()`, simple getters)
- Medium functions: 20-50 lines (pagination, list comprehension)
- Large functions: 100+ lines (markdown parser `markdown_to_adf()` ~75 lines, `main()` ~800 lines, phases like `phase_create_issues()`)
- No strict line limit observed

**Parameters:**
- Functions take explicit parameters rather than modifying global state
- Configuration globals (`TEAM_SPACE_MAP`, `LABEL_ISSUE_TYPE_MAP`, `DEFAULT_PRIORITY_MAP`) read but not modified at runtime
- Most API client methods: 1-3 parameters (e.g., `gql(api_key, query, variables=None)`)
- Complex operations pass large dicts: `build_jira_fields()` takes 9 parameters (line 1120-1131) to configure all field mapping

**Return Values:**
- Functions return data structures: `dict`, `list`, `Optional[str]`, `Optional[dict]`
- Phase functions (`phase_*()`) return `None` (side effects via API calls)
- Query functions return structured data (e.g., `linear_fetch_teams()` returns `list` of team dicts)
- Optional returns: `resolve_account_id()` returns `Optional[str]` — `None` on cache miss or API failure

## Module Design

**Exports:**
- Single entry point: `main()` function (line 2015)
- No explicit `__all__` or module-level exports defined
- All code executes in module scope when imported; `if __name__ == "__main__": main()` pattern not explicitly shown (file ends at line ~2350)

**Barrel Files:**
- Not applicable (single-file monolithic script)

**Classes:**
- One class: `JiraClient` (lines 754-946)
  - Encapsulates Jira REST API v3 interaction
  - Private `_request()` method (line 763) handles HTTP transport
  - Public methods for each Jira operation: `get_project()`, `create_issue()`, `upload_attachment()`, `add_comment()`, etc.
  - Caching: `_user_cache` dict to memoize email → account ID lookups (line 761, used in `resolve_account_id()`)

## Data Flow Conventions

**Dict structures:**
- Linear issue dict: Contains `identifier`, `title`, `description`, `priority`, `estimate`, `dueDate`, `state`, `assignee` (user dict), `creator`, `labels`, `project`, `team`, `parent`, `children`, `attachments`, `comments`, `relations`, etc.
- Jira field dict: Built by `build_jira_fields()` and contains `summary`, `project`, `issuetype`, `priority`, `duedate`, `labels`, `assignee`, `reporter`, custom fields
- Mapping dicts: `linear_jira_mapping.json` stores issue key → Jira issue key; `user_mapping.csv` stores Linear user email → Jira account ID

**Phase execution:**
- Each phase is a function that operates on shared state: fetched issues, mapping dict, JiraClient instance
- Phases in order: `phase_create_epics()`, `phase_create_bugs()`, `phase_create_feature_requests()`, `phase_create_stories()`, `phase_move_to_backlog()`, `phase_upload_attachments()`, `phase_post_activity_comments()`, `phase_create_links()`

---

*Convention analysis: 2026-02-24*
