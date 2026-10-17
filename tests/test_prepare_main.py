import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from support import ACTION_DIR, GitRepoCase, default_env, git, output_values

import prepare
import review_options


class PrepareMainTests(GitRepoCase):
  def setUp(self) -> None:
    super().setUp()
    git("remote", "add", "origin", str(self.root))

  def run_main(self, head: str, **options: str) -> Path:
    checked = review_options.outputs({**default_env(), "KIND": "code", **options})
    settings = output_values(checked)
    runner_temp = self.temp_root / "runner"
    runner_temp.mkdir(exist_ok=True)
    env = {
      "KIND": "code",
      "OUT": ".claude-review/code",
      "GITHUB_WORKSPACE": str(self.root),
      "RUNNER_TEMP": str(runner_temp),
      "GITHUB_OUTPUT": str(runner_temp / "output"),
      "BASE_REF": "main",
      "HEAD_SHA": head,
      "REPO": "owner/repo",
      "PR": "1",
      "HEADER": "header",
      "DOC_SOURCES": settings["doc_sources"],
      "COPY_SOURCES": settings["copy_sources"],
      "EXCLUDE_PATHS": settings["exclude_paths"],
      "REVIEW_POLICY_PATH": settings["review_policy_path"],
    }
    with mock.patch.dict(os.environ, env), mock.patch.object(prepare, "prior_reviews", return_value=""):
      prepare.main()
    return self.root / ".claude-review/code"

  def test_review_policy_path_reads_that_file_from_the_base_branch(self) -> None:
    self.commit(*self.write({"REVIEW.md": "root policy\n", "docs/policy.md": "base policy\n"}))
    git("checkout", "--quiet", "-b", "feature")
    head = self.commit(*self.write({"docs/policy.md": "pull request policy\n"}))

    out = self.run_main(head, REVIEW_POLICY_PATH="docs/policy.md")

    self.assertEqual((out / "review-policy.md").read_text(encoding="utf-8"), "base policy\n")

  def test_changed_control_files_are_snapshotted_and_listed(self) -> None:
    self.commit(*self.write({".claude/x": "base rule\n", "CLAUDE.md": "base memory\n"}))
    git("checkout", "--quiet", "-b", "feature")
    head = self.commit(
      *self.write({".claude/x": "pull request rule\n", "CLAUDE.md": "pull request memory\n"})
    )

    out = self.run_main(head)

    listed = (out / "control-files.txt").read_text().splitlines()
    for path, pull_request, base in (
      (".claude/x", "pull request rule\n", "base rule\n"),
      ("CLAUDE.md", "pull request memory\n", "base memory\n"),
    ):
      with self.subTest(path=path):
        self.assertEqual((self.root / ".claude-pr" / path).read_text(), pull_request)
        self.assertEqual((self.root / path).read_text(), base)
        self.assertTrue(
          any(path == line or (line.endswith("/") and path.startswith(line)) for line in listed)
        )


class GitIsolationTests(GitRepoCase):
  def test_git_older_than_attr_source_stops_before_any_diff(self) -> None:
    real_run = prepare.run

    def old_git(*args: str, stdin: str = "") -> str:
      return "git version 2.40.1\n" if args == ("git", "--version") else real_run(*args, stdin=stdin)

    with mock.patch.object(prepare, "run", side_effect=old_git), self.assertRaises(SystemExit):
      prepare.require_isolated_git()

  def test_supported_git_in_a_clean_checkout_passes(self) -> None:
    prepare.require_isolated_git()

  def test_info_attributes_in_the_checkout_stops(self) -> None:
    Path(git("rev-parse", "--git-path", "info/attributes")).write_text("*.md -diff\n")

    with self.assertRaises(SystemExit):
      prepare.require_isolated_git()

  def test_git_commands_do_not_read_system_attributes(self) -> None:
    with mock.patch.object(prepare.subprocess, "run", wraps=prepare.subprocess.run) as spy:
      prepare.run("git", "--version")
    self.assertEqual(spy.call_args.kwargs["env"]["GIT_ATTR_NOSYSTEM"], "1")


