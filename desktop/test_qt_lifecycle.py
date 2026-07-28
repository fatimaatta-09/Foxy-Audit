"""The rule that ends the process, not a test.

This build has hit **exit 127, no Python traceback, after every test has
already passed** several times. It cannot be caught, asserted on, or reported
by pytest — the process is gone before the summary line is written — so the
guard has to be structural: look at the source for the pattern that causes it.

**The cause: a QApplication that nothing holds.**

    def test_bad():
        QApplication.instance() or QApplication([])   # value DROPPED
        QWidget()                                     # any widget at all
        # ...the test passes, then exit 127 at interpreter shutdown

    @pytest.fixture(scope="module")
    def app():
        return QApplication.instance() or QApplication([])   # HELD -> exit 0

PyQt owns the C++ QApplication through its Python wrapper. Drop the only
reference and it is garbage-collected while widgets are still alive; the
teardown order at shutdown is what kills the process. Every test file in this
tree survives because its `app` fixture RETURNS the application and pytest
caches that value for the session — luck that became a convention, which is
why this file pins it.

**Deterministic alone, intermittent in a suite — and that is the whole
mystery.** Measured on this branch:

    the bad file, run alone, 5 runs   -> 127 127 127 127 127
    the good file, run alone, 5 runs  ->   0   0   0   0   0
    the FULL suite with the bad line  -> sometimes 127, sometimes 0

The suite is a coin flip because `QApplication.instance() or QApplication([])`
only CONSTRUCTS anything when no application exists yet. If some other
module's `app` fixture ran first and is holding one, the bare expression
short-circuits and nothing is dropped. Whether a holding fixture ran first
depends on collection order.

That settles a long-running disagreement: an executor reported an intermittent
exit-127 around `test_home_page.py` -> `test_p3_pages.py`, MAIN could not
reproduce it in nine runs and called it unconfirmed. Both observations were
correct. It is also why the guard below is structural rather than a test that
runs the suite and checks the exit code — such a test would pass most of the
time on broken code.

**What it is NOT: the worker-thread signal payload.**

The scheduled-debt item behind this file said "emitting an exception or any
custom object across the worker-thread signal boundary kills the interpreter
(bisected to the payload, not the signal type)". That does not reproduce. A
`QThread` emitting `pyqtSignal(object)` to a main-thread receiver, 200 times
each, was measured with three payloads:

    exception instance -> exit 0, 200/200 delivered
    a live QWidget     -> exit 0, 200/200 delivered
    a plain dict       -> exit 0, 200/200 delivered

So `FoxyClient.step_up_required.emit(e)` (foxy_client.py) is not the hazard it
was suspected of being, and nobody should "fix" it on the strength of the old
report. The original bisect was almost certainly confounded by a harness that
also dropped its QApplication — the same root cause wearing a different hat.
"""

from __future__ import annotations

import ast
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TESTS = sorted(_HERE.glob("test_*.py"))


def _discarded_qapplication(path: Path) -> list[str]:
    """Statements that CREATE a QApplication and throw the value away.

    An `ast.Expr` is a bare expression statement — its value goes nowhere.
    Assignments, returns and call arguments all keep a reference and are fine.

    It matches the CONSTRUCTOR CALL, not the text: two earlier versions of
    this function matched the word "QApplication" in a dumped tree and so
    flagged their own docstring, and then their own `write_text("…")` fixture
    strings. `QApplication.instance()` on its own is also not a construction
    and is left alone.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        for inner in ast.walk(node.value):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "QApplication"):
                bad.append(f"{path.name}:{node.lineno}: "
                           f"{ast.unparse(node)[:64]}")
                break
    return bad


def test_no_test_file_drops_its_qapplication_on_the_floor():
    """The assertion this whole file exists for.

    Checked in both directions: with `test_d12_companion.py`'s bare line
    restored this fails, and the minimal case exits 127 five times out of
    five. The full suite with that same line is a coin flip — see the module
    docstring — which is precisely why the guard reads the source instead of
    trusting a green run.
    """
    offenders = [line for path in _TESTS for line in _discarded_qapplication(path)]
    assert offenders == [], (
        "a discarded QApplication kills the interpreter at shutdown "
        "(exit 127, no traceback, AFTER the tests pass):\n  "
        + "\n  ".join(offenders))


def test_the_guard_recognises_the_shape_it_is_looking_for(tmp_path):
    """A structural check that never fires is indistinguishable from one that
    cannot fire. This proves it catches the bad shape and clears the good one.
    """
    bad = tmp_path / "test_bad.py"
    bad.write_text("from PyQt6.QtWidgets import QApplication\n"
                   "def test_x():\n"
                   "    QApplication.instance() or QApplication([])\n",
                   encoding="utf-8")
    assert _discarded_qapplication(bad)

    good = tmp_path / "test_good.py"
    good.write_text('"""A docstring mentioning QApplication."""\n'
                    "from PyQt6.QtWidgets import QApplication\n"
                    "def app():\n"
                    "    return QApplication.instance() or QApplication([])\n"
                    "def other(a=QApplication.instance()):\n"
                    "    return a\n",
                    encoding="utf-8")
    assert _discarded_qapplication(good) == []


def test_the_worker_error_signal_still_carries_a_plain_string():
    """Not a crash rule — a CONTRACT rule, and worth keeping for its own sake.

    `status_of()` and `detail_of()` parse `ApiError.__str__`'s "HTTP <code>:
    <detail>" out of that string, and every handler in the app is written
    against it. Widening `failed` to carry the exception would silently break
    the two helpers rather than the process.
    """
    source = (_HERE / "foxy_client.py").read_text(encoding="utf-8")
    worker = source.split("class ApiWorker")[1].split("\nclass ")[0]
    assert "failed = pyqtSignal(str)" in worker

def _app_built_inside_a_test(path):
    """QApplication constructed in a test FUNCTION body rather than a
    module-scoped fixture.

    Binding it to a name is not enough — a local dies when the function
    returns, so the C++ application is collected while widgets are still
    alive. That is exactly how exit 127 survived the b939f3f fix: the bare
    expression became `app = ...` inside a test body, which only looked safe
    because another module's cached fixture usually still held one. Whether it
    did was decided by collection order, which is why it read as flaky.
    """
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=str(path))
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == "QApplication"):
                bad.append(f"{os.path.basename(str(path))}:{sub.lineno} "
                           f"in {node.name}() — build it in a module-scoped "
                           f"`app` fixture instead")
    return bad


def test_no_test_builds_its_qapplication_inside_the_test_body():
    """The second half of the same bug, and the half that outlived the first fix.

    `test_d12_companion.py` had a bound-but-local application inside a test
    body; the suite still exited 127 on roughly one random order in four.
    Reverting this file's fixture back to that shape reproduces it.
    """
    offenders = [line for path in _TESTS for line in _app_built_inside_a_test(path)]
    assert offenders == [], (
        "a QApplication built inside a test body dies when that function "
        "returns:\n  " + "\n  ".join(offenders))

