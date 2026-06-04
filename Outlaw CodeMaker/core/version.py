"""Version + evolution marker.

This file doubles as the safe target for the *synthetic* dry-run evolution:
the dry-run bumps EVOLUTION_COUNT and BUILD, proving the shadow→validate→swap
pipeline end-to-end with zero risk of breaking real logic.
"""

VERSION = "3.0.0"
EVOLUTION_COUNT = 0
BUILD = "genesis"
