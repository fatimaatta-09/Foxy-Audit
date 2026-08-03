"""The guard: a salt or a key must never reach a log, a print, or an exception.

H2's whole point is a secret the customer holds and we never see. A salt that
lands in a debug log has left the vault — the file it was written to is the only
copy that was supposed to exist, and a log line is exactly the copy that gets
shipped to a support ticket.

`gemini.py` already sets the discipline this enforces: it logs the exception
TYPE, never `str(exc)`, because a provider key can appear inside the message.
This test applies the same rule to the SDK by reading its AST — a grep over the
diff would miss a rename and pass on a file nobody touched this week.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SDK_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "foxy_audit"

# Identifiers that must never be an argument to a log/print/raise.
SECRETS = {"salt", "event_salt", "new_salt", "commitment_key", "api_key", "key_hash"}

# Sinks that put text somewhere a human or a file can read it.
_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _sink_name(node):
    """Return a printable sink name if this Call writes text out, else None."""
    fn = node.func
    if isinstance(fn, ast.Name) and fn.id == "print":
        return "print"
    if isinstance(fn, ast.Attribute) and fn.attr in _LOG_METHODS:
        base = fn.value
        base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
        if base_name in {"log", "logger", "logging", "_log"}:
            return f"{base_name}.{fn.attr}"
    return None


def _identifiers(node):
    """Every bare name and attribute tail inside a subtree."""
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
    return found


def _violations(source, filename):
    bad = []
    tree = ast.parse(source, filename)
    for node in ast.walk(tree):
        args = None
        where = None
        if isinstance(node, ast.Call) and _sink_name(node):
            args, where = list(node.args) + [kw.value for kw in node.keywords], _sink_name(node)
        elif isinstance(node, ast.Raise) and node.exc is not None:
            args, where = [node.exc], "raise"
        if args is None:
            continue
        leaked = set()
        for arg in args:
            leaked |= _identifiers(arg) & SECRETS
        if leaked:
            bad.append(f"{filename}:{node.lineno} {where}() references {sorted(leaked)}")
    return bad


SDK_FILES = sorted(SDK_SRC.glob("*.py"))


def test_the_guard_actually_finds_a_leak():
    """A guard nobody has watched fail is a guard nobody should trust."""
    assert _violations("log.debug('salt=%s', salt)", "fake.py")
    assert _violations("print(f'{event_salt}')", "fake.py")
    assert _violations("raise RuntimeError(cfg.commitment_key)", "fake.py")
    assert _violations("logger.warning('key %s', self.cfg.api_key)", "fake.py")
    # ...and does not fire on the discipline it is meant to allow.
    assert not _violations("log.warning('could not write the sidecar (%s)',"
                           " type(exc).__name__)", "fake.py")


@pytest.mark.parametrize("path", SDK_FILES, ids=lambda p: p.name)
def test_no_sdk_log_print_or_raise_touches_a_secret(path):
    found = _violations(path.read_text(encoding="utf-8"), path.name)
    assert not found, "a secret can reach a log or an error message:\n" + "\n".join(found)


def test_the_sdk_files_were_actually_scanned():
    """Guard the guard: a glob that silently matches nothing passes forever."""
    names = {p.name for p in SDK_FILES}
    assert {"client.py", "hashing.py", "sidecar.py", "config.py"} <= names
