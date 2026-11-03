#!/usr/bin/env python3
"""ClickUp CLI for agents, configured per project by a `.clickup.json` file.

Config, first found: $CLICKUP_CONFIG; `.clickup.json` in the current directory or any parent; the same file in
the main checkout when running inside a git worktree. `cu.py init` writes one.

Token, first found (never put the token in the config file): $CLICKUP_TOKEN; the env var named by the config's
"token_env"; the file $CLICKUP_TOKEN_FILE; each of the config's "token_files" (relative to the project root,
also tried in the main checkout so worktrees find gitignored files); <root>/.clickup-token;
~/.config/clickup/token.

A token file holds either one token or several accounts as JSON: {"work": "pk_…", "personal": "pk_…"}.
The account is picked by $CLICKUP_ACCOUNT, else the config's "account", else a "default" entry, else the only one.

Usage:
  cu.py config                                                    # effective project config (no secrets)
  cu.py accounts                                                  # account names in the token file in use
  cu.py init --list-id ID [--from-list] [--force]                 # write .clickup.json in the current dir
  cu.py browse                                                    # workspaces > spaces > folders > lists, with IDs
  cu.py create-space --name NAME [--workspace ID]
  cu.py create-folder --name NAME [--space ID]
  cu.py create-list --name NAME [--space ID | --folder ID] [--description MD] [--due YYYY-MM-DD] [--use]
  cu.py sprints [--folder ID]                                     # sprint lists with start/end dates
  cu.py create-sprint [--folder ID] [--name N] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--dry-run]
  cu.py template                                                  # print the project's ticket body template
  cu.py list [--parent ID|ALIAS] [--status S] [--grep TEXT] [--open]
  cu.py show TASK_ID                                              # full ticket incl. markdown body
  cu.py create --name NAME --body FILE.md [--parent ID|ALIAS] [--tags a,b] [--priority 1-4] [--status S]
  cu.py update TASK_ID [--name NAME] [--body FILE.md] [--status S] [--priority 1-4] [--add-tags a,b]
  cu.py comment TASK_ID --body FILE.md                            # "-" reads stdin
  cu.py statuses [--apply] [--force]                              # show (or enforce) the configured statuses

Ticket bodies are checked for the config's "required_sections" on create and on update with --body.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.clickup.com/api/v2"
CONFIG_NAME = ".clickup.json"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED_TEMPLATE = os.path.join(SKILL_DIR, "templates", "ticket-template.md")

DEFAULTS = {
    "list_id": None,
    "name": None,
    "account": None,
    "token_env": None,
    "token_files": [],
    # None = accept whatever statuses the live list has; a list = validate against it and allow `statuses --apply`.
    "statuses": None,
    "default_status": None,
    "status_aliases": {},
    "required_sections": ["## Details", "## Why", "## How", "## Implementation details", "## Acceptance criteria"],
    "template": None,
    "name_pattern": None,
    "parents": {},
    "default_tags": [],
    "workflow": [],
    # Where create-space / create-list / create-sprint put things when no --workspace/--space/--folder is given.
    "workspace_id": None,
    "space_id": None,
    "sprint_folder_id": None,
    "sprint_length_days": 14,
    "sprint_name": "Sprint {n} ({start:%b %d} - {end:%b %d})",
}


def git_main_root(path):
    """Root of the main checkout: worktrees don't get untracked/ignored files, so resolve via the common git dir."""
    try:
        common = subprocess.run(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
        return os.path.dirname(common)
    except (OSError, subprocess.CalledProcessError):
        return None


def find_config(start):
    if os.environ.get("CLICKUP_CONFIG"):
        return os.path.abspath(os.path.expanduser(os.environ["CLICKUP_CONFIG"]))
    d = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(d, CONFIG_NAME)):
            return os.path.join(d, CONFIG_NAME)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    main = git_main_root(start)
    if main and os.path.isfile(os.path.join(main, CONFIG_NAME)):
        return os.path.join(main, CONFIG_NAME)
    return None


