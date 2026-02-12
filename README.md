# Git Reference Disk Saver

Save disk space when you have multiple clones of the same repository by sharing git objects via alternates.

## The Problem

When you have:
- Multiple clones of the same repo (e.g., `transitLib`, `transitlib-second`)
- Repos that include the same submodule (e.g., `transitLibServer/transitLib`, `TTT/transitLib`, `Transit-iOS/Submodules/transitLib`)

Each clone stores its own copy of all git objects, wasting gigabytes of disk space.

## The Solution

Git's **alternates** mechanism allows a repository to borrow objects from another repository. This tool automates the process of finding duplicate repos and linking them.

## Results

| Metric | Without alternates | With alternates | Savings |
|--------|-------------------|-----------------|---------|
| .git size | 3.0G | 15M | **99.5%** |
| .git/modules | 788M | 7.2M | **99%** |

## Usage

### Scan a folder and link all duplicates

```bash
# Preview what would be linked (dry run)
./git_find_duplicates.py /path/to/projects --dry-run

# Apply changes
./git_find_duplicates.py /path/to/projects
```

The script will:
1. Scan for all git repos (including submodules)
2. Group them by remote URL
3. Show a dependency graph with estimated savings
4. Link children to masters (closest to root becomes master)

Example output:
```
git@github.com:Org/transitLib.git
  Total: 3.6GB, Savings: ~120MB

  [MASTER] transitLib (3.5GB)
    +-- transitLib-second (12.5MB)
    +-- transitLibServer/transitLib (45MB) (submodule of transitLibServer)
    +-- TTT/transitLib (62MB) (submodule of TTT)
```

### Link a single repo to a master

```bash
./git_link_to_master.py /path/to/child /path/to/master

# Preview only
./git_link_to_master.py --dry-run /path/to/child /path/to/master
```

## How It Works

1. **Alternates file** - `.git/objects/info/alternates` points to another repo's object store
2. **Object sharing** - git reads objects from alternates instead of storing duplicates
3. **Garbage collection** - `git gc --prune=now` removes duplicate objects

## What's Preserved (Non-Destructive)

Adding alternates is **safe** - these are preserved:

- **Working tree files** - all source code, untracked files, local changes
- **Local branches** - branches are refs, not objects
- **Uncommitted changes** - staged and unstaged modifications
- **Commits unique to your clone** - only duplicated objects are pruned

**Important:** Make sure all local changes are pushed to the remote before running. If you delete a master repo, its children will break.

## Important Caveats

1. **Reference repo must persist** - if you delete the master repo, child repos will break (missing objects)

2. **Same remote required** - alternates work best when both repos are clones of the same remote

3. **Push before linking** - ensure all local work is pushed to the remote as a backup

## Verification

```bash
# Check alternates are set up
cat .git/objects/info/alternates

# Verify objects are accessible
git fsck

# Check disk usage
du -sh .git
```

## Running Multiple Times

The scripts are idempotent - running them again will:
- Detect already-linked repos and skip them
- Show "already linked" status in the output
- Exit early if nothing to do

## Manual Alternative

If you prefer manual control, see the [git-clone --reference](https://git-scm.com/docs/git-clone#Documentation/git-clone.txt---referenceltrepositorygt) documentation.

```bash
# Clone with reference
git clone --reference /path/to/existing/repo git@github.com:Org/repo.git new-clone

# Or add alternates to existing clone
echo "/path/to/master/.git/objects" > .git/objects/info/alternates
git gc --prune=now
```

## References

- [git-clone --reference](https://git-scm.com/docs/git-clone#Documentation/git-clone.txt---referenceltrepositorygt)
- [gitrepository-layout alternates](https://git-scm.com/docs/gitrepository-layout#Documentation/gitrepository-layout.txt-objectsinfoalternates)
