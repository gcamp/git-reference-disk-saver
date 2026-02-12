#!/usr/bin/env python3
"""
Find duplicate git repositories in a folder and link them to save disk space.
Shows a dependency graph preview before applying changes.
"""

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from git_link_to_master import link_repo, get_git_dir, format_size, get_current_alternate


class Colors:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    RESET = "\033[0m"

    _enabled = True

    @classmethod
    def disable(cls):
        cls._enabled = False

    @classmethod
    def __getattribute__(cls, name):
        if name.startswith('_') or name in ('disable',):
            return super().__getattribute__(name)
        value = super().__getattribute__(name)
        return value if cls._enabled else ""


def colored(text: str, *styles: str) -> str:
    if not Colors._enabled:
        return text
    return "".join(styles) + text + Colors.RESET


@dataclass
class RepoInfo:
    path: Path
    remote_url: str
    depth: int  # Depth from scan root (lower = closer to root)
    size: int = 0
    already_linked: bool = False
    submodule_of: Path | None = None

    @property
    def is_submodule(self) -> bool:
        return self.submodule_of is not None

    @property
    def normalized_url(self) -> str:
        """Normalize URL for comparison (handle ssh vs https, .git suffix)."""
        url = self.remote_url
        if url.endswith(".git"):
            url = url[:-4]
        url = url.replace("git@github.com:", "https://github.com/")
        url = url.replace("ssh://git@github.com/", "https://github.com/")
        return url.lower()


@dataclass
class RepoGroup:
    remote_url: str
    repos: list[RepoInfo] = field(default_factory=list)

    @property
    def master(self) -> RepoInfo:
        """The master repo: prefer non-submodule, non-linked, closest to root."""
        candidates = [r for r in self.repos if not r.already_linked]
        if not candidates:
            candidates = self.repos
        # Prefer non-submodules, then by depth, then by path length
        return min(candidates, key=lambda r: (r.is_submodule, r.depth, len(str(r.path))))

    @property
    def children(self) -> list[RepoInfo]:
        """Children that need linking (excludes already linked repos)."""
        master = self.master
        return [r for r in self.repos if r != master and not r.already_linked]

    @property
    def already_linked_children(self) -> list[RepoInfo]:
        """Children that are already linked."""
        master = self.master
        return [r for r in self.repos if r != master and r.already_linked]

    @property
    def total_size(self) -> int:
        return sum(r.size for r in self.repos)

    @property
    def potential_savings(self) -> int:
        return sum(r.size for r in self.children)


