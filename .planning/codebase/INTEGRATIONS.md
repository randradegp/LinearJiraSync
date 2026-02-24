# External Integrations

**Analysis Date:** 2026-02-24

## APIs & External Services

**Linear (Issue Export):**
- Linear GraphQL API at `https://api.linear.app/graphql`
  - SDK/Client: None - raw requests library with custom GraphQL implementation
  - Auth: Personal API token (Bearer not needed; passed as `Authorization` header directly, line 139)
  - Authentication function: `gql()` at line 138

**Jira Cloud (Issue Import):**
- Jira REST API v3 at `{JIRA_URL}/rest/api/3` (hardcoded base URL with project from `JIRA_URL` constant)
- Jira Agile API at `{JIRA_URL}/rest/agile/1.0`
  - SDK/Client: Custom `JiraClient` class at line 754
  - Auth: HTTP Basic Auth (email:api_token base64-encoded, line 759-760)
  - Endpoints used:
    - `/myself` - Get current user
    - `/project/search` - List projects
    - `/issue/createmeta` - Get issue types for project
    - `/field` - List custom fields
    - `/users/search` - Find users by email
    - `/issue` - Create issues
    - `/issue/{key}` - Get/update issue
    - `/issue/{key}/attachments` - Upload attachments
    - `/issue/{key}/comment` - Add comments
    - `/issue/{key}/remotelink` - Add remote links
    - `/issueLink` - Create issue links
    - `/attachment/{id}` - Get attachment metadata
    - `/attachment/content/{id}` - Download attachment content

## Data Storage

**Databases:**
- Not used - entirely file-based

**File Storage:**
- Local filesystem (CSV and JSON only)
  - `linear_jira_mapping.json` (line 79) - Stores mapping of Linear issue IDs to created Jira keys
  - `user_mapping.csv` (line 80) - Manual user email mapping for unmatched accounts
  - No remote file storage

**Caching:**
- In-memory user cache in JiraClient._user_cache (line 761, populated by resolve_account_id at line 837)
- No persistent caching

## Authentication & Identity

**Linear Auth:**
- Personal API token authentication (line 35)
- Token obtained from: Linear → Settings → Security & access → Personal API keys
- Passed in POST request header: `{"Authorization": api_key, "Content-Type": "application/json"}` (line 139)
- Interactive prompt for token entry at runtime (line 2035)

**Jira Auth:**
- HTTP Basic Authentication (email:token base64-encoded)
- Token obtained from: https://id.atlassian.com/manage-profile/security/api-tokens
- Credentials: email (line 2051) + API token (line 2055)
- Encoding: `base64.b64encode(f"{email}:{api_token}".encode())` at line 759-760
- Header format: `Authorization: Basic {base64_encoded_credentials}` (line 760)

**User Mapping:**
- Automatic: Email-based matching between Linear and Jira users
- Manual fallback: `user_mapping.csv` for unmatched users (line 80, line 1997-2008)
- Resolution priority:
  1. Load from CSV if exists
  2. Query Jira API to find user by Linear email
  3. Report unmatched users in output

## Monitoring & Observability

**Error Tracking:**
- Not integrated - errors surfaced via console output

**Logs:**
- Console-based progress reporting via print() statements
- Detailed error messages from:
  - Linear API GraphQL errors (line 157-159)
  - Jira REST API errors (line 788)
  - Connection timeouts (line 145-148, 773-776)
- Final migration report includes:
  - Failed issues list (report["failed_issues"])
  - Failed attachments (report["failed_attachments"])
  - Failed comments (report["failed_comments"])
  - Unmatched users (report["unmatched_users"])

## CI/CD & Deployment

**Hosting:**
- Local development machine (standalone CLI tool)
- No remote deployment infrastructure

**CI Pipeline:**
- Not configured - manual execution

## Environment Configuration

**Required env vars:**
- None - all credentials provided interactively at runtime

**Secrets location:**
- Provided via interactive prompts using `getpass.getpass()` for secure entry
- Not stored in files or environment variables
- Credentials kept only in memory during execution

**Hardcoded configuration:**
- Jira instance: `JIRA_URL = "https://govpilot.atlassian.net"` (line 44)
- Team mapping: `TEAM_SPACE_MAP` (line 54-61) - editable via code
- Issue type mapping: `LABEL_ISSUE_TYPE_MAP` (line 65-68)
- Priority mapping: `DEFAULT_PRIORITY_MAP` (line 82-88)
- Relation type mapping: `RELATION_TYPE_MAP` (line 90-97)

## Webhooks & Callbacks

**Incoming:**
- None - pull-only architecture

**Outgoing:**
- None - issues created via REST API calls, no webhooks triggered

## Linear API Usage Patterns

**GraphQL Queries:**
- Pagination: `first: 50` or `first: 250` with cursor-based continuation (line 179-192)
- Field filtering: Minimal fields initially fetched, then enriched per-issue
- Complexity budget: Nested collections (comments, attachments, relations) fetched separately (line 213-215)

**Data Fetched:**
- Viewers (authenticated user)
- Teams (with names and keys)
- Users (all users with displayNames)
- Projects (per team)
- Issues (with state, assignee, labels, relationships)
- Issue history/activity (line 374-427)
- Comments (line 245)
- Attachments with download URLs (line 246, 430-450)
- Relations/links between issues (line 247)

**File Attachments:**
- Downloaded from Linear URLs using requests.get() with auth header (line 434)
- Uploaded to Jira via multipart form (line 859-861)
- MIME type detection via mimetypes.guess_type() (line 858)

## Jira API Usage Patterns

**Batch Operations:**
- Issue creation: Individual POST requests (one per issue)
- Attachment upload: POST per attachment (120s timeout)
- Comments: Individual POST requests
- Issue linking: Individual POST requests

**Field Resolution:**
- Custom fields discovered at runtime via `/field` endpoint (line 819)
- Story Points field auto-detected by searching field list (line 1831-1834)
- Fields mapped: summary, description (ADF format), issue type, project, priority, assignee, reporter, due date, story points, labels, etc.

**Descriptive Format:**
- Markdown → Jira Atlassian Document Format (ADF) conversion (line 674-753)
- Image URLs extracted and embedded as Jira media (line 489-545)

---

*Integration audit: 2026-02-24*
