# Technology Stack

**Analysis Date:** 2026-02-24

## Languages

**Primary:**
- Python 3 - Single-file CLI application (`/c/own/dev/South/LinearJiraSync/linear_jira_sync.py`)

## Runtime

**Environment:**
- Python 3.x (standard library only for core utilities)

**Package Manager:**
- pip
- Lockfile: Present (requirements.txt)

## Frameworks

**Core:**
- requests 2.28.0+ - HTTP client for REST and GraphQL APIs

**Utilities:**
- Standard library: sys, json, re, base64, getpass, os, io, mimetypes, csv, datetime, typing

## Key Dependencies

**Critical:**
- requests - Handles all HTTP communication to Linear GraphQL API and Jira REST API v3
  - Used for POST requests to Linear with GraphQL queries
  - Used for GET/POST/PUT requests to Jira endpoints
  - Provides exception handling for connection errors, timeouts, and auth failures

**Infrastructure:**
- certifi - SSL/TLS certificate bundle (bundled in `.deps/` directory)
- charset_normalizer - Character encoding detection (bundled in `.deps/` directory)
- idna - Internationalized domain names support (bundled in `.deps/` directory)
- urllib3 - HTTP connection pooling (bundled via requests)

## Configuration

**Environment:**
- Credentials provided interactively via prompt at runtime
- No .env file support; uses getpass for secure token entry
- Required credentials:
  - Linear API key (from Linear → Settings → Security & access → Personal API keys)
  - Jira account email
  - Jira API token (from https://id.atlassian.com/manage-profile/security/api-tokens)

**Build:**
- No build system required; single executable Python script
- Dependencies included locally in `.deps/` directory for self-contained execution

**Configuration Files:**
- `linear_jira_mapping.json` - Runtime output mapping Linear UUIDs to Jira ticket keys
- `user_mapping.csv` - Manual user email mapping (Linear → Jira) for unmatched users
- `linear_jira_sync.py` - Contains hardcoded configuration:
  - `JIRA_URL = "https://govpilot.atlassian.net"` (line 44)
  - `TEAM_SPACE_MAP` (line 54-61) - Maps Linear teams to Jira project keys
  - `LABEL_ISSUE_TYPE_MAP` (line 65-68) - Maps Linear labels to Jira issue types
  - `TRIAGE_STATE_NAMES`, `TRIAGE_LABEL_NAMES` - Exclusion filters
  - `DEFAULT_PRIORITY_MAP` (line 82-88) - Priority translation table
  - `RELATION_TYPE_MAP` (line 90-97) - Issue link type mapping

## Platform Requirements

**Development:**
- Python 3.x installed
- Write permissions for JSON/CSV output files in working directory

**Production:**
- Deployment target: Command-line execution on any platform with Python 3
- Requires network access to:
  - Linear API at `https://api.linear.app/graphql`
  - Jira Cloud at hardcoded `JIRA_URL` (currently `https://govpilot.atlassian.net`)

---

*Stack analysis: 2026-02-24*
