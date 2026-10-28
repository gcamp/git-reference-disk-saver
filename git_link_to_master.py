#!/usr/bin/env python3
"""
Link a child git repository to a master repository using git alternates.
This saves disk space by sharing git objects between repos.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def get_git_dir(repo_path: Path) -> Path | None:
    """Get the .git directory for a repo (handles worktrees and submodules)."""
    git_path = repo_path / ".git"
    if git_path.is_dir():
        return git_path
    if git_path.is_file():
        content = git_path.read_text().strip()
        if content.startswith("gitdir: "):
            gitdir = Path(content[8:])
            if not gitdir.is_absolute():
                gitdir = (repo_path / gitdir).resolve()
            return gitdir
    return None


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def get_current_alternate(objects_dir: Path) -> Path | None:
    """Get the current alternate path if set."""
    alternates_file = objects_dir / "info" / "alternates"
    if alternates_file.exists():
        content = alternates_file.read_text().strip()
        if content:
            return Path(content.split('\n')[0])
    return None


def setup_alternates(child_objects: Path, master_objects: Path, dry_run: bool = False) -> bool:
    """Set up alternates file to point to master repo's objects."""
    if not master_objects.exists():
        return False

    master_resolved = master_objects.resolve()
    current = get_current_alternate(child_objects)

    if current and current.resolve() == master_resolved:
        print(f"  Already linked to {master_resolved}")
        return True

    alternates_dir = child_objects / "info"
    alternates_file = alternates_dir / "alternates"

    if dry_run:
        print(f"  Would link -> {master_resolved}")
        return True

    alternates_dir.mkdir(parents=True, exist_ok=True)
    alternates_file.write_text(str(master_resolved) + "\n")
    print(f"  Linked -> {master_resolved}")
    return True


def run_gc(repo_path: Path) -> None:
    """Repack to remove objects now available via alternates."""
    print(f"  Running git repack...")
    subprocess.run(
        ["git", "-C", str(repo_path), "repack", "-a", "-d", "-l"],
        capture_output=True, check=False
    )


def link_repo(child_path: Path, master_path: Path, dry_run: bool = False, run_gc_after: bool = True) -> bool:
    """Link a child repository to use a master repository's objects via alternates."""
    child_git = get_git_dir(child_path)
    master_git = get_git_dir(master_path)

    if not child_git or not master_git:
        print(f"Error: Could not find .git directory", file=sys.stderr)
        return False

    child_objects = child_git / "objects"
    master_objects = master_git / "objects"

    success = setup_alternates(child_objects, master_objects, dry_run)

    if success and run_gc_after and not dry_run:
        run_gc(child_path)

    return success


def main():
    parser = argparse.ArgumentParser(
        description="Link a child git repository to a master repository using alternates."
    )
    parser.add_argument("child", type=Path, help="Path to the child repository")
    parser.add_argument("master", type=Path, help="Path to the master repository")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Show what would be done")
    parser.add_argument("--no-gc", action="store_true", help="Skip garbage collection")

    args = parser.parse_args()

    if not args.child.exists():
        print(f"Error: Child path does not exist: {args.child}", file=sys.stderr)
        sys.exit(1)
    if not args.master.exists():
        print(f"Error: Master path does not exist: {args.master}", file=sys.stderr)
        sys.exit(1)

    print(f"Linking: {args.child}")
    print(f"To:      {args.master}")

    success = link_repo(args.child.resolve(), args.master.resolve(), args.dry_run, not args.no_gc)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
