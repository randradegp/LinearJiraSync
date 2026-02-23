#!/usr/bin/env python3
"""
Linear → Jira Cloud Unified Sync
=================================
Single-script migration: fetches everything from Linear and pushes to Jira Cloud.

What gets migrated:
  Linear projects        → Jira Epics (backlog of the mapped project)
  Linear label "Bug"     → Jira issue type "Bug"
  Linear "Feature Request" label → Jira issue type "Story"
  Everything else        → Jira issue type "Story"
  Triage items           → excluded

Field mapping:
  Linear estimate        → Jira story points (auto-detected field)
  Linear SLI / dueDate   → Jira due date
  Linear assignee        → Jira assignee (matched by email)
  Linear creator         → Jira reporter (matched by email)
  Linear history+comments → One consolidated Jira comment per issue
  Linear attachments     → Uploaded directly as Jira attachments

Usage:
    python linear_jira_sync.py
"""

import sys
import json
import re
import base64
import getpass
import os
import io
import mimetypes
import csv
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION  —  the only section you need to edit
# ═════════════════════════════════════════════════════════════════════════════

JIRA_URL = "https://govpilot.atlassian.net"

# ---------------------------------------------------------------------------
# TEAM_SPACE_MAP
# Maps each Linear team name → its destination Jira project.
# The Jira value can be the project KEY (e.g. "INT") or the display NAME
# (e.g. "Integration") — the script resolves whichever you provide.
# Only teams listed here are fetched from Linear and migrated.
# All other Linear teams are ignored.
# ---------------------------------------------------------------------------
TEAM_SPACE_MAP: dict = {
    # Linear team name   →   Jira project key or display name
    "Desktop":               "Desktop",
    #"Web":                   "Core Team",
    #"Security": "Security",
    #"DevOps": "DevOps",
    #"Onboarding": "Onboarding"
}
# ═════════════════════════════════════════════════════════════════════════════

# Linear label name  →  Jira issue type name
LABEL_ISSUE_TYPE_MAP: dict = {
    "Bug":             "Bug",
    "Feature Request": "Story",
}
DEFAULT_ISSUE_TYPE = "Story"

# Issues whose state name (case-insensitive) matches any of these are excluded
# (empty = include everything, including triage)
TRIAGE_STATE_NAMES = set()

# Issues that carry any of these label names (case-insensitive) are excluded
# (empty = include everything, including triage)
TRIAGE_LABEL_NAMES = set()

MAPPING_FILE      = "linear_jira_mapping.json"
USER_MAPPING_FILE = "user_mapping.csv"

DEFAULT_PRIORITY_MAP: dict = {
    "Urgent":      "Highest",
    "High":        "High",
    "Medium":      "Medium",
    "Low":         "Low",
    "No priority": "Medium",
}

RELATION_TYPE_MAP: dict = {
    "blocks":       "Blocks",
    "blocked_by":   "Blocks",
    "duplicate_of": "Duplicates",
    "duplicate_by": "Duplicates",
    "related_to":   "Relates",
    "relates_to":   "Relates",
}

LINEAR_API_URL = "https://api.linear.app/graphql"

