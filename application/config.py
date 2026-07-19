"""Server configuration dataclass.

@author  mincy
"""

from dataclasses import dataclass, field


@dataclass
class ServerConfig:
    """Per-server configuration.

    Example (Leader, P0)::

        ServerConfig(
            party_id=0,
            http_port=5000,
            set_size=2 ** 10,
            fpr_mantissa=1.0,
            fpr_exponent=-3,
            max_holders=32,
            prev_port=14000,
            next_host="127.0.0.1",
            next_port=14001,
        )
    """

    party_id: int          # 0 = Leader, 1 = Helper A, 2 = Helper B
    http_port: int         # Flask listen port
    set_size: int          # expected max set size
    fpr_mantissa: float    # BF false-positive rate mantissa (e.g. 1.0)
    fpr_exponent: int      # BF false-positive rate exponent (e.g. -3)
    max_holders: int       # max number of set holders

    # Rep3 ring — this party listens on *prev_port* and connects
    # to *next_host*:*next_port*.
    prev_port: int
    next_host: str
    next_port: int

    # Hash seeds — must be identical across servers (Leader generates,
    # passes to Helpers out of band, or pre-shared).
    hash_seeds: list[bytes] = field(default_factory=list)
