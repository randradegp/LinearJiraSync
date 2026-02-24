# Architecture

**Analysis Date:** 2026-02-24

## Pattern Overview

**Overall:** Single-script monolithic CLI with layered module organization

**Key Characteristics:**
- Interactive console-based workflow with multi-step phase execution
- GraphQL client for Linear API and REST client for Jira Cloud API
- Stateful migration tracking via persistent JSON mapping files
- Bidirectional data transformation (Linear → Jira) with enrichment pipeline
- No database; all state managed via files and in-memory collections

## Layers

**API Integration Layer:**
- Purpose: Encapsulate external API calls (Linear GraphQL and Jira REST)
- Location: `linear_jira_sync.py` — functions `gql()`, `linear_*()`, and class `JiraClient`
- Contains: GraphQL query builders, HTTP request/response handling, pagination logic
- Depends on: `requests` library, API credentials (Linear API key, Jira token)
- Used by: Data fetching and synchronization phases

**Data Transformation Layer:**
- Purpose: Convert Linear issue data to Jira-compatible format (ADF, fields, metadata)
- Location: `linear_jira_sync.py` — functions like `build_jira_fields()`, `markdown_to_adf()`, `build_description_adf()`, `build_activity_comment_md()`
- Contains: Field mapping logic, Markdown-to-ADF conversion, type detection, priority translation
- Depends on: Linear issue dictionaries, Jira field definitions
- Used by: Issue creation and enrichment phases

**User Matching Layer:**
- Purpose: Map Linear users to Jira users by email, with manual override via CSV
- Location: `linear_jira_sync.py` — functions `build_user_map()`, `load_user_csv()`, `save_user_csv()`
- Contains: Email-based matching, unmatched user tracking, CSV persistence
- Depends on: Linear users list, Jira users list, optional `user_mapping.csv`
- Used by: Issue field assignment (assignee, reporter, creator)

**State Management Layer:**
- Purpose: Track migrated items to avoid duplication and enable resumable operations
- Location: `linear_jira_sync.py` — functions `load_mapping()`, `save_mapping()`
- Contains: Linear ID ↔ Jira key mapping persistence
- Depends on: `linear_jira_mapping.json` file
- Used by: All creation and linking phases

**UI/CLI Layer:**
- Purpose: Interactive user prompts and formatted output (preview tables, progress)
- Location: `linear_jira_sync.py` — functions `prompt()`, `prompt_secret()`, `print_preview_table()`, `_preview_detail_line()`
- Contains: Input validation, ANSI color formatting, table rendering
- Depends on: Standard input/output, getpass module
- Used by: Main orchestration flow

**Orchestration Layer:**
- Purpose: Coordinate the multi-phase workflow
- Location: `linear_jira_sync.py` — `main()` function and phase functions (`phase_create_epics()`, `phase_create_bugs()`, etc.)
- Contains: Step sequencing, error handling, report aggregation
- Depends on: All other layers
- Used by: Entry point script execution

## Data Flow

**Linear Fetch Pipeline:**

1. User provides Linear API key → credential verification via `linear_fetch_viewer()`
2. Fetch all Linear teams → filter by `TEAM_SPACE_MAP` configuration
3. For each team:
   - Probe with minimal query via `linear_probe_issues()` (detect accessibility)
   - Fetch issues via `linear_fetch_all_issues()` (team.issues endpoint, paginated 50/page)
   - Fetch issues via `linear_fetch_project_issues()` (project.issues endpoint, handles project-only items)
   - Deduplicate by id
4. Filter out triage issues via `is_triage()` check (state or label-based)
5. Apply label inclusion/exclusion filters (user-selected)
6. Enrich with history via `linear_enrich_with_history()` (batched queries for comments, attachments, relations)

**Field Transformation Pipeline:**

1. Linear issue dict → `build_jira_fields()` → Jira field dict
2. Within `build_jira_fields()`:
   - Determine issue type via `determine_issue_type()` (check for Bug or Feature Request labels)
   - Resolve assignee/reporter via `build_user_map()` (email-based match or None)
   - Convert estimate → story points (if field detected)
   - Convert dueDate → Due date field
   - Convert priority via `DEFAULT_PRIORITY_MAP` lookup
   - Generate description via `build_description_adf()` (includes Linear metadata)
   - Generate activity comment via `build_activity_comment_md()` (consolidated history + comments)

**Jira Creation Pipeline:**

1. User provides Jira credentials → verification via `JiraClient.get_myself()`
2. Resolve team → project mapping (name/key lookup, interactive fallback)
3. Fetch Jira projects, detect story points field, epic name field
4. Create Epic for each Linear project via `phase_create_epics()` (stored in mapping)
5. Create issues by type:
   - Bugs via `phase_create_bugs()` (filtered via label)
   - Feature Requests via `phase_create_feature_requests()` (filtered via label)
   - All others via `phase_create_stories()` (default)
