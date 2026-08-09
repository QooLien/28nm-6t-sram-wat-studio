#!/usr/bin/env python3
"""Safely check and fast-forward this checkout to origin/main."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class SyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncStatus:
    branch: str
    local_sha: str
    remote_sha: str
    ahead: int
    behind: int
    tracked_changes: bool

    @property
    def is_current(self) -> bool:
        return self.ahead == 0 and self.behind == 0


class ProjectSynchronizer:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir.resolve()

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.project_dir, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise SyncError(detail or f"git {' '.join(args)} failed")
        return result.stdout.strip()

    def validate(self) -> None:
        if shutil.which("git") is None:
            raise SyncError("找不到 Git，請先安裝 Git。")
        root = Path(self.git("rev-parse", "--show-toplevel")).resolve()
        if root != self.project_dir:
            raise SyncError(f"同步工具不在 Git 專案根目錄：{root}")
        self.git("remote", "get-url", "origin")

    def fetch(self) -> None:
        self.git("fetch", "--prune", "origin", "main")

    def status(self, *, fetch: bool = True) -> SyncStatus:
        self.validate()
        if fetch:
            self.fetch()
        branch = self.git("branch", "--show-current") or "(detached HEAD)"
        local_sha = self.git("rev-parse", "HEAD")
        remote_sha = self.git("rev-parse", "origin/main")
        counts = self.git("rev-list", "--left-right", "--count", "HEAD...origin/main")
        ahead_text, behind_text = counts.split()
        tracked_changes = bool(self.git("status", "--porcelain", "--untracked-files=no"))
        return SyncStatus(
            branch=branch, local_sha=local_sha, remote_sha=remote_sha,
            ahead=int(ahead_text), behind=int(behind_text),
            tracked_changes=tracked_changes,
        )

    def sync(self) -> SyncStatus:
        before = self.status(fetch=True)
        if before.branch != "main":
            raise SyncError(f"目前分支是 {before.branch}；請先切換到 main 再同步。")
        if before.tracked_changes:
            raise SyncError("本機有尚未提交的追蹤檔案變更，為避免覆蓋，已停止同步。")
        if before.ahead:
            if before.behind:
                raise SyncError("本機 main 與 GitHub main 已分歧，無法安全快轉。")
            raise SyncError("本機 main 有尚未推送的 commit，請先 push 或確認處理方式。")
        if before.behind:
            self.git("merge", "--ff-only", "origin/main")
        after = self.status(fetch=False)
        if not after.is_current:
            raise SyncError("同步後驗證失敗：本機 HEAD 與 origin/main 不一致。")
        return after


def find_project_root(start: Path) -> Path:
    """Find the nearest parent containing the repository metadata."""
    for candidate in (start.resolve(), *start.resolve().parents):
        if (candidate / ".git").exists():
            return candidate
    raise SyncError("找不到 Git 專案根目錄。")


def short_sha(value: str) -> str:
    return value[:8]


def print_status(status: SyncStatus) -> None:
    print(f"目前分支       : {status.branch}")
    print(f"本機版本       : {short_sha(status.local_sha)}")
    print(f"GitHub main    : {short_sha(status.remote_sha)}")
    print(f"本機領先/落後  : {status.ahead}/{status.behind}")
    print(f"追蹤檔案修改   : {'有' if status.tracked_changes else '無'}")
    if status.is_current and status.tracked_changes:
        print("驗證結果       : GitHub commit 已是最新，但本機有未提交修改")
    elif status.is_current:
        print("驗證結果       : 已是最新版本")
    elif status.ahead == 0 and status.behind > 0:
        print(f"驗證結果       : 有 {status.behind} 個更新可下載")
    else:
        print("驗證結果       : 需要人工確認，未自動修改")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="檢查並安全同步 origin/main")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="只檢查，不更新")
    action.add_argument("--sync", action="store_true", help="安全快轉至最新版")
    action.add_argument("--interactive", action="store_true", help="檢查後詢問是否更新")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        synchronizer = ProjectSynchronizer(find_project_root(Path(__file__).parent))
        print("正在連線 GitHub 並驗證版本…")
        status = synchronizer.status(fetch=True)
        print_status(status)
        if args.check or (not args.sync and not args.interactive):
            return 0
        should_sync = args.sync
        if args.interactive and status.behind and not status.ahead:
            answer = input("是否立即同步？[y/N] ").strip().lower()
            should_sync = answer in {"y", "yes"}
        if should_sync:
            print("正在安全同步 origin/main…")
            result = synchronizer.sync()
            print_status(result)
        elif args.interactive and status.behind:
            print("已取消，未修改任何檔案。")
        return 0
    except SyncError as exc:
        print(f"同步停止：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
