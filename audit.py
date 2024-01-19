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

# Timeout in seconds for requests methods
TIMEOUT = 30


def debug(message: str) -> None:
    """Print a debug message to the GitHub Action log"""
    newline = "\n"
    print(f"""::debug::{message.replace(newline, " ")}""")


class Issue:
    """Basic Issue model for collecting the necessary info to send to GitHub."""

    def __init__(
        self,
        title: str,
        labels: List[str],
        assignees: List[str],
        body: str,
        rustsec_id: str,  # Should be the start of the title
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

    def id(self) -> str:
        """
        Return the ID of the entry.
        """

        # IMPORTANT: Coordinate this value with the `_get_existing_issues` method below.
        # Any value returned here must also be present in the filtering there, since the id will be used in the issue title.

        advisory = self.entry.get("advisory", None)
        if advisory:
            return advisory["id"]
        else:
            return f"Crate {self.entry['package']['name']} {self.entry['package']['version']}"

    def _entry_table(self) -> str:
        advisory = self.entry.get("advisory", None)

        if advisory:
            table = []
            table.append(("Details", ""))
            table.append(("---", "---"))
            table.append(("Package", f"`{advisory['package']}`"))
            table.append(("Version", f"`{self.entry['package']['version']}`"))
            if self.warning_type is not None:
                table.append(("Warning", str(self.warning_type)))
            table.append(("URL", advisory["url"]))
            table.append(
                (
                    "Patched Versions",
                    " OR ".join(self.entry["versions"]["patched"])
                    if len(self.entry["versions"]["patched"]) > 0
                    else "n/a",
                )
            )
            if len(self.entry["versions"]["unaffected"]) > 0:
                table.append(
                    (
                        "Unaffected Versions",
                        " OR ".join(self.entry["versions"]["unaffected"]),
                    )
                )
            if len(advisory["aliases"]) > 0:
                table.append(
                    (
                        "Aliases",
                        ", ".join(
                            Entry._md_autolink_advisory_id(advisory_id)
                            for advisory_id in advisory["aliases"]
                        ),
                    )
                )
            if len(advisory["related"]) > 0:
                table.append(
                    (
                        "Related Advisories",
                        ", ".join(
                            Entry._md_autolink_advisory_id(advisory_id)
                            for advisory_id in advisory["related"]
                        ),
                    )
                )

            table_parts = []
            for row in table:
                table_parts.append("| ")
                if row[0] is not None:
                    table_parts.append(row[0])
                table_parts.append(" | ")
                if row[1] is not None:
                    table_parts.append(row[1])
                else:
                    table_parts.append("n/a")
                table_parts.append(" |\n")

            return "".join(table_parts)
        else:
            # There is no advisory.
            # This occurs when a yanked version is detected.

            name = self.entry["package"]["name"]
            return f"""{self.id()} is yanked.
Switch to a different version of `{name}` to resolve this issue.
"""

    @classmethod
    def _md_autolink_advisory_id(cls, advisory_id: str) -> str:
        """
        If a supported advisory format, such as GHSA- is detected, return a markdown link.
        Otherwise return the ID as text.
        """

        if advisory_id.startswith("GHSA-"):
            return f"[{advisory_id}](https://github.com/advisories/{advisory_id})"
        if advisory_id.startswith("CVE-"):
            return f"[{advisory_id}](https://nvd.nist.gov/vuln/detail/{advisory_id})"
        if advisory_id.startswith("RUSTSEC-"):
            return f"[{advisory_id}](https://rustsec.org/advisories/{advisory_id})"
        return advisory_id

    def format_as_markdown(self) -> str:
        advisory = self.entry.get("advisory", None)

        if advisory:
            entry_table = self._entry_table()
            # Replace the @ with a ZWJ to avoid triggering markdown autolinks
            # Otherwise GitHub will interpret the @ as a mention
            description = advisory["description"].replace("@", "@\u200d")

            md = f"""## {self.entry_type.icon()} {advisory['id']}: {advisory['title']}

{entry_table}

{description}
"""
            return md
        else:
            # There is no advisory.
            # This occurs when a yanked version is detected.

            name = self.entry["package"]["name"]
            return f"""## {self.entry_type.icon()} {self.id()} is yanked.

Switch to a different version of `{name}` to resolve this issue.
"""

    def format_as_issue(self, labels: List[str], assignees: List[str]) -> Issue:
        advisory = self.entry.get("advisory", None)

        if advisory:
            entry_table = self._entry_table()

            title = f"{self.id()}: {advisory['title']}"
            body = f"""{entry_table}

{advisory['description']}"""
            return Issue(
                title=title,
                labels=labels,
                assignees=assignees,
                body=body,
                rustsec_id=self.id(),
            )
        else:
            # There is no advisory.
            # This occurs when a yanked version is detected.

            name = self.entry["package"]["name"]
            title = f"{self.id()} is yanked"
            body = (
                f"""Switch to a different version of `{name}` to resolve this issue."""
            )
            return Issue(
                title=title,
                labels=labels,
                assignees=assignees,
                body=body,
                rustsec_id=self.id(),
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

        debug("Existing issues:")
        for issue in self.existing_issues:
            debug(f"* {issue['title']}")

    def _get_existing_issues(self, page: int = 1) -> None:
        """Populate the existing issues list."""
        params: Dict[str, Union[str, int]] = {
            "per_page": 100,
            "page": page,
            "state": "open",
        }
        debug(f"Fetching existing issues from GitHub: {page=}")
        list_issues_request = requests.get(
            self.issues_url, headers=self.issue_headers, params=params, timeout=TIMEOUT
        )
        if list_issues_request.status_code == 200:
            self.existing_issues.extend(
                [
                    issue
                    for issue in list_issues_request.json()
                    if issue["title"].startswith("RUSTSEC-")
                    or issue["title"].startswith("Crate ")
                ]
            )
            links = list_issues_request.links
            if "next" in links:
                self._get_existing_issues(page + 1)

    def create_issue(self, issue: Issue) -> Optional[int]:
        """Create a dict containing the issue details and send it to GitHub."""
        title = issue.title
        debug(f"Creating issue: {title=}")

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
                        timeout=TIMEOUT,
                    )
                    return update_request.status_code

        debug(
            f"""No existing issue found for "{issue.rustsec_id}". Creating new issue."""
        )

        new_issue_body = {"title": title, "body": issue.body, "labels": issue.labels}

        # We need to check if any assignees/milestone specified exist, otherwise issue creation will fail.
        valid_assignees = []
        for assignee in issue.assignees:
            assignee_url = f"{self.repos_url}{self.repo}/assignees/{assignee}"
            assignee_request = requests.get(
                url=assignee_url,
                headers=self.issue_headers,
                timeout=TIMEOUT,
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
            timeout=TIMEOUT,
        )

        return new_issue_request.status_code

    def close_issue(self, issue: Dict[str, Any]) -> int:
        body = {"state": "closed"}
        close_request = requests.patch(
            issue["url"],
            headers=self.issue_headers,
            data=json.dumps(body),
            timeout=TIMEOUT,
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

    extra_args = []
    if os.environ["INPUT_DENY_WARNINGS"] == "true":
        extra_args.append("--deny")
        extra_args.append("warnings")

    if os.environ["INPUT_FILE"] != "":
        extra_args.append("--file")
        extra_args.append(os.environ["INPUT_FILE"])

    audit_cmd = ["cargo", "audit", "--json"] + extra_args + ignore_args
    debug(f"Running command: {audit_cmd}")
    completed = subprocess.run(
        audit_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    debug(f"Command return code: {completed.returncode}")
    debug(f"Command output: {completed.stdout}")
    debug(f"Command error: {completed.stderr}")
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
                if ex_issue["title"].startswith(entry.id()):
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
