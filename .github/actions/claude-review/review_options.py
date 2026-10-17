"""caller が reusable workflow に渡した値を検査し、通ったものだけを GITHUB_OUTPUT に書く。

prepare.py は検査済みの出力を env で受け、同じ review_settings で読む。
"""

import json
import os
import re
import sys

# --model と env に展開されるので、空白・引用符・$ と先頭の - を通すと Claude の引数を足せてしまう。
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}(\[[A-Za-z0-9]{1,16}\])?")
# CLAUDE_CODE_EFFORT_LEVEL が受ける値: https://code.claude.com/docs/en/model-config
EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "auto"})
# 検査済みの名前だけを gitattributes の diff= に書く。caller の xfuncname（正規表現）は受けない。
# git 2.55.0 の一覧: https://git-scm.com/docs/gitattributes/2.55.0 "Defining a custom hunk-header"
DIFF_DRIVERS = frozenset({
  "ada", "bash", "bibtex", "cpp", "csharp", "css", "dts", "elixir", "fortran", "fountain",
  "golang", "html", "java", "kotlin", "markdown", "matlab", "objc", "pascal", "perl", "php",
  "python", "ruby", "rust", "scheme", "tex",
})
# 最後のラベルを英字始まりにして IP アドレスを拒む。
HOSTNAME = re.compile(
  r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?"
)
# prepare.py は文書の判定でパスを小文字にしてから比べる。
DOC_SUFFIX = re.compile(r"\.[a-z0-9]{1,16}")
SOURCE_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,16}")
FIXED_DOC_DOMAINS = ("docs.github.com", "code.claude.com", "platform.claude.com")
MAX_LIST_ITEMS = 64
MAX_TEXT_LENGTH = 200
# 出力は後続 step の env になる。Linux は env の 1 文字列を MAX_ARG_STRLEN（128 KiB）までしか渡さない。
MAX_OUTPUT_BYTES = 32 * 1024
# GitHub-hosted runner の job 実行上限。どちらの runner でも同じ値が通るようにする。
MAX_TIMEOUT_MINUTES = 360
KIND_VARIABLES = {
  "code": {"model": "CODE_MODEL", "effort": "CODE_EFFORT"},
  "docs": {"model": "DOCS_MODEL", "effort": "DOCS_EFFORT"},
}


class RejectedOption(Exception):
  """caller の値が受け付けられない。"""


def is_model(value: str) -> bool:
  """モデル ID・エイリアス・プロバイダ形式の名前だけを受ける。"""
  return MODEL.fullmatch(value) is not None


def is_effort(value: str) -> bool:
  """CLAUDE_CODE_EFFORT_LEVEL が受ける値だけを受ける。"""
  return value in EFFORTS


def is_hostname(value: str) -> bool:
  """小文字の DNS 名だけを受ける。ワイルドカード・スキーム・パス・ポート・末尾の . は通さない。"""
  return len(value) <= 253 and HOSTNAME.fullmatch(value) is not None


def is_doc_suffix(value: str) -> bool:
  return DOC_SUFFIX.fullmatch(value) is not None


def is_source_suffix(value: str) -> bool:
  return SOURCE_SUFFIX.fullmatch(value) is not None


def is_text(value: str) -> bool:
  """空でなく、改行・タブ・制御文字を含まない短い文字列だけを受ける。"""
  return 0 < len(value) <= MAX_TEXT_LENGTH and value.isprintable()


def is_relative_path(value: str) -> bool:
  """リポジトリ内の相対パスだけを受ける。先頭・末尾の /、空の段、. と .. の段は通さない。"""
  return is_text(value) and all(
    part not in ("", ".", "..") for part in value.split("/")
  )


def is_exclude_pattern(value: str) -> bool:
  """* ? ** だけを使う相対パスの glob を受ける。[ ] は照合で扱わないので通さない。"""
  return is_relative_path(value) and not any(char in value for char in "[]")


