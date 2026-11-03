# agent-skill-clickup

A reusable agent skill (Claude Code and other `SKILL.md`-compatible agents) for running a project's ClickUp
tickets from the terminal: list/search, show, create with a required body template, update status/priority/tags,
and comment. One CLI (`skills/clickup/scripts/cu.py`, Python 3 stdlib only) serves every project; **each project
configures it with a `.clickup.json` at its root**.

## Install

Pick one:

```bash
# With the `skills` CLI (https://skills.sh). Works for Claude Code, Codex, Cursor and other agents.
# The repo is private, so the clone uses your git credentials (SSH key or gh auth).
npx skills add AminulBD/agent-skill-clickup -g                 # user-level: every project
npx skills add AminulBD/agent-skill-clickup                    # this project only (./.claude/skills etc.)
npx skills add AminulBD/agent-skill-clickup -g -a claude-code -y   # one agent, no prompts
npx skills update clickup                                       # pull the latest version later
```

Or manually:

```bash
# A. Every project on this machine (symlink into ~/.claude/skills)
git clone git@github.com:AminulBD/agent-skill-clickup.git ~/code/agent-skill-clickup
~/code/agent-skill-clickup/install.sh

# B. One project only
~/code/agent-skill-clickup/install.sh /path/to/project/.claude/skills

# C. As a Claude Code plugin (the repo is also a plugin marketplace)
/plugin marketplace add AminulBD/agent-skill-clickup
/plugin install clickup@agent-skill-clickup
```

`git pull` in the clone updates options A and B (they are symlinks).

## Use it with Claude Code

1. **Install once** (user-level), then start a new Claude Code session so it picks the skill up:

   ```bash
   npx skills add AminulBD/agent-skill-clickup -g -a claude-code -y
   ```

