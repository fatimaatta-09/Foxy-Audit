"""The customer-owned salt sidecar — the only place a commitment salt ever exists.

A salt never reaches Foxy: not the wire, not the database, not a response, not a
log line. It is appended here, on the customer's own disk, beside the event id it
belongs to, and read back only by the verifier's *optional* known-content check
(``foxy_verify.py --commitment-key --events``). The hash chain never needs it, so
nothing server-side changes and nothing server-side can lose it.

The file is JSON Lines — one ``{"event_id": …, "salt": …}`` per event — because an
append survives a crash mid-run and costs the same on the thousandth event as on
the first, which rewriting a whole JSON object would not.

If the append fails the event is committed **unsalted** rather than
salted-and-unprovable: degrading to the guarantee that shipped yesterday beats
writing a commitment whose salt exists nowhere.
"""

from __future__ import annotations

import json
import logging
import os
import secrets

log = logging.getLogger("foxy_audit")


def new_salt() -> str:
    """A fresh 128-bit salt from the OS CSPRNG.

    ``secrets``, never ``random`` or ``uuid4``: this is a security primitive, and
    the Mersenne Twister behind ``random`` is reconstructible from its own output.
    """
    return secrets.token_hex(16)


def record_salt(path: str, event_id: str) -> str | None:
    """Append a fresh salt for ``event_id``; return it, or None if it wasn't stored.

    A None return is the caller's signal to commit the event unsalted — see the
    module docstring.
    """
    salt = new_salt()
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event_id": str(event_id), "salt": salt}) + "\n")
            # fsync to match the event spool's durability. The spool is SQLite/WAL
            # and survives a power cut; without this the salt would not, and the
            # event would arrive salted with its only salt gone.
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        # Exception TYPE only, and never the value — this module's entire job is a
        # secret, and str(exc) is the classic way one escapes into a log.
        log.warning("foxy-audit: could not write the commitment sidecar (%s); "
                    "committing this event unsalted", type(exc).__name__)
        return None
    return salt