def segment_matches(pattern: str, name: str) -> bool:
  """1 段の * と ? を、後戻りせず O(len(pattern) * len(name)) で照合する。"""
  reachable = [True] + [False] * len(name)
  for char in pattern:
    if char == "*":
      for index in range(1, len(name) + 1):
        reachable[index] = reachable[index] or reachable[index - 1]
    else:
      reachable = [False] + [
        reachable[index] and char in ("?", name[index])
        for index in range(len(name))
      ]
  return reachable[-1]


def path_matches(pattern: str, path: str) -> bool:
  """パス全体を glob と照合する。** は途中なら 0 段以上、最後なら 1 段以上の全段に合う。"""
  parts, names = pattern.split("/"), path.split("/")
  matched = [False] * len(names) + [True]
  for i in range(len(parts) - 1, -1, -1):
    part = parts[i]
    row = [False] * (len(names) + 1)
    for j in range(len(names), -1, -1):
      if part == "**" and i == len(parts) - 1:
        row[j] = j < len(names)
      elif part == "**":
        row[j] = matched[j] or (j < len(names) and row[j + 1])
      else:
        row[j] = j < len(names) and matched[j + 1] and segment_matches(part, names[j])
    matched = row
  return matched[0]


def json_value(env: dict[str, str], variable: str, kind: type, label: str):
  """env の JSON を読み、kind の値でなければ止める。"""
  try:
    value = json.loads(env.get(variable, ""))
  except json.JSONDecodeError:
    raise RejectedOption(f"{variable} is not a JSON {label}.") from None
  if not isinstance(value, kind):
    raise RejectedOption(f"{variable} is not a JSON {label}.")
  return value


def json_list(env: dict[str, str], variable: str, accepts) -> list[str]:
  """JSON 配列の文字列を読み、各要素を検査し、順序を保って重複を除く。"""
  return checked_items(json_value(env, variable, list, "array"), variable, accepts)


def checked_items(value: list, variable: str, accepts) -> list[str]:
  """配列の各要素を検査し、順序を保って重複を除く。"""
  if len(value) > MAX_LIST_ITEMS:
    raise RejectedOption(f"{variable} has more than {MAX_LIST_ITEMS} items.")
  items = []
  for item in value:
    if not isinstance(item, str) or not accepts(item):
      raise RejectedOption(f"{variable} has an item that is not accepted.")
    if item not in items:
      items.append(item)
  return items


def suffix_groups(
  env: dict[str, str], variable: str, accepts_suffix, field: str, checked_field
) -> list[dict]:
  """[{"suffixes": 拡張子の配列, field: 値}] を検査して返す。1 つの拡張子は 1 つのグループにだけ置ける。"""
  groups = json_value(env, variable, list, "array")
  if len(groups) > MAX_LIST_ITEMS:
    raise RejectedOption(f"{variable} has more than {MAX_LIST_ITEMS} groups.")
  checked, seen = [], set()
  for group in groups:
    if not isinstance(group, dict) or set(group) != {"suffixes", field}:
      raise RejectedOption(f'{variable} groups must have only "suffixes" and "{field}".')
    if not isinstance(group["suffixes"], list):
      raise RejectedOption(f'{variable} "suffixes" must be an array.')
    suffixes = checked_items(group["suffixes"], variable, accepts_suffix)
    if not suffixes:
      raise RejectedOption(f'{variable} has a group with no "suffixes".')
    if seen.intersection(suffixes):
      raise RejectedOption(f"{variable} lists a suffix in more than one group.")
    seen.update(suffixes)
    checked.append({"suffixes": suffixes, field: checked_field(group[field])})
  return checked


def copy_sources(env: dict[str, str]) -> list[dict]:
  """COPY_SOURCES の [{"suffixes": [...], "ignore": 無視する行頭の配列}] を検査して返す。"""
  def ignore(value) -> list[str]:
    if not isinstance(value, list):
      raise RejectedOption('COPY_SOURCES "ignore" must be an array.')
    return checked_items(value, "COPY_SOURCES", is_text)

  return suffix_groups(env, "COPY_SOURCES", is_source_suffix, "ignore", ignore)


