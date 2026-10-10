"""
Fresh-install validation. Run via `scripts/run_fresh_install_test.sh`.

Simulates the path an end user takes:
  1. Download the shipped zip (we build one here).
  2. `bpy.ops.preferences.addon_install(filepath=...)` — Blender extracts
     the zip into the user addons directory.
  3. `bpy.ops.preferences.addon_enable(module="audio_nodes")` — fires the
     dependency installer, registers all classes, etc.
  4. Sanity-check: pyzmq imports, engine starts, a node can be created and
     synced to the engine.

The driver script wipes the user addons dir + the user modules dir for
audio_nodes/zmq before running this so we genuinely start cold.
"""
import sys
import time
from pathlib import Path

import bpy

REPO_ROOT = Path(__file__).resolve().parents[2]
ADDON_MODULE = "audio_nodes"  # module name as packaged by package_addon.sh


def fail(msg):
    print(f"FAIL fresh-install: {msg}")
    sys.exit(1)


def _parse_zip_path():
    if "--" not in sys.argv:
        fail("missing zip path argument (pass after `--`)")
    extra = sys.argv[sys.argv.index("--") + 1:]
    if not extra:
        fail("missing zip path argument")
    p = Path(extra[0])
    if not p.exists():
        fail(f"zip not found at {p}")
    return p


def main():
    zip_path = _parse_zip_path()
    print(f">>> installing {zip_path}")

    res = bpy.ops.preferences.addon_install(
        filepath=str(zip_path),
        overwrite=True,
        target="DEFAULT",
    )
    if "FINISHED" not in res:
        fail(f"addon_install returned {res}")

    print(">>> enabling addon by module name")
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        res = bpy.ops.preferences.addon_enable(module=ADDON_MODULE)
    captured = buf.getvalue()
    sys.stdout.write(captured)

    if "FINISHED" not in res:
        fail(f"addon_enable returned {res}")
    if bpy.context.preferences.addons.get(ADDON_MODULE) is None:
        fail("addon not in context.preferences.addons after enable")

    # The addon swallows register-time exceptions with a try/except + print,
    # so a class can fail registration silently. Catch any such warning.
    register_failures = [
        ln.strip() for ln in captured.splitlines()
        if "FAILED to register" in ln
    ]
    if register_failures:
        fail("register-time failures:\n  " + "\n  ".join(register_failures))

    # The dependency installer should have made pyzmq importable in this
    # Python interpreter. If not, the rest of the addon would silently run
    # in HAS_ZMQ=False mode.
    try:
        import zmq  # noqa: F401
        print(f">>> pyzmq importable: {zmq.zmq_version()}")
    except ImportError as e:
        fail(f"pyzmq not importable after addon enable: {e}")

    import importlib
    addon = importlib.import_module(ADDON_MODULE)
    print(f">>> all {len(addon.classes)} classes registered (no FAILED lines)")

    # Engine roundtrip via the addon's own operator (the same path users hit).
    print(">>> starting engine via operator")
    try:
        bpy.ops.audionodes.start_engine()
    except RuntimeError as e:
        fail(f"start_engine operator raised: {e}")
    if not addon.engine_manager.is_engine_running():
        fail("engine process not running after start_engine")
    if not addon.command_client.ping():
        fail("engine running but ZMQ ping failed")
    print(">>> engine up and ping OK")

    # Create a tree + a couple of nodes; verify create_node went through.
    tree = bpy.data.node_groups.new(name="FreshTree", type="AudioNodeTree")
    osc = tree.nodes.new("AudioOscillatorNode")
    gain = tree.nodes.new("AudioGainNode")
    out = tree.nodes.new("AudioOutputNode")

    # set_parameter_immediate returns True only if the engine knows the node.
    if not addon.command_client.set_parameter_immediate(
        gain.engine_node_id, "gain_db", -3.0
    ):
        fail("engine did not accept set_parameter on a freshly-created node")
    print(">>> node creation synced to engine")

    # Cleanup
    print(">>> stopping engine")
    bpy.ops.audionodes.stop_engine()
    bpy.data.node_groups.remove(tree)

    # Disable the addon so the user's environment is left clean. The driver
    # script will also nuke ~/Library/.../scripts/addons/audio_nodes after.
    bpy.ops.preferences.addon_disable(module=ADDON_MODULE)

    print("PASS fresh-install validation")
    sys.exit(0)


if __name__ == "__main__":
    main()
