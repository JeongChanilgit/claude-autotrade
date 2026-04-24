"""Unit tests for the strategy registry."""
from __future__ import annotations

import pytest

from kairos.strategies import registry
from kairos.strategies.registry import ParamField, StrategyEntry


def _dummy_strategy_cls(name: str = "dummy"):
    """Build a minimal Strategy-protocol-conforming class on the fly."""

    class _Dummy:
        # Instance attribute so factory() produces objects whose `.name`
        # matches the registry key.
        def __init__(self) -> None:
            self.name = name

        default_params = {"k": 1}

        def generate_signals(self, data, params):  # pragma: no cover - not invoked
            raise NotImplementedError

    _Dummy.__name__ = f"_Dummy_{name}"
    return _Dummy


def _make_entry(name: str = "dummy") -> StrategyEntry:
    cls = _dummy_strategy_cls(name)
    return StrategyEntry(
        name=name,
        label=name.title(),
        factory=lambda: cls(),
        default_params=cls.default_params,
        param_schema={"k": ParamField(type="int", default=1, min=0, max=10)},
    )


@pytest.fixture(autouse=True)
def _snapshot_registry():
    """Freeze/restore the module-level registry around each test so test-only
    entries do not leak into other tests that depend on the production set.
    """
    # Ensure production strategies are registered.
    import kairos.strategies  # noqa: F401

    saved = dict(registry._REGISTRY)
    try:
        yield
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)


# --- Core registry behaviour ----------------------------------------------

def test_register_adds_new_entry():
    entry = _make_entry("test_new_entry")
    registry.register(entry)
    assert registry.get("test_new_entry") is entry


def test_register_duplicate_name_raises():
    first = _make_entry("test_dup")
    second = _make_entry("test_dup")
    registry.register(first)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(second)


def test_register_same_object_is_idempotent():
    entry = _make_entry("test_idem")
    registry.register(entry)
    # Re-registering the SAME object (simulating module re-import) must not raise.
    registry.register(entry)
    assert registry.get("test_idem") is entry


def test_get_missing_raises_keyerror():
    with pytest.raises(KeyError):
        registry.get("definitely_not_a_strategy_xyz")


def test_all_entries_sorted_by_name():
    # Add a couple of distinctly-named entries to exercise ordering.
    registry.register(_make_entry("zzz_test"))
    registry.register(_make_entry("aaa_test"))
    entries = registry.all_entries()
    names = [e.name for e in entries]
    assert names == sorted(names)


def test_build_returns_strategy_instance():
    entry = _make_entry("test_build")
    registry.register(entry)
    built = registry.build("test_build")
    # Must be the type the factory produces, not a cached object.
    assert built.name == "test_build"
    assert getattr(built, "default_params", None) == {"k": 1}


# --- Production strategy registrations ------------------------------------

EXPECTED_NAMES = {"orb", "funding_counter", "vol_compression", "ensemble"}


def test_registry_contains_exactly_four_production_strategies():
    import kairos.strategies  # noqa: F401 - triggers registration

    registered_names = {e.name for e in registry.all_entries()}
    # May contain test fixtures registered earlier in this run; filter to the
    # production set so the assertion stays precise.
    assert EXPECTED_NAMES.issubset(registered_names)
    # And none of the other production names snuck in.
    production_only = registered_names - {
        n for n in registered_names if n.startswith("test_") or n.startswith("aaa_") or n.startswith("zzz_")
    }
    assert production_only == EXPECTED_NAMES


@pytest.mark.parametrize("name", sorted(EXPECTED_NAMES))
def test_factory_produces_strategy_with_matching_name(name: str):
    entry = registry.get(name)
    built = entry.factory()
    assert built.name == name


@pytest.mark.parametrize("name", sorted(EXPECTED_NAMES))
def test_entry_default_params_matches_factory_default_params(name: str):
    entry = registry.get(name)
    built = entry.factory()
    assert dict(entry.default_params) == dict(built.default_params)
