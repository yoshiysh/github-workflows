"""このジョブの kind のモデルと effort を選んで検査し、通ったものだけを GITHUB_OUTPUT に書く。"""

import os
import re
import sys

# --model と env に展開されるので、空白・引用符・$ と先頭の - を通すと Claude の引数を足せてしまう。
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}(\[[A-Za-z0-9]{1,16}\])?")
# CLAUDE_CODE_EFFORT_LEVEL が受ける値: https://code.claude.com/docs/en/model-config
EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "auto"})
KIND_VARIABLES = {
  "code": {"model": "CODE_MODEL", "effort": "CODE_EFFORT"},
  "docs": {"model": "DOCS_MODEL", "effort": "DOCS_EFFORT"},
}


class RejectedOption(Exception):
  """kind・モデル名・effort のどれかが受け付けられない。"""


def is_model(value: str) -> bool:
  """モデル ID・エイリアス・プロバイダ形式の名前だけを受ける。"""
  return MODEL.fullmatch(value) is not None


def is_effort(value: str) -> bool:
  """CLAUDE_CODE_EFFORT_LEVEL が受ける値だけを受ける。"""
  return value in EFFORTS


def outputs(env: dict[str, str]) -> str:
  """kind のモデル・effort とサブエージェントのモデルを選んで検査し、GITHUB_OUTPUT の行にする。"""
  kind = env.get("KIND", "")
  if kind not in KIND_VARIABLES:
    raise RejectedOption(f"Unsupported review kind: {kind!r}")
  variables = KIND_VARIABLES[kind]
  checks = (
    ("model", variables["model"], is_model, "model name"),
    ("effort", variables["effort"], is_effort, "effort level"),
    ("subagent_model", "SUBAGENT_MODEL", is_model, "model name"),
  )
  lines = []
  for output, variable, accepts, label in checks:
    if not accepts(value := env.get(variable, "")):
      raise RejectedOption(f"{variable} is not an accepted {label}.")
    lines.append(f"{output}={value}\n")
  return "".join(lines)


def main() -> None:
  try:
    text = outputs(dict(os.environ))
  except RejectedOption as error:
    print(f"::error::{error}")
    sys.exit(1)
  with open(os.environ["GITHUB_OUTPUT"], "a") as output:
    output.write(text)


if __name__ == "__main__":
  main()
