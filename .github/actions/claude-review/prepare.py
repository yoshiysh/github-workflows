"""Claude レビューの 1 ジョブ分の入力を作る。

PR の差分を変更ファイルごとに書き出し、このジョブが照合するファイルの一覧と、
既存のレビュースレッド・このジョブの前回の総括を用意する。PR 由来の文字列は
ファイルにだけ書き、ワークフローの式には展開しない。
"""

import functools
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

import review_options

ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
# --attr-source が入った版。RelNotes 2.41.0
MIN_GIT = (2, 41)
BLOB_CHUNK = 64 * 1024
SUBMODULE_MODE = "160000"
SYMLINK_MODE = "120000"
COPY_MIN_LENGTH = 40
COPY_MAX_PLACES = 12
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")
FAILURE_MARK = "**レビューの失敗**"
PR_SNAPSHOT = ".claude-pr"
REVIEW_OUTPUT = ".claude-review"
CONTROL_DIR = ".claude"
CONTROL_FILES = ("CLAUDE.md", ".mcp.json", ".gitmodules")
THREADS_QUERY = """
query($owner: String!, $name: String!, $n: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $n) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved path line originalLine
          first: comments(first: 1) { nodes { author { login } body } }
          last: comments(last: 1) { totalCount nodes { author { login } body } }
        }
      }
    }
  }
}
"""


def git_env() -> dict[str, str]:
  """git の子プロセスの env。system-wide の gitattributes は読ませない。"""
  return {**os.environ, "GIT_ATTR_NOSYSTEM": "1"}


def run(*args: str, stdin: str = "") -> str:
  """コマンドを実行し、失敗したら標準エラーを添えて止まる。"""
  result = subprocess.run(
    args,
    input=stdin,
    capture_output=True,
    encoding="utf-8",
    errors="replace",
    check=False,
    env=git_env(),
  )
  if result.returncode != 0:
    sys.exit(f"{' '.join(args[:3])} failed: {result.stderr.strip()}")
  return result.stdout


@functools.cache
def empty_tree() -> str:
  """このリポジトリのハッシュ形式での空の tree の id。"""
  return run("git", "hash-object", "-t", "tree", "--stdin").strip()


def attribute_options(attributes: Path) -> list[str]:
  """git に attributes を action の書いたファイルからだけ読ませる引数を返す。

  --attr-source に空の tree を渡し、作業ツリー（PR head）とリポジトリの .gitattributes を読ませない。
  """
  return [f"--attr-source={empty_tree()}", "-c", f"core.attributesFile={attributes}"]


def changed_files(
  diff_range: str, attributes: Path
) -> list[tuple[str, str, list[str], tuple[str, str], tuple[str, str]]]:
  """変更ファイルを (表示パス, 状態, git diff に渡すパス, (変更前, 変更後の mode), (変更前, 変更後の blob)) で返す。"""
  raw = run(
    "git", *attribute_options(attributes), "-c", "core.quotePath=false",
    "diff", "--raw", "--no-abbrev", "-z", "-M", diff_range,
  )
  fields = raw.split("\0")
  entries = []
  i = 0
  while i < len(fields) and fields[i].startswith(":"):
    meta = fields[i][1:].split()
    old_mode, new_mode, old_blob, new_blob, status = meta[:5]
    modes, blobs = (old_mode, new_mode), (old_blob, new_blob)
    if status[0] in "RC":
      old, new = fields[i + 1], fields[i + 2]
      entries.append((new, status[0], [old, new], modes, blobs))
      i += 3
    else:
      path = fields[i + 1]
      entries.append((path, status[0], [path], modes, blobs))
      i += 2
  return entries


def is_binary(blobs: Iterable[str]) -> bool:
  """どちらかの側の blob が NUL を含めば binary とする。

  差分は --text で取るので、git の設定や attributes ではなくここで決める。片側でも NUL があれば
  その側の行が差分に入りうるので、先頭だけでなく blob 全体を見る。全 0 の id は存在しない側。
  """
  return any(blob_has_nul(blob) for blob in blobs if set(blob) != {"0"})


