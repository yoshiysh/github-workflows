import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/claude-review.yml"
ACTION = ROOT / ".github/actions/claude-review/action.yml"
INPUT = re.compile(r"^      ([a-z][a-z-]*):$")
DEFAULT = re.compile(r"^        default: (.+)$")

# 既定値の正本は workflow の default: なので、テストもそこから読む（PyYAML は無い）。
EXPECTED = {
  "code-model", "docs-model", "code-effort", "docs-effort", "subagent-model",
  "doc-domains", "doc-suffixes", "source-suffixes", "copy-ignore-prefixes",
  "exclude-paths", "review-policy-path", "timeout-minutes",
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
    "SOURCE_SUFFIXES": defaults["source-suffixes"],
    "COPY_IGNORE_PREFIXES": defaults["copy-ignore-prefixes"],
    "EXCLUDE_PATHS": defaults["exclude-paths"],
    "REVIEW_POLICY_PATH": defaults["review-policy-path"],
    "TIMEOUT_MINUTES": defaults["timeout-minutes"],
  }
