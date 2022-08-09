import enum
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Union

import requests

# GitHub API CLient copied and adapted from
# https://github.com/alstr/todo-to-issue-action/blob/25c80e9c4999d107bec208af49974d329da26370/main.py
# Originally licensed under MIT license


class Issue:
    """Basic Issue model for collecting the necessary info to send to GitHub."""

    def __init__(
        self,
        title: str,
        labels: List[str],
        assignees: List[str],
        body: str,
        rustsec_id: str,
    ) -> None:
        self.title = title
        self.labels = labels
        self.assignees = assignees
        self.body = body

        self.rustsec_id = rustsec_id


class EntryType(enum.Enum):
    ERROR = "error"
    WARNING = "warning"

    def icon(self) -> str:
        if self == EntryType.ERROR:
            return "🛑"
        elif self == EntryType.WARNING:
            return "⚠️"
        else:
            return ""


class Entry:
    entry: Dict[str, Any]
    entry_type: EntryType
    warning_type: Optional[str] = None

    def __init__(
        self,
        entry: Dict[str, Any],
        entry_type: EntryType,
        warning_type: Optional[str] = None,
    ):
        self.entry = entry
        self.entry_type = entry_type
        self.warning_type = warning_type

    def _entry_table(self) -> str:
        advisory = self.entry["advisory"]

        if self.warning_type is None:
            warning = ""
        else:
            warning = f"\n| Warning            | {self.warning_type} |"
        unaffected = " OR ".join(self.entry["versions"]["unaffected"])
        if unaffected != "":
            unaffected = f"\n| Unaffected Versions | `{unaffected}` |"
        patched = " OR ".join(self.entry["versions"]["patched"])
        if patched == "":
            patched = "n/a"
        else:
            patched = f"`{patched}`"
        table = f"""| Details            |                                      |
| ---                | ---                                  |
| Package            | `{advisory['package']}`              |
| Version            | `{self.entry['package']['version']}` |{warning}
| URL                | <{advisory['url']}>                  |
| Patched Versions   | {patched}                            |{unaffected}
"""
        return table

    def format_as_markdown(self) -> str:
        advisory = self.entry["advisory"]

        entry_table = self._entry_table()
        md = f"""## {self.entry_type.icon()} {advisory['id']}: {advisory['title']}

{entry_table}

{advisory['description']}
"""
        return md

    def format_as_issue(self, labels: List[str], assignees: List[str]) -> Issue:
        advisory = self.entry["advisory"]

        entry_table = self._entry_table()

        title = f"{advisory['id']}: {advisory['title']}"
        body = f"""{entry_table}

{advisory['description']}"""
        return Issue(
            title=title,
            labels=labels,
            assignees=assignees,
            body=body,
            rustsec_id=advisory["id"],
        )


class GitHubClient:
    """Basic client for getting the last diff and creating/closing issues."""

    existing_issues: List[Dict[str, Any]] = []
    base_url = "https://api.github.com/"
    repos_url = f"{base_url}repos/"

    def __init__(self) -> None:
        self.repo = os.getenv("REPO")
        self.token = os.getenv("INPUT_TOKEN")
        self.issues_url = f"{self.repos_url}{self.repo}/issues"
        self.issue_headers = {
            "Content-Type": "application/json",
            "Authorization": f"token {self.token}",
        }
        # Retrieve the existing repo issues now so we can easily check them later.
        self._get_existing_issues()

    def _get_existing_issues(self, page: int = 1) -> None:
        """Populate the existing issues list."""
        params: Dict[str, Union[str, int]] = {
            "per_page": 100,
            "page": page,
            "state": "open",
        }
        list_issues_request = requests.get(
            self.issues_url, headers=self.issue_headers, params=params
        )
        print(f"DBG: {list_issues_request.status_code=}")
        if list_issues_request.status_code == 200:
            self.existing_issues.extend(
                [
                    issue
                    for issue in list_issues_request.json()
                    if issue["title"].startswith("RUSTSEC-")
                ]
            )
            links = list_issues_request.links
            if "next" in links:
                self._get_existing_issues(page + 1)

    def create_issue(self, issue: Issue) -> Optional[int]:
        """Create a dict containing the issue details and send it to GitHub."""
        title = issue.title

        # Check if the current issue already exists - if so, skip it.
        # The below is a simple and imperfect check based on the issue title.
        for existing_issue in self.existing_issues:
            if existing_issue["title"].startswith(issue.rustsec_id):
                if (
                    existing_issue["title"] == issue.title
                    and existing_issue["body"] == issue.body
                ):
                    print(f"Skipping {issue.rustsec_id} - already exists.")
                    return None
                else:
                    print(f"Update existing {issue.rustsec_id}.")
                    body = {"title": title, "body": issue.body}
                    update_request = requests.patch(
                        existing_issue["url"],
                        headers=self.issue_headers,
                        data=json.dumps(body),
                    )
                    return update_request.status_code

        new_issue_body = {"title": title, "body": issue.body, "labels": issue.labels}

        # We need to check if any assignees/milestone specified exist, otherwise issue creation will fail.
        valid_assignees = []
        for assignee in issue.assignees:
            assignee_url = f"{self.repos_url}{self.repo}/assignees/{assignee}"
            assignee_request = requests.get(
                url=assignee_url, headers=self.issue_headers
            )
            if assignee_request.status_code == 204:
                valid_assignees.append(assignee)
            else:
                print(f"Assignee {assignee} does not exist! Dropping this assignee!")
        new_issue_body["assignees"] = valid_assignees

        new_issue_request = requests.post(
            url=self.issues_url,
            headers=self.issue_headers,
            data=json.dumps(new_issue_body),
        )

        return new_issue_request.status_code

    def close_issue(self, issue: Dict[str, Any]) -> int:
        body = {"state": "closed"}
        close_request = requests.patch(
            issue["url"], headers=self.issue_headers, data=json.dumps(body)
        )
        return close_request.status_code