def blob_has_nul(blob: str) -> bool:
  """blob を BLOB_CHUNK ずつ読み、NUL が出た時点で読むのをやめる。大きな blob を丸ごと載せない。

  stderr はパイプにせず一時ファイルへ流す。stdout を読む間に git が stderr を詰まらせて止まらないため。
  kill は NUL を見つけたときと例外のときだけにする。読み切った直後の git はまだ終了処理中のことがある。
  """
  with tempfile.TemporaryFile() as errors:
    process = subprocess.Popen(
      ["git", "cat-file", "blob", blob],
      stdin=subprocess.DEVNULL,
      stdout=subprocess.PIPE,
      stderr=errors,
      env=git_env(),
    )
    found, finished = False, False
    try:
      while chunk := process.stdout.read(BLOB_CHUNK):
        if b"\0" in chunk:
          found = True
          break
      finished = not found
    finally:
      if not finished:
        process.kill()
      process.wait()
      process.stdout.close()
    if found:
      return True
    if process.returncode != 0:
      errors.seek(0)
      message = errors.read().decode(errors="replace").strip()
      sys.exit(f"git cat-file blob {blob} failed: {message}")
  return False


def base_review_policy(base_ref: str, policy_path: Path) -> str:
  """ベースにあるレビュー方針を返し、未作成なら空文字列を返す。"""
  path = policy_path.as_posix()
  listing = subprocess.run(
    [
      "git", "ls-tree", "--full-tree", "-z", base_ref, "--",
      f":(literal){path}",
    ],
    capture_output=True,
    check=False,
  )
  if listing.returncode != 0:
    sys.exit(f"Could not inspect the base review policy: {base_ref}:{path}")
  for entry in listing.stdout.split(b"\0"):
    if not entry:
      continue
    metadata, entry_path = entry.split(b"\t", 1)
    _mode, object_type, _object_id = metadata.decode().split()
    if entry_path != path.encode():
      continue
    if object_type != "blob":
      sys.exit(f"The base review policy is not a file: {base_ref}:{path}")
    return run("git", "show", f"{base_ref}:{path}")
  return ""


def file_diff(
  diff_range: str, paths: list[str], *, whole_sections: bool, attributes: Path
) -> str:
  """1 ファイルの差分を返す。whole_sections なら、diff driver が区切る節を丸ごと含める。"""
  config = [*attribute_options(attributes), "--literal-pathspecs", "-c", "core.quotePath=false"]
  options = ["--no-color", "--no-ext-diff", "--no-textconv", "--text", "-M"]
  if whole_sections:
    options.append("-W")
  return run("git", *config, "diff", *options, diff_range, "--", *paths)


def added_lines(diff: str) -> list[tuple[int, str]]:
  """差分が足した行を (変更後の行番号, 前後の空白を除いた行) で返す。"""
  added, number = [], None
  for line in diff.split("\n"):
    hunk = HUNK.match(line)
    if hunk:
      number = int(hunk.group(1))
    elif number is None:
      continue
    elif line.startswith("+"):
      added.append((number, line[1:].strip()))
      number += 1
    elif line.startswith(" "):
      number += 1
  return added


def copies(
  added: dict[str, list[tuple[int, str]]],
  sources: dict[str, str],
  ignore_prefixes: Mapping[str, tuple[str, ...]],
) -> list[str]:
  """足した行と同じ行がある場所を、"<場所の数>\t<場所>\t<行>" の行にして返す。

  場所は path:line の並びで、この PR が足した行には + を付ける。短い行、そのファイルの拡張子に
  ignore_prefixes が割り当てた行頭（コメント・import など）で始まる行、COPY_MAX_PLACES を超える
  場所にある行（どこにでもある書き方）は出さない。
  """
  wanted = {}
  for path, lines in added.items():
    prefixes = next(
      (prefixes for suffix, prefixes in ignore_prefixes.items() if path.endswith(suffix)), ()
    )
    for _, text in lines:
      if len(text) >= COPY_MIN_LENGTH and not text.startswith(prefixes):
        wanted.setdefault(text, set())
  new = {(path, number) for path, lines in added.items() for number, _ in lines}
  for path in sorted(sources):
    for number, line in enumerate(sources[path].split("\n"), 1):
      text = line.strip()
      if text in wanted:
        wanted[text].add((path, number))
  rows = []
  for text, places in wanted.items():
    if 2 <= len(places) <= COPY_MAX_PLACES:
      listed = " ".join(
        f"{'+' if (path, number) in new else ''}{path}:{number}"
        for path, number in sorted(places)
      )
      rows.append((len(places), text, listed))
  return [f"{count}\t{listed}\t{text}" for count, text, listed in sorted(rows)]


def tracked_sources(
  head: str, changed: Iterable[str], source_suffixes: tuple[str, ...]
) -> dict[str, str]:
  """git が追うソースのファイルを {パス: 中身} で返す。

  changed のパスは head のコミットから読む。足した行の番号は head のものなので、
  base を取り込んだ作業ツリーから読むと、番号が合わなくなる。
  """
  paths = run("git", "-c", "core.quotePath=false", "ls-files", "-z").split("\0")
  sources = {}
  for path in paths:
    file = Path(path)
    if path.endswith(source_suffixes) and file.is_file() and not file.is_symlink():
      sources[path] = file.read_text(encoding="utf-8", errors="replace")
  for path in changed:
    sources[path] = run("git", "--literal-pathspecs", "show", f"{head}:{path}")
  return sources


