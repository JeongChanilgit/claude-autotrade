"""Kairos strategies package.

Importing this package eagerly imports every strategy module so that each
one's ``register(...)`` call fires and the registry is populated. The
ensemble module is imported last because its factory builds sub-strategies
via the registry, which must already contain them.
"""
from __future__ import annotations

# Base strategies first — their registrations must land before the ensemble
# tries to compose them.
from kairos.strategies import orb as _orb  # noqa: F401
from kairos.strategies import funding as _funding  # noqa: F401
from kairos.strategies import vol_compression as _vol_compression  # noqa: F401

# Phase-6 single-symbol 15m strategies (registered independently of the ensemble).
from kairos.strategies import vwap_reversion as _vwap_reversion  # noqa: F401
from kairos.strategies import ema_pullback as _ema_pullback  # noqa: F401
from kairos.strategies import donchian_breakout as _donchian_breakout  # noqa: F401
from kairos.strategies import rsi2_pullback as _rsi2_pullback  # noqa: F401

# Ensemble last — its factory resolves sub-strategies through the registry.
from kairos.strategies import ensemble as _ensemble  # noqa: F401
