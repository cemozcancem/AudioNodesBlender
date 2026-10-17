#!/usr/bin/env python3
"""PreToolUse hook: block shell commands the agent team must never run.

- pushing to main, force-pushing, pushing tags
- merging PRs, creating tags or GitHub releases (the maintainer does these)
- killing Audio Nodes engines without scoping to a port (the user's own
  Blender may be running one)
- `rm -r` on absolute or home paths outside the repo / temp directories

Exit code 2 blocks the command and shows stderr to the agent.
"""
import json
import os
import re
import shlex
import sys

from guard_paths import allowed_roots, inside

RULES = [
    (r"\bgit\s+push\b[^|;&]*(\s|:|\+)(refs/heads/)?main(\s|$|[;&|])",
     "Pushing to main is not allowed; push an agent/* branch and open a PR."),
    (r"\bgit\s+push\b[^|;&]*\s(--force(-with-lease)?|-f)(\s|$|=)",
     "Force-pushing is not allowed."),
    (r"\bgit\s+push\b[^|;&]*\s--tags\b", "Pushing tags is not allowed."),
    (r"\bgit\s+tag\b", "Creating tags is for the maintainer only."),
    (r"\bgh\s+pr\s+merge\b", "Merging PRs is for the maintainer only."),
    (r"\bgh\s+release\b", "Releases are for the maintainer only."),
]

ENGINE_KILL = re.compile(r"\b(pkill|killall)\b[^|;&]*(Audio Nodes Engine|AudioNodesEngine|Engine)")
PORT_SCOPED = re.compile(r"--zmq-port\s+(\d+|\$\{?\w+\}?)")


def risky_rm_targets(command, cwd):
    """Absolute/home paths given to `rm -r...` that fall outside allowed roots."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    bad = []
    roots = allowed_roots(cwd)
    for i, tok in enumerate(tokens):
        if os.path.basename(tok) != "rm":
            continue
        args = []
        for t in tokens[i + 1:]:
            if t in (";", "&&", "||", "|"):
                break
            args.append(t)
        if not any(a.startswith("-") and "r" in a.lower() for a in args):
            continue
        for a in args:
            if a.startswith("-"):
                continue
            if a.startswith(("/", "~")):
                p = os.path.realpath(os.path.expanduser(a))
                if not any(inside(p, r) for r in roots):
                    bad.append(a)
    return bad


def main():
    data = json.load(sys.stdin)
    command = (data.get("tool_input") or {}).get("command") or ""
    cwd = data.get("cwd") or os.getcwd()

    for pattern, message in RULES:
        if re.search(pattern, command):
            print(f"Blocked: {message}", file=sys.stderr)
            return 2

    if ENGINE_KILL.search(command) and not PORT_SCOPED.search(command):
        print("Blocked: killing Audio Nodes engines must be scoped to a port "
              "(e.g. pkill -f 'Engine.*--zmq-port 5599( |$)'); the user's own "
              "Blender may be running an engine.", file=sys.stderr)
        return 2

    bad = risky_rm_targets(command, cwd)
    if bad:
        print(f"Blocked: rm -r outside the repository: {' '.join(bad)}",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