def load_config(start="."):
    path = find_config(start)
    cfg = dict(DEFAULTS)
    cfg["_path"], cfg["_root"] = path, os.path.dirname(path) if path else os.path.abspath(start)
    if path:
        try:
            data = json.load(open(path))
        except (OSError, ValueError) as e:
            sys.exit(f"Cannot read {path}: {e}")
        if "token" in data:
            sys.exit(f"{path} contains a 'token' key: never store the token in the config; use token_files/token_env")
        unknown = set(data) - set(DEFAULTS) - {"$schema", "_comment"}
        if unknown:
            sys.exit(f"{path}: unknown keys {sorted(unknown)}. Valid: {sorted(DEFAULTS)}")
        cfg.update(data)
    if os.environ.get("CLICKUP_LIST_ID"):
        cfg["list_id"] = os.environ["CLICKUP_LIST_ID"]
    if cfg["statuses"] is not None:
        cfg["statuses"] = [s if isinstance(s, dict) else {"name": s} for s in cfg["statuses"]]
    return cfg


CFG = None


def cfg():
    global CFG
    if CFG is None:
        CFG = load_config()
    return CFG


def list_id():
    if not cfg()["list_id"]:
        sys.exit(f"No list_id: run `cu.py browse` to find the list, then `cu.py init --list-id ID` "
                 f"(or set CLICKUP_LIST_ID). Looked for {CONFIG_NAME} from {os.getcwd()}")
    return cfg()["list_id"]


def resolve_path(p):
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(cfg()["_root"], p)


def git_toplevel(path):
    try:
        return subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def token_candidates():
    root = cfg()["_root"]
    main, top = git_main_root(root), git_toplevel(root)
    in_worktree = main and top and os.path.realpath(main) != os.path.realpath(top)
    files = [os.environ["CLICKUP_TOKEN_FILE"]] if os.environ.get("CLICKUP_TOKEN_FILE") else []
    for rel in list(cfg()["token_files"]) + [".clickup-token"]:
        files.append(resolve_path(rel))
        if in_worktree:  # same relative path inside the main checkout, where gitignored files actually live
            files.append(os.path.join(main, os.path.relpath(os.path.realpath(resolve_path(rel)), os.path.realpath(top))))
    files.append("~/.config/clickup/token")
    return list(dict.fromkeys(os.path.expanduser(f) for f in files))


def account():
    return os.environ.get("CLICKUP_ACCOUNT") or cfg()["account"]


def pick_account(path, accounts):
    """A token file may hold {"account-a": "pk_…", "account-b": "pk_…"}; the config's "account" picks one."""
    if not isinstance(accounts, dict) or not all(isinstance(v, str) for v in accounts.values()):
        sys.exit(f'{path}: a JSON token file must be an object of account name -> token, e.g. {{"work": "pk_…"}}')
    name = account()
    if name:
        if name not in accounts:
            sys.exit(f"{path} has no account {name!r}. Accounts: {sorted(accounts)}")
        return name
    if "default" in accounts:
        return "default"
    if len(accounts) == 1:
        return next(iter(accounts))
    sys.exit(f'{path} holds several accounts {sorted(accounts)}: set "account" in {CONFIG_NAME} '
             f"or CLICKUP_ACCOUNT (or add a \"default\" entry)")


def token_source():
    """(token, source, account): source is an env var name or a file path, never the token itself."""
    if os.environ.get("CLICKUP_TOKEN"):
        return os.environ["CLICKUP_TOKEN"].strip(), "CLICKUP_TOKEN env", None
    env = cfg()["token_env"]
    if env and os.environ.get(env):
        return os.environ[env].strip(), f"{env} env", None
    for path in token_candidates():
        try:
            text = open(path).read().strip()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if not text.startswith("{"):
            return text, path, None
        try:
            accounts = json.loads(text)
        except ValueError as e:
            sys.exit(f"{path}: invalid JSON ({e.msg} at line {e.lineno})")
        name = pick_account(path, accounts)
        return accounts[name].strip(), path, name
    sys.exit("No ClickUp token: set CLICKUP_TOKEN, or create ~/.config/clickup/token or <project>/.clickup-token "
             "(chmod 600, gitignored)")