def create_summary(data: Dict[str, Any]) -> str:
    res = []

    # Collect summary information
    num_vulns: int = data["vulnerabilities"]["count"]
    num_warnings: int = 0
    num_warning_types: dict[str, int] = {}
    for warning_type, warnings in data["warnings"].items():
        num_warnings += len(warnings)
        num_warning_types[warning_type] = len(warnings)

    if num_vulns == 0:
        res.append("No vulnerabilities found.")
    elif num_vulns == 1:
        res.append("1 vulnerability found.")
    else:
        res.append(f"{num_vulns} vulnerabilities found.")

    if num_warnings == 0:
        res.append("No warnings found.")
    elif num_warnings == 1:
        res.append("1 warning found.")
    else:
        desc = ", ".join(
            f"{count}x {warning_type}"
            for warning_type, count in num_warning_types.items()
        )
        res.append(f"{num_warnings} warnings found ({desc}).")
    return " ".join(res)


def create_entries(data: Dict[str, Any]) -> List[Entry]:
    entries = []

    for vuln in data["vulnerabilities"]["list"]:
        entries.append(Entry(vuln, EntryType.ERROR))

    for warning_type, warnings in data["warnings"].items():
        for warning in warnings:
            entries.append(Entry(warning, EntryType.WARNING, warning_type=warning_type))
    return entries


def run() -> None:
    # Process ignore list of Rustsec IDs
    ignore_args = []
    ignores = os.environ["INPUT_IGNORE"].split(",")
    for ign in ignores:
        if ign.strip() != "":
            ignore_args.append("--ignore")
            ignore_args.append(ign)

    completed = subprocess.run(
        ["cargo", "audit", "--json"] + ignore_args,
        capture_output=True,
        text=True,
        check=False,
    )
    data = json.loads(completed.stdout)

    summary = create_summary(data)
    entries = create_entries(data)
    print(f"{len(entries)} entries found.")

    if os.environ["INPUT_DENY_WARNINGS"] == "true":
        for entry in entries:
            entry.entry_type = EntryType.ERROR

    # Print a summary of the found issues
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as step_summary:
        step_summary.write("# Rustsec Advisories\n\n")
        step_summary.write(summary)
        step_summary.write("\n")
        for entry in entries:
            step_summary.write(entry.format_as_markdown())
            step_summary.write("\n")
    print("Posted step summary")

    if os.environ["INPUT_CREATE_ISSUES"] == "true":
        # Post each entry as an issue to GitHub
        gh_client = GitHubClient()
        print("Create/Update issues")
        for entry in entries:
            issue = entry.format_as_issue(labels=[], assignees=[])
            gh_client.create_issue(issue)

        # Close all issues which no longer exist
        # First remove all still existing issues, then close the remaining ones
        num_existing_issues = len(gh_client.existing_issues)
        for entry in entries:
            for ex_issue in gh_client.existing_issues:
                if ex_issue["title"].startswith(entry.entry["advisory"]["id"]):
                    gh_client.existing_issues.remove(ex_issue)
        num_old_issues = len(gh_client.existing_issues)
        print(
            f"Close old issues: {num_existing_issues} exist, {len(entries)} current issues, {num_old_issues} old issues to close."
        )
        for ex_issue in gh_client.existing_issues:
            gh_client.close_issue(ex_issue)

    # Fail if any error exists
    if any(entry.entry_type == EntryType.ERROR for entry in entries):
        sys.exit(1)
    else:
        sys.exit(0)
