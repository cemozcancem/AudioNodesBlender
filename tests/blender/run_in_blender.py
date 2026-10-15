"""
Entry point invoked by `blender --background --python tests/blender/run_in_blender.py`.

Adds the repo root to sys.path so `blender_addon` is importable as a package,
enables it via Blender's preferences (which auto-installs pyzmq on first run),
runs the test cases, then cleans up. Exits the process with a non-zero code on
any failure.

Pass `--quick` after `--` to skip Phase-2 engine tests entirely.
"""
import os
import sys
import time
from pathlib import Path

# Make the repo root importable so `import blender_addon` works (and therefore
# also `import tests.blender.cases`).
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bpy  # available inside Blender

def _parse_args():
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1:]
    return []


def _enable_addon():
    """Enable blender_addon by module name. Lives in the repo, not the user
    addons dir — Blender's addon_enable resolves it via sys.path."""
    print(">>> enabling blender_addon")

    # Capture stdout/stderr so we can detect silent registration failures
    # (e.g. props that start with '_' fail to register but the addon's
    # try/except just prints a warning).
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        res = bpy.ops.preferences.addon_enable(module="blender_addon")
    captured = buf.getvalue()
    sys.stdout.write(captured)

    from tests.blender.utils import REGISTER_FAILURES
    for line in captured.splitlines():
        if "FAILED to register" in line:
            REGISTER_FAILURES.append(line.strip())

    if "FINISHED" not in res:
        print(f"!!! addon_enable returned {res}")
        sys.exit(2)

    # Use the test port (set by the runner script) instead of 5555, so the
    # engine never collides with one belonging to a Blender you have open.
    from tests.blender.utils import apply_test_port
    print(f">>> engine port: {apply_test_port()}")


def main():
    extra = _parse_args()
    quick = "--quick" in extra

    _enable_addon()

    from tests.blender import cases as case_module
    from tests.blender.utils import run_cases, summarize

    all_cases = case_module.discover()
    if quick:
        all_cases = [(n, f) for n, f in all_cases
                     if not n.startswith(("case_engine_",
                                          "case_node_creation_syncs_",
                                          "case_connection_syncs_",
                                          "case_parameter_set_propagates_",
                                          "case_sampler_load_",
                                          "case_sequencer_play_",
                                          "case_audio_callback_"))]

    print(f">>> running {len(all_cases)} cases")
    results = run_cases(all_cases)
    ok = summarize(results)

    # Make sure the addon's engine subprocess, if still up, is gone before we
    # exit so a follow-up run can rebind the test port.
    try:
        from blender_addon import engine_manager
        engine_manager.stop_engine()
    except Exception as exc:
        print(f">>> engine cleanup raised: {exc}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
