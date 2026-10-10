"""
Dependency management for Audio Nodes add-on.

Handles automatic installation of required Python packages.

Why not `pip install --user`?
    Blender runs `--background` and certain GUI configurations with
    `site.ENABLE_USER_SITE = False`. In that mode the user-site directory
    (`~/.local/...` / `~/Library/Python/...`) is **not** on `sys.path`, so a
    `--user` install lands in an unsearched location and `import` still fails.
    Symptom: command_client.HAS_ZMQ silently stays False and every engine call
    returns None — hard to spot, even harder to debug.

Strategy:
    Install with `pip install --target=<path>` into a Blender-managed location
    that's already on `sys.path` (or that we can prepend to it):
      1. `bpy.utils.user_resource("SCRIPTS") + "/modules"` — user-writable.
      2. `<bundle>/python/lib/python3.11/site-packages` — fallback when the
         user dir isn't writable but the bundle is.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_PACKAGES = [
    ("zmq", "pyzmq"),  # (import_name, pip_name)
]


def _candidate_targets():
    """Return install-target candidates in preference order."""
    targets = []
    try:
        import bpy
        user_scripts = bpy.utils.user_resource("SCRIPTS")
        if user_scripts:
            targets.append(Path(user_scripts) / "modules")
    except Exception:
        pass

    # Bundle site-packages (writable on dev installs, not always on shipped).
    try:
        py_exe = Path(sys.executable)
        # /.../python/bin/python3.x -> /.../python/lib/python3.x/site-packages
        site_pkgs = py_exe.parent.parent / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        targets.append(site_pkgs)
    except Exception:
        pass

    return targets


def _ensure_on_sys_path(target: Path):
    target_str = str(target)
    if target_str not in sys.path:
        sys.path.insert(0, target_str)


def is_package_installed(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def _install_to(pip_name: str, target: Path) -> bool:
    target.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [sys.executable, "-m", "ensurepip"],
            capture_output=True, timeout=60,
        )
    except Exception:
        pass

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install",
             f"--target={target}", "--upgrade", pip_name],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            print(f"Audio Nodes: pip install --target={target} {pip_name} failed:")
            print(result.stderr.strip() or result.stdout.strip())
            return False
        return True
    except Exception as e:
        print(f"Audio Nodes: install_to({target}, {pip_name}) raised: {e}")
        return False


def install_package(pip_name: str) -> bool:
    """Install `pip_name` into the first writable Blender-managed location and
    make sure that location is on sys.path before we attempt to import."""
    for target in _candidate_targets():
        try:
            target.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            continue
        # Probe write access without leaving junk.
        probe = target / ".audio_nodes_write_probe"
        try:
            probe.write_text("")
            probe.unlink()
        except Exception:
            continue

        if _install_to(pip_name, target):
            _ensure_on_sys_path(target)
            return True

    print(f"Audio Nodes: no writable target for installing {pip_name}")
    return False


def ensure_dependencies() -> tuple[bool, list[str]]:
    """Install any missing required packages.

    Returns (all_ok, missing_pip_names_after_install_attempts).
    """
    # Make sure prior installs are visible before we conclude something is
    # missing — Blender's --background disables user-site by default, so
    # first add any of our managed targets that already exist.
    for target in _candidate_targets():
        if target.exists():
            _ensure_on_sys_path(target)

    missing: list[str] = []
    for import_name, pip_name in REQUIRED_PACKAGES:
        if is_package_installed(import_name):
            continue
        print(f"Audio Nodes: installing {pip_name}...")
        if not install_package(pip_name):
            missing.append(pip_name)
            continue
        try:
            importlib.import_module(import_name)
            print(f"Audio Nodes: successfully installed {pip_name}")
        except ImportError as e:
            print(f"Audio Nodes: installed {pip_name} but import still fails: {e}")
            missing.append(pip_name)

    return not missing, missing


def check_dependencies() -> tuple[bool, list[str]]:
    """Read-only: list what's missing without trying to install."""
    missing = [pip for imp, pip in REQUIRED_PACKAGES if not is_package_installed(imp)]
    return not missing, missing
