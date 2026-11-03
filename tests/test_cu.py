"""Offline tests for cu.py: config discovery, validation and token lookup. Run: python3 -m unittest discover tests"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CU = os.path.join(HERE, "..", "skills", "clickup", "scripts", "cu.py")


def load_cu():
    spec = importlib.util.spec_from_file_location("cu", CU)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_cwd, self.old_env = os.getcwd(), dict(os.environ)
        for k in ("CLICKUP_CONFIG", "CLICKUP_LIST_ID", "CLICKUP_TOKEN", "CLICKUP_TOKEN_FILE"):
            os.environ.pop(k, None)
        os.environ["HOME"] = self.tmp  # keep ~/.config/clickup/token out of the test
        self.cu = load_cu()

    def tearDown(self):
        os.chdir(self.old_cwd)
        os.environ.clear()
        os.environ.update(self.old_env)

    def write_config(self, data, where=None):
        where = where or self.tmp
        with open(os.path.join(where, ".clickup.json"), "w") as f:
            json.dump(data, f)

    def use(self, start):
        os.chdir(start)
        self.cu.CFG = self.cu.load_config(start)

    def test_finds_config_in_parent_dir(self):
        self.write_config({"list_id": "123"})
        sub = os.path.join(self.tmp, "a", "b")
        os.makedirs(sub)
        self.use(sub)
        self.assertEqual(self.cu.list_id(), "123")
        self.assertEqual(os.path.realpath(self.cu.CFG["_root"]), os.path.realpath(self.tmp))

    def test_env_overrides(self):
        other = os.path.join(self.tmp, "other.json")
        json.dump({"list_id": "from-env-file"}, open(other, "w"))
        os.environ["CLICKUP_CONFIG"] = other
        self.use(self.tmp)
        self.assertEqual(self.cu.list_id(), "from-env-file")
        os.environ["CLICKUP_LIST_ID"] = "999"
        self.use(self.tmp)
        self.assertEqual(self.cu.list_id(), "999")

    def test_rejects_token_and_unknown_keys(self):
        self.write_config({"list_id": "1", "token": "pk_secret"})
        with self.assertRaises(SystemExit) as e:
            self.cu.load_config(self.tmp)
        self.assertIn("never store the token", str(e.exception))
        self.write_config({"list_id": "1", "statusses": []})
        with self.assertRaises(SystemExit):
            self.cu.load_config(self.tmp)

    def test_statuses_aliases_and_parents(self):
        self.write_config({"list_id": "1", "statuses": ["Backlog", {"name": "in review", "type": "custom"}],
                           "status_aliases": {"To Do": "backlog"}, "parents": {"checkout": "abc"}})
        self.use(self.tmp)
        self.assertEqual(self.cu.norm_status("to do"), "backlog")
        self.assertEqual(self.cu.norm_status("IN REVIEW"), "in review")
        with self.assertRaises(SystemExit):
            self.cu.norm_status("shipped")
        self.assertEqual(self.cu.resolve_parent("checkout"), "abc")
        self.assertEqual(self.cu.resolve_parent("raw123"), "raw123")

    def test_required_sections_and_name_pattern(self):
        self.write_config({"list_id": "1", "required_sections": ["## Why"], "name_pattern": r"^\[WEB-\d+\] "})
        self.use(self.tmp)
        body = os.path.join(self.tmp, "b.md")
        open(body, "w").write("## Details\n")
        with self.assertRaises(SystemExit):
            self.cu.read_body(body)
        open(body, "w").write("## Why\nbecause\n")
        self.assertIn("because", self.cu.read_body(body))
        self.cu.check_name("[WEB-12] thing")
        with self.assertRaises(SystemExit):
            self.cu.check_name("thing")

    def test_template_project_override_and_bundled_default(self):
        self.write_config({"list_id": "1"})
        self.use(self.tmp)
        self.assertEqual(self.cu.template_path(), self.cu.BUNDLED_TEMPLATE)
        self.assertTrue(os.path.isfile(self.cu.BUNDLED_TEMPLATE))
        self.write_config({"list_id": "1", "template": "docs/t.md"})
        self.use(self.tmp)
        self.assertEqual(self.cu.template_path(), os.path.join(self.cu.CFG["_root"], "docs/t.md"))

    def test_token_order(self):
        self.write_config({"list_id": "1", "token_files": ["secrets/cu"], "token_env": "PROJ_CU"})
        os.makedirs(os.path.join(self.tmp, "secrets"))
        open(os.path.join(self.tmp, "secrets", "cu"), "w").write("from-file\n")
        self.use(self.tmp)
        self.assertEqual(self.cu.token(), "from-file")
        os.environ["PROJ_CU"] = "from-project-env"
        self.assertEqual(self.cu.token(), "from-project-env")
        os.environ["CLICKUP_TOKEN"] = "from-env"
        self.assertEqual(self.cu.token(), "from-env")

    def write_global_tokens(self, data):
        os.makedirs(os.path.join(self.tmp, ".config", "clickup"), exist_ok=True)
        with open(os.path.join(self.tmp, ".config", "clickup", "token"), "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_multi_account_token_file(self):
        self.write_global_tokens({"account-a": "token-a", "account-b": "token-b"})
        self.write_config({"list_id": "1", "account": "account-b"})
        self.use(self.tmp)
        self.assertEqual(self.cu.token(), "token-b")
        os.environ["CLICKUP_ACCOUNT"] = "account-a"
        self.assertEqual(self.cu.token(), "token-a")
        os.environ["CLICKUP_ACCOUNT"] = "nope"
        with self.assertRaises(SystemExit) as e:
            self.cu.token()
        self.assertIn("account-a", str(e.exception))
        self.assertNotIn("token-a", str(e.exception))

    def test_multi_account_default_single_and_ambiguous(self):
        self.write_config({"list_id": "1"})
        self.use(self.tmp)
        self.write_global_tokens({"only": "t1"})
        self.assertEqual(self.cu.token(), "t1")
        self.write_global_tokens({"x": "t1", "default": "t2"})
        self.assertEqual(self.cu.token(), "t2")
        self.write_global_tokens({"x": "t1", "y": "t2"})
        with self.assertRaises(SystemExit) as e:
            self.cu.token()
        self.assertIn('set "account"', str(e.exception))
        self.write_global_tokens({"x": 5})
        with self.assertRaises(SystemExit):
            self.cu.token()
        self.write_global_tokens("pk_plain\n")
        self.assertEqual(self.cu.token(), "pk_plain")

    def test_cli_accounts_lists_names_not_tokens(self):
        self.write_global_tokens({"account-a": "secret-a", "account-b": "secret-b"})
        self.write_config({"list_id": "1", "account": "account-a"})
        out = subprocess.run([sys.executable, CU, "accounts"], cwd=self.tmp, capture_output=True, text=True,
                             env={**os.environ}, check=True).stdout
        self.assertIn("* account-a", out)
        self.assertIn("  account-b", out)
        self.assertNotIn("secret", out)
        out = subprocess.run([sys.executable, CU, "config"], cwd=self.tmp, capture_output=True, text=True,
                             env={**os.environ}, check=True).stdout
        self.assertIn("(account 'account-a')", out)
        self.assertNotIn("secret", out)

    def fake_api(self, responses):
        """Replace req() with a recorder; responses maps 'METHOD path-prefix' -> JSON returned."""
        calls = []

        def req(method, path, data=None):
            calls.append((method, path, data))
            for key, resp in responses.items():
                m, prefix = key.split(" ", 1)
                if m == method and path.startswith(prefix):
                    return resp
            raise AssertionError(f"unexpected {method} {path}")
        self.cu.req = req
        return calls

    def run_cmd(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            a = self.cu.build_parser().parse_args(list(argv))
            a.f(a)
        return out.getvalue()

    def test_next_sprint_numbering_and_start(self):
        d = self.cu.datetime.date
        today = d(2026, 11, 3)
        self.assertEqual(self.cu.next_sprint([], today), (1, today))
        lists = [{"name": "Sprint 4 (Oct 06 - Oct 19)", "due_date": str(self.cu.day_ms(d(2026, 10, 19), end=True))},
                 {"name": "Sprint 5 (Oct 20 - Nov 02)", "due_date": str(self.cu.day_ms(d(2026, 11, 2), end=True))},
                 {"name": "Backlog grooming", "due_date": None}]
        self.assertEqual(self.cu.next_sprint(lists, today), (6, d(2026, 11, 3)))

    def test_create_sprint_payload(self):
        self.write_config({"list_id": "1", "sprint_folder_id": "F1", "sprint_length_days": 7,
                           "sprint_name": "S{n} {start:%m/%d}-{end:%m/%d}"})
        self.use(self.tmp)
        calls = self.fake_api({"GET /folder/F1/list": {"lists": []},
                               "POST /folder/F1/list": {"id": "L9", "name": "S1 12/01-12/07"}})
        out = self.run_cmd("create-sprint", "--start", "2026-12-01")
        post = [c for c in calls if c[0] == "POST"][0][2]
        self.assertEqual(post["name"], "S1 12/01-12/07")
        self.assertEqual(self.cu.ms_day(post["start_date"]), self.cu.datetime.date(2026, 12, 1))
        self.assertEqual(self.cu.ms_day(post["due_date"]), self.cu.datetime.date(2026, 12, 7))
        self.assertIn("L9", out)
        calls.clear()
        self.assertIn("would create", self.run_cmd("create-sprint", "--start", "2026-12-01", "--dry-run"))
        self.assertFalse([c for c in calls if c[0] == "POST"])
        with self.assertRaises(SystemExit):
            self.run_cmd("create-sprint", "--start", "2026-12-08", "--end", "2026-12-01")

    def test_create_space_folder_list(self):
        self.write_config({"list_id": "1", "space_id": "S1"})
        self.use(self.tmp)
        calls = self.fake_api({"GET /team": {"teams": [{"id": "W1", "name": "Mine"}]},
                               "POST /team/W1/space": {"id": "S2", "name": "New"},
                               "POST /space/S1/folder": {"id": "F2", "name": "Sprints"},
                               "POST /space/S1/list": {"id": "L2", "name": "Tasks"},
                               "POST /folder/F2/list": {"id": "L3", "name": "Inner"}})
        self.assertIn("space S2", self.run_cmd("create-space", "--name", "New"))
        self.assertIn("folder F2", self.run_cmd("create-folder", "--name", "Sprints"))
        self.assertIn("list L3", self.run_cmd("create-list", "--name", "Inner", "--folder", "F2"))
        self.run_cmd("create-list", "--name", "Tasks", "--due", "2026-12-31", "--use")
        self.assertEqual(json.load(open(os.path.join(self.tmp, ".clickup.json")))["list_id"], "L2")
        self.assertEqual(json.load(open(os.path.join(self.tmp, ".clickup.json")))["space_id"], "S1")
        self.assertIn("due_date", calls[-1][2])

    def test_missing_ids_and_ambiguous_workspace(self):
        self.write_config({"list_id": "1"})
        self.use(self.tmp)
        self.fake_api({"GET /team": {"teams": [{"id": "W1", "name": "a"}, {"id": "W2", "name": "b"}]}})
        for argv in (["create-folder", "--name", "x"], ["create-list", "--name", "x"], ["create-sprint"],
                     ["create-space", "--name", "x"]):
            with self.assertRaises(SystemExit):
                self.run_cmd(*argv)

    def test_token_found_in_main_checkout_from_worktree(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        git = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        self.write_config({"list_id": "1", "token_files": ["tools/.tok"]}, where=repo)
        open(os.path.join(repo, ".gitignore"), "w").write("tools/.tok\n")
        git("add", "."); git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        os.makedirs(os.path.join(repo, "tools"))
        open(os.path.join(repo, "tools", ".tok"), "w").write("main-token")
        wt = os.path.join(self.tmp, "wt")
        git("worktree", "add", "-q", wt)
        self.use(wt)
        self.assertEqual(os.path.realpath(self.cu.CFG["_root"]), os.path.realpath(wt))
        self.assertEqual(self.cu.token(), "main-token")

    def test_cli_config_runs_and_hides_nothing_secret(self):
        self.write_config({"list_id": "42"})
        open(os.path.join(self.tmp, ".clickup-token"), "w").write("pk_supersecret")
        out = subprocess.run([sys.executable, CU, "config"], cwd=self.tmp, capture_output=True, text=True,
                             env={**os.environ}, check=True).stdout
        self.assertIn('"list_id": "42"', out)
        self.assertNotIn("pk_supersecret", out)


if __name__ == "__main__":
    unittest.main()