BLOB_PRELUDE = """
import subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import prepare

def blob(content):
  return subprocess.run(
    ["git", "hash-object", "-w", "--stdin"], input=content, capture_output=True, check=True
  ).stdout.decode().strip()
"""


class BlobNulTests(GitRepoCase):
  def run_isolated(self, code: str) -> None:
    """blob_has_nul の回帰がハングでなく失敗として出るよう、別プロセスで時間を区切って走らせる。"""
    try:
      result = subprocess.run(
        [sys.executable, "-c", BLOB_PRELUDE + code, str(ACTION_DIR)],
        cwd=self.root,
        capture_output=True,
        text=True,
        timeout=60,
      )
    except subprocess.TimeoutExpired:
      self.fail("blob_has_nul did not finish within 60 seconds")
    self.assertEqual(result.returncode, 0, result.stderr)

  def test_nul_is_found_at_and_around_chunk_boundaries(self) -> None:
    self.run_isolated("""
chunk = prepare.BLOB_CHUNK
for position in (0, chunk - 1, chunk, chunk + 1, 2 * chunk + 5):
  content = bytearray(b"a" * (3 * chunk))
  content[position] = 0
  assert prepare.blob_has_nul(blob(bytes(content))), position
assert not prepare.blob_has_nul(blob(b"a" * (3 * chunk)))
assert not prepare.blob_has_nul(blob(b""))
""")

  def test_reading_stops_and_the_process_is_killed_after_the_first_nul(self) -> None:
    self.run_isolated("""
large = blob(b"\\0" + b"a" * (32 * 1024 * 1024))
started = []
real_popen = subprocess.Popen

def popen(*args, **kwargs):
  started.append(real_popen(*args, **kwargs))
  return started[-1]

prepare.subprocess.Popen = popen
assert prepare.blob_has_nul(large)
assert len(started) == 1
assert started[0].poll() is not None
assert started[0].returncode < 0, started[0].returncode
""")

  def test_a_clean_blob_is_not_killed_while_git_is_still_exiting(self) -> None:
    class ExitingGit:
      """stdout は EOF だが、wait() までは終了していない git。"""

      def __init__(self, *args, **kwargs) -> None:
        self.stdout = io.BytesIO(b"text without nul\n")
        self.returncode = None
        self.killed = False

      def poll(self):
        return self.returncode

      def kill(self) -> None:
        self.killed = True
        self.returncode = -9

      def wait(self) -> int:
        if self.returncode is None:
          self.returncode = 0
        return self.returncode

    started = []

    def popen(*args, **kwargs):
      started.append(ExitingGit())
      return started[-1]

    with mock.patch.object(prepare.subprocess, "Popen", side_effect=popen):
      self.assertFalse(prepare.blob_has_nul("0" * 39 + "1"))
    self.assertFalse(started[0].killed)

  def test_cat_file_does_not_read_system_attributes(self) -> None:
    blob = subprocess.run(
      ["git", "hash-object", "-w", "--stdin"], input=b"text\n", capture_output=True, check=True
    ).stdout.decode().strip()
    with mock.patch.object(prepare.subprocess, "Popen", wraps=prepare.subprocess.Popen) as spy:
      prepare.blob_has_nul(blob)
    self.assertEqual(spy.call_args.kwargs["env"]["GIT_ATTR_NOSYSTEM"], "1")

  def test_a_large_stderr_does_not_block_reading(self) -> None:
    self.run_isolated("""
real_popen = subprocess.Popen

def noisy(args, **kwargs):
  script = "import sys; sys.stderr.write('x' * (4 * 1024 * 1024)); sys.stdout.write('ok'); sys.exit(1)"
  return real_popen([sys.executable, "-c", script], **kwargs)

prepare.subprocess.Popen = noisy
try:
  prepare.blob_has_nul("0" * 40)
except SystemExit as error:
  assert str(error).startswith("git cat-file blob"), str(error)[:80]
else:
  raise AssertionError("expected SystemExit")
""")

  def test_missing_blob_stops_with_an_error(self) -> None:
    with self.assertRaises(SystemExit):
      prepare.blob_has_nul("1" * 40)


if __name__ == "__main__":
  unittest.main()