2. **Store your token once per machine.** Use a ClickUp personal token (Settings → Apps, starts with `pk_`):

   ```bash
   mkdir -p ~/.config/clickup && pbpaste > ~/.config/clickup/token && chmod 600 ~/.config/clickup/token
   ```

   With several ClickUp accounts, make the file JSON instead, e.g. `{"work": "pk_…", "personal": "pk_…"}`
   (see [Several ClickUp accounts](#several-clickup-accounts)).

3. **Set up each project once.** In the project, ask Claude:

   > Set up ClickUp for this project using the clickup skill. The list is "My Project Tasks".

   Claude runs `cu.py browse` to find the list ID, then `cu.py init --list-id … --from-list`, which writes
   `.clickup.json` with the list's current statuses. Add your own rules to it, for example:

   ```json
   "workflow": [
     "Move a ticket to 'in progress' when you start it and to 'review' when the PR is open.",
     "After each commit, comment the commit SHA on the ticket."
   ]
   ```

   Commit `.clickup.json`. Add one line to the project's `CLAUDE.md` so Claude follows the workflow without being
   asked: *"Track work in ClickUp with the `clickup` skill."*

4. **Use it.** Ask in plain words and Claude loads the skill when the request fits:
   - "What ClickUp tickets are in review?"
   - "Create a ticket for adding rate limiting under the checkout epic."
   - "Move 86abc0042 to in progress."
   - "Comment on the ticket with what we just did."

   Or call it directly: `/clickup list open tickets under checkout`.

Each time, Claude runs `cu.py config` first, so it reads that project's list, statuses, name pattern and workflow
rules. Another project gets its own `.clickup.json` and uses the same skill.

**claude.ai or the desktop app:** you can upload a skill there as a zip, but it runs in a sandbox that usually
can't reach `api.clickup.com` or your token file. This skill is meant for Claude Code. In claude.ai, use the
built-in ClickUp connector instead.

## Set up a project manually

```bash
cd /path/to/project
python3 ~/.claude/skills/clickup/scripts/cu.py browse                       # find the list ID
python3 ~/.claude/skills/clickup/scripts/cu.py init --list-id 9012… --from-list
```

Then edit `.clickup.json` (commit it) and provide a token (don't commit it). Optionally add a line to the
project's `CLAUDE.md`, such as "Track work in ClickUp with the `clickup` skill."

### Token

Lookup order, first found: `$CLICKUP_TOKEN` → the env var named by `token_env` → the file `$CLICKUP_TOKEN_FILE` →
each `token_files` entry → `<project root>/.clickup-token` → `~/.config/clickup/token`.
Relative `token_files` entries resolve against the project root and are also tried in the **main checkout** when
you're in a git worktree, because worktrees don't get gitignored files. Use a personal token (`pk_…`, ClickUp →
Settings → Apps), `chmod 600` it, and gitignore it. The CLI refuses a config that contains a `token` key.

#### Several ClickUp accounts

Any token file, usually the global `~/.config/clickup/token`, can hold several accounts as a JSON object instead of a
single token:

```json
{"account-a": "pk_111…", "account-b": "pk_222…"}
```

Each project picks one with `"account": "account-b"` in its `.clickup.json`. `cu.py init --account account-b`
writes that for you. The account is chosen in this order: `$CLICKUP_ACCOUNT` (one-off override), then the config's
`account`, then an entry named `default`, then the only entry if there is just one. Otherwise the CLI stops and
lists the account names. `cu.py accounts` shows the names in the token file in use and marks the selected one with
`*`. Tokens are never printed.

### `.clickup.json` reference

Only `list_id` is required. Every key:

| Key | Default | Meaning |
|---|---|---|
| `list_id` | — | ClickUp list the tickets live in. `$CLICKUP_LIST_ID` overrides it. |
| `name` | `null` | Label for humans. |
| `account` | `null` | Which account to use from a multi-account JSON token file. `$CLICKUP_ACCOUNT` overrides it. |
| `token_env` | `null` | Name of a project-specific env var holding the token. |
| `token_files` | `[]` | Token file paths (relative to the project root, or absolute/`~`). |
| `statuses` | `null` | `null` = accept the live list's statuses. A list of names or `{name, type, color}` (type: `open`/`custom`/`done`/`closed`) = validate against it offline and allow `cu.py statuses --apply`. |
| `default_status` | `null` | Status for new tickets (else the list's first status). |
| `status_aliases` | `{}` | e.g. `{"to do": "backlog", "done": "complete"}`. Matching ignores case. |
| `required_sections` | the 5 template headings | Headings a body must contain. `[]` disables the check. |
| `template` | bundled `templates/ticket-template.md` | Project template path, printed by `cu.py template`. |
| `name_pattern` | `null` | Regex a ticket name must match, e.g. `"^\\[(WEB\|API)-\\d+\\] "`. |
| `parents` | `{}` | Aliases for parent/epic task IDs, e.g. `{"checkout": "86abc0001"}`, used by `--parent checkout`. |
| `default_tags` | `[]` | Tags added to every new ticket. |
| `workflow` | `[]` | Plain-language rules the agent must follow (printed by `cu.py config`). |
| `workspace_id` | `null` | Workspace for `create-space`. If unset and you have only one workspace, that one is used. |
| `space_id` | `null` | Space for `create-folder` and `create-list` when `--space`/`--folder` isn't given. |
| `sprint_folder_id` | `null` | Sprint folder for `sprints` and `create-sprint`. |
| `sprint_length_days` | `14` | Default sprint length. |
| `sprint_name` | `"Sprint {n} ({start:%b %d} - {end:%b %d})"` | Sprint name format: `{n}` number, `{start}`/`{end}` dates (strftime after `:`). |

Config lookup: `$CLICKUP_CONFIG`, else `.clickup.json` in the current directory or a parent, else in the main
checkout of a git worktree. Unknown keys are rejected so typos don't fail silently. See `examples/` for a
minimal config and a full one using every option (`full.clickup.json`).

## Commands

```bash
cu.py config                                   # effective config, token source (never the token)
cu.py accounts                                 # account names in a multi-account token file (* = selected)
cu.py init --list-id ID [--account NAME] [--from-list] [--force]
cu.py browse                                   # workspaces > spaces > folders > lists, with IDs
cu.py template                                 # body template
cu.py list [--parent ID|ALIAS] [--status S] [--grep TEXT] [--open]
cu.py show TASK_ID
cu.py create --name N --body FILE.md [--parent ID|ALIAS] [--tags a,b] [--priority 1-4] [--status S]
cu.py update TASK_ID [--name N] [--body FILE.md] [--status S] [--priority 1-4] [--add-tags a,b]
cu.py comment TASK_ID --body FILE.md           # "-" = stdin
cu.py statuses [--apply] [--force]
cu.py create-space --name N [--workspace ID]
cu.py create-folder --name N [--space ID]
cu.py create-list --name N [--space ID | --folder ID] [--description MD] [--due YYYY-MM-DD] [--use]
cu.py sprints [--folder ID]                    # id, start, end, name
cu.py create-sprint [--folder ID] [--name N] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--description MD] [--dry-run]
```

## Gotchas

- **Sprints are lists.** ClickUp's public API has no sprint endpoint. A sprint is a list inside a folder that has
  the Sprints ClickApp turned on. Create that Sprint folder once in the ClickUp UI and put its ID in
  `sprint_folder_id`. `create-sprint` then adds a list there with start and end dates. By default the number is one
  above the highest number in the existing sprint names, and the start is the day after the latest sprint ends
  (or today if there are none). `start_date` isn't documented for lists, so `create-sprint` prints the dates
  ClickUp actually stored. Check them, or use `--dry-run` first.
- `create-list --use` sets the new list as this project's `list_id` in `.clickup.json`.
- The CLI can create spaces, folders, lists and sprints but never deletes them. Remove them in the ClickUp UI.
- `statuses --apply` replaces the list's statuses. Tasks on a removed status become **orphaned** (hidden and not
  updatable), so the CLI refuses while any task uses a status that would be removed. Move those tasks first.
  `--force` skips the check.
- ClickUp's `markdown_content` is used for bodies. `show` reads them back with `include_markdown_description`.
- Comments are posted as plain `comment_text`. ClickUp renders some Markdown there, but not all of it.

## Development

```bash
python3 -m unittest discover tests     # offline: config discovery, validation, token lookup incl. worktrees
```