def token():
    return token_source()[0]


def req(method, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(API + path, data=body, method=method,
                               headers={"Authorization": token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()}")


_live = None


def status_names():
    global _live
    if cfg()["statuses"] is not None:
        return [s["name"].lower() for s in cfg()["statuses"]]
    if _live is None:
        _live = [s["status"].lower() for s in req("GET", f"/list/{list_id()}").get("statuses", [])]
    return _live


def norm_status(s):
    s = s.strip().lower()
    s = {k.lower(): v.lower() for k, v in cfg()["status_aliases"].items()}.get(s, s)
    if s not in status_names():
        sys.exit(f"Unknown status {s!r}. Valid: {status_names()}")
    return s


def resolve_parent(p):
    return cfg()["parents"].get(p, p) if p else p


def read_text(path):
    return sys.stdin.read() if path == "-" else open(path).read()


def read_body(path):
    text = read_text(path)
    missing = [h for h in cfg()["required_sections"] if h not in text]
    if missing:
        sys.exit(f"Body {path} missing required sections: {missing}. Start from `cu.py template`.")
    return text


def check_name(name):
    pat = cfg()["name_pattern"]
    if pat and not re.search(pat, name):
        sys.exit(f"Name {name!r} does not match the project's name_pattern {pat!r}")


def split_tags(s):
    return [t.strip() for t in (s or "").split(",") if t.strip()]


def all_tasks(include_closed=True):
    page = 0
    while True:
        q = urllib.parse.urlencode({"page": page, "subtasks": "true", "include_closed": str(include_closed).lower()})
        d = req("GET", f"/list/{list_id()}/task?{q}")
        yield from d.get("tasks", [])
        if d.get("last_page", True) or not d.get("tasks"):
            return
        page += 1


def cmd_config(a):
    c = {k: v for k, v in cfg().items() if not k.startswith("_")}
    print(f"# config: {cfg()['_path'] or '(none found: defaults only)'}")
    print(f"# project root: {cfg()['_root']}")
    print(f"# template: {template_path()}")
    try:
        _, src, name = token_source()
        src += f" (account {name!r})" if name else ""
    except SystemExit as e:
        src = f"NOT USABLE: {e}"
    print(f"# token source: {src}")
    print(json.dumps(c, indent=2))


def cmd_accounts(a):
    _, src, selected = token_source()
    if selected is None:
        print(f"single token from {src}")
        return
    print(f"# {src}")
    for name in sorted(json.load(open(src))):
        print(f"{'*' if name == selected else ' '} {name}")


def cmd_init(a):
    path = os.path.join(os.getcwd(), CONFIG_NAME)
    if os.path.exists(path) and not a.force:
        sys.exit(f"{path} exists (use --force to overwrite)")
    data = {"list_id": a.list_id, "name": a.name, "account": a.account, "token_files": [".clickup-token"],
            "statuses": None, "default_status": None, "status_aliases": {},
            "required_sections": DEFAULTS["required_sections"], "template": None, "name_pattern": None,
            "parents": {}, "default_tags": [], "workflow": []}
    if a.from_list:
        CFG["list_id"] = a.list_id
        lst = req("GET", f"/list/{a.list_id}")
        data["name"] = data["name"] or lst.get("name")
        data["statuses"] = [{"name": s["status"], "type": s["type"], "color": s["color"]}
                            for s in lst.get("statuses", [])]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"wrote {path}\nAdd `.clickup-token` to .gitignore if the token lives in the project.")


def cmd_browse(a):
    for team in req("GET", "/team").get("teams", []):
        print(f'workspace {team["id"]}\t{team["name"]}')
        for sp in req("GET", f'/team/{team["id"]}/space?archived=false').get("spaces", []):
            print(f'  space {sp["id"]}\t{sp["name"]}')
            for lst in req("GET", f'/space/{sp["id"]}/list?archived=false').get("lists", []):
                print(f'    list {lst["id"]}\t{lst["name"]}')
            for fo in req("GET", f'/space/{sp["id"]}/folder?archived=false').get("folders", []):
                print(f'    folder {fo["id"]}\t{fo["name"]}')
                for lst in fo.get("lists", []):
                    print(f'      list {lst["id"]}\t{lst["name"]}')


def template_path():
    return resolve_path(cfg()["template"]) if cfg()["template"] else BUNDLED_TEMPLATE


def cmd_template(a):
    print(open(template_path()).read(), end="")


def cmd_list(a):
    parent, status = resolve_parent(a.parent), norm_status(a.status) if a.status else None
    for t in all_tasks(include_closed=not a.open):
        if parent and t.get("parent") != parent:
            continue
        if status and t["status"]["status"].lower() != status:
            continue
        if a.grep and a.grep.lower() not in t["name"].lower():
            continue
        print(f'{t["id"]}\t{t.get("parent") or "-"}\t{t["status"]["status"]}\t{t["name"]}')


def cmd_show(a):
    t = req("GET", f"/task/{a.task_id}?include_markdown_description=true")
    print(f'# {t["name"]}\nid: {t["id"]}  status: {t["status"]["status"]}  parent: {t.get("parent") or "-"}  url: {t["url"]}')
    print("tags:", ", ".join(x["name"] for x in t.get("tags", [])) or "-")
    print()
    print(t.get("markdown_description") or t.get("description") or "")


def cmd_create(a):
    check_name(a.name)
    data = {"name": a.name, "markdown_content": read_body(a.body)}
    if a.parent:
        data["parent"] = resolve_parent(a.parent)
    tags = list(dict.fromkeys(cfg()["default_tags"] + split_tags(a.tags)))
    if tags:
        data["tags"] = tags
    if a.priority:
        data["priority"] = int(a.priority)
    status = a.status or cfg()["default_status"]
    if status:
        data["status"] = norm_status(status)
    t = req("POST", f"/list/{list_id()}/task", data)
    print(f'{t["id"]}\t{t["url"]}\t{t["name"]}')


def cmd_update(a):
    data = {}
    if a.name:
        check_name(a.name)
        data["name"] = a.name
    if a.body:
        data["markdown_content"] = read_body(a.body)
    if a.status:
        data["status"] = norm_status(a.status)
    if a.priority:
        data["priority"] = int(a.priority)
    tags = split_tags(a.add_tags)
    if not data and not tags:
        sys.exit("Nothing to update")
    for tag in tags:
        req("POST", f"/task/{a.task_id}/tag/{urllib.parse.quote(tag)}")
    t = req("PUT", f"/task/{a.task_id}", data) if data else req("GET", f"/task/{a.task_id}")
    print(f'{t["id"]}\t{t["url"]}\t{t["name"]}\t{t["status"]["status"]}')


def cmd_comment(a):
    req("POST", f"/task/{a.task_id}/comment", {"comment_text": read_text(a.body)})
    print("ok")


def cmd_statuses(a):
    if not a.apply:
        d = req("GET", f"/list/{list_id()}")
        print("\n".join(f'{s["status"]}\t{s["type"]}' for s in d.get("statuses", [])))
        return
    wanted = cfg()["statuses"]
    if wanted is None:
        sys.exit("No 'statuses' in the project config; nothing to apply")
    # Tasks on a status that is removed become orphaned (hidden, not updatable), so refuse unless --force.
    live = {s["status"].lower() for s in req("GET", f"/list/{list_id()}").get("statuses", [])}
    removed = live - {s["name"].lower() for s in wanted}
    stuck = [t for t in all_tasks() if t["status"]["status"].lower() in removed] if removed else []
    if stuck and not a.force:
        lines = "\n".join(f'  {t["id"]}\t{t["status"]["status"]}\t{t["name"]}' for t in stuck)
        sys.exit(f"Applying would remove {sorted(removed)} while these tasks use them (they would be orphaned):\n"
                 f"{lines}\nMove them to a kept status first, or pass --force.")
    d = req("PUT", f"/list/{list_id()}", {"override_statuses": True, "statuses": [
        {"status": s["name"], "type": s.get("type", "custom"), **({"color": s["color"]} if s.get("color") else {})}
        for s in wanted]})
    print("\n".join(f'{s["status"]}\t{s["type"]}' for s in d.get("statuses", [])))


def parse_day(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"Bad date {s!r}: use YYYY-MM-DD")


def day_ms(day, end=False):
    """ClickUp dates are epoch milliseconds; use local start (or end) of the day so the UI shows that date."""
    t = datetime.datetime.combine(day, datetime.time(23, 59, 59) if end else datetime.time())
    return int(t.timestamp() * 1000)


def ms_day(ms):
    return datetime.datetime.fromtimestamp(int(ms) / 1000).date() if ms else None


def workspace_id(given):
    wid = given or cfg()["workspace_id"]
    if wid:
        return wid
    teams = req("GET", "/team").get("teams", [])
    if len(teams) == 1:
        return teams[0]["id"]
    sys.exit("Several workspaces: pass --workspace ID or set workspace_id in the config. "
             + ", ".join(f'{t["id"]} {t["name"]}' for t in teams))


def need(value, flag, key):
    if not value:
        sys.exit(f"Pass {flag} ID or set {key!r} in {CONFIG_NAME} (`cu.py browse` shows the IDs)")
    return value


def cmd_create_space(a):
    wid = workspace_id(a.workspace)
    sp = req("POST", f"/team/{wid}/space", {"name": a.name, "multiple_assignees": True})
    print(f'space {sp["id"]}\t{sp["name"]}')


def cmd_create_folder(a):
    sid = need(a.space or cfg()["space_id"], "--space", "space_id")
    fo = req("POST", f"/space/{sid}/folder", {"name": a.name})
    print(f'folder {fo["id"]}\t{fo["name"]}')


def cmd_create_list(a):
    if a.space and a.folder:
        sys.exit("Pass --space or --folder, not both")
    data = {"name": a.name}
    if a.description:
        data["markdown_content"] = a.description
    if a.due:
        data["due_date"], data["due_date_time"] = day_ms(parse_day(a.due), end=True), False
    if a.folder:
        lst = req("POST", f"/folder/{a.folder}/list", data)
    else:
        lst = req("POST", f'/space/{need(a.space or cfg()["space_id"], "--space/--folder", "space_id")}/list', data)
    print(f'list {lst["id"]}\t{lst["name"]}')
    if a.use:
        set_config_key("list_id", lst["id"])


def set_config_key(key, value):
    path = cfg()["_path"] or os.path.join(os.getcwd(), CONFIG_NAME)
    data = json.load(open(path)) if os.path.isfile(path) else {}
    data[key] = value
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"set {key} = {value} in {path}")


