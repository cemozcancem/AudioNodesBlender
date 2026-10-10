"""
Tiny test runner used inside Blender — pytest is not bundled with Blender's
Python and we don't want to install it just for this. Each test is a function
that either returns silently (pass), raises Skip (skip), or raises any other
exception (fail).
"""
import time
import traceback


class Skip(Exception):
    pass


# Populated by run_in_blender._enable_addon. Lives here so test cases see the
# same list (the runner script is loaded as __main__, not as a normal module).
REGISTER_FAILURES = []


class _Result:
    __slots__ = ("name", "status", "message", "elapsed")

    def __init__(self, name, status, message, elapsed):
        self.name = name
        self.status = status
        self.message = message
        self.elapsed = elapsed


def run_cases(cases, *, stop_on_fail=False, log=print):
    results = []
    for name, fn in cases:
        log(f"\n--- {name} ---")
        t0 = time.time()
        try:
            fn()
        except Skip as exc:
            elapsed = time.time() - t0
            results.append(_Result(name, "SKIP", str(exc), elapsed))
            log(f"SKIP {name}: {exc}")
        except Exception as exc:
            elapsed = time.time() - t0
            tb = traceback.format_exc()
            results.append(_Result(name, "FAIL", f"{exc}\n{tb}", elapsed))
            log(f"FAIL {name}: {exc}")
            log(tb)
            if stop_on_fail:
                break
        else:
            elapsed = time.time() - t0
            results.append(_Result(name, "PASS", "", elapsed))
            log(f"PASS {name} ({elapsed:.2f}s)")
    return results


def summarize(results, log=print):
    p = sum(1 for r in results if r.status == "PASS")
    s = sum(1 for r in results if r.status == "SKIP")
    f = sum(1 for r in results if r.status == "FAIL")
    log("\n" + "=" * 60)
    log(f"RESULTS: {p} passed, {f} failed, {s} skipped, {len(results)} total")
    if f:
        log("\nFailures:")
        for r in results:
            if r.status == "FAIL":
                log(f"  - {r.name}: {r.message.splitlines()[0]}")
    log("=" * 60)
    return f == 0


def assert_eq(actual, expected, msg=""):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")


def assert_true(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "expected truthy value")


def wait_until(predicate, timeout=3.0, interval=0.05, desc="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {desc} (>{timeout}s)")