def prior_reviews(repo: str, pr: str, paths: set[str], header: str) -> str:
  """このジョブのファイルに付いたスレッドと、このジョブの前回の総括を文章にする。"""
  owner, name = repo.split("/", 1)
  pages = json_values(
    run(
      "gh",
      "api",
      "graphql",
      "--paginate",
      "-f",
      f"owner={owner}",
      "-f",
      f"name={name}",
      "-F",
      f"n={pr}",
      "-f",
      f"query={THREADS_QUERY}",
    )
  )
  threads = [
    node
    for page in pages
    for node in page["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
  ]
  lines = []
  for thread in threads:
    if thread["path"] not in paths or not thread["first"]["nodes"]:
      continue
    head = thread["first"]["nodes"][0]
    state = "解決済み" if thread["isResolved"] else "未解決"
    entry = f"- {thread['path']}:{thread['line'] or thread['originalLine']} ({state}) {short(head['body'])}"
    if thread["last"]["totalCount"] > 1:
      last = thread["last"]["nodes"][0]
      author = (last["author"] or {}).get("login")
      entry += f" ／ 最後の返信 ({author}): {short(last['body'])}"
    lines.append(entry)
  comments = run(
    "gh",
    "api",
    f"repos/{repo}/issues/{pr}/comments",
    "--paginate",
    "--jq",
    '[.[] | select(.user.login == "github-actions[bot]") | .body]',
  )
  previous = [
    c
    for c in [c for page in json_values(comments) for c in page]
    if c.lstrip().startswith(header) and FAILURE_MARK not in c
  ]
  out = [
    "スレッド:",
    "\n".join(lines) if lines else "(このジョブのファイルに付いたスレッドは無い)",
  ]
  out += ["", "前回の総括:", previous[-1] if previous else "(無い)"]
  return "\n".join(out) + "\n"


def pr_description(repo: str, pr: str) -> str:
  """PR のタイトルと本文を、切り詰めずに文章にする。"""
  pull = json.loads(run("gh", "api", f"repos/{repo}/pulls/{pr}"))
  return f"タイトル: {pull['title']}\n\n本文:\n{pull['body'] or '(無い)'}\n"


def json_values(text: str) -> list:
  """--paginate がページごとに出す JSON を順に読み、ページの一覧にする。"""
  decoder = json.JSONDecoder()
  pages, index = [], 0
  while True:
    while index < len(text) and text[index].isspace():
      index += 1
    if index >= len(text):
      return pages
    page, index = decoder.raw_decode(text, index)
    pages.append(page)


def short(text: str, limit: int = 200) -> str:
  """改行を詰めて limit 文字に切る。"""
  text = " ".join((text or "").split())
  return text if len(text) <= limit else text[:limit] + "…"


def doc_driver(path: str, diff_drivers: Mapping[str, str | None]) -> tuple[bool, str | None]:
  """文書なら (True, その拡張子の diff driver か None)、文書でなければ (False, None) を返す。"""
  name = path.translate(ASCII_LOWER)
  for suffix, driver in diff_drivers.items():
    if name.endswith(suffix):
      return True, driver
  return False, None


def attribute_lines(diff_drivers: Mapping[str, str | None]) -> str:
  """driver のある拡張子に diff=<driver> を付ける gitattributes を返す。大文字の拡張子にも合わせる。"""
  lines = []
  for suffix, driver in diff_drivers.items():
    if driver is not None:
      pattern = "".join(
        f"[{char}{char.upper()}]" if char.isalpha() else char for char in suffix
      )
      lines.append(f"*{pattern} diff={driver}\n")
  return "".join(lines)


def collect(
  kind: str,
  diff_range: str,
  diff_dir: Path,
  attributes: Path,
  *,
  diff_drivers: Mapping[str, str | None],
  source_suffixes: tuple[str, ...],
  exclude_paths: Iterable[str],
) -> tuple[list[str], list[str], list[str], dict[str, list[tuple[int, str]]]]:
  """このジョブのファイルの差分を書き出し、一覧の行・除外の行・変更後に symlink のパス・ソースに足した行を返す。"""
  rows, excluded, symlinks, added = [], [], [], {}
  patterns = list(exclude_paths)
  for path, status, paths, modes, blobs in changed_files(diff_range, attributes):
    is_doc, driver = doc_driver(path, diff_drivers)
    if is_doc != (kind == "docs"):
      continue
    if any(
      path == reserved or path.startswith(f"{reserved}/")
      for reserved in (PR_SNAPSHOT, REVIEW_OUTPUT)
    ):
      excluded.append(f"{path}\treserved workflow snapshot path")
      continue
    if SUBMODULE_MODE in modes:
      excluded.append(f"{path}\tsubmodule")
      continue
    if matched := next(
      (pattern for pattern in patterns if review_options.path_matches(pattern, path)), None
    ):
      excluded.append(f"{path}\texclude-paths: {matched}")
      continue
    if is_binary(blobs):
      excluded.append(f"{path}\tbinary")
      continue
    text = file_diff(
      diff_range, paths, whole_sections=driver is not None, attributes=attributes
    )
    diff_file = diff_dir / f"{len(rows) + 1:04d}.diff"
    diff_file.write_text(text)
    rows.append(f"{path}\t{status}\t{diff_file}\t{len(text.encode())}\t{driver or '-'}")
    if modes[1] == SYMLINK_MODE:
      symlinks.append(path)
    elif path.endswith(source_suffixes) and (lines := added_lines(text)):
      added[path] = lines
  return rows, excluded, symlinks, added


def remove_path(path: Path) -> None:
  """Remove a file, symlink, or directory without following symlinks."""
  if path.is_symlink() or path.is_file():
    path.unlink()
  elif path.exists():
    shutil.rmtree(path)


def tree_entries(ref: str, pathspec: str) -> list[tuple[str, str, str]]:
  """Return (mode, object id, path) entries for one literal pathspec."""
  raw = run(
    "git", "--literal-pathspecs", "ls-tree", "-z", ref, "--", pathspec
  )
  entries = []
  for entry in raw.split("\0"):
    if not entry:
      continue
    metadata, path = entry.split("\t", 1)
    mode, _kind, object_id = metadata.split()
    entries.append((mode, object_id, path))
  return entries


def write_tree_entry(
  root: Path, mode: str, object_id: str, path: str, *, preserve_symlink: bool
) -> None:
  """Write a Git tree entry under root after validating its repository-relative path."""
  relative = Path(path)
  if relative.is_absolute() or ".." in relative.parts:
    sys.exit(f"Refusing to write an unsafe repository path: {path!r}")
  target = root / relative
  if not target.parent.resolve().is_relative_to(root.resolve()):
    sys.exit(f"Refusing to write through a symlinked parent: {path!r}")
  remove_path(target)
  if mode == SUBMODULE_MODE:
    return
  target.parent.mkdir(parents=True, exist_ok=True)
  content = run("git", "cat-file", "blob", object_id)
  if mode == SYMLINK_MODE and preserve_symlink:
    target.symlink_to(content)
  else:
    target.write_text(content, encoding="utf-8")
    if mode == "100755":
      target.chmod(0o755)


def snapshot_and_restore_control_files(
  diff_range: str, head: str, base: str, root: Path, attributes: Path
) -> None:
  """Keep PR-edited Claude configuration as review data and restore trusted base copies."""
  control_paths = set()
  for _path, _status, paths, _modes, _blobs in changed_files(diff_range, attributes):
    control_paths.update(
      path
      for path in paths
      if path == CONTROL_DIR or path.startswith(f"{CONTROL_DIR}/") or path in CONTROL_FILES
    )

  snapshot_root = root / PR_SNAPSHOT
  remove_path(snapshot_root)
  if not control_paths:
    return
  for path in sorted(control_paths):
    for mode, object_id, entry_path in tree_entries(head, path):
      write_tree_entry(
        snapshot_root,
        mode,
        object_id,
        entry_path,
        preserve_symlink=False,
      )

  for path in (CONTROL_DIR, *CONTROL_FILES):
    remove_path(root / path)

  # Restore the entire base rules tree so added PR rules cannot become active config.
  base_entries = run("git", "ls-tree", "-r", "-z", base).split("\0")
  for entry in base_entries:
    if not entry:
      continue
    metadata, path = entry.split("\t", 1)
    if path != CONTROL_DIR and not path.startswith(f"{CONTROL_DIR}/"):
      continue
    mode, _kind, object_id = metadata.split()
    write_tree_entry(root, mode, object_id, path, preserve_symlink=True)

  for path in CONTROL_FILES:
    for mode, object_id, entry_path in tree_entries(base, path):
      write_tree_entry(root, mode, object_id, entry_path, preserve_symlink=True)


def fail(message: str) -> None:
  """GitHub Actions のエラー注記を出して止まる。"""
  print(f"::error::{message}")
  sys.exit(1)


def require_isolated_git() -> None:
  """PR とリポジトリの attributes を締め出せない git では止める。

  --attr-source が無い git では作業ツリーの .gitattributes が driver を変えたり
  ファイルを binary 扱いで隠したりでき、$GIT_DIR/info/attributes は action の attributes より
  優先されるので、どちらの場合もレビューせずに失敗させる。
  """
  text = run("git", "--version")
  match = re.match(r"git version (\d+)\.(\d+)", text)
  if not match or (int(match.group(1)), int(match.group(2))) < MIN_GIT:
    fail(
      f"Git {MIN_GIT[0]}.{MIN_GIT[1]} or newer is required, found {text.strip()!r}: older Git "
      "has no --attr-source, so the pull request's .gitattributes could change or hide diffs."
    )
  info = run("git", "rev-parse", "--path-format=absolute", "--git-path", "info/attributes").strip()
  if Path(info).exists():
    fail(f"{info} exists and would override the review's diff attributes; use a clean checkout.")


def main() -> None:
  """ジョブの入力を書き出し、件数を GITHUB_OUTPUT に書く。"""
  env = os.environ
  kind = env["KIND"]
  if kind not in review_options.KIND_VARIABLES:
    sys.exit(f"Unsupported review kind: {kind!r}")
  try:
    settings = review_options.review_settings(env)
  except review_options.RejectedOption as error:
    sys.exit(str(error))
  if env.get("OUT") != f"{REVIEW_OUTPUT}/{kind}":
    sys.exit("OUT must be the reserved workflow output directory for KIND.")
  require_isolated_git()
  workspace = Path(env["GITHUB_WORKSPACE"]).resolve()
  remove_path(workspace / REVIEW_OUTPUT)
  out = workspace / REVIEW_OUTPUT / kind
  diff_dir = out / "diff"
  diff_dir.mkdir(parents=True, exist_ok=True)
  drivers = review_options.diff_drivers(settings["doc_sources"])
  attributes = Path(env["RUNNER_TEMP"]) / "doc-sources.attributes"
  attributes.write_text(attribute_lines(drivers))
  run(
    "git",
    "fetch",
    "--no-tags",
    "--quiet",
    "origin",
    f"+refs/heads/{env['BASE_REF']}:refs/remotes/origin/pr-base",
  )
  policy_text = base_review_policy("origin/pr-base", Path(settings["review_policy_path"]))
  (out / "review-policy.md").write_text(policy_text, encoding="utf-8")
  profile_path = Path(__file__).parent / "profiles" / f"{kind}.md"
  if not profile_path.is_file():
    sys.exit(f"No shared review profile is defined for kind {kind!r}.")
  (out / "control-files.txt").write_text(
    "".join(f"{path}\n" for path in (f"{CONTROL_DIR}/", *CONTROL_FILES))
  )
  (out / "review-profile.md").write_text(
    profile_path.read_text(encoding="utf-8"), encoding="utf-8"
  )
  prefixes = review_options.ignore_prefixes(settings["copy_sources"])
  source_suffixes = tuple(prefixes)
  rows, excluded, symlinks, added = collect(
    kind,
    f"origin/pr-base...{env['HEAD_SHA']}",
    diff_dir,
    attributes,
    diff_drivers=drivers,
    source_suffixes=source_suffixes,
    exclude_paths=settings["exclude_paths"],
  )
  repeated = (
    copies(added, tracked_sources(env["HEAD_SHA"], added, source_suffixes), prefixes)
    if added
    else []
  )
  snapshot_and_restore_control_files(
    f"origin/pr-base...{env['HEAD_SHA']}",
    env["HEAD_SHA"],
    "origin/pr-base",
    workspace,
    attributes,
  )
  (out / "copies.tsv").write_text("".join(f"{r}\n" for r in repeated))
  (out / "files.tsv").write_text("".join(f"{r}\n" for r in rows))
  (out / "excluded.tsv").write_text("".join(f"{e}\n" for e in excluded))
  (out / "symlinks.txt").write_text("".join(f"{s}\n" for s in symlinks))
  paths = {r.split("\t", 1)[0] for r in rows}
  if rows:
    (out / "pr.md").write_text(pr_description(env["REPO"], env["PR"]))
    (out / "prior.md").write_text(
      prior_reviews(env["REPO"], env["PR"], paths, env["HEADER"])
    )
  with open(env["GITHUB_OUTPUT"], "a") as output:
    output.write(f"count={len(rows)}\n")
  print(
    f"kind={kind} files={len(rows)} excluded={len(excluded)} copies={len(repeated)}"
  )


if __name__ == "__main__":
  main()