def doc_sources(env: dict[str, str]) -> list[dict]:
  """DOC_SOURCES の [{"suffixes": [...], "diff-driver": 組み込み driver 名か null}] を検査して返す。"""
  def driver(value) -> str | None:
    if value is not None and not (isinstance(value, str) and value in DIFF_DRIVERS):
      raise RejectedOption('DOC_SOURCES "diff-driver" must be a built-in Git diff driver or null.')
    return value

  return suffix_groups(env, "DOC_SOURCES", is_doc_suffix, "diff-driver", driver)


def diff_drivers(groups: list[dict]) -> dict[str, str | None]:
  """検査済みの DOC_SOURCES を、拡張子 → diff driver の対応に展開する。"""
  return {suffix: group["diff-driver"] for group in groups for suffix in group["suffixes"]}


def ignore_prefixes(groups: list[dict[str, list[str]]]) -> dict[str, tuple[str, ...]]:
  """検査済みの COPY_SOURCES を、拡張子 → 無視する行頭の対応に展開する。"""
  return {
    suffix: tuple(group["ignore"]) for group in groups for suffix in group["suffixes"]
  }


def review_settings(env: dict[str, str]) -> dict:
  """prepare.py が使う値を検査して返す。"""
  policy = env.get("REVIEW_POLICY_PATH", "")
  if not is_relative_path(policy):
    raise RejectedOption("REVIEW_POLICY_PATH is not an accepted repository-relative path.")
  docs, sources = doc_sources(env), copy_sources(env)
  if set(diff_drivers(docs)) & {suffix.lower() for suffix in ignore_prefixes(sources)}:
    raise RejectedOption("A suffix is in both DOC_SOURCES and COPY_SOURCES.")
  return {
    "doc_sources": docs,
    "copy_sources": sources,
    "exclude_paths": json_list(env, "EXCLUDE_PATHS", is_exclude_pattern),
    "review_policy_path": policy,
  }


def doc_domains(env: dict[str, str]) -> list[str]:
  """共有側の固定ドメインに caller のドメインを足し、重複を除く。"""
  domains = list(FIXED_DOC_DOMAINS)
  for domain in json_list(env, "DOC_DOMAINS", is_hostname):
    if domain not in domains:
      domains.append(domain)
  return domains


def timeout_minutes(env: dict[str, str]) -> int:
  value = env.get("TIMEOUT_MINUTES", "")
  if not re.fullmatch(r"[1-9][0-9]{0,3}", value) or int(value) > MAX_TIMEOUT_MINUTES:
    raise RejectedOption(
      f"TIMEOUT_MINUTES must be a whole number from 1 to {MAX_TIMEOUT_MINUTES}."
    )
  return int(value)


def outputs(env: dict[str, str]) -> str:
  """全ての値を検査してから、GITHUB_OUTPUT の行にする。"""
  kind = env.get("KIND", "")
  if kind not in KIND_VARIABLES:
    raise RejectedOption(f"Unsupported review kind: {kind!r}")
  variables = KIND_VARIABLES[kind]
  checks = (
    ("model", variables["model"], is_model, "model name"),
    ("effort", variables["effort"], is_effort, "effort level"),
    ("subagent_model", "SUBAGENT_MODEL", is_model, "model name"),
  )
  values = {}
  for output, variable, accepts, label in checks:
    if not accepts(value := env.get(variable, "")):
      raise RejectedOption(f"{variable} is not an accepted {label}.")
    values[output] = value
  timeout_minutes(env)
  domains = doc_domains(env)
  values["doc_domains"] = f"{', '.join(domains[:-1])}, and {domains[-1]}"
  values["webfetch_tools"] = ",".join(f"WebFetch(domain:{domain})" for domain in domains)
  for name, value in review_settings(env).items():
    values[name] = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
  for name, value in values.items():
    if len(value.encode()) > MAX_OUTPUT_BYTES:
      raise RejectedOption(f"The checked {name} value is longer than {MAX_OUTPUT_BYTES} bytes.")
  return "".join(f"{name}={value}\n" for name, value in values.items())


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
