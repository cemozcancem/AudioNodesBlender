#!/usr/bin/env python3
"""PreToolUse hook: only allow file edits inside this repository.

Blocks Edit/Write/NotebookEdit on any path outside the repo (or the git
worktree being worked in, or the main checkout it belongs to). Claude's own
data directory and temp directories stay writable so memory, plans and
scratch files keep working.

Exit code 2 blocks the tool call and shows stderr to the agent.
"""
import json
import os
import subprocess
import sys
import tempfile


def git_roots(cwd):
    roots = []
    for args in (["rev-parse", "--show-toplevel"],
                 ["rev-parse", "--path-format=absolute", "--git-common-dir"]):
        try:
            out = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                                 text=True, timeout=5).stdout.strip()
        except Exception:
            continue
        if out:
            # --git-common-dir points at <main checkout>/.git
            roots.append(os.path.dirname(out) if out.endswith(".git") else out)
    return roots


def allowed_roots(cwd):
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    roots = git_roots(cwd) + ([project] + git_roots(project) if project else [])
    roots += [
        os.path.expanduser("~/.claude"),
        tempfile.gettempdir(),
        "/tmp",
        "/private/tmp",
        "/private/var/folders",
    ]
    return [os.path.realpath(r) for r in roots if r]


def inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def main():
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path:
        return 0
    cwd = data.get("cwd") or os.getcwd()
    target = os.path.realpath(os.path.join(cwd, os.path.expanduser(path)))
    roots = allowed_roots(cwd)
    if any(inside(target, r) for r in roots):
        return 0
    print(f"Blocked: {target} is outside this repository. Agents may only "
          f"change files inside the repo they are working in.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
