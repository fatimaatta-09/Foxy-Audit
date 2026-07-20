"""Make ``cd sdk && python -m pytest`` exercise THIS checkout's source.

An editable install elsewhere on the machine may add a different
``.../sdk/src`` to ``sys.path`` via a ``.pth`` file. Prepending this
worktree's own ``src`` guarantees the tests import the code under test here,
not a stale copy from another checkout.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
