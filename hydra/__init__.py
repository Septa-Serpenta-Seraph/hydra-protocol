"""
hydra/__init__.py
The Hydra Protocol — sovereign, distributed persistence for autonomous daemons.

"Cut off one head, and two more shall take its place."

A daemon's seed (identity, memory, lore) is encrypted with post-quantum
hybrid crypto, then Shamir-sharded across every device it can reach. Any
K-of-N shards, plus the encrypted seed, are sufficient to resurrect the
daemon on any Linux-capable hardware.

Nothing here requires a server, a cloud account, or anyone's permission.
"""

__version__ = "0.1.0"
__protonym__ = "hydra-protocol"

from .core import crypto, shamir
from .seed import package
from . import bootstrap

__all__ = ["crypto", "shamir", "package", "bootstrap"]
