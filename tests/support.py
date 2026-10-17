import json
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
SELF_REVIEW = ROOT / ".github/workflows/claude-review-self.yml"
ACTION = ACTION_DIR / "action.yml"
INPUT = re.compile(r"^      ([a-z][a-z-]*):$")
FIELD = re.compile(r"^        (default|required): (.+)$")
WITH_VALUE = re.compile(r"^      ([a-z][a-z-]*): (.+)$")

sys.path.insert(0, str(ACTION_DIR))

REQUIRED_INPUTS = {
  "runs-on", "doc-domains", "doc-suffixes", "copy-sources", "exclude-paths",
  "review-policy-path",
}
DEFAULTED_INPUTS = {
  "code-model", "docs-model", "code-effort", "docs-effort", "subagent-model",
  "timeout-minutes",
}

# 必須 input は共有側に既定値が無いので、テストは caller の例としてこの値を渡す。
FIXTURE_INPUTS = {
  "doc-domains": "[]",
  "doc-suffixes": '[".md",".markdown",".mdx"]',
  "copy-sources": json.dumps([
    {"suffixes": [".rs"], "ignore": ["//", "/*", "*", "#", "use ", "pub use "]},
    {"suffixes": [".py"], "ignore": ["#", "import ", "from "]},
    {"suffixes": [".sh"], "ignore": ["#"]},
    {
      "suffixes": [".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"],
      "ignore": ["//", "/*", "*", "#!", "import ", "} from ", "export * ", "export {"],
    },
  ]),
  "exclude-paths": '["**/*.lock","**/package-lock.json","**/pnpm-lock.yaml"]',
  "review-policy-path": "REVIEW.md",
}


def scalar(text: str) -> str:
  if text.startswith("'") and text.endswith("'"):
    return text[1:-1].replace("''", "'")
  return text


def workflow_inputs() -> dict[str, dict[str, str]]:
  """on.workflow_call.inputs の各 input の default: と required: を文字列で返す。"""
  inputs, name, inside = {}, None, False
  for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
    if line == "    inputs:":
      inside = True
    elif inside and not line.startswith("      "):
      break
    elif inside and (match := INPUT.match(line)):
      name = match.group(1)
      inputs[name] = {}
    elif inside and name and (match := FIELD.match(line)):
      inputs[name][match.group(1)] = scalar(match.group(2))
  if set(inputs) != REQUIRED_INPUTS | DEFAULTED_INPUTS:
    raise AssertionError(f"workflow inputs changed: {sorted(inputs)}")
  return inputs


def self_review_inputs() -> dict[str, str]:
  """claude-review-self.yml が reusable workflow に渡す with: の値を返す。"""
  values, inside = {}, False
  for line in SELF_REVIEW.read_text(encoding="utf-8").splitlines():
    if line == "    with:":
      inside = True
    elif inside and (match := WITH_VALUE.match(line)):
      values[match.group(1)] = scalar(match.group(2))
    elif inside:
      break
  return values


def inputs_env(inputs: dict[str, str]) -> dict[str, str]:
  """reusable workflow の input 名の値を、review_options が読む env にする。"""
  defaults = {
    name: field["default"]
    for name, field in workflow_inputs().items()
    if name in DEFAULTED_INPUTS
  }
  values = {**defaults, **inputs}
  return {
    name.upper().replace("-", "_"): values[name]
    for name in (REQUIRED_INPUTS - {"runs-on"}) | DEFAULTED_INPUTS
  }


def default_env() -> dict[str, str]:
  """workflow の既定値に、必須 input の FIXTURE_INPUTS を足した env を作る。"""
  return inputs_env(FIXTURE_INPUTS)


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