def get_remote_url(repo_path: Path) -> str | None:
    """Get the origin remote URL for a repository."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def get_dir_size(path: Path) -> int:
    """Get total size of a directory in bytes."""
    if not path.exists():
        return 0
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total


def find_git_repos(root: Path, max_depth: int = 10) -> list[RepoInfo]:
    """Find all git repositories under root, including submodules."""
    repos = []
    visited = set()

    def scan_submodules(repo_path: Path, depth: int):
        """Scan submodules of a repository."""
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "submodule", "foreach", "--quiet", "echo $sm_path"],
                capture_output=True, text=True, check=True
            )
            for sm_path in result.stdout.strip().split('\n'):
                if not sm_path:
                    continue
                submod_full = repo_path / sm_path
                if not submod_full.exists():
                    continue

                remote = get_remote_url(submod_full)
                if not remote:
                    continue

                # Use get_git_dir to find actual git directory (follows .git file)
                git_dir = get_git_dir(submod_full)
                size = get_dir_size(git_dir) if git_dir else 0
                linked = get_current_alternate(git_dir / "objects") is not None if git_dir else False

                repos.append(RepoInfo(
                    path=submod_full.resolve(),
                    remote_url=remote,
                    depth=depth + 1,
                    size=size,
                    already_linked=linked,
                    submodule_of=repo_path.resolve()
                ))
                # Recursively scan nested submodules
                scan_submodules(submod_full, depth + 1)
        except subprocess.CalledProcessError:
            pass

    def scan_dir(path: Path, depth: int):
        if depth > max_depth:
            return
        resolved = path.resolve()
        if resolved in visited:
            return
        visited.add(resolved)

        git_path = path / ".git"
        if git_path.exists() and git_path.is_dir():
            remote = get_remote_url(path)
            if remote:
                git_dir = get_git_dir(path)
                size = get_dir_size(git_dir) if git_dir else 0
                linked = get_current_alternate(git_dir / "objects") is not None if git_dir else False
                repos.append(RepoInfo(
                    path=resolved,
                    remote_url=remote,
                    depth=depth,
                    size=size,
                    already_linked=linked
                ))
                # Scan submodules
                scan_submodules(path, depth)
            return  # Don't recurse into git repo subdirectories

        try:
            for entry in path.iterdir():
                if entry.is_dir() and not entry.name.startswith('.'):
                    scan_dir(entry, depth + 1)
        except PermissionError:
            pass

    print(colored("⏳ Scanning ", Colors.DIM) + colored(str(root), Colors.CYAN) + colored(" for git repositories...", Colors.DIM))
    scan_dir(root, 0)
    return repos


def group_by_remote(repos: list[RepoInfo]) -> tuple[list[RepoGroup], list[RepoGroup]]:
    """Group repositories by their normalized remote URL.

    Returns (actionable_groups, already_done_groups)
    """
    groups: dict[str, RepoGroup] = {}

    for repo in repos:
        key = repo.normalized_url
        if key not in groups:
            groups[key] = RepoGroup(remote_url=repo.remote_url)
        groups[key].repos.append(repo)

    duplicates = [g for g in groups.values() if len(g.repos) > 1]
    actionable = [g for g in duplicates if g.children]  # Has unlinked children
    already_done = [g for g in duplicates if not g.children]  # All children already linked

    return actionable, already_done


def print_dependency_graph(groups: list[RepoGroup], already_done: list[RepoGroup], root: Path) -> None:
    """Print a visual dependency graph of the proposed changes."""

    def short_path(p: Path) -> str:
        try:
            return str(p.relative_to(root))
        except ValueError:
            return str(p)

    total_savings = 0
    total_repos = 0
    total_groups = len(groups)

    if groups:
        print()
        print(colored("╔══════════════════════════════════════════════════════════════════════╗", Colors.CYAN))
        print(colored("║", Colors.CYAN) + colored("  PROPOSED CHANGES", Colors.BOLD, Colors.YELLOW) + " " * 51 + colored("║", Colors.CYAN))
        print(colored("╚══════════════════════════════════════════════════════════════════════╝", Colors.CYAN))

        for i, group in enumerate(sorted(groups, key=lambda g: g.potential_savings, reverse=True)):
            master = group.master
            children = group.children
            linked_children = group.already_linked_children
            all_children = children + linked_children

            print()
            print(colored("  ◆ ", Colors.CYAN) + colored(group.remote_url, Colors.CYAN, Colors.BOLD))
            print(colored(f"    Savings: ~{format_size(group.potential_savings)}", Colors.GREEN) +
                  colored(f"  (total: {format_size(group.total_size)})", Colors.DIM))
            print()

            # Master repo
            print(colored("    ★ ", Colors.GREEN) + colored("MASTER ", Colors.GREEN, Colors.BOLD) +
                  short_path(master.path) + colored(f" ({format_size(master.size)})", Colors.DIM))

            # Children
            for j, child in enumerate(all_children):
                is_last = (j == len(all_children) - 1)
                prefix = "└── " if is_last else "├── "
                submod_info = colored(f" ⊂ {short_path(child.submodule_of)}", Colors.DIM) if child.is_submodule else ""

                if child in children:
                    print(colored(f"      {prefix}", Colors.YELLOW) +
                          colored(short_path(child.path), Colors.YELLOW) +
                          colored(f" ({format_size(child.size)})", Colors.DIM) + submod_info)
                else:
                    print(colored(f"      {prefix}", Colors.DIM) +
                          colored(short_path(child.path), Colors.DIM) +
                          colored(" (already linked)", Colors.DIM) + submod_info)

            total_savings += group.potential_savings
            total_repos += len(children)

    if already_done:
        print()
        header = f"  ALREADY LINKED ({len(already_done)} group{'s' if len(already_done) != 1 else ''})"
        print(colored("┌" + "─" * 70 + "┐", Colors.DIM))
        print(colored("│", Colors.DIM) + colored(header, Colors.DIM) + " " * (70 - len(header)) + colored("│", Colors.DIM))
        print(colored("└" + "─" * 70 + "┘", Colors.DIM))
        for group in sorted(already_done, key=lambda g: g.total_size, reverse=True):
            master = group.master
            linked_count = len(group.already_linked_children)
            print(colored(f"    ✓ {short_path(master.path)}", Colors.DIM) +
                  colored(f" ← {linked_count} repo(s)", Colors.DIM))

    # Summary box
    if groups or already_done:
        print()
        print(colored("╔══════════════════════════════════════════════════════════════════════╗", Colors.BOLD))
        print(colored("║", Colors.BOLD) + colored("  SUMMARY", Colors.BOLD) + " " * 60 + colored("║", Colors.BOLD))
        print(colored("╠══════════════════════════════════════════════════════════════════════╣", Colors.BOLD))

        if groups:
            repos_str = f"  Repos to link:      {total_repos}"
            groups_str = f"  Duplicate groups:   {total_groups}"
            savings_str = f"  Estimated savings:  ~{format_size(total_savings)}"
            print(colored("║", Colors.BOLD) + colored(groups_str, Colors.CYAN) + " " * (69 - len(groups_str)) + colored("║", Colors.BOLD))
            print(colored("║", Colors.BOLD) + colored(repos_str, Colors.YELLOW) + " " * (69 - len(repos_str)) + colored("║", Colors.BOLD))
            print(colored("║", Colors.BOLD) + colored(savings_str, Colors.GREEN, Colors.BOLD) + " " * (69 - len(savings_str)) + colored("║", Colors.BOLD))
        else:
            done_str = f"  All {len(already_done)} duplicate groups already linked - nothing to do!"
            print(colored("║", Colors.BOLD) + colored(done_str, Colors.GREEN) + " " * (69 - len(done_str)) + colored("║", Colors.BOLD))

        print(colored("╚══════════════════════════════════════════════════════════════════════╝", Colors.BOLD))


def apply_changes(groups: list[RepoGroup], dry_run: bool) -> None:
    """Apply the disk savings by linking children to masters."""
    linked_repos = []

    for group in groups:
        master = group.master

        print()
        print(colored("━" * 60, Colors.CYAN))
        print(colored("  ◆ ", Colors.CYAN) + colored(group.remote_url, Colors.CYAN, Colors.BOLD))
        print(colored(f"    Master: ", Colors.DIM) + colored(str(master.path), Colors.GREEN))

        for child in group.children:
            print(colored(f"\n    → Linking: ", Colors.YELLOW) + str(child.path))
            link_repo(child.path, master.path, dry_run, run_gc_after=False)
            if not dry_run:
                linked_repos.append(child.path)

    if linked_repos and not dry_run:
        print()
        print(colored(f"⏳ Running garbage collection on {len(linked_repos)} repos...", Colors.DIM))
        for repo_path in linked_repos:
            subprocess.run(
                ["git", "-C", str(repo_path), "gc", "--prune=now"],
                capture_output=True, check=False
            )


def main():
    parser = argparse.ArgumentParser(
        description="Find duplicate git repositories and link them to save disk space."
    )
    parser.add_argument("folder", type=Path, help="Root folder to scan for git repositories")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview changes without applying them")
    parser.add_argument("--max-depth", type=int, default=10, help="Maximum directory depth to scan")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    args = parser.parse_args()

    if args.no_color or not sys.stdout.isatty():
        Colors.disable()

    if not args.folder.exists():
        print(f"Error: Folder does not exist: {args.folder}", file=sys.stderr)
        sys.exit(1)

    root = args.folder.resolve()

    repos = find_git_repos(root, args.max_depth)
    print(colored(f"✓ Found {len(repos)} git repositories", Colors.GREEN))

    groups, already_done = group_by_remote(repos)

    if not groups and not already_done:
        print(colored("\n✓ No duplicate repositories found.", Colors.GREEN))
        sys.exit(0)

    print_dependency_graph(groups, already_done, root)

    if not groups:
        sys.exit(0)

    if args.dry_run:
        print(colored("\n(DRY RUN - no changes will be made)", Colors.YELLOW))
        sys.exit(0)

    print()
    print(colored("╔══════════════════════════════════════════════════════════════════════╗", Colors.RED))
    print(colored("║", Colors.RED) + colored("  ⚠  BEFORE PROCEEDING", Colors.RED, Colors.BOLD) + " " * 47 + colored("║", Colors.RED))
    print(colored("╚══════════════════════════════════════════════════════════════════════╝", Colors.RED))
    print(f"""
{colored("Make sure all your local changes are pushed to the remote!", Colors.YELLOW, Colors.BOLD)}

This operation is non-destructive (source files, branches, uncommitted
changes are preserved), but child repos will depend on master repos.
{colored("If you delete a master repo, its children will break.", Colors.RED)}

Run {colored("git status", Colors.CYAN)} and {colored("git push", Colors.CYAN)} on any repos with unpushed work.
""")

    if not args.yes:
        response = input(colored("Proceed with linking repositories? [y/N] ", Colors.BOLD))
        if response.lower() != 'y':
            print(colored("Aborted.", Colors.YELLOW))
            sys.exit(0)

    apply_changes(groups, dry_run=False)

    print()
    print(colored("╔══════════════════════════════════════════════════════════════════════╗", Colors.GREEN))
    print(colored("║", Colors.GREEN) + colored("  ✓  DONE! Repositories have been linked.", Colors.GREEN, Colors.BOLD) + " " * 27 + colored("║", Colors.GREEN))
    print(colored("╚══════════════════════════════════════════════════════════════════════╝", Colors.GREEN))


if __name__ == "__main__":
    main()
