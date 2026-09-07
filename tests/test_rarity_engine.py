from decimal import Decimal
from typing import Optional

from rarity_engine import (
    RarityContext,
    RarityOverride,
    RarityResult,
    calculate_rarity,
)


def _make_context(**kwargs) -> RarityContext:
    defaults = {
        "cosmetic_id": "TEST_ID",
        "cosmetic_type": "skin",
    }
    defaults.update(kwargs)
    return RarityContext(**defaults)


def test_unknown_facts_do_not_inflate_rarity():
    ctx = _make_context(
        is_shop=None,
        days_since_last_seen=None,
        is_battle_pass=None,
        is_old_battle_pass=None,
    )
    res = calculate_rarity(ctx)
    assert res.final_multiplier == Decimal("1.0")
    assert "shop_status_unknown" in res.warnings
    assert "source_of_item_unknown" in res.warnings
    assert not res.capped


def test_availability_thresholds():
    # < 500 days
    res1 = calculate_rarity(_make_context(is_shop=True, days_since_last_seen=400))
    assert res1.final_multiplier == Decimal("1.0")
    
    # 500-999 days
    res2 = calculate_rarity(_make_context(is_shop=True, days_since_last_seen=600))
    assert res2.final_multiplier == Decimal("1.2")
    assert res2.component_multipliers["availability"] == Decimal("1.2")
    
    # 1000-1499 days
    res3 = calculate_rarity(_make_context(is_shop=True, days_since_last_seen=1200))
    assert res3.final_multiplier == Decimal("1.5")
    assert res3.component_multipliers["availability"] == Decimal("1.5")
    
    # 1500+ days
    res4 = calculate_rarity(_make_context(is_shop=True, days_since_last_seen=1600))
    assert res4.final_multiplier == Decimal("2.0")
    assert res4.component_multipliers["availability"] == Decimal("2.0")


def test_old_bp_plus_legacy_plus_exclusive_gives_5_point_5():
    # old BP x2.0, legacy x3.0, exclusive x4.0
    # max = 4.0 -> premium = 3.0
    # rest premiums = 1.0 (from BP) + 2.0 (from legacy) = 3.0
    # formula: 1 + 3.0 + 0.5 * 3.0 = 5.5
    ctx = _make_context(
        is_old_battle_pass=True,
        is_legacy_exclusive=True,
        cosmetic_is_exclusive=True,
    )
    res = calculate_rarity(ctx)
    assert res.final_multiplier == Decimal("5.5")
    assert not res.capped


def test_full_combination_capped_at_6():
    # old BP x2.0, legacy x3.0, exclusive x4.0, variant exclusive x4.0
    # max = 4.0 -> premium 3.0
    # rest premiums = 1.0 + 2.0 + 3.0 = 6.0
    # computed = 1 + 3.0 + 0.5 * 6.0 = 7.0
    # should cap at 6.0
    ctx = _make_context(
        is_old_battle_pass=True,
        is_legacy_exclusive=True,
        cosmetic_is_exclusive=True,
        owned_variant_id="V1",
        owned_variant_is_exclusive=True
    )
    res = calculate_rarity(ctx)
    assert res.final_multiplier == Decimal("6.0")
    assert res.capped
    assert "maximum_multiplier_cap_applied" in res.reasons


def test_season_1_alone_is_not_legacy():
    # Just being Season 1 shouldn't trigger anything unless is_legacy_exclusive is True
    ctx = _make_context(
        introduction_chapter="1",
        introduction_season="1"
    )
    res = calculate_rarity(ctx)
    assert res.final_multiplier == Decimal("1.0")
    assert "legacy_exclusive" not in res.component_multipliers


def test_owned_exclusive_variant_affects_only_that_ownership():
    # Variant exclusive logic only triggers if owned_variant_id is provided
    ctx = _make_context(
        owned_variant_is_exclusive=True
    )
    # Without owned_variant_id, it should not apply
    res = calculate_rarity(ctx)
    assert res.final_multiplier == Decimal("1.0")

    ctx2 = _make_context(
        owned_variant_id="V_OG",
        owned_variant_is_exclusive=True
    )
    res2 = calculate_rarity(ctx2)
    assert res2.final_multiplier == Decimal("4.0")


def test_override_precedence_and_cap():
    cosmetic_override = RarityOverride(
        multiplier=Decimal("5.0"),
        reason="admin_choice",
        active=True,
        target="cosmetic"
    )
    variant_override = RarityOverride(
        multiplier=Decimal("10.0"), # Should be capped to 6.0
        reason="variant_super_rare",
        active=True,
        target="variant"
    )
    
    # Only cosmetic override
    ctx1 = _make_context(
        cosmetic_override=cosmetic_override,
        is_old_battle_pass=True # Should be ignored because override wins
    )
    res1 = calculate_rarity(ctx1)
    assert res1.final_multiplier == Decimal("5.0")
    assert "manual_override:admin_choice" in res1.reasons
    assert "old_battle_pass" not in res1.component_multipliers
    
    # Variant override wins over cosmetic override
    ctx2 = _make_context(
        cosmetic_override=cosmetic_override,
        owned_variant_override=variant_override,
        owned_variant_id="V1"
    )
    res2 = calculate_rarity(ctx2)
    assert res2.final_multiplier == Decimal("6.0")
    assert res2.capped
    assert "manual_override:variant_super_rare" in res2.reasons


def test_unknown_facts_with_third_party():
    ctx = _make_context(
        is_shop=True,
        days_since_last_seen=600,
        shop_history_provenance="third_party"
    )
    res = calculate_rarity(ctx)
    assert res.final_multiplier == Decimal("1.2")
    assert "availability_based_on_third_party_data_only" in res.warnings
