# Codebase Structure

**Analysis Date:** 2026-02-24

## Directory Layout

```
LinearJiraSync/
├── linear_jira_sync.py           # Main script (monolithic, ~2400 lines)
├── linear_jira_mapping.json      # Persistent state: Linear ID ↔ Jira key mapping
├── user_mapping.csv              # Optional: manual Linear user email → Jira account ID overrides
├── requirements.txt              # Python dependencies (requests>=2.28.0)
├── README.md                      # Project documentation
├── .planning/
│   └── codebase/                 # GSD codebase analysis output
│       ├── ARCHITECTURE.md
│       ├── STRUCTURE.md (this file)
│       ├── STACK.md
│       ├── INTEGRATIONS.md
│       ├── CONVENTIONS.md
│       ├── TESTING.md
│       └── CONCERNS.md
└── .deps/                        # Dependencies cache (certifi, charset_normalizer, idna, requests, urllib3)
```

## Directory Purposes

**Root Directory:**
- Purpose: Main execution context and configuration
- Contains: Executable Python script, state files, configuration
- Key files: `linear_jira_sync.py` (entry point), `linear_jira_mapping.json` (state), `user_mapping.csv` (overrides)

**.planning/codebase/:**
- Purpose: GSD framework output — codebase analysis documents
- Contains: Architecture, structure, conventions, testing patterns, concerns analysis
- Key files: ARCHITECTURE.md, STRUCTURE.md, STACK.md, INTEGRATIONS.md, CONVENTIONS.md, TESTING.md, CONCERNS.md

**.deps/:**
- Purpose: Python package cache (bundled dependencies)
- Contains: Compiled/installed versions of requests, urllib3, certifi, charset_normalizer, idna
- Generated: Yes (via pip/wheel)
- Committed: Yes (precompiled for reproducible execution)

## Key File Locations

**Entry Points:**
- `linear_jira_sync.py`: Main CLI script — run with `python linear_jira_sync.py`

**Configuration:**
- Top of `linear_jira_sync.py` (lines 40–100):
  - `JIRA_URL`: Jira Cloud base URL
  - `TEAM_SPACE_MAP`: Linear team → Jira project mapping
  - `LABEL_ISSUE_TYPE_MAP`: Label → issue type routing
  - `DEFAULT_PRIORITY_MAP`: Priority translation
  - `RELATION_TYPE_MAP`: Relation type translation
  - `TRIAGE_STATE_NAMES`, `TRIAGE_LABEL_NAMES`: Exclusion filters

**Core Logic:**

1. **Linear API Integration** (`linear_jira_sync.py` lines 104–372):
   - `gql()`: GraphQL request dispatcher
   - `linear_fetch_viewer()`, `linear_fetch_teams()`, `linear_fetch_all_users()`, `linear_fetch_projects()`: Data fetching
   - `linear_fetch_all_issues()`, `linear_fetch_project_issues()`: Paginated issue queries
   - `linear_probe_issues()`: Accessibility check
   - `linear_enrich_with_history()`: Batch enrichment (comments, attachments, history, relations)
   - `linear_download_file()`: Binary attachment download

2. **Data Transformation** (`linear_jira_sync.py` lines 489–1120):
   - `markdown_to_adf()`: Markdown → Atlassian Document Format
   - `build_description_adf()`: Issue description builder
   - `build_activity_comment_md()`: Consolidated history comment
   - `build_jira_fields()`: Complete issue field mapping
   - `determine_issue_type()`: Label-based type routing
   - `resolve_due_date()`: Date field resolution

3. **Jira Client** (`linear_jira_sync.py` lines 754–952):
   - `JiraClient.__init__()`: Authentication setup
   - `JiraClient._request()`: HTTP request wrapper with error handling
   - `JiraClient.get_myself()`, `list_projects()`, `get_issue_types_for_project()`
   - `JiraClient.create_issue()`, `upload_attachment()`, `get_media_uuid_for_attachment()`
   - `JiraClient.get_fields()`, `get_all_users()`, `resolve_account_id()`

4. **Execution Phases** (`linear_jira_sync.py` lines 1481–1912):
   - `phase_create_epics()`: Create Jira epics for Linear projects
   - `phase_create_bugs()`, `phase_create_feature_requests()`, `phase_create_stories()`: Type-specific issue creation
   - `phase_move_to_backlog()`: Move all issues to project backlog
   - `phase_upload_attachments()`: Download and re-upload attachments
   - `phase_post_activity_comments()`: Post consolidated history comments
   - `phase_create_links()`: Create issue links/relations

5. **User Mapping** (`linear_jira_sync.py` lines 1912–2000):
   - `load_user_csv()`: Read manual overrides from `user_mapping.csv`
   - `save_user_csv()`: Write discovered mappings
   - `build_user_map()`: Auto-detect via email, merge with CSV, track unmatched

6. **UI/Preview** (`linear_jira_sync.py` lines 1224–1397):
   - `print_preview_table()`: Table of issues with user status (green = mapped, red = unmapped)
   - `_preview_detail_line()`: Format single preview row
   - `parse_selection()`: Parse user input (numbers, ranges, comma-separated)
   - `apply_selection()`: Filter issues by selection

