"""
Unit tests for linear_jira_sync.py

Run with:
    python -m pytest test_linear_jira_sync.py -v
    python -m pytest test_linear_jira_sync.py -v -k "TestBuildJiraFields"
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_jira_sync as ljs

# ─────────────────────────────────────────────────────────────────────────────
# Helpers shared across tests
# ─────────────────────────────────────────────────────────────────────────────

PRIORITY_MAP = {
    "Urgent": "Highest", "High": "High",
    "Medium": "Medium",  "Low": "Low",
    "No priority": "Medium",
}


def _issue(**overrides):
    """Minimal Linear issue dict suitable for build_jira_fields."""
    base = {
        "id": "issue-1",
        "identifier": "TST-1",
        "title": "Test issue",
        "description": "Some description",
        "priorityLabel": "Medium",
        "estimate": None,
        "dueDate": None,
        "slaBreachesAt": None,
        "labels": {"nodes": []},
        "assignee": None,
        "creator": None,
        "project": None,
        "state": {"name": "In Progress"},
        "cycle": None,
    }
    base.update(overrides)
    return base


def _entry(issue_overrides=None, is_project=False, project_item=None):
    """Build a preview-table entry dict."""
    if is_project:
        return {
            "num": 1, "is_project": True, "team": "Desktop",
            "project_key": "DES", "item": project_item or {},
        }
    issue = _issue(**(issue_overrides or {}))
    return {
        "num": 1, "is_project": False, "team": "Desktop",
        "project_key": "DES", "item": issue,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. _nodes  –  GraphQL node extractor
# ─────────────────────────────────────────────────────────────────────────────

class TestNodes:
    def test_none_returns_empty(self):
        assert ljs._nodes(None) == []

    def test_empty_dict_returns_empty(self):
        assert ljs._nodes({}) == []

    def test_extracts_nodes(self):
        assert ljs._nodes({"nodes": [1, 2, 3]}) == [1, 2, 3]

    def test_custom_key(self):
        assert ljs._nodes({"values": ["a", "b"]}, "values") == ["a", "b"]

    def test_missing_key_returns_empty(self):
        assert ljs._nodes({"other": [1]}) == []

    def test_null_nodes_returns_empty(self):
        assert ljs._nodes({"nodes": None}) == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. Date helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestFmtDate:
    def test_none_returns_dash(self):
        assert ljs._fmt_date(None) == "—"

    def test_empty_returns_dash(self):
        assert ljs._fmt_date("") == "—"

    def test_valid_iso_z_suffix(self):
        assert ljs._fmt_date("2024-03-15T10:30:00Z") == "2024-03-15 10:30 UTC"

    def test_invalid_returns_raw(self):
        assert ljs._fmt_date("not-a-date") == "not-a-date"


class TestParseIsoToDate:
    def test_z_suffix(self):
        assert ljs._parse_iso_to_date("2024-06-01T00:00:00.000Z") == "2024-06-01"

    def test_with_offset(self):
        assert ljs._parse_iso_to_date("2024-06-01T12:00:00+05:00") == "2024-06-01"

    def test_date_only_string(self):
        assert ljs._parse_iso_to_date("2024-06-01") == "2024-06-01"

    def test_invalid_returns_none(self):
        assert ljs._parse_iso_to_date("not-a-date") is None

    def test_empty_returns_none(self):
        assert ljs._parse_iso_to_date("") is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. is_triage  –  triage detection
# ─────────────────────────────────────────────────────────────────────────────

class TestIsTriage:
    def setup_method(self):
        self._orig_states = set(ljs.TRIAGE_STATE_NAMES)
        self._orig_labels = set(ljs.TRIAGE_LABEL_NAMES)
        ljs.TRIAGE_STATE_NAMES.clear()
        ljs.TRIAGE_LABEL_NAMES.clear()

    def teardown_method(self):
        ljs.TRIAGE_STATE_NAMES.clear()
        ljs.TRIAGE_STATE_NAMES.update(self._orig_states)
        ljs.TRIAGE_LABEL_NAMES.clear()
        ljs.TRIAGE_LABEL_NAMES.update(self._orig_labels)

    def test_not_triage_when_sets_empty(self):
        assert ljs.is_triage({"state": {"name": "Triage"}, "labels": {"nodes": []}}) is False

    def test_triage_by_state_name(self):
        ljs.TRIAGE_STATE_NAMES.add("triage")
        assert ljs.is_triage({"state": {"name": "Triage"}, "labels": {"nodes": []}}) is True

    def test_state_match_is_case_insensitive(self):
        ljs.TRIAGE_STATE_NAMES.add("triage")
        assert ljs.is_triage({"state": {"name": "TRIAGE"}, "labels": {"nodes": []}}) is True

    def test_triage_by_label(self):
        ljs.TRIAGE_LABEL_NAMES.add("triage")
        issue = {"state": {"name": "In Progress"},
                 "labels": {"nodes": [{"name": "Triage"}]}}
        assert ljs.is_triage(issue) is True

    def test_non_triage_label_not_flagged(self):
        ljs.TRIAGE_LABEL_NAMES.add("triage")
        issue = {"state": {"name": "In Progress"},
                 "labels": {"nodes": [{"name": "Bug"}]}}
        assert ljs.is_triage(issue) is False

    def test_missing_state_and_labels(self):
        assert ljs.is_triage({}) is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. determine_issue_type  –  label → Jira type mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestDetermineIssueType:
    def test_bug_label_maps_to_bug(self):
        assert ljs.determine_issue_type({"labels": {"nodes": [{"name": "Bug"}]}}) == "Bug"

    def test_feature_request_maps_to_story(self):
        assert ljs.determine_issue_type({"labels": {"nodes": [{"name": "Feature Request"}]}}) == "Story"

    def test_no_labels_returns_default(self):
        assert ljs.determine_issue_type({"labels": {"nodes": []}}) == ljs.DEFAULT_ISSUE_TYPE

    def test_no_labels_field_returns_default(self):
        assert ljs.determine_issue_type({}) == ljs.DEFAULT_ISSUE_TYPE

    def test_first_matching_label_wins(self):
        issue = {"labels": {"nodes": [{"name": "Bug"}, {"name": "Feature Request"}]}}
        assert ljs.determine_issue_type(issue) == "Bug"

    def test_unknown_label_returns_default(self):
        issue = {"labels": {"nodes": [{"name": "Enhancement"}]}}
        assert ljs.determine_issue_type(issue) == ljs.DEFAULT_ISSUE_TYPE

    def test_falls_back_to_native_issue_type_field(self):
        issue = {"labels": {"nodes": []}, "issueType": {"name": "Bug"}}
        assert ljs.determine_issue_type(issue) == "Bug"


# ─────────────────────────────────────────────────────────────────────────────
# 5. resolve_due_date  –  priority ordering of date sources
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveDueDate:
    def test_due_date_has_highest_priority(self):
        issue = {
            "dueDate": "2024-12-31",
            "slaBreachesAt": "2024-11-01T00:00:00Z",
            "project": {"targetDate": "2024-10-01"},
        }
        assert ljs.resolve_due_date(issue) == "2024-12-31"

    def test_sla_fallback_when_no_due_date(self):
        issue = {"slaBreachesAt": "2024-11-01T00:00:00Z", "project": None}
        assert ljs.resolve_due_date(issue) == "2024-11-01"

    def test_project_target_date_fallback(self):
        issue = {"project": {"targetDate": "2024-10-01"}}
        assert ljs.resolve_due_date(issue) == "2024-10-01"

    def test_no_dates_returns_none(self):
        assert ljs.resolve_due_date({}) is None

    def test_custom_sli_field_fallback(self):
        issue = {
            "customFieldValues": [
                {"customField": {"name": "SLI Date"}, "value": "2024-09-15T00:00:00Z"}
            ]
        }
        assert ljs.resolve_due_date(issue) == "2024-09-15"

    def test_none_project_does_not_crash(self):
        issue = {"dueDate": None, "slaBreachesAt": None, "project": None}
        assert ljs.resolve_due_date(issue) is None


# ─────────────────────────────────────────────────────────────────────────────
# 6. parse_selection  –  selection string parser
# ─────────────────────────────────────────────────────────────────────────────

class TestParseSelection:
    def test_all_keyword_returns_none(self):
        assert ljs.parse_selection("all", 10) is None

    def test_a_shorthand_returns_none(self):
        assert ljs.parse_selection("a", 10) is None

    def test_empty_string_returns_none(self):
        assert ljs.parse_selection("", 10) is None

    def test_single_number(self):
        assert ljs.parse_selection("3", 10) == {3}

    def test_comma_separated(self):
        assert ljs.parse_selection("1,3,5", 10) == {1, 3, 5}

    def test_range(self):
        assert ljs.parse_selection("2-5", 10) == {2, 3, 4, 5}

    def test_mixed_numbers_and_ranges(self):
        assert ljs.parse_selection("1,3-5,8", 10) == {1, 3, 4, 5, 8}

    def test_number_above_max_raises(self):
        with pytest.raises(ValueError):
            ljs.parse_selection("11", 10)

    def test_number_zero_raises(self):
        with pytest.raises(ValueError):
            ljs.parse_selection("0", 10)

    def test_inverted_range_raises(self):
        with pytest.raises(ValueError):
            ljs.parse_selection("5-3", 10)

    def test_range_exceeds_max_raises(self):
        with pytest.raises(ValueError):
            ljs.parse_selection("1-15", 10)

    def test_whitespace_tolerance(self):
        assert ljs.parse_selection("  2 , 4 ", 10) == {2, 4}


# ─────────────────────────────────────────────────────────────────────────────
# 7. _cycle_label  –  cycle display name
# ─────────────────────────────────────────────────────────────────────────────

class TestCycleLabel:
    def test_uses_name_when_present(self):
        assert ljs._cycle_label({"name": "Q1 Sprint", "number": 5}) == "Q1 Sprint"

    def test_falls_back_to_number(self):
        assert ljs._cycle_label({"name": "", "number": 3}) == "Cycle 3"

    def test_whitespace_name_falls_back_to_number(self):
        assert ljs._cycle_label({"name": "   ", "number": 7}) == "Cycle 7"

    def test_no_name_no_number_returns_question_mark(self):
        assert ljs._cycle_label({}) == "Cycle ?"

    def test_name_takes_priority_over_number(self):
        assert ljs._cycle_label({"name": "Sprint Alpha", "number": 99}) == "Sprint Alpha"


# ─────────────────────────────────────────────────────────────────────────────
# 8. ANSI terminal helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestVisibleLen:
    def test_plain_string(self):
        assert ljs._visible_len("hello") == 5

    def test_ignores_ansi_green(self):
        assert ljs._visible_len(f"\033[92mhello\033[0m") == 5

    def test_ignores_ansi_red(self):
        assert ljs._visible_len(f"\033[91mAB\033[0mCD") == 4

    def test_empty_string(self):
        assert ljs._visible_len("") == 0

    def test_only_ansi_codes(self):
        assert ljs._visible_len("\033[92m\033[0m") == 0


class TestPadDetail:
    def test_pads_plain_string(self):
        assert ljs._pad_detail("hi", 5) == "hi   "

    def test_no_padding_when_already_wide(self):
        assert ljs._pad_detail("hello world", 5) == "hello world"

    def test_ansi_aware_padding(self):
        s = f"\033[92mhi\033[0m"          # visible len = 2
        result = ljs._pad_detail(s, 5)
        assert ljs._visible_len(result) == 5

    def test_exact_width_no_padding(self):
        assert ljs._pad_detail("hello", 5) == "hello"


class TestTruncateAnsi:
    def test_truncates_plain_string(self):
        result = ljs._truncate_ansi("hello world", 5)
        visible = ljs._ANSI_RE.sub("", result)
        assert visible == "hello…"

    def test_short_string_not_truncated(self):
        result = ljs._truncate_ansi("hi", 10)
        assert "hi" in ljs._ANSI_RE.sub("", result)

    def test_always_ends_with_reset(self):
        result = ljs._truncate_ansi("test", 2)
        assert result.endswith(ljs._C_RESET)

    def test_preserves_ansi_codes(self):
        s = f"\033[92mhello world\033[0m"
        result = ljs._truncate_ansi(s, 5)
        assert "\033[92m" in result


# ─────────────────────────────────────────────────────────────────────────────
# 9. build_jira_fields  –  Jira create payload construction
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildJiraFields:
    def _build(self, issue_overrides=None, project_key="PROJ", issue_type="Story",
                sp_field=None, epic_name_field=None, epic_key=None,
                assignee_map=None, reporter_map=None, is_epic=False):
        return ljs.build_jira_fields(
            _issue(**(issue_overrides or {})),
            project_key, issue_type, PRIORITY_MAP,
            sp_field, epic_name_field, epic_key,
            assignee_map or {}, reporter_map or {},
            is_epic=is_epic,
        )

    def test_summary_set(self, capsys):
        fields = self._build()
        assert fields["summary"] == "Test issue"

    def test_project_key_set(self, capsys):
        fields = self._build(project_key="DESK")
        assert fields["project"] == {"key": "DESK"}

    def test_issuetype_set(self, capsys):
        fields = self._build(issue_type="Bug")
        assert fields["issuetype"] == {"name": "Bug"}

    def test_description_not_in_create_payload(self, capsys):
        fields = self._build()
        assert "description" not in fields

    def test_long_title_truncated_to_250(self, capsys):
        fields = self._build({"title": "A" * 300})
        assert len(fields["summary"]) == 251   # 250 chars + ellipsis
        assert fields["summary"].endswith("…")

    def test_title_exactly_250_not_truncated(self, capsys):
        fields = self._build({"title": "B" * 250})
        assert fields["summary"] == "B" * 250
        assert "…" not in fields["summary"]

    def test_story_points_integer(self, capsys):
        fields = self._build({"estimate": 5.0}, sp_field="customfield_10104")
        assert fields["customfield_10104"] == 5

    def test_story_points_float(self, capsys):
        fields = self._build({"estimate": 2.5}, sp_field="customfield_10104")
        assert fields["customfield_10104"] == 2.5

    def test_story_points_absent_when_none(self, capsys):
        fields = self._build(sp_field="customfield_10104")
        assert "customfield_10104" not in fields

    def test_story_points_absent_when_no_field_id(self, capsys):
        fields = self._build({"estimate": 3.0}, sp_field=None)
        assert "customfield_10104" not in fields

    def test_due_date_from_issue(self, capsys):
        fields = self._build({"dueDate": "2024-12-31"})
        assert fields["duedate"] == "2024-12-31"

    def test_no_due_date_field_absent(self, capsys):
        fields = self._build()
        assert "duedate" not in fields

    def test_sla_date_used_as_due_date(self, capsys):
        fields = self._build({"slaBreachesAt": "2024-11-01T00:00:00Z"})
        assert fields["duedate"] == "2024-11-01"

    def test_epic_key_sets_parent(self, capsys):
        fields = self._build(epic_key="EPIC-1")
        assert fields["parent"] == {"key": "EPIC-1"}

    def test_no_parent_when_is_epic(self, capsys):
        fields = self._build(epic_key="EPIC-1", is_epic=True)
        assert "parent" not in fields

    def test_epic_name_field_set_for_epics(self, capsys):
        fields = self._build(
            {"title": "My Epic"}, issue_type="Epic",
            epic_name_field="customfield_10011", is_epic=True,
        )
        assert fields["customfield_10011"] == "My Epic"

    def test_epic_name_field_not_set_for_non_epics(self, capsys):
        fields = self._build(epic_name_field="customfield_10011", is_epic=False)
        assert "customfield_10011" not in fields

    def test_linear_identifier_label_added(self, capsys):
        fields = self._build({"identifier": "DES-42"})
        assert "linear-DES-42" in fields["labels"]

    def test_non_type_label_included(self, capsys):
        fields = self._build({
            "labels": {"nodes": [{"name": "backend"}]},
            "identifier": "",
        })
        assert "backend" in fields["labels"]

    def test_type_labels_excluded_from_jira_labels(self, capsys):
        fields = self._build({
            "labels": {"nodes": [{"name": "Bug"}, {"name": "Feature Request"}]},
            "identifier": "",
        })
        labels = fields.get("labels", [])
        assert "Bug" not in labels
        assert "Feature-Request" not in labels

    def test_label_spaces_replaced_with_hyphens(self, capsys):
        fields = self._build({
            "labels": {"nodes": [{"name": "needs review"}]},
            "identifier": "",
        })
        assert "needs-review" in fields["labels"]

    def test_assignee_mapped(self, capsys):
        fields = self._build(
            {"assignee": {"email": "dev@co.com", "name": "Dev"}},
            assignee_map={"dev@co.com": "account-123"},
        )
        assert fields["assignee"] == {"accountId": "account-123"}

    def test_assignee_not_set_when_unmapped(self, capsys):
        fields = self._build({"assignee": {"email": "unknown@co.com", "name": "X"}})
        assert "assignee" not in fields

    def test_assignee_not_set_when_none(self, capsys):
        fields = self._build()
        assert "assignee" not in fields

    def test_reporter_mapped(self, capsys):
        fields = self._build(
            {"creator": {"email": "boss@co.com", "name": "Boss"}},
            reporter_map={"boss@co.com": "reporter-456"},
        )
        assert fields["reporter"] == {"accountId": "reporter-456"}

    def test_reporter_not_set_when_unmapped(self, capsys):
        fields = self._build({"creator": {"email": "ghost@co.com", "name": "Ghost"}})
        assert "reporter" not in fields

    def test_priority_urgent_maps_to_highest(self, capsys):
        fields = self._build({"priorityLabel": "Urgent"})
        assert fields["priority"] == {"name": "Highest"}

    def test_priority_unknown_defaults_to_medium(self, capsys):
        fields = self._build({"priorityLabel": "Whatever"})
        assert fields["priority"] == {"name": "Medium"}

    def test_no_priority_label_defaults_to_medium(self, capsys):
        fields = self._build({"priorityLabel": None})
        assert fields["priority"] == {"name": "Medium"}


# ─────────────────────────────────────────────────────────────────────────────
# 10. _try_create_issue  –  retry / self-healing logic
# ─────────────────────────────────────────────────────────────────────────────

class TestTryCreateIssue:
    def test_success_on_first_attempt(self):
        jira = MagicMock()
        jira.create_issue.return_value = {"key": "P-1"}
        assert ljs._try_create_issue(jira, {"summary": "X"}) == {"key": "P-1"}
        assert jira.create_issue.call_count == 1

    def test_retries_and_removes_reporter_on_rejection(self):
        jira = MagicMock()
        jira.create_issue.side_effect = [
            Exception('Jira 400: {"reporter": "field not on screen"}'),
            {"key": "P-2"},
        ]
        fields = {"summary": "X", "reporter": {"accountId": "r1"}}
        result = ljs._try_create_issue(jira, fields)
        assert result == {"key": "P-2"}
        second_fields = jira.create_issue.call_args_list[1][0][0]
        assert "reporter" not in second_fields

    def test_original_dict_not_mutated_by_reporter_retry(self):
        jira = MagicMock()
        jira.create_issue.side_effect = [
            Exception('Jira 400: {"reporter": "error"}'),
            {"key": "P-x"},
        ]
        original = {"summary": "X", "reporter": {"accountId": "r1"}}
        ljs._try_create_issue(jira, original)
        assert "reporter" in original  # must not be mutated

    def test_retries_parent_field_switches_to_customfield_10014(self):
        jira = MagicMock()
        jira.create_issue.side_effect = [
            Exception("Jira 400: parent link not allowed"),
            {"key": "P-3"},
        ]
        fields = {"summary": "X", "parent": {"key": "EPIC-1"}}
        result = ljs._try_create_issue(jira, fields)
        assert result == {"key": "P-3"}
        second_fields = jira.create_issue.call_args_list[1][0][0]
        assert "parent" not in second_fields
        assert second_fields.get("customfield_10014") == "EPIC-1"

    def test_retries_customfield_10014_removes_it(self):
        jira = MagicMock()
        jira.create_issue.side_effect = [
            Exception("Jira 400: customfield_10014 error"),
            {"key": "P-4"},
        ]
        fields = {"summary": "X", "customfield_10014": "EPIC-1"}
        result = ljs._try_create_issue(jira, fields)
        assert result == {"key": "P-4"}
        second_fields = jira.create_issue.call_args_list[1][0][0]
        assert "customfield_10014" not in second_fields

    def test_retries_array_type_field_error(self):
        jira = MagicMock()
        jira.create_issue.side_effect = [
            Exception('Jira 400: {"Labels": "data was not an array"}'),
            {"key": "P-5"},
        ]
        jira.get_fields.return_value = [{"id": "labels", "name": "Labels"}]
        fields = {"summary": "X", "labels": "wrong-value"}
        result = ljs._try_create_issue(jira, fields)
        assert result == {"key": "P-5"}
        second_fields = jira.create_issue.call_args_list[1][0][0]
        assert "labels" not in second_fields

    def test_raises_on_unrecoverable_error(self):
        jira = MagicMock()
        jira.create_issue.side_effect = Exception("Unexpected server error")
        with pytest.raises(Exception, match="Unexpected server error"):
            ljs._try_create_issue(jira, {"summary": "X"})

    def test_raises_after_exhausting_retries(self):
        jira = MagicMock()
        # Each attempt triggers reporter removal, but reporter keeps coming back somehow
        # Force 4 reporter errors so it exhausts retries
        jira.create_issue.side_effect = [
            Exception('{"reporter": "error"}'),
            Exception('{"reporter": "error"}'),
            Exception('{"reporter": "error"}'),
            Exception('{"reporter": "error"}'),
        ]
        with pytest.raises(Exception):
            ljs._try_create_issue(jira, {"summary": "X", "reporter": {"accountId": "r"}})


# ─────────────────────────────────────────────────────────────────────────────
# 11. ensure_sprint_map  –  find-or-create Jira sprints for Linear cycles
# ─────────────────────────────────────────────────────────────────────────────

class TestEnsureSprintMap:
    def _jira(self, boards=None, sprints=None):
        j = MagicMock()
        j.get_boards_for_project.return_value = boards or []
        j.get_sprints_for_board.return_value  = sprints or []
        return j

    def test_no_boards_returns_empty(self, capsys):
        j = self._jira(boards=[])
        assert ljs.ensure_sprint_map(j, "PROJ", []) == {}
        j.create_sprint.assert_not_called()

    def test_matches_existing_sprint_by_name(self, capsys):
        j = self._jira([{"id": 1}], [{"id": 42, "name": "Q1 Sprint"}])
        issues = [{"cycle": {"name": "Q1 Sprint", "number": 1}}]
        result = ljs.ensure_sprint_map(j, "PROJ", issues)
        assert result.get("q1 sprint") == 42
        j.create_sprint.assert_not_called()

    def test_match_is_case_insensitive(self, capsys):
        j = self._jira([{"id": 1}], [{"id": 10, "name": "Sprint One"}])
        issues = [{"cycle": {"name": "sprint one", "number": 1}}]
        result = ljs.ensure_sprint_map(j, "PROJ", issues)
        assert result.get("sprint one") == 10
        j.create_sprint.assert_not_called()

    def test_creates_missing_sprint_with_dates(self, capsys):
        j = self._jira([{"id": 5}], [])
        j.create_sprint.return_value = {"id": 99}
        issues = [{"cycle": {
            "name": "New Sprint", "number": 2,
            "startsAt": "2024-01-01T00:00:00Z",
            "endsAt":   "2024-01-14T00:00:00Z",
        }}]
        result = ljs.ensure_sprint_map(j, "PROJ", issues)
        j.create_sprint.assert_called_once_with(
            "New Sprint", 5, "2024-01-01T00:00:00Z", "2024-01-14T00:00:00Z"
        )
        assert result.get("new sprint") == 99

    def test_creates_sprint_using_number_label_when_no_name(self, capsys):
        j = self._jira([{"id": 1}], [])
        j.create_sprint.return_value = {"id": 77}
        issues = [{"cycle": {"name": "", "number": 4, "startsAt": None, "endsAt": None}}]
        ljs.ensure_sprint_map(j, "PROJ", issues)
        j.create_sprint.assert_called_once_with("Cycle 4", 1, None, None)

    def test_same_cycle_not_created_twice(self, capsys):
        j = self._jira([{"id": 1}], [])
        j.create_sprint.return_value = {"id": 50}
        # Two issues in the same cycle
        issues = [
            {"cycle": {"name": "Sprint 1", "number": 1, "startsAt": None, "endsAt": None}},
            {"cycle": {"name": "Sprint 1", "number": 1, "startsAt": None, "endsAt": None}},
        ]
        ljs.ensure_sprint_map(j, "PROJ", issues)
        assert j.create_sprint.call_count == 1

    def test_issues_without_cycle_are_ignored(self, capsys):
        j = self._jira([{"id": 1}], [])
        ljs.ensure_sprint_map(j, "PROJ", [{"cycle": None}, {}])
        j.create_sprint.assert_not_called()

    def test_sprint_creation_failure_warns_and_continues(self, capsys):
        j = self._jira([{"id": 1}], [])
        j.create_sprint.side_effect = Exception("board locked")
        issues = [{"cycle": {"name": "Broken", "number": 3, "startsAt": None, "endsAt": None}}]
        result = ljs.ensure_sprint_map(j, "PROJ", issues)  # must not raise
        assert "WARN" in capsys.readouterr().out
        assert "broken" not in result

    def test_uses_first_board_when_multiple(self, capsys):
        j = self._jira([{"id": 10}, {"id": 20}], [{"id": 5, "name": "S1"}])
        ljs.ensure_sprint_map(j, "PROJ", [])
        j.get_sprints_for_board.assert_called_once_with(10)


# ─────────────────────────────────────────────────────────────────────────────
# 12. linear_fetch_team_cycles  –  Linear Agile API (gql mocked)
# ─────────────────────────────────────────────────────────────────────────────

class TestLinearFetchTeamCycles:
    def _resp(self, nodes, has_next=False, cursor=None):
        return {"team": {"cycles": {
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
            "nodes": nodes,
        }}}

    def _node(self, cid, name, number, issue_ids=None):
        return {
            "id": cid, "name": name, "number": number,
            "startsAt": None, "endsAt": None,
            "issues": {"nodes": [{"id": i} for i in (issue_ids or [])]},
        }

    def test_fetches_single_page(self):
        nodes = [self._node("c1", "Sprint 1", 1, ["i1", "i2"])]
        with patch.object(ljs, "gql", return_value=self._resp(nodes)):
            result = ljs.linear_fetch_team_cycles("key", "team-1")
        assert len(result) == 1
        assert result[0]["name"] == "Sprint 1"
        assert result[0]["issueIds"] == {"i1", "i2"}

    def test_paginates_across_pages(self):
        page1 = [self._node("c1", "S1", 1)]
        page2 = [self._node("c2", "S2", 2)]
        responses = [
            {"team": {"cycles": {"pageInfo": {"hasNextPage": True,  "endCursor": "tok"}, "nodes": page1}}},
            {"team": {"cycles": {"pageInfo": {"hasNextPage": False, "endCursor": None},  "nodes": page2}}},
        ]
        with patch.object(ljs, "gql", side_effect=responses):
            result = ljs.linear_fetch_team_cycles("key", "team-1")
        assert len(result) == 2

    def test_empty_page_returns_empty_list(self):
        with patch.object(ljs, "gql", return_value=self._resp([])):
            result = ljs.linear_fetch_team_cycles("key", "team-1")
        assert result == []

    def test_api_error_returns_empty_list(self):
        with patch.object(ljs, "gql", side_effect=Exception("network error")):
            result = ljs.linear_fetch_team_cycles("key", "team-1")
        assert result == []

    def test_issue_ids_is_a_set(self):
        nodes = [self._node("c1", "S1", 1, ["i1", "i2", "i3"])]
        with patch.object(ljs, "gql", return_value=self._resp(nodes)):
            result = ljs.linear_fetch_team_cycles("key", "team-1")
        assert isinstance(result[0]["issueIds"], set)

    def test_cycle_with_no_issues_has_empty_set(self):
        nodes = [self._node("c1", "S1", 1, [])]
        with patch.object(ljs, "gql", return_value=self._resp(nodes)):
            result = ljs.linear_fetch_team_cycles("key", "team-1")
        assert result[0]["issueIds"] == set()


# ─────────────────────────────────────────────────────────────────────────────
# 13. _preview_detail_line  –  formatted table row
# ─────────────────────────────────────────────────────────────────────────────

class TestPreviewDetailLine:
    def test_unassigned_issue(self):
        line = ljs._preview_detail_line(_entry(), {}, {})
        assert "unassigned" in line

    def test_mapped_assignee_shown_in_green(self):
        line = ljs._preview_detail_line(
            _entry({"assignee": {"name": "Alice", "email": "alice@x.com"}}),
            {"alice@x.com": "aid"},
            {"alice@x.com": "Alice <alice@jira.com>"},
        )
        assert ljs._C_GREEN in line
        assert "Alice" in line

    def test_unmapped_assignee_shown_in_red(self):
        line = ljs._preview_detail_line(
            _entry({"assignee": {"name": "Bob", "email": "bob@x.com"}}),
            {}, {},
        )
        assert ljs._C_RED in line
        assert "bob@x.com" in line

    def test_labels_listed(self):
        line = ljs._preview_detail_line(
            _entry({"labels": {"nodes": [{"name": "backend"}, {"name": "urgent"}]}}),
            {}, {},
        )
        assert "backend" in line
        assert "urgent" in line

    def test_no_labels_shows_dash(self):
        line = ljs._preview_detail_line(_entry(), {}, {})
        assert "Labels: —" in line

    def test_story_points_integer_display(self):
        line = ljs._preview_detail_line(_entry({"estimate": 5.0}), {}, {})
        assert "Pts: 5" in line

    def test_story_points_float_display(self):
        line = ljs._preview_detail_line(_entry({"estimate": 1.5}), {}, {})
        assert "Pts: 1.5" in line

    def test_no_story_points_shows_dash(self):
        line = ljs._preview_detail_line(_entry(), {}, {})
        assert "Pts: —" in line

    def test_due_date_displayed(self):
        line = ljs._preview_detail_line(_entry({"dueDate": "2024-12-31"}), {}, {})
        assert "Due: 2024-12-31" in line

    def test_sla_date_displayed(self):
        line = ljs._preview_detail_line(
            _entry({"slaBreachesAt": "2024-11-01T00:00:00Z"}), {}, {},
        )
        assert "SLA: 2024-11-01" in line

    def test_both_due_and_sla_shown(self):
        line = ljs._preview_detail_line(
            _entry({"dueDate": "2024-12-31", "slaBreachesAt": "2024-11-01T00:00:00Z"}),
            {}, {},
        )
        assert "Due: 2024-12-31" in line
        assert "SLA: 2024-11-01" in line

    def test_no_dates_shows_dash(self):
        line = ljs._preview_detail_line(_entry(), {}, {})
        # date section should be a lone dash
        assert "   —   " in line or line.strip().endswith("—")

    def test_cycle_with_name_shown_in_cyan(self):
        line = ljs._preview_detail_line(
            _entry({"cycle": {"name": "Q1 Sprint", "number": 5}}), {}, {},
        )
        assert ljs._C_CYAN in line
        assert "Q1 Sprint" in line

    def test_cycle_name_and_number_combined(self):
        # When both name and number are present, format is "Cycle N: Name"
        line = ljs._preview_detail_line(
            _entry({"cycle": {"name": "Alpha", "number": 3}}), {}, {},
        )
        assert "Alpha" in line
        assert "Cycle 3: Alpha" in line

    def test_cycle_number_only_when_no_name(self):
        line = ljs._preview_detail_line(
            _entry({"cycle": {"name": "", "number": 3}}), {}, {},
        )
        assert "Cycle 3" in line

    def test_no_cycle_no_cycle_text(self):
        line = ljs._preview_detail_line(_entry({"cycle": None}), {}, {})
        assert "Cycle" not in line

    def test_state_displayed(self):
        line = ljs._preview_detail_line(_entry({"state": {"name": "Done"}}), {}, {})
        assert "State: Done" in line

    # Project (Epic) entries
    def test_project_entry_shows_lead(self):
        proj = {"name": "Proj", "lead": {"name": "Alice", "email": "a@x.com"}, "state": ""}
        line = ljs._preview_detail_line(_entry(is_project=True, project_item=proj), {}, {})
        assert "Lead:" in line
        assert "Alice" in line

    def test_project_entry_no_lead_shows_no_lead(self):
        proj = {"name": "Proj", "lead": None, "state": ""}
        line = ljs._preview_detail_line(_entry(is_project=True, project_item=proj), {}, {})
        assert "(no lead)" in line

    def test_project_entry_mapped_lead_is_green(self):
        proj = {"name": "Proj", "lead": {"name": "Alice", "email": "a@x.com"}, "state": ""}
        line = ljs._preview_detail_line(
            _entry(is_project=True, project_item=proj),
            {"a@x.com": "aid"}, {},
        )
        assert ljs._C_GREEN in line

    def test_project_entry_unmapped_lead_is_red(self):
        proj = {"name": "Proj", "lead": {"name": "Alice", "email": "a@x.com"}, "state": ""}
        line = ljs._preview_detail_line(
            _entry(is_project=True, project_item=proj), {}, {},
        )
        assert ljs._C_RED in line

    def test_project_entry_state_shown(self):
        proj = {"name": "Proj", "lead": None, "state": "in_progress"}
        line = ljs._preview_detail_line(_entry(is_project=True, project_item=proj), {}, {})
        assert "in progress" in line.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 14. build_user_map  –  Linear ↔ Jira user matching
# ─────────────────────────────────────────────────────────────────────────────

def _lu(*emails):
    return [{"id": f"lid-{i}", "email": e, "name": e.split("@")[0]}
            for i, e in enumerate(emails)]


def _ju(*pairs):
    return [{"emailAddress": e, "accountId": aid, "displayName": e.split("@")[0]}
            for e, aid in pairs]


class TestBuildUserMap:
    @patch("linear_jira_sync.load_user_csv", return_value={})
    @patch("linear_jira_sync.save_user_csv")
    def test_matched_user_in_user_map(self, _save, _load):
        report = {"unmatched_users": []}
        user_map, _ = ljs.build_user_map(
            _lu("dev@co.com"), _ju(("dev@co.com", "account-1")), report
        )
        assert user_map["dev@co.com"] == "account-1"

    @patch("linear_jira_sync.load_user_csv", return_value={})
    @patch("linear_jira_sync.save_user_csv")
    def test_matched_user_in_label_map(self, _save, _load):
        report = {"unmatched_users": []}
        _, label_map = ljs.build_user_map(
            _lu("dev@co.com"), _ju(("dev@co.com", "account-1")), report
        )
        assert "dev@co.com" in label_map

    @patch("linear_jira_sync.load_user_csv", return_value={})
    @patch("linear_jira_sync.save_user_csv")
    def test_unmatched_user_in_report(self, _save, _load):
        report = {"unmatched_users": []}
        ljs.build_user_map(_lu("ghost@co.com"), [], report)
        assert any(u["email"] == "ghost@co.com" for u in report["unmatched_users"])

    @patch("linear_jira_sync.load_user_csv", return_value={})
    @patch("linear_jira_sync.save_user_csv")
    def test_unmatched_user_not_in_user_map(self, _save, _load):
        report = {"unmatched_users": []}
        user_map, _ = ljs.build_user_map(_lu("ghost@co.com"), [], report)
        assert "ghost@co.com" not in user_map

    @patch("linear_jira_sync.load_user_csv", return_value={"dev@co.com": "jira@co.com"})
    @patch("linear_jira_sync.save_user_csv")
    def test_csv_override_maps_to_different_jira_email(self, _save, _load):
        report = {"unmatched_users": []}
        user_map, _ = ljs.build_user_map(
            _lu("dev@co.com"), _ju(("jira@co.com", "account-jira")), report
        )
        assert user_map["dev@co.com"] == "account-jira"

    @patch("linear_jira_sync.load_user_csv", return_value={})
    @patch("linear_jira_sync.save_user_csv")
    def test_empty_inputs_return_empty_maps(self, _save, _load):
        report = {"unmatched_users": []}
        user_map, label_map = ljs.build_user_map([], [], report)
        assert user_map == {}
        assert label_map == {}

    @patch("linear_jira_sync.load_user_csv", return_value={})
    @patch("linear_jira_sync.save_user_csv")
    def test_multiple_users_mixed_match(self, _save, _load):
        report = {"unmatched_users": []}
        user_map, _ = ljs.build_user_map(
            _lu("a@co.com", "b@co.com"),
            _ju(("a@co.com", "aid-a")),
            report,
        )
        assert user_map.get("a@co.com") == "aid-a"
        assert "b@co.com" not in user_map
        assert len(report["unmatched_users"]) == 1
        assert report["unmatched_users"][0]["email"] == "b@co.com"

    @patch("linear_jira_sync.load_user_csv", return_value={})
    @patch("linear_jira_sync.save_user_csv")
    def test_save_csv_called_when_new_users_found(self, mock_save, _load):
        report = {"unmatched_users": []}
        ljs.build_user_map(_lu("new@co.com"), [], report)
        mock_save.assert_called_once()

    @patch("linear_jira_sync.load_user_csv", return_value={"existing@co.com": ""})
    @patch("linear_jira_sync.save_user_csv")
    def test_save_csv_not_called_when_no_new_users(self, mock_save, _load):
        report = {"unmatched_users": []}
        ljs.build_user_map(_lu("existing@co.com"), [], report)
        mock_save.assert_not_called()