_PRIORITY_LABELS: dict = {0: "No priority", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def prompt(message: str, default: str = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{message}{suffix}: ").strip()
    return value if value else (default or "")


def prompt_secret(message: str) -> str:
    return getpass.getpass(f"{message}: ").strip()


def _nodes(obj, key: str = "nodes") -> list:
    if not obj:
        return []
    return obj.get(key) or []


def _fmt_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return iso


# ─────────────────────────────────────────────────────────────────────────────
# Linear GraphQL client
# ─────────────────────────────────────────────────────────────────────────────

def gql(api_key: str, query: str, variables: dict = None) -> dict:
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
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
    if "errors" in body:
        msgs = [e.get("message", str(e)) for e in body["errors"]]
        raise Exception("Linear GraphQL errors: " + " | ".join(msgs))
    if not resp.ok:
        raise Exception(f"Linear API {resp.status_code}: {json.dumps(body)[:300]}")
    return body["data"]


def linear_fetch_viewer(api_key: str) -> dict:
    return gql(api_key, "query { viewer { id name email } }")["viewer"]


def linear_fetch_teams(api_key: str) -> list:
    data = gql(api_key, "query { teams(first: 50) { nodes { id name key description } } }")
    return data["teams"]["nodes"]


def linear_fetch_all_users(api_key: str) -> list:
    """Fetch all Linear users with pagination (max 250 per page)."""
    users = []
    cursor: Optional[str] = None
    while True:
        after_clause = f', after: "{cursor}"' if cursor else ""
        data = gql(api_key, f"""
        query {{
            users(first: 250{after_clause}) {{
                nodes {{ id name email displayName }}
                pageInfo {{ hasNextPage endCursor }}
            }}
        }}
        """)
        page = data["users"]
        users.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return users


def linear_fetch_projects(api_key: str, team_id: str) -> list:
    data = gql(api_key, """
    query($teamId: String!) {
        team(id: $teamId) {
            projects(first: 250) {
                nodes {
                    id name description state url
                    lead { id name email displayName }
                }
            }
        }
    }
    """, {"teamId": team_id})
    return data["team"]["projects"]["nodes"]


# Issue fields — base (always available)
_FIELDS_BASE = """
    id identifier title description priority priorityLabel
    estimate dueDate slaStartedAt slaDueAt completedAt canceledAt archivedAt createdAt updatedAt
    url branchName
    state        { id name type color }
    assignee     { id name email displayName }
    creator      { id name email displayName }
    labels       { nodes { id name color } }
    project      { id name state description url }
    issueType    { id name }
    team         { id name key }
    parent       { id identifier title }
    children     { nodes { id identifier title state { name type } } }
    attachments  { nodes { id title subtitle url metadata createdAt creator { name email } } }
    comments     { nodes { id body createdAt updatedAt user { id name email displayName } } }
    relations    { nodes { id type relatedIssue { id identifier title state { name } } } }
"""

# History fields fetched separately (too expensive to inline with issue list query)
_HISTORY_NODE_FIELDS = (
    "id createdAt "
    "actor { id name email } "
    "fromState { name } "
    "toState { name } "
    "fromAssignee { name email } "
    "toAssignee { name email } "
    "fromPriority toPriority "
    "addedLabels { id name } "
    "removedLabels { id name }"
)


def _build_issue_query(since_str: Optional[str] = None) -> str:
    if since_str:
        issues_args = (
            'filter: { createdAt: { gte: "' + since_str + '" } }, '
            'first: 25, after: $cursor, orderBy: createdAt'
        )
    else:
        issues_args = "first: 25, after: $cursor, orderBy: createdAt"
    return (
        "query($teamId: String!, $cursor: String) {"
        "  team(id: $teamId) {"
        "    issues(" + issues_args + ") {"
        "      pageInfo { hasNextPage endCursor }"
        "      nodes {" + _FIELDS_BASE + "}"
        "    }"
        "  }"
        "}"
    )


def _paginate_issues(api_key: str, team_id: str, query: str) -> list:
    issues: list = []
    cursor: Optional[str] = None
    page = 1
    while True:
        print(f"    Page {page} ({len(issues)} issues collected)…")
        variables: dict = {"teamId": team_id}
        if cursor:
            variables["cursor"] = cursor
        data = gql(api_key, query, variables)
        result = data["team"]["issues"]
        issues.extend(result["nodes"])
        if not result["pageInfo"]["hasNextPage"]:
            break
        cursor = result["pageInfo"]["endCursor"]
        page += 1
    return issues


def linear_fetch_all_issues(api_key: str, team_id: str,
                             since_date: Optional[datetime] = None) -> list:
    """Fetch issues using only base fields (history is enriched separately)."""
    since_str = since_date.strftime("%Y-%m-%dT%H:%M:%S.000Z") if since_date else None
    query = _build_issue_query(since_str)
    return _paginate_issues(api_key, team_id, query)


def linear_enrich_with_history(api_key: str, issues: list,
                                batch_size: int = 8) -> None:
    """
    Fetch issue history in batches (using GraphQL aliases) and merge into
    each issue dict in-place.  Silently skips if the API doesn't support it.
    """
    if not issues:
        return
    print(f"    Enriching {len(issues)} issue(s) with history…")
    enriched = 0

    for start in range(0, len(issues), batch_size):
        batch = issues[start:start + batch_size]
        # Build a query with one alias per issue
        alias_lines = []
        for i, iss in enumerate(batch):
            alias_lines.append(
                "h" + str(i) + ': issue(id: "' + iss["id"] + '") {'
                "  history(first: 50) {"
                "    nodes { " + _HISTORY_NODE_FIELDS + " }"
                "  }"
                "}"
            )
        query = "query { " + " ".join(alias_lines) + " }"
        try:
            data = gql(api_key, query)
        except Exception as exc:
            # History not supported or too complex — skip silently
            print(f"    (history skipped: {str(exc)[:80]})")
            return
        for i, iss in enumerate(batch):
            hist = (data.get("h" + str(i)) or {}).get("history") or {}
            iss["history"] = {"nodes": hist.get("nodes") or []}
            enriched += 1

    print(f"    ✓ History enriched for {enriched} issue(s)")


def linear_download_file(url: str, api_key: str) -> Optional[bytes]:
    """Download a Linear attachment. Tries with auth header first, then without."""
    for hdrs in [{"Authorization": api_key}, {}]:
        try:
            resp = requests.get(url, headers=hdrs, timeout=60)
            if resp.status_code == 200:
                return resp.content
        except Exception:
            pass
    return None


_IMAGE_PATTERN = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
_IMAGE_SPLIT_PATTERN = re.compile(r'(!\[[^\]]*\]\([^)]+\))')


def extract_image_urls(markdown: str) -> list:
    """Return list of (alt, url) tuples for all Markdown images in text."""
    if not markdown:
        return []
    return _IMAGE_PATTERN.findall(markdown)


def build_description_adf_with_media(markdown: str,
                                      media_map: Optional[dict] = None) -> dict:
    """
    Convert markdown to ADF, splitting at image boundaries.
    Each image becomes a mediaSingle / media node.

    media_map values:
      ("file", uuid, collection)  → type:file — renders inline natively.
      ("external", url)           → type:external fallback (Jira-hosted URL).
      Missing entry               → image is skipped (no Linear URLs ever used).
    """
    if not markdown:
        return {"version": 1, "type": "doc", "content": []}

    remap = media_map or {}
    content = []
    for seg in _IMAGE_SPLIT_PATTERN.split(markdown):
        if not seg:
            continue
        img_match = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', seg)
        if img_match:
            original_url = img_match.group(2)
            entry = remap.get(original_url)
            if entry and entry[0] == "file":
                _, uuid, collection = entry
                media_attrs: dict = {
                    "id":         uuid,
                    "type":       "file",
                    "collection": collection,
                }
                single_attrs = {"layout": "center", "width": 760, "widthType": "pixel"}
            elif entry and entry[0] == "external":
                media_attrs  = {"type": "external", "url": entry[1]}
                single_attrs = {"layout": "center"}
            else:
                # No Jira-hosted copy available — skip the image entirely
                # so no Linear URL ever leaks into the Jira description.
                continue
            content.append({
                "type": "mediaSingle",
                "attrs": single_attrs,
                "content": [{"type": "media", "attrs": media_attrs}],
            })
        else:
            seg_adf = markdown_to_adf(seg)
            content.extend(seg_adf.get("content", []))

    return {"version": 1, "type": "doc", "content": content}


def upload_images_and_build_description(
    description_md: str,
    jira_key: str,
    jira_issue_id: str,
    identifier: str,
    jira: "JiraClient",
    linear_key: str,
) -> dict:
    """
    1. Download every inline image from the Markdown description from Linear.
    2. Upload each one to Jira as an attachment (permanent copy on Jira).
    3. Build and return an ADF description where every image is embedded inline.

    jira_issue_id: the numeric Jira issue ID (e.g. "12345") returned by
                   create_issue — used to set the correct media collection so
                   Jira renders images at full size without a click.
    """
    collection = f"contentId-{jira_issue_id}" if jira_issue_id else ""
    image_urls = extract_image_urls(description_md)
    if not image_urls:
        return build_description_adf_with_media(description_md, {})

    print(f"  INFO  {identifier}  found {len(image_urls)} image(s) — downloading & uploading to {jira_key} …")

    # media_map values:
    #   ("file", uuid, filename, mime, size, collection)  — renders inline natively
    #   ("external", jira_content_url)                    — Jira-hosted fallback
    media_map: dict = {}

    for alt, url in image_urls:
        print(f"  INFO  {identifier}  downloading: {url[:80]}")
        file_bytes = linear_download_file(url, linear_key)
        if file_bytes is None:
            print(f"  WARN  {identifier}  download FAILED — image will be omitted from description")
            continue
        print(f"  INFO  {identifier}  downloaded {len(file_bytes)} bytes")
        filename = os.path.basename(url.split("?")[0])
        if not filename or "." not in filename:
            safe_alt = re.sub(r"[^\w\-.]", "_", alt or "image")[:40]
            filename = safe_alt + ".png"
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        size = len(file_bytes)
        try:
            att = jira.upload_attachment(jira_key, filename, file_bytes)
            if not att:
                print(f"  WARN  {identifier}  upload returned no data for {filename} — image omitted")
                continue

            att_id      = att.get("id", "")
            content_url = att.get("content", "")
            print(f"  INFO  {identifier}  uploaded {filename} (att_id={att_id})")

            uuid = jira.get_media_uuid_for_attachment(att_id) if att_id else None
            if uuid:
                media_map[url] = ("file", uuid, collection)
                print(f"  OK    {identifier}  image → {jira_key}: {filename[:40]} uuid={uuid[:8]}… [inline]")
            elif content_url:
                media_map[url] = ("external", content_url)
                print(f"  WARN  {identifier}  image → {jira_key}: {filename[:40]} uuid not found — using content URL (may not render)")
            else:
                print(f"  WARN  {identifier}  no att_id or content URL for {filename} — image omitted")
        except Exception as exc:
            print(f"  WARN  {identifier}  image upload failed ({filename}): {exc} — image omitted")

    return build_description_adf_with_media(description_md, media_map)


# ─────────────────────────────────────────────────────────────────────────────
# Markdown → Atlassian Document Format (ADF)
# ─────────────────────────────────────────────────────────────────────────────

def _inline_marks(text: str) -> list:
    nodes = []
    pattern = re.compile(
        r'(\*\*\*(.+?)\*\*\*)'
        r'|(\*\*(.+?)\*\*)'
        r'|(\*(.+?)\*)'
        r'|(_(.+?)_)'
        r'|(~~(.+?)~~)'
        r'|(`(.+?)`)'
        r'|(!\[([^\]]*)\]\(([^)]+)\))'   # image  ![alt](url)  — must come before link
        r'|(\[(.+?)\]\((.+?)\))',         # link   [text](url)
        re.DOTALL,
    )
    last_end = 0
    for m in pattern.finditer(text):
        if m.start() > last_end:
            nodes.append({"type": "text", "text": text[last_end:m.start()]})
        if m.group(1):
            nodes.append({"type": "text", "text": m.group(2),
                          "marks": [{"type": "strong"}, {"type": "em"}]})
        elif m.group(3):
            nodes.append({"type": "text", "text": m.group(4),
                          "marks": [{"type": "strong"}]})
        elif m.group(5):
            nodes.append({"type": "text", "text": m.group(6),
                          "marks": [{"type": "em"}]})
        elif m.group(7):
            nodes.append({"type": "text", "text": m.group(8),
                          "marks": [{"type": "em"}]})
        elif m.group(9):
            nodes.append({"type": "text", "text": m.group(10),
                          "marks": [{"type": "strike"}]})
        elif m.group(11):
            nodes.append({"type": "text", "text": m.group(12),
                          "marks": [{"type": "code"}]})
        elif m.group(13):  # image — render as a link so the URL survives
            alt = m.group(14) or "image"
            url = m.group(15)
            nodes.append({"type": "text", "text": f"[image: {alt}]",
                          "marks": [{"type": "link", "attrs": {"href": url}}]})
        elif m.group(16):  # plain link
            nodes.append({"type": "text", "text": m.group(17),
                          "marks": [{"type": "link", "attrs": {"href": m.group(18)}}]})
        last_end = m.end()
    if last_end < len(text):
        nodes.append({"type": "text", "text": text[last_end:]})
    return nodes or [{"type": "text", "text": text}]


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "content": _inline_marks(text)}


def _list_item(text: str) -> dict:
    return {"type": "listItem", "content": [_paragraph(text)]}


def markdown_to_adf(markdown: str) -> dict:
    if not markdown:
        return {"version": 1, "type": "doc", "content": []}
    content = []
    lines = markdown.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        fence = re.match(r'^(`{3,}|~{3,})(.*)', line)
        if fence:
            fc, fl = fence.group(1)[0], len(fence.group(1))
            lang = fence.group(2).strip()
            code_lines = []
            i += 1
            while i < n:
                if re.match(rf'^{re.escape(fc)}{{{fl},}}$', lines[i].strip()):
                    i += 1
                    break
                code_lines.append(lines[i])
                i += 1
            content.append({"type": "codeBlock",
                            "attrs": {"language": lang or "text"},
                            "content": [{"type": "text", "text": "\n".join(code_lines)}]})
            continue
        hm = re.match(r'^(#{1,6})\s+(.*)', line)
        if hm:
            content.append({"type": "heading",
                            "attrs": {"level": min(len(hm.group(1)), 6)},
                            "content": _inline_marks(hm.group(2).strip())})
            i += 1
            continue
        if re.match(r'^\s*(?:---+|\*\*\*+|___+)\s*$', line):
            content.append({"type": "rule"})
            i += 1
            continue
        if line.startswith("> ") or line == ">":
            qlines = []
            while i < n and (lines[i].startswith("> ") or lines[i] == ">"):
                qlines.append(lines[i][2:] if lines[i].startswith("> ") else "")
                i += 1
            inner = markdown_to_adf("\n".join(qlines))
            content.append({"type": "blockquote", "content": inner.get("content", [])})
            continue
        if re.match(r'^\s*[-*+] ', line):
            items = []
            while i < n and re.match(r'^\s*[-*+] ', lines[i]):
                items.append(_list_item(re.sub(r'^\s*[-*+] ', '', lines[i])))
                i += 1
            content.append({"type": "bulletList", "content": items})
            continue
        if re.match(r'^\s*\d+\.\s+', line):
            items = []
            while i < n and re.match(r'^\s*\d+\.\s+', lines[i]):
                items.append(_list_item(re.sub(r'^\s*\d+\.\s+', '', lines[i])))
                i += 1
            content.append({"type": "orderedList", "content": items})
            continue
        if not line.strip():
            i += 1
            continue
        para_lines = []
        while i < n and lines[i].strip():
            l = lines[i]
            if (re.match(r'^(`{3,}|~{3,})', l) or re.match(r'^#{1,6}\s', l)
                    or re.match(r'^\s*(?:---+|\*\*\*+|___+)\s*$', l)
                    or l.startswith("> ")
                    or re.match(r'^\s*[-*+] ', l)
                    or re.match(r'^\s*\d+\.\s+', l)):
                break
            para_lines.append(l)
            i += 1
        if para_lines:
            content.append(_paragraph(" ".join(para_lines)))
    return {"version": 1, "type": "doc", "content": content}


# ─────────────────────────────────────────────────────────────────────────────
# Jira REST API v3 client
# ─────────────────────────────────────────────────────────────────────────────

class JiraClient:
    def __init__(self, email: str, api_token: str) -> None:
        base = JIRA_URL.rstrip("/")
        self.base       = base + "/rest/api/3"
        self.agile_base = base + "/rest/agile/1.0"
        raw = f"{email}:{api_token}".encode()
        self._auth = "Basic " + base64.b64encode(raw).decode()
        self._user_cache: dict = {}

    def _request(self, method: str, path: str, *,
                 json_body=None, params=None,
                 expected=(200, 201)) -> Optional[dict]:
        url = f"{self.base}/{path.lstrip('/')}"
        headers = {"Authorization": self._auth, "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            resp = requests.request(method, url, headers=headers,
                                    json=json_body, params=params, timeout=60)
        except requests.exceptions.ConnectionError:
            raise Exception(f"Connection error: {url}")
        except requests.exceptions.Timeout:
            raise Exception(f"Timeout: {url}")
        if resp.status_code == 401:
            raise Exception("Jira authentication failed (401).")
        if resp.status_code == 403:
            raise Exception(f"Jira permission denied (403): {method} {path}")
        if resp.status_code == 204:
            return None
        if resp.status_code not in expected:
            try:
                msg = json.dumps(resp.json())[:400]
            except Exception:
                msg = resp.text[:400]
            raise Exception(f"Jira {resp.status_code} {method} {path}: {msg}")
        return resp.json() if resp.content else None

    def get_myself(self) -> dict:
        return self._request("GET", "/myself")

    def get_project(self, key: str) -> dict:
        return self._request("GET", f"/project/{key}")

    def list_projects(self, max_results: int = 200) -> list:
        data = self._request("GET", "/project/search",
                             params={"maxResults": max_results, "orderBy": "key"})
        return data.get("values", []) if data else []

    def get_issue_types_for_project(self, key: str) -> list:
        data = self._request("GET", "/issue/createmeta",
                             params={"projectKeys": key, "expand": "projects.issuetypes"})
        if not data:
            return []
        projects = data.get("projects", [])
        return projects[0].get("issuetypes", []) if projects else []

    def issue_exists(self, key: str) -> bool:
        """Return True if the Jira issue key exists (False on 404 or any error)."""
        try:
            self._request("GET", f"/issue/{key}", params={"fields": "summary"},
                          expected=(200,))
            return True
        except Exception:
            return False

    def get_fields(self) -> list:
        return self._request("GET", "/field") or []

    def get_all_users(self) -> list:
        users = []
        start = 0
        while True:
            page = self._request("GET", "/users/search",
                                 params={"query": "", "maxResults": 100, "startAt": start}) or []
            if not page:
                break
            users.extend(page)
            if len(page) < 100:
                break
            start += len(page)
        return users

    def resolve_account_id(self, email: str) -> Optional[str]:
        if email in self._user_cache:
            return self._user_cache[email]
        try:
            results = self._request("GET", "/user/search", params={"query": email}) or []
            aid = results[0]["accountId"] if results else None
        except Exception:
            aid = None
        self._user_cache[email] = aid
        return aid

    def create_issue(self, fields: dict) -> dict:
        return self._request("POST", "/issue", json_body={"fields": fields})

    def upload_attachment(self, issue_key: str, filename: str, content: bytes) -> Optional[dict]:
        """
        Upload a file attachment and return the Jira attachment object, e.g.:
          {"id": "10001", "content": "https://…/rest/api/3/attachment/content/10001", …}
        Raises on HTTP error; returns None if the response body is unexpected.
        """
        url = f"{self.base}/issue/{issue_key}/attachments"
        headers = {"Authorization": self._auth, "X-Atlassian-Token": "no-check"}
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        resp = requests.post(url, headers=headers,
                             files={"file": (filename, io.BytesIO(content), mime)},
                             timeout=120)
        if resp.status_code not in (200, 201):
            raise Exception(f"Upload failed ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return None

    def get_media_uuid_for_attachment(self, att_id: str) -> Optional[str]:
        """
        Try every available method to get the Atlassian Media UUID for an attachment.
        1. GET /attachment/{id}         → check mediaApiFileId field
        2. GET /attachment/content/{id} → no redirect, check Location header
        3. GET /attachment/content/{id} → follow redirects, check final URL + history
        Returns the UUID string, or None if all methods fail.
        """
        _UUID_RE = re.compile(
            r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
            re.IGNORECASE,
        )

        # Method 1: attachment metadata API (cleanest — no redirect tricks needed)
        try:
            meta = self._request("GET", f"/attachment/{att_id}", expected=(200,))
            if meta:
                uuid = (meta.get("mediaApiFileId") or "").strip()
                if uuid:
                    return uuid
        except Exception:
            pass

        # Method 2 + 3: follow (or not) the redirect from the content URL
        content_url = f"{self.base}/attachment/content/{att_id}"
        try:
            # No-follow: check Location header directly
            r1 = requests.get(content_url, headers={"Authorization": self._auth},
                               allow_redirects=False, timeout=30)
            location = r1.headers.get("Location", "")
            m = _UUID_RE.search(location)
            if m:
                return m.group(0)
        except Exception:
            pass

        try:
            # Follow all redirects: UUID lives in the final CDN URL
            r2 = requests.get(content_url, headers={"Authorization": self._auth},
                               allow_redirects=True, timeout=30)
            for candidate in [r2.url] + [h.headers.get("Location", "") for h in r2.history]:
                m = _UUID_RE.search(candidate or "")
                if m:
                    return m.group(0)
        except Exception:
            pass

        return None

    def update_issue(self, issue_key: str, fields: dict) -> None:
        """Update fields on an existing issue (PUT). Jira returns 204 on success."""
        self._request("PUT", f"/issue/{issue_key}",
                      json_body={"fields": fields}, expected=(200, 204))

    def add_comment(self, issue_key: str, adf_body: dict) -> None:
        self._request("POST", f"/issue/{issue_key}/comment", json_body={"body": adf_body})

    def add_remote_link(self, issue_key: str, title: str, url: str) -> None:
        self._request("POST", f"/issue/{issue_key}/remotelink",
                      json_body={"object": {"url": url, "title": title}})

    def create_issue_link(self, link_type: str, outward_key: str, inward_key: str) -> None:
        self._request("POST", "/issueLink", expected=(200, 201, 204),
                      json_body={"type": {"name": link_type},
                                 "outwardIssue": {"key": outward_key},
                                 "inwardIssue":  {"key": inward_key}})

    def move_to_backlog(self, issue_keys: list) -> None:
        if not issue_keys:
            return
        url = f"{self.agile_base}/backlog/issue"
        headers = {"Authorization": self._auth,
                   "Content-Type": "application/json",
                   "Accept": "application/json"}
        resp = requests.post(url, headers=headers,
                             json={"issues": issue_keys}, timeout=60)
        if resp.status_code not in (200, 204):
            raise Exception(f"move_to_backlog failed ({resp.status_code}): {resp.text[:200]}")


# ─────────────────────────────────────────────────────────────────────────────
# Issue classification helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_triage(issue: dict) -> bool:
    state_name = ((issue.get("state") or {}).get("name") or "").lower()
    if state_name in TRIAGE_STATE_NAMES:
        return True
    for lbl in _nodes(issue.get("labels")):
        if (lbl.get("name") or "").lower() in TRIAGE_LABEL_NAMES:
            return True
    return False


def determine_issue_type(issue: dict) -> str:
    # Check labels first
    for lbl in _nodes(issue.get("labels")):
        name = lbl.get("name", "")
        if name in LABEL_ISSUE_TYPE_MAP:
            return LABEL_ISSUE_TYPE_MAP[name]
    # Fall back to Linear's native issue type field
    issue_type_name = ((issue.get("issueType") or {}).get("name") or "").strip()
    if issue_type_name in LABEL_ISSUE_TYPE_MAP:
        return LABEL_ISSUE_TYPE_MAP[issue_type_name]
    return DEFAULT_ISSUE_TYPE


def _parse_iso_to_date(s: str) -> Optional[str]:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def resolve_due_date(issue: dict) -> Optional[str]:
    """Return YYYY-MM-DD due date from dueDate, Linear SLA, or SLI custom field."""
    if issue.get("dueDate"):
        return issue["dueDate"]
    # Linear SLA due date
    sla_due = issue.get("slaDueAt")
    if sla_due:
        result = _parse_iso_to_date(sla_due)
        if result:
            return result
    # Check custom fields for SLI / SLA indicators
    for cfv in (issue.get("customFieldValues") or []):
        cf = cfv.get("customField") or {}
        name = (cf.get("name") or cf.get("key") or "").lower()
        if "sli" in name or "sla" in name or "service level" in name:
            val = cfv.get("value")
            if val:
                # Try as ISO date string
                result = _parse_iso_to_date(str(val))
                if result:
                    return result
                # Try as float days from creation
                try:
                    created = datetime.fromisoformat(
                        issue["createdAt"].replace("Z", "+00:00"))
                    return (created + timedelta(days=float(val))).strftime("%Y-%m-%d")
                except Exception:
                    pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Consolidated activity comment builder
# ─────────────────────────────────────────────────────────────────────────────

def build_activity_comment_md(issue: dict) -> str:
    """
    Build one Markdown string capturing all Linear activity:
    history events (state/assignee/priority/label changes) and comments,
    sorted chronologically.
    """
    identifier = issue.get("identifier", "?")
    url = issue.get("url", "")

    lines = [f"## Linear Activity — {identifier}"]
    if url:
        lines.append(f"Original: [{identifier}]({url})")
    lines += ["", "---", ""]

    events: list = []  # list of (iso_ts, markdown_text)

    # History events
    for h in _nodes(issue.get("history")):
        ts    = h.get("createdAt", "")
        actor = (h.get("actor") or {}).get("name", "Unknown")
        parts = []
        if h.get("fromState") or h.get("toState"):
            frm = (h.get("fromState") or {}).get("name", "?")
            to  = (h.get("toState")   or {}).get("name", "?")
            parts.append(f"Status: **{frm}** → **{to}**")
        if h.get("fromAssignee") or h.get("toAssignee"):
            frm = (h.get("fromAssignee") or {}).get("name", "none")
            to  = (h.get("toAssignee")   or {}).get("name", "none")
            parts.append(f"Assignee: **{frm}** → **{to}**")
        fp = h.get("fromPriority")
        tp = h.get("toPriority")
        if fp is not None or tp is not None:
            parts.append(
                f"Priority: **{_PRIORITY_LABELS.get(fp,'?')}** → **{_PRIORITY_LABELS.get(tp,'?')}**")
        # addedLabels/removedLabels are direct arrays in Linear (no nodes wrapper)
        added   = [l["name"] for l in (h.get("addedLabels")   or [])]
        removed = [l["name"] for l in (h.get("removedLabels") or [])]
        if added:
            parts.append(f"Labels added: {', '.join(added)}")
        if removed:
            parts.append(f"Labels removed: {', '.join(removed)}")
        if parts:
            events.append((ts,
                f"**{_fmt_date(ts)}** _(by {actor})_ — " + " | ".join(parts)))

    # Comments
    for c in _nodes(issue.get("comments")):
        ts   = c.get("createdAt", "")
        user = (c.get("user") or {}).get("name", "Unknown")
        body = (c.get("body") or "").strip()
        events.append((ts,
            f"**{_fmt_date(ts)}** — **{user}** commented:\n\n{body}"))

    events.sort(key=lambda e: e[0])

    if not events:
        lines.append("_No activity history._")
    else:
        for _, text in events:
            lines.append(text)
            lines += ["", "---", ""]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Jira field helpers
# ─────────────────────────────────────────────────────────────────────────────

def _detect_field_by_keywords(fields: list, substrings: list,
                               exact: frozenset = frozenset()) -> Optional[str]:
    for f in fields:
        name = (f.get("name") or "").lower().strip()
        if any(kw in name for kw in substrings) or name in exact:
            return f["id"]
    return None


def detect_story_points_field(fields: list) -> Optional[str]:
    # Exact match only for short abbreviations (avoid "sp" as substring — it matches "responders")
    return _detect_field_by_keywords(fields,
        ["story point", "story_point", "storypoint"],
        frozenset({"sp", "s.p.", "story pts", "story pt"}))


def detect_epic_name_field(fields: list) -> Optional[str]:
    return _detect_field_by_keywords(fields, ["epic name", "epic_name", "epicname"])


def build_description_adf(issue: dict) -> dict:
    """Convert the Linear issue/project description to ADF (no added header)."""
    body_md = (issue.get("description") or "").strip()
    if not body_md:
        return {"version": 1, "type": "doc", "content": []}
    return markdown_to_adf(body_md)


def build_jira_fields(
    issue:           dict,
    project_key:     str,
    issue_type:      str,
    priority_map:    dict,
    sp_field_id:     Optional[str],
    epic_name_field: Optional[str],
    epic_key:        Optional[str],
    assignee_map:    dict,
    reporter_map:    dict,
    is_epic:         bool = False,
) -> dict:
    fields: dict = {}
    title = (issue.get("title") or "Untitled").strip()
    fields["summary"]   = title[:250] + ("…" if len(title) > 250 else "")
    fields["project"]   = {"key": project_key}
    fields["issuetype"] = {"name": issue_type}
    # Description is NOT set here — it is applied after creation via update_issue
    # so that inline images are uploaded to Jira first (no Linear URLs ever sent).

    # Epic name (classic Jira projects require this when creating Epics)
    if is_epic and epic_name_field:
        fields[epic_name_field] = title[:250]

    # Link to parent Epic
    if epic_key and not is_epic:
        fields["parent"] = {"key": epic_key}

    # Priority
    linear_p = issue.get("priorityLabel") or "No priority"
    fields["priority"] = {"name": priority_map.get(linear_p, "Medium")}

    # Story points (Linear estimate)
    if sp_field_id and issue.get("estimate") is not None:
        fields[sp_field_id] = float(issue["estimate"])

    # Due date (dueDate or SLI custom field)
    due = resolve_due_date(issue)
    if due:
        fields["duedate"] = due

    # Labels (traceability + non-type Linear labels)
    type_labels = {k.lower() for k in LABEL_ISSUE_TYPE_MAP} | TRIAGE_LABEL_NAMES
    identifier  = issue.get("identifier", "")
    jira_labels = []
    if identifier:
        jira_labels.append(f"linear-{identifier}")
    for lbl in _nodes(issue.get("labels")):
        name = (lbl.get("name") or "").strip()
        if name and name.lower() not in type_labels:
            jira_labels.append(name.replace(" ", "-"))
    if jira_labels:
        fields["labels"] = jira_labels

    assignee = issue.get("assignee")
    if assignee:
        ae = (assignee.get("email") or "").lower()
        aid = assignee_map.get(ae) if ae else None
        print(f"  ASSIGNEE  {issue.get('identifier') or issue.get('title','?')!r:<20}"
              f"  linear={ae or '(none)'}  mapped={'YES → '+aid[:8]+'…' if aid else 'NO'}")
        if aid:
            fields["assignee"] = {"accountId": aid}
    else:
        print(f"  ASSIGNEE  {issue.get('identifier') or issue.get('title','?')!r:<20}"
              f"  linear=(none)  mapped=NO")

    creator = issue.get("creator")
    if creator and creator.get("email"):
        rid = reporter_map.get((creator["email"] or "").lower())
        if rid:
            fields["reporter"] = {"accountId": rid}

    return fields


# ─────────────────────────────────────────────────────────────────────────────
# Preview table
# ─────────────────────────────────────────────────────────────────────────────

def build_preview_items(
    mapped_teams:         list,
    all_issues_by_team:   dict,
    all_projects_by_team: dict,
) -> list:
    """
    Return a flat numbered list of every item that would be migrated.
    Each entry: {num, kind, team, project_key, item, is_project}
    """
    items = []
    num   = 1
    for team in mapped_teams:
        tname       = team["name"]
        project_key = TEAM_SPACE_MAP[tname]
        for proj in all_projects_by_team.get(tname, []):
            items.append({"num": num, "kind": "Epic", "team": tname,
                          "project_key": project_key, "item": proj, "is_project": True})
            num += 1
        for iss in all_issues_by_team.get(tname, []):
            items.append({"num": num, "kind": determine_issue_type(iss), "team": tname,
                          "project_key": project_key, "item": iss, "is_project": False})
            num += 1
    return items


def _preview_detail_line(entry: dict) -> str:
    """Build a detail string (assignee, labels, points, SLA/due, state) for one preview entry."""
    if entry["is_project"]:
        proj  = entry["item"]
        lead  = (proj.get("lead") or {})
        name  = lead.get("name") or lead.get("displayName") or "(no lead)"
        email = lead.get("email") or ""
        lead_str = f"{name} <{email}>" if email else name
        state = (proj.get("state") or "").replace("_", " ")
        parts = [f"Lead: {lead_str}"]
        if state:
            parts.append(f"State: {state}")
        return "  " + "   ·   ".join(parts)

    iss = entry["item"]

    # Assignee
    assignee = iss.get("assignee")
    if assignee:
        aname  = assignee.get("name") or assignee.get("displayName") or "?"
        aemail = assignee.get("email") or ""
        assignee_str = f"{aname} <{aemail}>" if aemail else aname
    else:
        assignee_str = "(unassigned)"

    # Labels (all of them)
    label_names = [l.get("name") for l in _nodes(iss.get("labels")) if l.get("name")]
    labels_str  = ", ".join(label_names) if label_names else "—"

    # Story points
    estimate = iss.get("estimate")
    pts_str  = str(int(estimate) if estimate == int(estimate) else estimate) if estimate is not None else "—"

    # Due date / SLA
    due_str = resolve_due_date(iss) or "—"

    # State
    state_str = (iss.get("state") or {}).get("name") or "?"

    parts = [
        f"Assignee: {assignee_str}",
        f"Labels: {labels_str}",
        f"Pts: {pts_str}",
        f"Due/SLA: {due_str}",
        f"State: {state_str}",
    ]
    return "  " + "   ·   ".join(parts)


def print_preview_table(preview_items: list) -> None:
    """Print the numbered preview table with a detail line per item."""
    C_NUM   = 5
    C_ID    = 12
    C_TITLE = 44
    C_TYPE  = 9
    C_PROJ  = 28
    C_DEST  = 8
    W = C_NUM + C_ID + C_TITLE + C_TYPE + C_PROJ + C_DEST + 6

    header = (
        f"{'#':<{C_NUM}} {'ID':<{C_ID}} {'TITLE':<{C_TITLE}} {'TYPE':<{C_TYPE}}"
        f" {'LINEAR PROJECT (→ EPIC)':<{C_PROJ}} {'→ JIRA':>{C_DEST}}"
    )
    sep = "─" * W

    print()
    print("┌" + "─" * (W + 2) + "┐")

    current_team = None
    for entry in preview_items:
        tname = entry["team"]

        # Team header
        if tname != current_team:
            if current_team is not None:
                print(f"│  {' ' * W}│")
            print(f"│  TEAM: {tname:<{W - 8}}│")
            print(f"│  {sep}│")
            print(f"│  {header}│")
            print(f"│  {sep}│")
            current_team = tname

        num         = entry["num"]
        project_key = entry["project_key"]

        if entry["is_project"]:
            proj        = entry["item"]
            identifier  = "(Epic)"
            title_trunc = proj["name"][:C_TITLE]
            issue_type  = "Epic"
            proj_name   = "— (Linear project)"[:C_PROJ]
        else:
            iss         = entry["item"]
            identifier  = (iss.get("identifier") or "?")[:C_ID]
            title_trunc = (iss.get("title") or "Untitled")[:C_TITLE]
            issue_type  = entry["kind"][:C_TYPE]
            proj        = iss.get("project")
            proj_name   = (proj["name"][:C_PROJ] if proj else "")

        row = (
            f"{num:<{C_NUM}} {identifier:<{C_ID}} {title_trunc:<{C_TITLE}}"
            f" {issue_type:<{C_TYPE}} {proj_name:<{C_PROJ}} {project_key:>{C_DEST}}"
        )
        print(f"│  {row}│")

        # Detail line
        detail = _preview_detail_line(entry)
        # Pad or truncate to fit inside the box
        if len(detail) > W:
            detail = detail[:W - 1] + "…"
        print(f"│  {detail:<{W}}│")
        print(f"│  {' ' * W}│")

    print("└" + "─" * (W + 2) + "┘")

    n_epics  = sum(1 for e in preview_items if e["is_project"])
    n_issues = sum(1 for e in preview_items if not e["is_project"])
    print(f"  {len(preview_items)} item(s) total: {n_epics} Epic(s) + {n_issues} issue(s)\n")


def parse_selection(raw: str, max_num: int) -> Optional[set]:
    """
    Parse a selection string like "1,3,5-8,12" into a set of ints.
    Returns None if the user typed "all".
    Raises ValueError on invalid input.
    """
    raw = raw.strip().lower()
    if raw in ("all", "a", ""):
        return None  # meaning: all items
    selected = set()
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo, hi = int(lo.strip()), int(hi.strip())
            if lo < 1 or hi > max_num or lo > hi:
                raise ValueError(f"Range {lo}-{hi} out of bounds (1–{max_num})")
            selected.update(range(lo, hi + 1))
        else:
            n = int(part)
            if n < 1 or n > max_num:
                raise ValueError(f"Number {n} out of bounds (1–{max_num})")
            selected.add(n)
    return selected


def apply_selection(
    preview_items:        list,
    selected_nums:        Optional[set],
    mapped_teams:         list,
    all_issues_by_team:   dict,
    all_projects_by_team: dict,
) -> tuple:
    """
    Filter all_issues_by_team and all_projects_by_team to only the selected
    items. If an issue's parent project is not explicitly selected, it is
    auto-included so Epic linking still works.
    Returns (filtered_issues_by_team, filtered_projects_by_team).
    """
    if selected_nums is None:
        return all_issues_by_team, all_projects_by_team

    # Collect selected item IDs
    selected_issue_ids:   set = set()
    selected_project_ids: set = set()
    for entry in preview_items:
        if entry["num"] in selected_nums:
            if entry["is_project"]:
                selected_project_ids.add(entry["item"]["id"])
            else:
                selected_issue_ids.add(entry["item"]["id"])

    # Auto-include the parent project for any selected issue
    for entry in preview_items:
        if not entry["is_project"] and entry["item"]["id"] in selected_issue_ids:
            proj = entry["item"].get("project")
            if proj:
                selected_project_ids.add(proj["id"])

    new_issues:   dict = {}
    new_projects: dict = {}
    for team in mapped_teams:
        tname = team["name"]
        new_issues[tname]   = [i for i in all_issues_by_team.get(tname, [])
                                if i["id"] in selected_issue_ids]
        new_projects[tname] = [p for p in all_projects_by_team.get(tname, [])
                                if p["id"] in selected_project_ids]

    return new_issues, new_projects


# ─────────────────────────────────────────────────────────────────────────────
# Mapping file helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_mapping() -> dict:
    if not os.path.exists(MAPPING_FILE):
        return {}
    try:
        with open(MAPPING_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_mapping(mapping: dict) -> None:
    tmp = MAPPING_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, indent=2)
    os.replace(tmp, MAPPING_FILE)


# ─────────────────────────────────────────────────────────────────────────────
# Migration phases
# ─────────────────────────────────────────────────────────────────────────────

def _check_existing_mapping(mapping: dict, key: str, label: str, jira) -> Optional[str]:
    """Return existing jira_key if still valid, else clean mapping and return None."""
    if key not in mapping:
        return None
    jkey = mapping[key]
    if jira.issue_exists(jkey):
        print(f"  SKIP  {label}  →  {jkey}  (already created)")
        return jkey
    print(f"  STALE {label}  →  {jkey} no longer exists — recreating")
    del mapping[key]
    save_mapping(mapping)
    return None


def phase_create_epics(
    projects:        list,
    project_key:     str,
    jira:            JiraClient,
    mapping:         dict,
    priority_map:    dict,
    sp_field_id:     Optional[str],
    epic_name_field: Optional[str],
    assignee_map:    dict,
    linear_key:      str,
    report:          dict,
) -> dict:
    """Create a Jira Epic per Linear project. Returns {linear_project_id → jira_epic_key}."""
    epic_map: dict = {}
    if not projects:
        return epic_map

    print(f"\n  Creating {len(projects)} Epic(s) for Linear projects…")
    for proj in projects:
        pid          = proj["id"]
        mapping_key  = f"__epic__{pid}"

        existing = _check_existing_mapping(mapping, mapping_key, f"Epic [{proj['name']}]", jira)
        if existing:
            epic_map[pid] = existing
            continue

        proj_desc = (proj.get("description") or "").strip()

        # Debug: show raw assignee/member data from Linear
        print(f"  PROJECT [{proj['name']}]  lead={proj.get('lead')}  members={proj.get('members')}")

        # Build a synthetic issue dict so we can reuse build_jira_fields
        synthetic = {
            "id": pid, "identifier": "", "title": proj["name"],
            "description": proj_desc,
            "url": proj.get("url") or "",
            "priorityLabel": "No priority", "estimate": None,
            "dueDate": None, "createdAt": "", "labels": {"nodes": []},
            "creator": None, "assignee": proj.get("lead"),
        }
        fields = build_jira_fields(
            synthetic, project_key, "Epic", priority_map,
            sp_field_id, epic_name_field, epic_key=None,
            assignee_map=assignee_map, reporter_map={}, is_epic=True,
        )

        try:
            result        = jira.create_issue(fields)
            jkey          = result["key"]
            jira_issue_id = result.get("id", "")
            epic_map[pid] = jkey
            mapping[mapping_key] = jkey
            save_mapping(mapping)
            print(f"  OK    Epic [{proj['name']}]  →  {jkey}")

            if proj_desc:
                desc_adf = upload_images_and_build_description(
                    proj_desc, jkey, jira_issue_id, f"Epic:{proj['name']}", jira, linear_key)
            else:
                desc_adf = {"version": 1, "type": "doc", "content": []}
            try:
                jira.update_issue(jkey, {"description": desc_adf})
                print(f"  OK    Epic [{proj['name']}]  description set on {jkey}")
            except Exception as upd_exc:
                print(f"  FAIL  Epic [{proj['name']}]  description update FAILED: {upd_exc}")
                report["failed_issues"].append(
                    {"id": f"Epic:{proj['name']} (description)", "reason": str(upd_exc)})
        except Exception as exc:
            print(f"  FAIL  Epic [{proj['name']}]  ({exc})")
            report["failed_issues"].append(
                {"id": f"Epic:{proj['name']}", "reason": str(exc)})

    return epic_map


def _try_create_issue(jira: JiraClient, fields: dict) -> dict:
    """
    Try create_issue; retries up to 4 times to fix known Jira field errors:
      - "data was not an array" → looks up the field ID and sets it to []
      - "reporter" rejected → retries without reporter
      - "parent" / customfield_10014 errors → retries without / with alternate field
    """
    current = dict(fields)
    last_exc: Optional[Exception] = None
    for _pass in range(4):
        try:
            return jira.create_issue(current)
        except Exception as exc:
            last_exc = exc
            msg = str(exc)

            # Fix: array-type fields sent as wrong type (e.g. float instead of [])
            if "was not an array" in msg:
                bad_names = re.findall(r'"([^"]+)":\s*"data was not an array"', msg)
                if not bad_names:
                    raise
                name_to_id: dict = {}
                try:
                    for f in jira.get_fields():
                        if f.get("id") and f.get("name"):
                            name_to_id[f["name"].lower()] = f["id"]
                except Exception:
                    pass
                fixed_any = False
                for fname in bad_names:
                    fid = name_to_id.get(fname.lower())
                    if fid and fid in current:
                        current.pop(fid)   # remove the bad field entirely
                        fixed_any = True
                if not fixed_any:
                    raise
                continue

            # Fix: reporter field rejected by Jira (not licensed / not on screen)
            if '"reporter"' in msg:
                current.pop("reporter", None)
                continue

            # Fix: Epic link via 'parent' field
            ml = msg.lower()
            if "parent" in ml or "customfield_10014" in ml:
                parent_val = current.pop("parent", None)
                if parent_val and "customfield_10014" not in current:
                    current["customfield_10014"] = parent_val.get("key", "")
                elif "customfield_10014" in current:
                    current.pop("customfield_10014", None)
                continue

            raise  # unrecoverable error

    raise last_exc


def _create_one_issue(
    issue:           dict,
    project_key:     str,
    epic_map:        dict,
    jira:            JiraClient,
    mapping:         dict,
    priority_map:    dict,
    sp_field_id:     Optional[str],
    epic_name_field: Optional[str],
    assignee_map:    dict,
    reporter_map:    dict,
    linear_key:      str,
    report:          dict,
) -> tuple:
    """
    Migrate a single Linear issue to Jira.
    Returns (created: bool, skipped: bool, failed: bool).
    """
    linear_id  = issue.get("id", "")
    identifier = issue.get("identifier", "?")

    existing = _check_existing_mapping(mapping, linear_id, identifier, jira)
    if existing:
        return False, True, False

    proj     = issue.get("project")
    epic_key = epic_map.get(proj["id"]) if proj else None

    issue_type = determine_issue_type(issue)
    issue_desc = (issue.get("description") or "").strip()

    try:
        fields = build_jira_fields(
            issue, project_key, issue_type, priority_map,
            sp_field_id, epic_name_field, epic_key,
            assignee_map, reporter_map, is_epic=False,
        )
    except Exception as exc:
        print(f"  FAIL  {identifier}  (field build: {exc})")
        report["failed_issues"].append({"id": identifier, "reason": str(exc)})
        return False, False, True

    try:
        result        = _try_create_issue(jira, fields)
        jira_key      = result["key"]
        jira_issue_id = result.get("id", "")
        mapping[linear_id] = jira_key
        save_mapping(mapping)
        print(f"  OK    {identifier}  →  {jira_key}  [{issue_type}]  |  {fields['summary'][:45]}")

        if issue_desc:
            desc_adf = upload_images_and_build_description(
                issue_desc, jira_key, jira_issue_id, identifier, jira, linear_key)
        else:
            desc_adf = {"version": 1, "type": "doc", "content": []}
        try:
            jira.update_issue(jira_key, {"description": desc_adf})
        except Exception as upd_exc:
            print(f"  WARN  {identifier}  description update failed: {upd_exc}")

        return True, False, False
    except Exception as exc:
        print(f"  FAIL  {identifier}  ({exc})")
        report["failed_issues"].append({"id": identifier, "reason": str(exc)})
        return False, False, True


def _run_issue_phase(
    label:           str,
    subset:          list,
    project_key:     str,
    epic_map:        dict,
    jira:            JiraClient,
    mapping:         dict,
    priority_map:    dict,
    sp_field_id:     Optional[str],
    epic_name_field: Optional[str],
    assignee_map:    dict,
    reporter_map:    dict,
    linear_key:      str,
    report:          dict,
) -> None:
    """Print a header for `label`, iterate `subset`, delegate each to _create_one_issue."""
    print(f"\n  ── {label}: {len(subset)} issue(s) ──")
    if not subset:
        return
    created = skipped = failed = 0
    for issue in subset:
        c, s, f = _create_one_issue(
            issue, project_key, epic_map, jira, mapping,
            priority_map, sp_field_id, epic_name_field,
            assignee_map, reporter_map, linear_key, report,
        )
        created += c; skipped += s; failed += f
    print(f"  {label} — created: {created}  skipped: {skipped}  failed: {failed}")


def _phase_issues(label, issues, filter_fn, project_key, epic_map, jira, mapping,
                  priority_map, sp_field_id, epic_name_field,
                  assignee_map, reporter_map, linear_key, report):
    _run_issue_phase(
        label, [i for i in issues if filter_fn(i)],
        project_key, epic_map, jira, mapping,
        priority_map, sp_field_id, epic_name_field,
        assignee_map, reporter_map, linear_key, report,
    )


def phase_create_bugs(issues, *args) -> None:
    """Migrate issues whose Linear labels map to Jira type 'Bug'."""
    _phase_issues("Bugs", issues, lambda i: determine_issue_type(i) == "Bug", *args)


def phase_create_feature_requests(issues, *args) -> None:
    """Migrate issues with a 'Feature Request' label (→ Jira Story)."""
    _phase_issues("Feature Requests", issues,
        lambda i: any((l.get("name") or "") == "Feature Request"
                      for l in _nodes(i.get("labels"))),
        *args)


def phase_create_stories(issues, *args) -> None:
    """Migrate remaining issues that are neither Bugs nor Feature Requests (→ Jira Story)."""
    _type_label_names = {"Bug", "Feature Request"}
    _phase_issues("Stories", issues,
        lambda i: not any((l.get("name") or "") in _type_label_names
                          for l in _nodes(i.get("labels"))),
        *args)


def phase_move_to_backlog(mapping: dict, jira: JiraClient) -> None:
    keys = [v for v in mapping.values()
            if v and not v.startswith("__")]
    if not keys:
        return
    print(f"\n  Moving {len(keys)} issue(s) to backlog…")
    for start in range(0, len(keys), 50):
        batch = keys[start:start + 50]
        try:
            jira.move_to_backlog(batch)
        except Exception as exc:
            print(f"  Warning: backlog move partial failure — {exc}")
    print("  ✓ Backlog move done")


def phase_upload_attachments(
    issues:     list,
    mapping:    dict,
    jira:       JiraClient,
    linear_key: str,
    report:     dict,
) -> None:
    uploaded = skipped = failed = 0
    for issue in issues:
        linear_id  = issue.get("id", "")
        identifier = issue.get("identifier", "?")
        jira_key   = mapping.get(linear_id)
        if not jira_key:
            continue

        for att in _nodes(issue.get("attachments")):
            url   = att.get("url")
            title = att.get("title") or "attachment"
            if not url:
                skipped += 1
                continue

            filename = os.path.basename(url.split("?")[0]) or title
            if "." not in os.path.basename(filename):
                filename = title

            content = linear_download_file(url, linear_key)
            if content is None:
                # Fall back: add as remote link
                print(f"  WARN  {identifier}  cannot download {url[:60]}  — adding remote link")
                try:
                    jira.add_remote_link(jira_key, title, url)
                except Exception:
                    pass
                report["failed_attachments"].append({"issue": identifier, "url": url,
                                                     "reason": "download failed"})
                failed += 1
                continue
            try:
                jira.upload_attachment(jira_key, filename, content)
                print(f"  OK    {identifier}  →  {jira_key}  attached: {filename[:40]}")
                uploaded += 1
            except Exception as exc:
                print(f"  FAIL  {identifier}  attach '{filename}'  ({exc})")
                report["failed_attachments"].append({"issue": identifier,
                                                     "filename": filename,
                                                     "reason": str(exc)})
                failed += 1

    print(f"\n  Attachments — uploaded: {uploaded}  skipped: {skipped}  failed: {failed}")


def phase_post_activity_comments(
    issues:  list,
    mapping: dict,
    jira:    JiraClient,
    report:  dict,
) -> None:
    posted = skipped = failed = 0
    for issue in issues:
        linear_id  = issue.get("id", "")
        identifier = issue.get("identifier", "?")
        jira_key   = mapping.get(linear_id)
        if not jira_key:
            skipped += 1
            continue

        has_history  = bool(_nodes(issue.get("history")))
        has_comments = bool(_nodes(issue.get("comments")))
        if not has_history and not has_comments:
            skipped += 1
            continue

        md  = build_activity_comment_md(issue)
        adf = markdown_to_adf(md)
        try:
            jira.add_comment(jira_key, adf)
            posted += 1
        except Exception as exc:
            print(f"  FAIL  {identifier}  comment  ({exc})")
            report["failed_comments"].append({"issue": identifier, "reason": str(exc)})
            failed += 1

    print(f"\n  Activity comments — posted: {posted}  skipped: {skipped}  failed: {failed}")


def phase_create_links(
    issues:  list,
    mapping: dict,
    jira:    JiraClient,
    report:  dict,
) -> None:
    created = skipped = failed = 0
    linked_pairs: set = set()

    for issue in issues:
        linear_id = issue.get("id", "")
        jira_key  = mapping.get(linear_id)
        if not jira_key:
            continue

        for rel in _nodes(issue.get("relations")):
            rel_type    = (rel.get("type") or "").lower()
            related_id  = (rel.get("relatedIssue") or {}).get("id", "")
            related_key = mapping.get(related_id)
            if not related_key:
                skipped += 1
                continue

            link_type = RELATION_TYPE_MAP.get(rel_type, "Relates")
            if rel_type in ("blocked_by", "duplicate_by"):
                outward, inward = related_key, jira_key
            else:
                outward, inward = jira_key, related_key

            pair = (link_type, tuple(sorted([outward, inward])))
            if pair in linked_pairs:
                continue
            linked_pairs.add(pair)

            try:
                jira.create_issue_link(link_type, outward, inward)
                created += 1
            except Exception as exc:
                print(f"  FAIL  {link_type}  {outward}  →  {inward}  ({exc})")
                failed += 1

    print(f"\n  Issue links — created: {created}  skipped: {skipped}  failed: {failed}")


# ─────────────────────────────────────────────────────────────────────────────
# User mapping  (CSV-backed)
# ─────────────────────────────────────────────────────────────────────────────

def load_user_csv() -> dict:
    """
    Read user_mapping.csv → {linear_email: jira_email}.
    Jira email is empty string when not yet known.
    """
    result: dict = {}
    if not os.path.exists(USER_MAPPING_FILE):
        return result
    with open(USER_MAPPING_FILE, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if not row or row[0].lower() == "linear_email":
                continue   # skip header
            linear_email = row[0].strip().lower()
            jira_email   = row[1].strip().lower() if len(row) > 1 else ""
            if linear_email:
                result[linear_email] = jira_email
    return result


def save_user_csv(csv_map: dict) -> None:
    """Write user_mapping.csv sorted by linear_email."""
    tmp = USER_MAPPING_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["linear_email", "jira_email"])
        for le, je in sorted(csv_map.items()):
            w.writerow([le, je or ""])
    os.replace(tmp, USER_MAPPING_FILE)


def build_user_map(linear_users: list, jira_users: list, report: dict,
                   jira: "JiraClient" = None) -> dict:
    """
    Build {linear_email → jira_accountId} for all matchable users.

    Strategy:
      1. Load user_mapping.csv (persisted across runs).
      2. For every Linear user not yet in the CSV:
           - If their email exists in Jira bulk list → auto-fill both columns.
           - Otherwise → add row with empty jira_email for manual completion.
      3. Save updated CSV.
      4. Build accountId map from CSV entries:
           - First try the bulk jira_by_email dict.
           - If not found there, do a targeted individual lookup via Jira API
             (catches users missing from the bulk search: guests, inactive, etc.)
    """
    # Jira bulk lookup: email → accountId
    jira_by_email: dict = {}
    for ju in jira_users:
        email = (ju.get("emailAddress") or "").lower()
        if email:
            jira_by_email[email] = ju["accountId"]

    # Load / update CSV
    csv_map = load_user_csv()
    changed = False
    for lu in linear_users:
        le = (lu.get("email") or "").lower()
        if not le or le in csv_map:
            continue
        csv_map[le] = le if le in jira_by_email else ""
        changed = True

    if changed:
        save_user_csv(csv_map)

    # Build accountId map — with individual-lookup fallback
    user_map:  dict = {}
    unmatched: list = []
    for lu in linear_users:
        le = (lu.get("email") or "").lower()
        if not le:
            continue
        je = csv_map.get(le, "")
        aid = None
        if je:
            aid = jira_by_email.get(je)
            if not aid and jira:
                # Targeted lookup — finds users missed by the bulk search
                aid = jira.resolve_account_id(je)
        if aid:
            user_map[le] = aid
        else:
            unmatched.append({"name": lu.get("name", "?"), "email": le,
                              "jira_email": je})

    report["unmatched_users"] = unmatched
    return user_map


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    W = 80
    print()
    print("╔" + "═" * (W - 2) + "╗")
    print("║" + "  LINEAR → JIRA UNIFIED SYNC".center(W - 2) + "║")
    print("╚" + "═" * (W - 2) + "╝")
    print()

    report: dict = {
        "failed_issues":      [],
        "failed_attachments": [],
        "failed_comments":    [],
        "unmatched_users":    [],
        "skipped_triage":     0,
        "skipped_teams":      [],
    }

    # ── Step 1: Linear credentials ────────────────────────────────────────────
    print("Step 1 — Linear API key")
    print("  Generate at: Linear → Settings → Security & access → Personal API keys")
    linear_key = prompt_secret("Linear API key")
    if not linear_key:
        print("Error: Linear API key is required.")
        sys.exit(1)

    print("\n  Verifying Linear credentials…")
    try:
        viewer = linear_fetch_viewer(linear_key)
    except Exception as exc:
        print(f"  Error: {exc}")
        sys.exit(1)
    print(f"  ✓ {viewer['name']} ({viewer['email']})")

    # ── Step 2: Jira credentials ──────────────────────────────────────────────
    print(f"\nStep 2 — Jira credentials  ({JIRA_URL})")
    print("  Generate token at: https://id.atlassian.com/manage-profile/security/api-tokens")
    jira_email = prompt("Jira account email")
    if not jira_email:
        print("Error: Jira email is required.")
        sys.exit(1)
    jira_token = prompt_secret("Jira API token")
    if not jira_token:
        print("Error: Jira API token is required.")
        sys.exit(1)

    jira = JiraClient(jira_email, jira_token)
    print("\n  Verifying Jira credentials…")
    try:
        myself = jira.get_myself()
    except Exception as exc:
        print(f"  Error: {exc}")
        sys.exit(1)
    print(f"  ✓ {myself.get('displayName','?')} ({myself.get('emailAddress','?')})")

    # ── Resolve Jira project keys interactively ───────────────────────────────
    print("\n  Fetching available Jira projects…")
    try:
        jira_projects = jira.list_projects()
    except Exception as exc:
        print(f"  Warning: could not list projects — {exc}")
        jira_projects = []

    jira_proj_by_key = {p["key"].upper(): p for p in jira_projects}
    # name index: exact lower, and also key→name for partial matching
    jira_proj_by_name = {p["name"].lower(): p for p in jira_projects}

    if jira_projects:
        print(f"  {len(jira_projects)} project(s) available:")
        for i, p in enumerate(jira_projects, 1):
            print(f"    {i:>3}.  [{p['key']}]  {p['name']}")

    def _resolve_jira_project(value: str) -> Optional[dict]:
        """
        Try to find a Jira project matching `value` (the TEAM_SPACE_MAP value).
        Tries in order:
          1. Exact key match (case-insensitive)
          2. Exact name match (case-insensitive)
          3. Partial name match (value is a substring of a project name)
        """
        v_up   = value.strip().upper()
        v_low  = value.strip().lower()
        # 1. Key
        if v_up in jira_proj_by_key:
            return jira_proj_by_key[v_up]
        # 2. Exact name
        if v_low in jira_proj_by_name:
            return jira_proj_by_name[v_low]
        # 3. Partial name
        for name_low, proj in jira_proj_by_name.items():
            if v_low in name_low or name_low in v_low:
                return proj
        return None

    # Resolve each entry in TEAM_SPACE_MAP
    resolved_map: dict = {}   # team_name → verified Jira project key
    print()
    for team_name, configured_value in TEAM_SPACE_MAP.items():
        matched = _resolve_jira_project(configured_value)
        if matched:
            resolved_map[team_name] = matched["key"]
            print(f"  ✓ Linear [{team_name}]  →  Jira [{matched['key']}] \"{matched['name']}\"")
            continue

        # Could not resolve automatically — ask the user
        print(f"\n  ⚠  Could not find a Jira project matching \"{configured_value}\" "
              f"for Linear team \"{team_name}\".")
        print("     Enter a number from the list above, or type a project key/name:")
        raw = input("     Selection: ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(jira_projects):
                chosen = jira_projects[idx]
            else:
                raise ValueError
        except ValueError:
            chosen = _resolve_jira_project(raw)

        if not chosen:
            print(f"     No match found — Linear team \"{team_name}\" will be skipped.")
        else:
            resolved_map[team_name] = chosen["key"]
            print(f"  ✓ Linear [{team_name}]  →  Jira [{chosen['key']}] \"{chosen['name']}\"")

    # Apply resolved mapping for this run
    TEAM_SPACE_MAP.update(resolved_map)

    # ── Step 3: Fetch Linear data ──────────────────────────────────────────────
    print("\nStep 3 — Fetching Linear data")

    print("  Teams…")
    try:
        all_teams = linear_fetch_teams(linear_key)
    except Exception as exc:
        print(f"  Error: {exc}")
        sys.exit(1)

    mapped_teams  = [t for t in all_teams if t["name"] in TEAM_SPACE_MAP]
    skipped_teams = [t["name"] for t in all_teams if t["name"] not in TEAM_SPACE_MAP]
    report["skipped_teams"] = skipped_teams

    if not mapped_teams:
        print(f"  Error: no Linear teams match TEAM_SPACE_MAP keys: "
              f"{list(TEAM_SPACE_MAP.keys())}")
        print(f"  Your teams: {[t['name'] for t in all_teams]}")
        sys.exit(1)

    print(f"  ✓ Migrating teams: {[t['name'] for t in mapped_teams]}")
    print(f"  ✓ Skipping teams:  {skipped_teams}")

    print("  Linear users…")
    try:
        linear_users = linear_fetch_all_users(linear_key)
        print(f"  ✓ {len(linear_users)} Linear user(s)")
    except Exception as exc:
        print(f"  Warning: {exc}")
        linear_users = []

    # Date range
    days_raw = prompt("\n  Fetch issues created in the last N days (enter 0 for all time)",
                      default="90")
    try:
        days = int(days_raw)
        if days < 0:
            raise ValueError
    except ValueError:
        print("  Invalid input, defaulting to 90 days.")
        days = 90

    if days == 0:
        since_date: Optional[datetime] = None
        print("  → Fetching ALL issues (no date limit)")
    else:
        since_date = datetime.now(timezone.utc) - timedelta(days=days)
        print(f"  → Issues created on or after {since_date.strftime('%Y-%m-%d %H:%M UTC')}")

    all_projects_by_team: dict = {}
    all_issues_by_team:   dict = {}

    for team in mapped_teams:
        tname = team["name"]
        print(f"\n  [{tname}] Fetching projects…")
        try:
            projects = linear_fetch_projects(linear_key, team["id"])
            print(f"  ✓ {len(projects)} project(s)")
        except Exception as exc:
            print(f"  Warning: {exc}")
            projects = []
        all_projects_by_team[tname] = projects

        print(f"  [{tname}] Fetching issues…")
        try:
            raw = linear_fetch_all_issues(linear_key, team["id"], since_date)
        except Exception as exc:
            print(f"  Error: {exc}")
            raw = []

        kept = []
        n_triage = 0
        for iss in raw:
            if is_triage(iss):
                n_triage += 1
            else:
                kept.append(iss)
        report["skipped_triage"] += n_triage
        print(f"  ✓ {len(kept)} issue(s) kept  ({n_triage} triage excluded)")

        # Enrich with history (separate queries — too complex to inline)
        linear_enrich_with_history(linear_key, kept)

        all_issues_by_team[tname] = kept

    total_issues = sum(len(v) for v in all_issues_by_team.values())

    # ── Step 4: Jira auto-config ───────────────────────────────────────────────
    print("\nStep 4 — Jira auto-configuration")

    print("  Jira users…")
    try:
        jira_users = jira.get_all_users()
        print(f"  ✓ {len(jira_users)} Jira user(s)")
    except Exception as exc:
        print(f"  Warning: {exc}")
        jira_users = []

    user_map = build_user_map(linear_users, jira_users, report, jira=jira)
    matched   = len(user_map)
    unmatched = len(report["unmatched_users"])
    print(f"  ✓ User mapping: {matched} matched, {unmatched} unmatched")

    print("  Jira custom fields…")
    try:
        all_fields      = jira.get_fields()
        sp_field_id     = detect_story_points_field(all_fields)
        epic_name_field = detect_epic_name_field(all_fields)
    except Exception as exc:
        print(f"  Warning: {exc}")
        sp_field_id, epic_name_field = None, None
    print(f"  Story points field: {sp_field_id or '(not detected)'}")
    print(f"  Epic name field:    {epic_name_field or '(not detected)'}")

    # ── Step 5: Confirm ────────────────────────────────────────────────────────
    print("\nStep 5 — Confirm")
    print(f"""
  Migration summary:
    Linear teams to migrate:  {[t['name'] for t in mapped_teams]}
    Linear teams skipped:     {skipped_teams}
    Date window:              {'ALL time' if days == 0 else f'last {days} day(s) (since {since_date.strftime("%Y-%m-%d")})'}
    Total issues to migrate:  {total_issues}
    Triage items excluded:    {report['skipped_triage']}
    Linear users matched:     {matched}/{matched + unmatched}
    Jira URL:                 {JIRA_URL}
    Story points field:       {sp_field_id or '(skipped)'}
    Mapping file:             {MAPPING_FILE}
""")

    preview_items = build_preview_items(mapped_teams, all_issues_by_team, all_projects_by_team)
    print_preview_table(preview_items)

    # Let the user pick specific items or migrate everything
    print("  Select items to migrate:")
    print("    • Enter numbers/ranges:  1,3,5-8,12")
    print("    • Press Enter or type 'all' to migrate everything")
    while True:
        raw_sel = input("  Selection: ").strip()
        try:
            selected_nums = parse_selection(raw_sel, len(preview_items))
            break
        except ValueError as exc:
            print(f"  Invalid input: {exc}  — try again.")

    if selected_nums is not None:
        all_issues_by_team, all_projects_by_team = apply_selection(
            preview_items, selected_nums,
            mapped_teams, all_issues_by_team, all_projects_by_team,
        )
        total_issues = sum(len(v) for v in all_issues_by_team.values())
        n_epics      = sum(len(v) for v in all_projects_by_team.values())
        print(f"\n  Selection: {n_epics} Epic(s) + {total_issues} issue(s) will be migrated.")
    else:
        print(f"\n  Migrating all {len(preview_items)} item(s).")

    # Narrow unmatched-user report to only emails referenced in the selected issues
    selected_issues_flat = [
        iss for issues in all_issues_by_team.values() for iss in issues
    ]
    referenced_emails = set()
    for iss in selected_issues_flat:
        for person in (iss.get("assignee"), iss.get("creator")):
            if person and person.get("email"):
                referenced_emails.add(person["email"].lower())
    report["unmatched_users"] = [
        u for u in report["unmatched_users"]
        if u["email"] in referenced_emails
    ]

    go = prompt("\nProceed? (y/n)", default="y").lower()
    if go not in ("y", "yes"):
        print("Aborted.")
        sys.exit(0)

    # ── Migration ──────────────────────────────────────────────────────────────
    mapping = load_mapping()
    all_issues_flat: list = []

    for team in mapped_teams:
        tname       = team["name"]
        project_key = TEAM_SPACE_MAP[tname]
        projects    = all_projects_by_team[tname]
        issues      = all_issues_by_team[tname]

        print("\n" + "═" * W)
        print(f"  TEAM: {tname}  →  Jira project: {project_key}")
        print("═" * W)

        # Skip team if it has no resolved project key (user skipped it during setup)
        if not project_key or tname not in resolved_map:
            print(f"  Skipping — no Jira project resolved for team '{tname}'.")
            all_issues_flat.extend(issues)
            continue
        print(f"  Jira project: [{project_key}]")

        # Epics for this team's Linear projects
        epic_map = phase_create_epics(
            projects, project_key, jira, mapping,
            DEFAULT_PRIORITY_MAP, sp_field_id, epic_name_field,
            user_map, linear_key, report,
        )

        # Issues — one phase per type
        _issue_args = (
            project_key, epic_map, jira, mapping,
            DEFAULT_PRIORITY_MAP, sp_field_id, epic_name_field,
            user_map, user_map, linear_key, report,
        )
        phase_create_bugs(issues,             *_issue_args)
        phase_create_feature_requests(issues, *_issue_args)
        phase_create_stories(issues,          *_issue_args)

        all_issues_flat.extend(issues)

    # Backlog
    print("\n" + "─" * W)
    print("  Moving all issues to backlog…")
    phase_move_to_backlog(mapping, jira)

    # Attachments
    print("\n" + "─" * W)
    print("  Uploading attachments…")
    phase_upload_attachments(all_issues_flat, mapping, jira, linear_key, report)

    # Activity comments
    print("\n" + "─" * W)
    print("  Posting consolidated activity comments…")
    phase_post_activity_comments(all_issues_flat, mapping, jira, report)

    # Issue links
    print("\n" + "─" * W)
    print("  Creating issue links…")
    phase_create_links(all_issues_flat, mapping, jira, report)

    # ── Final report ───────────────────────────────────────────────────────────
    print()
    print("╔" + "═" * (W - 2) + "╗")
    print("║" + "  Migration complete".center(W - 2) + "║")
    print("╚" + "═" * (W - 2) + "╝")

    print(f"\n  Mapping saved to: {MAPPING_FILE}")
    print(f"  Total entries:    {len(mapping)}")

    print(f"\n  Triage items excluded:  {report['skipped_triage']}")
    print(f"  Teams skipped:          {report['skipped_teams']}")

    if report["unmatched_users"]:
        print(f"\n  ⚠  Unmatched Linear users ({len(report['unmatched_users'])}) — "
              f"fill in the jira_email column in {USER_MAPPING_FILE} and re-run:")
        for u in report["unmatched_users"]:
            je = u.get("jira_email", "")
            suffix = f"  →  jira: {je}" if je else "  →  jira: (empty — fill manually)"
            print(f"       {u['name']:<30}  {u['email']}{suffix}")

    _REPORT_SECTIONS = [
        ("failed_issues",      "Failed issues",
         lambda e: f"       {e['id']}:  {e['reason'][:80]}"),
        ("failed_attachments", "Failed attachments",
         lambda e: f"       {e.get('issue','?')}:  "
                   f"{e.get('filename', e.get('url', '?'))[:60]}  — {e.get('reason','')[:40]}"),
        ("failed_comments",    "Failed activity comments",
         lambda e: f"       {e['issue']}:  {e['reason'][:80]}"),
    ]
    for key, title, fmt in _REPORT_SECTIONS:
        items = report.get(key) or []
        if items:
            print(f"\n  ✗  {title} ({len(items)}):")
            for item in items:
                print(fmt(item))

    if not any([report["failed_issues"], report["failed_attachments"],
                report["failed_comments"], report["unmatched_users"]]):
        print("\n  ✓ All clean — no failures or unmatched users.")

    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAborted.")
        sys.exit(0)
