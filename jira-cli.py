#!/usr/bin/env python3
"""
jira-cli: Compact Jira CLI for reducing token usage in AI-assisted workflows.

Required environment variables:
  JIRA_BASE_URL   — Jira instance URL (e.g., https://yourorg.atlassian.net)
  JIRA_EMAIL      — Jira account email
  JIRA_API_TOKEN  — Jira API token

Optional:
  JIRA_DEFAULT_PROJECT — Default project key for create/components (default: none)
  JIRA_DEFAULT_BOARD   — Default board ID for sprints (default: none)
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from base64 import b64encode
from textwrap import indent


def get_base_url():
    url = os.environ.get("JIRA_BASE_URL")
    if not url:
        print("Error: JIRA_BASE_URL must be set (e.g., https://yourorg.atlassian.net)", file=sys.stderr)
        sys.exit(1)
    return url.rstrip("/")
ISSUE_FIELDS = "summary,status,issuetype,priority,assignee,reporter,parent,subtasks,components,labels,created,updated,description,comment,fixVersions,sprint"
COMPACT_FIELDS = "summary,status,issuetype,priority,assignee,parent,components,labels"


def get_auth():
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not email or not token:
        print("Error: JIRA_EMAIL and JIRA_API_TOKEN must be set in environment", file=sys.stderr)
        sys.exit(1)
    cred = b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {cred}", "Content-Type": "application/json", "Accept": "application/json"}


def api_get(path, params=None):
    url = f"{get_base_url()}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=get_auth())
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def api_post(path, data):
    url = f"{get_base_url()}{path}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers=get_auth(), method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()) if resp.status != 204 else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def api_put(path, data):
    url = f"{get_base_url()}{path}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers=get_auth(), method="PUT")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()) if resp.length else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


# --- Formatters ---

def fmt_person(obj):
    if not obj:
        return "Unassigned"
    return obj.get("displayName", obj.get("emailAddress", "?"))


def fmt_issue_compact(fields, key):
    status = fields.get("status", {}).get("name", "?")
    itype = fields.get("issuetype", {}).get("name", "?")
    priority = fields.get("priority", {}).get("name", "?")
    summary = fields.get("summary", "?")
    assignee = fmt_person(fields.get("assignee"))
    return f"[{key}] {itype} | {status} | P:{priority} | @{assignee} — {summary}"


def fmt_issue_full(data):
    f = data["fields"]
    key = data["key"]
    lines = []
    lines.append(f"Key:        {key}")
    lines.append(f"Type:       {f.get('issuetype', {}).get('name', '?')}")
    lines.append(f"Status:     {f.get('status', {}).get('name', '?')}")
    lines.append(f"Priority:   {f.get('priority', {}).get('name', '?')}")
    lines.append(f"Summary:    {f.get('summary', '?')}")
    lines.append(f"Assignee:   {fmt_person(f.get('assignee'))}")
    lines.append(f"Reporter:   {fmt_person(f.get('reporter'))}")

    parent = f.get("parent")
    if parent:
        pf = parent.get("fields", {})
        lines.append(f"Parent:     [{parent['key']}] {pf.get('summary', '?')}")

    components = [c["name"] for c in f.get("components", [])]
    if components:
        lines.append(f"Components: {', '.join(components)}")

    labels = f.get("labels", [])
    if labels:
        lines.append(f"Labels:     {', '.join(labels)}")

    fix_versions = [v["name"] for v in f.get("fixVersions", [])]
    if fix_versions:
        lines.append(f"Fix Ver:    {', '.join(fix_versions)}")

    sprint_field = f.get("sprint")
    if sprint_field:
        sname = sprint_field.get("name", "?") if isinstance(sprint_field, dict) else str(sprint_field)
        lines.append(f"Sprint:     {sname}")

    lines.append(f"Created:    {f.get('created', '?')[:10]}")
    lines.append(f"Updated:    {f.get('updated', '?')[:10]}")

    # Description — first 500 chars of plain text
    desc = f.get("description")
    if desc:
        desc_text = extract_adf_text(desc) if isinstance(desc, dict) else str(desc)
        if desc_text:
            truncated = desc_text[:500] + ("..." if len(desc_text) > 500 else "")
            lines.append(f"Description:\n{indent(truncated, '  ')}")

    # Subtasks
    subtasks = f.get("subtasks", [])
    if subtasks:
        lines.append(f"Subtasks ({len(subtasks)}):")
        for st in subtasks:
            sf = st.get("fields", {})
            status = sf.get("status", {}).get("name", "?")
            lines.append(f"  [{st['key']}] {status} — {sf.get('summary', '?')}")

    # Recent comments (last 3)
    comment_data = f.get("comment", {})
    comments = comment_data.get("comments", []) if isinstance(comment_data, dict) else []
    if comments:
        recent = comments[-3:]
        lines.append(f"Comments ({comment_data.get('total', len(comments))} total, last {len(recent)}):")
        for c in recent:
            author = fmt_person(c.get("author"))
            date = c.get("created", "?")[:10]
            body_raw = c.get("body", "")
            body = extract_adf_text(body_raw) if isinstance(body_raw, dict) else str(body_raw)
            body = body[:200] + ("..." if len(body) > 200 else "")
            lines.append(f"  [{date}] @{author}: {body}")

    return "\n".join(lines)


def extract_adf_text(node):
    """Recursively extract plain text from Atlassian Document Format."""
    if not isinstance(node, dict):
        return ""
    parts = []
    if node.get("type") == "text":
        parts.append(node.get("text", ""))
    for child in node.get("content", []):
        parts.append(extract_adf_text(child))
    return " ".join(parts).strip()


# --- Commands ---

def cmd_issue(args):
    """Get issue details."""
    data = api_get(f"/rest/api/3/issue/{args.key}", {"fields": ISSUE_FIELDS})
    if args.compact:
        print(fmt_issue_compact(data["fields"], data["key"]))
    else:
        print(fmt_issue_full(data))


def cmd_parent(args):
    """Get parent issue."""
    data = api_get(f"/rest/api/3/issue/{args.key}", {"fields": "parent"})
    parent = data["fields"].get("parent")
    if not parent:
        print(f"{args.key} has no parent.")
        return
    parent_data = api_get(f"/rest/api/3/issue/{parent['key']}", {"fields": ISSUE_FIELDS})
    print(fmt_issue_full(parent_data))


def cmd_children(args):
    """Get subtasks / child issues."""
    # First try subtasks field
    data = api_get(f"/rest/api/3/issue/{args.key}", {"fields": "subtasks,issuetype,summary"})
    subtasks = data["fields"].get("subtasks", [])

    if subtasks:
        print(f"Subtasks of {args.key} ({len(subtasks)}):")
        for st in subtasks:
            sf = st.get("fields", {})
            status = sf.get("status", {}).get("name", "?")
            itype = sf.get("issuetype", {}).get("name", "?")
            assignee = fmt_person(sf.get("assignee"))
            print(f"  [{st['key']}] {itype} | {status} | @{assignee} — {sf.get('summary', '?')}")
    else:
        # Search for child issues (epics, stories with children)
        jql = f"parent = {args.key} ORDER BY rank ASC"
        results = api_post("/rest/api/3/search/jql", {"jql": jql, "fields": COMPACT_FIELDS.split(","), "maxResults": 50})
        issues = results.get("issues", [])
        if not issues:
            print(f"{args.key} has no children or subtasks.")
            return
        print(f"Children of {args.key} ({results.get('total', len(issues))}):")
        for iss in issues:
            print(f"  {fmt_issue_compact(iss['fields'], iss['key'])}")


def cmd_create(args):
    """Create a new issue."""
    payload = {
        "fields": {
            "project": {"key": args.project},
            "summary": args.summary,
            "issuetype": {"name": args.type},
        }
    }
    if args.parent:
        payload["fields"]["parent"] = {"key": args.parent}
    if args.component:
        payload["fields"]["components"] = [{"name": args.component}]
    if args.description:
        payload["fields"]["description"] = {
            "version": 1,
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": args.description}]}]
        }
    if args.label:
        payload["fields"]["labels"] = args.label
    if args.assignee:
        # Look up account ID
        users = api_get("/rest/api/3/user/search", {"query": args.assignee, "maxResults": 1})
        if users:
            payload["fields"]["assignee"] = {"accountId": users[0]["accountId"]}
        else:
            print(f"Warning: user '{args.assignee}' not found, creating without assignee", file=sys.stderr)

    result = api_post("/rest/api/3/issue", payload)
    print(f"Created: {result['key']} — {get_base_url()}/browse/{result['key']}")


def cmd_comment(args):
    """Add a comment to an issue."""
    body = {
        "body": {
            "version": 1,
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": args.message}]}]
        }
    }
    api_post(f"/rest/api/3/issue/{args.key}/comment", body)
    print(f"Comment added to {args.key}.")


def cmd_transitions(args):
    """List available transitions for an issue."""
    data = api_get(f"/rest/api/3/issue/{args.key}/transitions")
    transitions = data.get("transitions", [])
    if not transitions:
        print(f"No transitions available for {args.key}.")
        return
    current = api_get(f"/rest/api/3/issue/{args.key}", {"fields": "status"})
    print(f"Current status: {current['fields']['status']['name']}")
    print("Available transitions:")
    for t in transitions:
        to_status = t.get("to", {}).get("name", "?")
        print(f"  [{t['id']}] {t['name']} → {to_status}")


def cmd_transition(args):
    """Transition an issue to a new status."""
    # Find the transition ID by name
    data = api_get(f"/rest/api/3/issue/{args.key}/transitions")
    transitions = data.get("transitions", [])

    target = args.status.lower()
    match = None
    for t in transitions:
        if t["name"].lower() == target or t.get("to", {}).get("name", "").lower() == target:
            match = t
            break
        # Partial match
        if target in t["name"].lower() or target in t.get("to", {}).get("name", "").lower():
            match = t

    if not match:
        names = [f"{t['name']} → {t.get('to', {}).get('name', '?')}" for t in transitions]
        print(f"No transition matching '{args.status}'. Available: {', '.join(names)}", file=sys.stderr)
        sys.exit(1)

    api_post(f"/rest/api/3/issue/{args.key}/transitions", {"transition": {"id": match["id"]}})
    print(f"{args.key} → {match.get('to', {}).get('name', match['name'])}")


def cmd_search(args):
    """Search issues with JQL."""
    results = api_post("/rest/api/3/search/jql", {
        "jql": args.jql,
        "fields": COMPACT_FIELDS.split(","),
        "maxResults": args.max or 20
    })
    issues = results.get("issues", [])
    total = results.get("total", 0)
    print(f"Results: {total} total (showing {len(issues)})")
    for iss in issues:
        print(f"  {fmt_issue_compact(iss['fields'], iss['key'])}")


def cmd_assign(args):
    """Assign an issue to a user."""
    users = api_get("/rest/api/3/user/search", {"query": args.user, "maxResults": 1})
    if not users:
        print(f"User '{args.user}' not found.", file=sys.stderr)
        sys.exit(1)
    api_put(f"/rest/api/3/issue/{args.key}/assignee", {"accountId": users[0]["accountId"]})
    print(f"{args.key} assigned to {users[0]['displayName']}.")


def cmd_components(args):
    """List components for a project."""
    data = api_get(f"/rest/api/3/project/{args.project}/components")
    if not data:
        print(f"No components for {args.project}.")
        return
    for c in data:
        lead = fmt_person(c.get("lead"))
        print(f"  {c['name']} (lead: {lead})")


def cmd_sprints(args):
    """List active/future sprints for a board."""
    board_id = args.board or os.environ.get("JIRA_DEFAULT_BOARD")
    if not board_id:
        print("Error: --board required (or set JIRA_DEFAULT_BOARD)", file=sys.stderr)
        sys.exit(1)
    data = api_get(f"/rest/agile/1.0/board/{board_id}/sprint", {"state": "active,future", "maxResults": 5})
    sprints = data.get("values", [])
    if not sprints:
        print("No active/future sprints.")
        return
    for s in sprints:
        start = s.get("startDate", "?")[:10]
        end = s.get("endDate", "?")[:10]
        print(f"  [{s['id']}] {s['name']} | {s['state']} | {start} → {end}")


def main():
    parser = argparse.ArgumentParser(
        prog="jira-cli",
        description="Compact Jira CLI — reduces token usage by 10-30x vs raw API responses."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # issue
    p = sub.add_parser("issue", aliases=["i"], help="Get issue details")
    p.add_argument("key", help="Issue key (e.g., ENG-1234)")
    p.add_argument("-c", "--compact", action="store_true", help="One-line summary")
    p.set_defaults(func=cmd_issue)

    # parent
    p = sub.add_parser("parent", aliases=["p"], help="Get parent issue")
    p.add_argument("key")
    p.set_defaults(func=cmd_parent)

    # children
    p = sub.add_parser("children", aliases=["ch"], help="Get subtasks/children")
    p.add_argument("key")
    p.set_defaults(func=cmd_children)

    # create
    p = sub.add_parser("create", aliases=["new"], help="Create issue")
    default_project = os.environ.get("JIRA_DEFAULT_PROJECT")
    p.add_argument("--project", "-p", default=default_project, required=not default_project,
                   help="Project key (or set JIRA_DEFAULT_PROJECT env var)")
    p.add_argument("--summary", "-s", required=True, help="Issue summary")
    p.add_argument("--type", "-t", default="Task", help="Issue type (default: Task)")
    p.add_argument("--parent", help="Parent issue key")
    p.add_argument("--component", help="Component name")
    p.add_argument("--description", "-d", help="Description text")
    p.add_argument("--label", "-l", action="append", help="Label (repeatable)")
    p.add_argument("--assignee", "-a", help="Assignee name/email")
    p.set_defaults(func=cmd_create)

    # comment
    p = sub.add_parser("comment", aliases=["cm"], help="Add comment")
    p.add_argument("key")
    p.add_argument("message")
    p.set_defaults(func=cmd_comment)

    # transitions
    p = sub.add_parser("transitions", aliases=["tr"], help="List available transitions")
    p.add_argument("key")
    p.set_defaults(func=cmd_transitions)

    # transition
    p = sub.add_parser("transition", aliases=["mv"], help="Transition issue status")
    p.add_argument("key")
    p.add_argument("status", help="Target status name (partial match OK)")
    p.set_defaults(func=cmd_transition)

    # search
    p = sub.add_parser("search", aliases=["s"], help="Search with JQL")
    p.add_argument("jql")
    p.add_argument("-m", "--max", type=int, default=20, help="Max results")
    p.set_defaults(func=cmd_search)

    # assign
    p = sub.add_parser("assign", help="Assign issue")
    p.add_argument("key")
    p.add_argument("user", help="User name or email")
    p.set_defaults(func=cmd_assign)

    # components
    p = sub.add_parser("components", aliases=["comp"], help="List project components")
    p.add_argument("project", nargs="?", default=os.environ.get("JIRA_DEFAULT_PROJECT"))
    p.set_defaults(func=cmd_components)

    # sprints
    p = sub.add_parser("sprints", aliases=["sp"], help="List active/future sprints")
    p.add_argument("--board", "-b", help="Board ID (default: 731)")
    p.set_defaults(func=cmd_sprints)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
