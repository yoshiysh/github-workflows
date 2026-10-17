import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION_DIR = ROOT / ".github/actions/claude-review"
WORKFLOW = ROOT / ".github/workflows/claude-review.yml"
ACTION = ACTION_DIR / "action.yml"
INPUT = re.compile(r"^      ([a-z][a-z-]*):$")
DEFAULT = re.compile(r"^        default: (.+)$")

sys.path.insert(0, str(ACTION_DIR))

# 既定値の正本は workflow の default: なので、テストもそこから読む（PyYAML は無い）。
EXPECTED = {
  "code-model", "docs-model", "code-effort", "docs-effort", "subagent-model",
  "doc-domains", "doc-suffixes", "copy-sources", "exclude-paths", "review-policy-path",
  "timeout-minutes",
}


def scalar(text: str) -> str:
  if text.startswith("'") and text.endswith("'"):
    return text[1:-1].replace("''", "'")
  return text


def workflow_defaults() -> dict[str, str]:
  """on.workflow_call.inputs の各 default: を文字列で返す。"""
  defaults, name, inside = {}, None, False
  for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
    if line == "    inputs:":
      inside = True
    elif inside and not line.startswith("      "):
      break
    elif inside and (match := INPUT.match(line)):
      name = match.group(1)
    elif inside and name and (match := DEFAULT.match(line)):
      defaults[name] = scalar(match.group(1))
  if set(defaults) != EXPECTED:
    raise AssertionError(f"workflow defaults changed: {sorted(defaults)}")
  return defaults


def default_env() -> dict[str, str]:
  """review_options が読む env を、workflow の既定値で作る。"""
  defaults = workflow_defaults()
  return {
    "CODE_MODEL": defaults["code-model"],
    "DOCS_MODEL": defaults["docs-model"],
    "CODE_EFFORT": defaults["code-effort"],
    "DOCS_EFFORT": defaults["docs-effort"],
    "SUBAGENT_MODEL": defaults["subagent-model"],
    "DOC_DOMAINS": defaults["doc-domains"],
    "DOC_SUFFIXES": defaults["doc-suffixes"],
    "COPY_SOURCES": defaults["copy-sources"],
    "EXCLUDE_PATHS": defaults["exclude-paths"],
    "REVIEW_POLICY_PATH": defaults["review-policy-path"],
    "TIMEOUT_MINUTES": defaults["timeout-minutes"],
  }


def output_values(text: str) -> dict[str, str]:
  """GITHUB_OUTPUT の name=value 行を dict にする。"""
  return dict(line.split("=", 1) for line in text.splitlines())


def git(*args: str) -> str:
  return subprocess.run(
    ["git", *args], capture_output=True, text=True, check=True
  ).stdout.strip()


class GitRepoCase(unittest.TestCase):
  """一時ディレクトリの repo/ に main ブランチの git リポジトリを作り、そこへ移る。"""

  def setUp(self) -> None:
    original_cwd = Path.cwd()
    temp_dir = tempfile.TemporaryDirectory()
    self.addCleanup(temp_dir.cleanup)
    self.addCleanup(os.chdir, original_cwd)
    self.temp_root = Path(temp_dir.name)
    self.root = self.temp_root / "repo"
    self.root.mkdir()
    os.chdir(self.root)
    git("init", "--quiet", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")

  def write(self, files: dict[str, str]) -> list[str]:
    for path, text in files.items():
      Path(path).parent.mkdir(parents=True, exist_ok=True)
      Path(path).write_text(text, encoding="utf-8")
    return list(files)

  def commit(self, *paths: str) -> str:
    git("add", *paths)
    git("commit", "--quiet", "-m", "snapshot")
    return git("rev-parse", "HEAD")