7. **Utilities** (`linear_jira_sync.py` lines 105–500):
   - `prompt()`, `prompt_secret()`: Interactive input
   - `_nodes()`: Extract "nodes" array from GraphQL response
   - `_fmt_date()`: Format ISO datetime to readable string
   - `_visible_len()`, `_truncate_ansi()`: ANSI-aware string handling
   - `extract_image_urls()`: Parse Markdown image links
   - `detect_story_points_field()`, `detect_epic_name_field()`: Jira field detection

**State Management:**
- `linear_jira_mapping.json`: Persistent mapping file
  - Format: JSON dict, Linear UUID → Jira key
  - Epic keys prefixed with `__epic__` for identification
  - Location: Project root
  - Load: `load_mapping()` (line 1446), Save: `save_mapping()` (line 1456)

- `user_mapping.csv`: Optional manual user overrides
  - Format: CSV with headers, Linear email → Jira account ID (one per line)
  - Location: Project root
  - Load: `load_user_csv()` (line 1912), Save: `save_user_csv()` (line 1931)

**Testing:**
- Not applicable (no test suite present)

## Naming Conventions

**Files:**
- Python modules: `linear_jira_sync.py` (lowercase with underscores)
- State files: `linear_jira_mapping.json`, `user_mapping.csv` (descriptive names with snake_case)
- Config: All in single file (no separate config module)

**Functions:**
- Fetch operations: `linear_fetch_*()`, `jira.get_*()` (verb_noun pattern)
- Transformation: `build_*()`, `markdown_to_adf()` (action verbs)
- Helpers: `_*()` prefix for internal utilities (underscore for privacy by convention)
- Phase execution: `phase_*()` (explicit naming for orchestration steps)

**Variables:**
- Configuration: UPPERCASE (e.g., `JIRA_URL`, `TEAM_SPACE_MAP`)
- Collections: plural nouns (e.g., `linear_users`, `all_projects_by_team`)
- Temporary: snake_case (e.g., `resp`, `since_date`, `batch`)
- Booleans: `is_*`, `has_*` prefixes (e.g., `is_triage()`)

**Types:**
- Dicts: `*_map` or `*_by_*` (e.g., `user_map`, `all_projects_by_team`)
- Lists: plural nouns (e.g., `teams`, `issues`)
- Optional: type hint with `Optional[T]`
- Enums: N/A (uses string constants and mapping dicts)

## Where to Add New Code

**New Linear Data Field:**
1. Add field to `_FIELDS_FULL` query (line 216)
2. Update `_FIELDS_SAFE` fallback (line 231)
3. Extract in `build_jira_fields()` (line 1120) and map to Jira field
4. If enrichment (comments/attachments) required, add to `linear_enrich_with_history()` (line 374)

**New Jira Field Mapping:**
1. Add logic to `build_jira_fields()` (line 1120)
2. Use `detect_*_field()` helpers if custom field detection needed (line 1092)
3. Add to field list passed to `phase_create_*()` functions

**New Issue Type or Filter:**
1. Add entry to `LABEL_ISSUE_TYPE_MAP` (line 65)
2. Update `determine_issue_type()` (line 963) if special handling needed
3. Create new `phase_create_*()` function (line 1741 pattern)
4. Call phase in `main()` orchestration (line 2015)

**New User Matching Strategy:**
1. Modify `build_user_map()` (line 1942) logic
2. Update `save_user_csv()` format if different override structure needed
3. Test with `load_user_csv()` (line 1912)

**New API Integration (e.g., webhooks, monitoring):**
1. Add new client class (pattern: `JiraClient`, line 754)
2. Implement in dedicated functions, not embedded in main
3. Call from appropriate phase or main orchestration

**Utilities and Helpers:**
- ANSI formatting: `_C_*` constants (line 446) and `_truncate_ansi()` (line 462)
- String parsing: Add to utilities section (line 105) before main logic
- GraphQL query builders: Follow `_FIELDS_*` pattern (line 216)

## Special Directories

**.deps/ (dependencies cache):**
- Purpose: Pre-installed Python packages for reproducible execution
- Generated: Yes (via pip wheel download)
- Committed: Yes (included in repo)
- Note: Allows offline execution if needed

## Configuration Editing

**Most common edits:**

1. **Change Jira URL:**
   - Edit: `JIRA_URL` (line 44)
   - Type: String
   - Example: `"https://mycompany.atlassian.net"`

2. **Map teams:**
   - Edit: `TEAM_SPACE_MAP` (line 54)
   - Type: Dict
   - Example: `{"Desktop": "DSK", "Web": "WEB"}`

3. **Filter triage (exclude states/labels):**
   - Edit: `TRIAGE_STATE_NAMES`, `TRIAGE_LABEL_NAMES` (lines 73–77)
   - Type: Set
   - Example: `{"Triage", "Backlog"}`

4. **Add issue type routing:**
   - Edit: `LABEL_ISSUE_TYPE_MAP` (line 65)
   - Type: Dict
   - Example: `{"Enhancement": "Story", "Blocker": "Bug"}`

---

*Structure analysis: 2026-02-24*
