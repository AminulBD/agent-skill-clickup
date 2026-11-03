---
name: clickup
description: Manage the current project's ClickUp tickets from the terminal — list/search, show, create (with a required body template), update status, and comment — configured per project by a `.clickup.json` file. Use when the user mentions ClickUp, tickets, tasks, epics or ticket statuses, asks to create/move/comment on a ticket, or when project instructions say to track work in ClickUp.
---

# ClickUp (per-project)

All operations go through the bundled CLI `scripts/cu.py` (Python 3 stdlib only, no install). Run it by its
absolute path inside this skill's directory, from anywhere inside the project:

```bash
python3 <this-skill-dir>/scripts/cu.py <command> …
```

## 1. Load the project's config first

Always start with:

```bash
python3 <skill>/scripts/cu.py config
```

It prints which `.clickup.json` was found (searched from the current directory upwards, then the main checkout
of a git worktree), the project root, the template in use, where the token comes from (never the token itself),
and the effective settings. **Follow the `workflow` rules it prints** — they are the project owner's rules
(when to move statuses, what to put in comments, etc.). Also read the project's own CLAUDE.md / AGENTS.md for
ClickUp rules; those win over anything here.

If no config is found, set one up (ask the user which list to use if it isn't obvious):

```bash
python3 <skill>/scripts/cu.py browse                          # find the list ID
python3 <skill>/scripts/cu.py init --list-id ID [--from-list] # writes ./.clickup.json at the project root
```

`--from-list` copies the list's current statuses into the config so they are validated offline. The config
reference is in this skill's `README` (repo root) and `examples/`.

## 2. Commands

```bash
cu.py accounts                                                      # accounts in a multi-account token file
cu.py list [--parent ID|ALIAS] [--status S] [--grep TEXT] [--open]   # id, parent, status, name
cu.py show TASK_ID                                                  # full ticket incl. markdown body
cu.py template > "$TMP/body.md"                                     # the project's body template
cu.py create --name NAME --body FILE.md [--parent ID|ALIAS] [--tags a,b] [--priority 1-4] [--status S]
cu.py update TASK_ID [--name N] [--body FILE.md] [--status S] [--priority 1-4] [--add-tags a,b]
cu.py comment TASK_ID --body FILE.md                                # "-" reads stdin
cu.py statuses [--apply] [--force]                                  # show / enforce configured statuses

# Workspace structure
cu.py create-space --name NAME [--workspace ID]                     # default: config workspace_id, or the only one
cu.py create-folder --name NAME [--space ID]                        # default: config space_id
cu.py create-list --name NAME [--space ID | --folder ID] [--description MD] [--due YYYY-MM-DD] [--use]
cu.py sprints [--folder ID]                                         # id, start, end, name
cu.py create-sprint [--folder ID] [--name N] [--start D] [--end D] [--description MD] [--dry-run]
```

- `create-list --use` writes the new list's ID into `.clickup.json` as `list_id`, so tickets go there.
- **Sprints** are lists inside a Sprint folder (ClickUp's API has no sprint endpoint). `create-sprint` uses the
  config's `sprint_folder_id`. By default it numbers the sprint one above the highest number in the existing
  sprint names, starts it the day after the latest sprint ends (or today), makes it `sprint_length_days` long
  and names it with `sprint_name`. Run with `--dry-run` first and show the user the name and dates.

- `--parent` accepts a raw task ID or an alias from the config's `parents` map (e.g. `checkout`).
- Statuses are case-insensitive and may use the config's `status_aliases`. Without a `statuses` list in the
  config, they are validated against the live list.
- Priority: 1 urgent, 2 high, 3 normal, 4 low.

## 3. Rules

- **Before creating a ticket**, search for duplicates: `cu.py list --grep "<keywords>"`.
- **Spaces, folders, lists and sprints** are shared structure the whole team sees, and the CLI can't delete
  them. Create one only when the user asks for it, and confirm the name and location first. Run `cu.py browse`
  (or `cu.py sprints`) first so you don't create a duplicate.
- **Bodies** are Markdown files. Start from `cu.py template`, fill every section, write the file to a temp dir
  (not the repo), then pass it with `--body`. The CLI rejects bodies missing any `required_sections`.
- **Names** must match the config's `name_pattern` when one is set (e.g. a prefix like `[WEB-42] …`).
- **Status changes**: only move tickets to statuses the workflow allows; never mark something released/done
  that isn't. When the workflow says so, comment what was done with the commit SHA and links.
- **`statuses --apply`** replaces the list's statuses. It refuses if any task is on a status that would be
  removed (those tasks would be orphaned). Move them first; use `--force` only if the user explicitly asks.
- **Token**: never print, commit, or paste it into tickets, PRs, logs or artifacts. Never put it in
  `.clickup.json`. If it's missing, tell the user to create `~/.config/clickup/token` (chmod 600) or a
  gitignored `.clickup-token` in the project, or to set `CLICKUP_TOKEN`.
- **Several accounts**: a token file may be JSON `{"account-a": "pk_…", "account-b": "pk_…"}`. The project's
  `.clickup.json` picks one with `"account"`, and `CLICKUP_ACCOUNT` overrides it for one command. `cu.py accounts`
  lists the names. If the CLI says several accounts exist and none is selected, ask the user which one this
  project uses, then set `account` in `.clickup.json`. Don't guess.
- When reporting a ticket to the user, link it as `[Task name](url)`.