def sprint_lists(folder):
    return req("GET", f"/folder/{folder}/list?archived=false").get("lists", [])


def cmd_sprints(a):
    folder = need(a.folder or cfg()["sprint_folder_id"], "--folder", "sprint_folder_id")
    for lst in sprint_lists(folder):
        print(f'{lst["id"]}\t{ms_day(lst.get("start_date")) or "-"}\t{ms_day(lst.get("due_date")) or "-"}\t{lst["name"]}')


def next_sprint(lists, today):
    """(number, start) of the sprint after the existing ones: highest number in a name + 1, the day after the
    latest end date (or today)."""
    nums = [int(m.group(1)) for l in lists if (m := re.search(r"(\d+)", l["name"]))]
    ends = [d for d in (ms_day(l.get("due_date")) for l in lists) if d]
    start = max(ends) + datetime.timedelta(days=1) if ends else today
    return (max(nums) + 1 if nums else len(lists) + 1), start


def cmd_create_sprint(a):
    folder = need(a.folder or cfg()["sprint_folder_id"], "--folder", "sprint_folder_id")
    n, start = next_sprint(sprint_lists(folder), datetime.date.today())
    start = parse_day(a.start) if a.start else start
    end = parse_day(a.end) if a.end else start + datetime.timedelta(days=int(cfg()["sprint_length_days"]) - 1)
    if end < start:
        sys.exit(f"Sprint ends ({end}) before it starts ({start})")
    name = a.name or cfg()["sprint_name"].format(n=n, start=start, end=end)
    if a.dry_run:
        print(f"would create in folder {folder}: {name!r} {start} .. {end}")
        return
    # Sprints are lists in a Sprint folder; the API has no sprint endpoint. start_date is undocumented for
    # lists, so the output shows what ClickUp actually stored.
    data = {"name": name, "start_date": day_ms(start), "start_date_time": False,
            "due_date": day_ms(end, end=True), "due_date_time": False}
    if a.description:
        data["markdown_content"] = a.description
    lst = req("POST", f"/folder/{folder}/list", data)
    print(f'list {lst["id"]}\t{ms_day(lst.get("start_date")) or "-"}\t{ms_day(lst.get("due_date")) or "-"}\t{lst["name"]}')


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    s.add_parser("config").set_defaults(f=cmd_config)
    x = s.add_parser("init"); x.set_defaults(f=cmd_init)
    x.add_argument("--list-id", required=True); x.add_argument("--name")
    x.add_argument("--account", help="account name in a multi-account token file")
    x.add_argument("--from-list", action="store_true", help="copy the list's current statuses into the config")
    x.add_argument("--force", action="store_true")
    s.add_parser("accounts").set_defaults(f=cmd_accounts)
    s.add_parser("browse").set_defaults(f=cmd_browse)
    x = s.add_parser("create-space"); x.set_defaults(f=cmd_create_space)
    x.add_argument("--name", required=True); x.add_argument("--workspace")
    x = s.add_parser("create-folder"); x.set_defaults(f=cmd_create_folder)
    x.add_argument("--name", required=True); x.add_argument("--space")
    x = s.add_parser("create-list"); x.set_defaults(f=cmd_create_list)
    x.add_argument("--name", required=True); x.add_argument("--space"); x.add_argument("--folder")
    x.add_argument("--description"); x.add_argument("--due", help="YYYY-MM-DD")
    x.add_argument("--use", action="store_true", help="set it as list_id in .clickup.json")
    x = s.add_parser("sprints"); x.set_defaults(f=cmd_sprints); x.add_argument("--folder")
    x = s.add_parser("create-sprint"); x.set_defaults(f=cmd_create_sprint)
    x.add_argument("--folder"); x.add_argument("--name"); x.add_argument("--start", help="YYYY-MM-DD")
    x.add_argument("--end", help="YYYY-MM-DD (default: start + sprint_length_days - 1)")
    x.add_argument("--description"); x.add_argument("--dry-run", action="store_true")
    s.add_parser("template").set_defaults(f=cmd_template)
    x = s.add_parser("list"); x.set_defaults(f=cmd_list)
    x.add_argument("--parent"); x.add_argument("--status"); x.add_argument("--grep")
    x.add_argument("--open", action="store_true", help="exclude closed tasks")
    x = s.add_parser("show"); x.set_defaults(f=cmd_show); x.add_argument("task_id")
    x = s.add_parser("create"); x.set_defaults(f=cmd_create)
    x.add_argument("--name", required=True); x.add_argument("--body", required=True)
    x.add_argument("--parent"); x.add_argument("--tags"); x.add_argument("--priority"); x.add_argument("--status")
    x = s.add_parser("update"); x.set_defaults(f=cmd_update)
    x.add_argument("task_id"); x.add_argument("--name"); x.add_argument("--body"); x.add_argument("--status")
    x.add_argument("--priority"); x.add_argument("--add-tags")
    x = s.add_parser("comment"); x.set_defaults(f=cmd_comment)
    x.add_argument("task_id"); x.add_argument("--body", required=True)
    x = s.add_parser("statuses"); x.set_defaults(f=cmd_statuses)
    x.add_argument("--apply", action="store_true"); x.add_argument("--force", action="store_true")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    CFG = load_config()
    args.f(args)
