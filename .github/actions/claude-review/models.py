"""このジョブの kind のモデル名を選んで検査し、通ったものだけを GITHUB_OUTPUT に書く。"""

import os
import re
import sys

# --model と env に展開されるので、空白・引用符・$ と先頭の - を通すと Claude の引数を足せてしまう。
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}(\[[A-Za-z0-9]{1,16}\])?")
KIND_MODEL = {"code": "CODE_MODEL", "docs": "DOCS_MODEL"}


class RejectedModel(Exception):
  """モデル名か kind が受け付けられない。"""


def is_model(value: str) -> bool:
  """モデル ID・エイリアス・プロバイダ形式の名前だけを受ける。"""
  return MODEL.fullmatch(value) is not None


def outputs(env: dict[str, str]) -> str:
  """kind のモデルとサブエージェントのモデルを選んで検査し、GITHUB_OUTPUT の行にする。"""
  kind = env.get("KIND", "")
  if kind not in KIND_MODEL:
    raise RejectedModel(f"Unsupported review kind: {kind!r}")
  lines = []
  for output, variable in (("model", KIND_MODEL[kind]), ("subagent_model", "SUBAGENT_MODEL")):
    if not is_model(value := env.get(variable, "")):
      raise RejectedModel(f"{variable} is not an accepted model name.")
    lines.append(f"{output}={value}\n")
  return "".join(lines)


def main() -> None:
  try:
    text = outputs(dict(os.environ))
  except RejectedModel as error:
    print(f"::error::{error}")
    sys.exit(1)
  with open(os.environ["GITHUB_OUTPUT"], "a") as output:
    output.write(text)


if __name__ == "__main__":
  main()
