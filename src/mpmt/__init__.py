"""MPMT — Multiparty Private Membership Test.

Layered architecture:

    Application (Flask routes, port mgmt)
    └── ProtocolHandler  (single facade)
        ├── QueryServer   (query primitives)
        └── TreeCache     (aggregation tree)

@author  mincy
@ref     emp-toolkit (https://github.com/emp-toolkit/emp-tool)
"""

# ——— C++ bindings ———
from mpmt._mpmt import *                        # noqa: F401,F403

# ——— utility ———
from mpmt.util import bf_param                  # noqa: F401,E402

# ——— tree cache ———
from mpmt.tree_cache import (                   # noqa: F401,E402
    TreeCache, MergeStep, NOT_LOADED,
)

# ——— domain services ———
from mpmt.query import QueryServer, QueryClient   # noqa: F401,E402

# ——— protocol handler ———
from mpmt.protocol_handler import ProtocolHandler  # noqa: F401,E402

# ——— MPMT roles ———
from mpmt.server_leader import MpmtServerLeader   # noqa: F401,E402
from mpmt.server_helper import MpmtServerHelper   # noqa: F401,E402
from mpmt.set_holder import MpmtSetHolder          # noqa: F401,E402
