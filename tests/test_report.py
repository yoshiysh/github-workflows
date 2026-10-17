import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import support

import report

HEADER = "**Claude レビュー（コード）**"
OTHER_HEADER = "**Claude レビュー（文書）**"


def comments_page(nodes: list[dict]) -> str:
  return json.dumps({
    "data": {"repository": {"pullRequest": {"comments": {
      "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes,
    }}}}
  })


def node(comment_id: str, body: str, mine: bool = True, minimized: bool = False) -> dict:
  return {"id": comment_id, "body": body, "viewerDidAuthor": mine, "isMinimized": minimized}


def call_kind(args: list[str]) -> str:
  """gh の argv を投稿・一覧・畳むのどれかに分ける。どれでもなければテストを落とす。"""
  if args[:3] == ["gh", "pr", "comment"]:
    return "post"
  query = next((a[len("query="):] for a in args if a.startswith("query=")), None)
  if args[:3] == ["gh", "api", "graphql"] and query == report.MINIMIZE_MUTATION:
    return "minimize"
  if args[:3] == ["gh", "api", "graphql"] and query == report.COMMENTS_QUERY:
    return "list"
  raise AssertionError(f"unexpected gh call: {args}")


class FakeGh:
  """report が呼ぶ gh を記録し、総括の一覧の問い合わせと畳む mutation に答える。"""

  def __init__(self, pages: list[str], fail_list: bool = False, fail_minimize: set[str] = frozenset()) -> None:
    self.pages, self.fail_list, self.fail_minimize = pages, fail_list, fail_minimize
    self.calls: list[list[str]] = []

  def __call__(self, args: list[str], **_kwargs) -> subprocess.CompletedProcess:
    self.calls.append(args)
    kind = call_kind(args)
    if kind == "minimize":
      if comment_id(args) in self.fail_minimize:
        raise subprocess.CalledProcessError(1, args, stderr="GraphQL: Resource not accessible\nby integration")
      return subprocess.CompletedProcess(args, 0, stdout="{}")
    if kind == "list":
      if self.fail_list:
        raise subprocess.CalledProcessError(1, args, stderr="HTTP 502")
      return subprocess.CompletedProcess(args, 0, stdout="".join(self.pages))
    return subprocess.CompletedProcess(args, 0, stdout="")

  def minimized(self) -> list[str]:
    return [comment_id(call) for call in self.calls if call_kind(call) == "minimize"]

  def kinds(self) -> list[str]:
    return [call_kind(call) for call in self.calls]

  def call(self, kind: str) -> list[str]:
    return next(call for call in self.calls if call_kind(call) == kind)


def comment_id(args: list[str]) -> str:
  return next(a for a in args if a.startswith("id="))[len("id="):]


class PostTests(unittest.TestCase):
  def setUp(self) -> None:
    temp_dir = tempfile.TemporaryDirectory()
    self.addCleanup(temp_dir.cleanup)
    self.out = Path(temp_dir.name)
    self.env = {
      "HEADER": HEADER, "PR": "7", "REPO": "owner/name", "CLAUDE_OUTCOME": "success",
      "RUN_URL": "https://example.invalid/run",
    }

  def post(self, fake: FakeGh) -> str:
    log = StringIO()
    with mock.patch.object(report.subprocess, "run", side_effect=fake), redirect_stdout(log):
      report.post(self.env, self.out, ["note"])
    return log.getvalue()

  def test_only_own_unminimized_summaries_of_this_kind_are_minimized_after_posting(self) -> None:
    (self.out / "summary.md").write_text("指摘なし\n")
    fake = FakeGh([
      comments_page([
        node("old", f"{HEADER}\n\n前回"),
        node("failed", f"  {HEADER}\n\n**レビューの失敗**: 総括が書かれなかった。"),
        node("other-kind", f"{OTHER_HEADER}\n\n前回"),
        node("someone", f"{HEADER}\n\n引用", mine=False),
        node("hidden", f"{HEADER}\n\n前々回", minimized=True),
        node("mention", f"返信 {HEADER}"),
      ]),
      comments_page([node("older", f"{HEADER}\n\n前々々回")]),
    ])
    log = self.post(fake)
    self.assertEqual(fake.minimized(), ["old", "failed", "older"])
    self.assertEqual(fake.kinds(), ["list", "post", "minimize", "minimize", "minimize"])
    self.assertIn("3/3", log)
    self.assertRegex(report.MINIMIZE_MUTATION, r"minimizeComment\(\s*input:\s*\{[^}]*classifier:\s*OUTDATED")

  def test_the_listing_pages_through_this_pull_request_and_the_post_targets_it(self) -> None:
    (self.out / "summary.md").write_text("指摘なし\n")
    fake = FakeGh([comments_page([])])
    self.post(fake)
    listing = fake.call("list")
    self.assertIn("--paginate", listing)
    query = next(a for a in listing if a.startswith("query="))
    self.assertRegex(query, r"\$endCursor:\s*String")
    self.assertRegex(query, r"comments\([^)]*after:\s*\$endCursor")
    for flag, value in (("-f", "owner=owner"), ("-f", "name=name"), ("-F", "n=7")):
      self.assertIn(value, listing)
      self.assertEqual(listing[listing.index(value) - 1], flag)
    self.assertEqual(
      fake.call("post"),
      ["gh", "pr", "comment", "7", "--repo", "owner/name", "--body-file", str(self.out / "comment.md")],
    )

  def test_a_null_node_is_skipped_and_the_rest_are_minimized(self) -> None:
    (self.out / "summary.md").write_text("指摘なし\n")
    fake = FakeGh([comments_page([None, node("old", f"{HEADER}\n\n前回")])])
    log = self.post(fake)
    self.assertEqual(fake.minimized(), ["old"])
    self.assertNotIn("::warning::", log)

  def test_a_failed_minimize_warns_and_does_not_stop_the_job(self) -> None:
    (self.out / "summary.md").write_text("指摘なし\n")
    fake = FakeGh(
      [comments_page([node("a", f"{HEADER}\n\n前回"), node("b", f"{HEADER}\n\n前々回")])],
      fail_minimize={"a"},
    )
    log = self.post(fake)
    self.assertEqual(fake.kinds(), ["list", "post", "minimize", "minimize"])
    self.assertIn("::warning::前回の総括 a を畳めなかった: GraphQL: Resource not accessible by integration\n", log)
    self.assertIn("1/2", log)

  def test_a_failed_listing_warns_and_still_posts(self) -> None:
    (self.out / "summary.md").write_text("指摘なし\n")
    fake = FakeGh([], fail_list=True)
    log = self.post(fake)
    self.assertEqual(fake.kinds(), ["list", "post"])
    self.assertIn("::warning::前回の総括を探せなかった: HTTP 502", log)

  def test_a_missing_summary_posts_the_failure_and_minimizes_nothing(self) -> None:
    fake = FakeGh([comments_page([node("old", f"{HEADER}\n\n前回")])])
    with self.assertRaises(SystemExit):
      self.post(fake)
    self.assertEqual(fake.kinds(), ["post"])


if __name__ == "__main__":
  unittest.main()