6. For each issue:
   - Call `_try_create_issue()` → `JiraClient.create_issue()` (REST POST)
   - Store Linear ID → Jira key in mapping
   - Collect failures in report
7. Move all issues to project backlog via `phase_move_to_backlog()`
8. Upload attachments via `phase_upload_attachments()` (binary download from Linear, multipart POST to Jira)
9. Post activity comments via `phase_post_activity_comments()` (consolidated MD comment)
10. Create issue links via `phase_create_links()` (blocks/blocked_by/duplicate/relates-to)

**State Management Flow:**

1. Load existing `linear_jira_mapping.json` (empty on first run)
2. Epics created → map Linear project ID to Jira key (prefixed with `__epic__`)
3. Issues created → map Linear issue ID to Jira issue key
4. Save mapping after each phase (checkpoint)
5. On resume: check existing mapping before creating duplicates

## Key Abstractions

**JiraClient:**
- Purpose: Encapsulate all Jira REST API interactions
- Examples: `linear_jira_sync.py` lines 754–952
- Pattern: Stateful HTTP client with auth caching, error normalization, pagination helpers

**Linear GraphQL Query Builders:**
- Purpose: Construct parameterized queries with field flexibility and retry fallback
- Examples: `_build_issue_query()`, `_FIELDS_FULL`, `_FIELDS_SAFE`
- Pattern: Query templates with fallback paths (full → safe fields on API rejection)

**Markdown to ADF Converter:**
- Purpose: Transform Linear's Markdown descriptions into Jira's Atlassian Document Format
- Examples: `markdown_to_adf()`, `_paragraph()`, `_list_item()`, `_inline_marks()`
- Pattern: Recursive parser with ANSI-aware text handling, media integration

**Issue Type Classifier:**
- Purpose: Determine Jira issue type based on Linear labels and `LABEL_ISSUE_TYPE_MAP`
- Examples: `determine_issue_type()`
- Pattern: Label-based routing with fallback to default

## Entry Points

**Main CLI Entry:**
- Location: `linear_jira_sync.py` lines 2015–end
- Triggers: `python linear_jira_sync.py`
- Responsibilities:
  1. Prompt for Linear + Jira credentials
  2. Resolve team-to-project mapping
  3. Fetch and filter all Linear data
  4. Auto-detect Jira fields and user mappings
  5. Display migration summary for confirmation
  6. Execute multi-phase creation (epics, issues, attachments, comments, links)
  7. Generate final report with failure counts

**Configuration Entry Points:**
- `TEAM_SPACE_MAP`: Dict mapping Linear team names to Jira project keys/names (top of file)
- `LABEL_ISSUE_TYPE_MAP`: Label name → Jira issue type translations
- `DEFAULT_PRIORITY_MAP`: Linear priority → Jira priority levels
- `RELATION_TYPE_MAP`: Linear relation types → Jira link types
- `TRIAGE_STATE_NAMES`, `TRIAGE_LABEL_NAMES`: Exclusion filters

## Error Handling

**Strategy:** Graceful degradation with comprehensive reporting

**Patterns:**

1. **API Errors:**
   - Linear GraphQL: Parse `errors` array, extract messages, raise Exception
   - Jira REST: Check status codes (401 auth, 403 permission, 404 not found, 5xx server)
   - Network: Catch `ConnectionError`, `Timeout` → convert to Exception with context

2. **Fallback Queries:**
   - Full field query fails → retry with safe field set (reduced complexity)
   - Full enrichment fails (history+comments+attachments) → retry history-only
   - History-only fails → skip enrichment, continue with other issues

3. **Phase Resilience:**
   - Individual issue creation failure → collect in report, continue next
   - Attachment download/upload failure → collect in report, continue
   - User lookup failure → default to None (issue created without assignment)
   - Attachment failure → still create issue, skip attachments

4. **User Feedback:**
   - Warnings printed but execution continues (non-fatal)
   - Errors halt current step but may have partial state (resumable)
   - Final report summarizes all failures for manual remediation

## Cross-Cutting Concerns

**Logging:** Console-based only (no file logging)
- Progress markers: `✓ success`, `⚠ warning`, `Error: failure`
- Table output for preview data (assignee/creator status in color)
- Pagination progress (page N, cumulative count)

**Validation:**
- Linear API key verified on first call
- Jira credentials verified at startup
- Team/project mapping verified with interactive fallback
- User email mapping validated (unmatched tracked)
- Issue selection input parsed with error recovery

**Authentication:**
- Linear: API key in Authorization header (no Bearer prefix)
- Jira: Basic auth (email:token in base64)
- Both cached in client objects during session

**Concurrency:** None (synchronous, single-threaded)

**Pagination:**
- Linear: cursor-based, 50 issues/page
- Jira users: offset-based, 100 users/page
- Both implemented as loops with `hasNextPage` / `len(page) < maxResults` termination

---

*Architecture analysis: 2026-02-24*
