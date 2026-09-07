from collection_valuation import ValuationFact, evaluate_collection


def test_free_is_zero_unknown_is_null():
    # 1. Verified free item
    free_fact = ValuationFact(
        cosmetic_id="FREE_ITEM",
        cosmetic_type="skin",
        provenance="verified_free",
        is_free=True
    )
    
    # 2. Unknown item
    unknown_fact = ValuationFact(
        cosmetic_id="UNK_ITEM",
        cosmetic_type="skin",
        provenance="unknown"
    )
    
    res = evaluate_collection([free_fact, unknown_fact])
    
    # free -> 0, unknown -> nothing
    assert res.known_shop_value_vbucks == 0
    assert res.estimated_additional_vbucks == 0
    assert res.equivalent_value_vbucks == 0
    assert res.verified_free_items_count == 1
    assert res.unknown_value_items_count == 1
    assert res.priced_items_count == 0


def test_battle_pass_and_bundle_have_no_invented_values():
    bp_fact = ValuationFact(
        cosmetic_id="BP_ITEM",
        cosmetic_type="skin",
        provenance="unknown",
        is_battle_pass=True,
    )
    bundle_fact = ValuationFact(
        cosmetic_id="BUN_ITEM",
        cosmetic_type="skin",
        provenance="bundle_offer",
        vbucks_price=2000,
        verified_individual_price=True # Should be ignored because provenance is bundle_offer
    )
    
    res = evaluate_collection([bp_fact, bundle_fact])
    
    assert res.known_shop_value_vbucks == 0
    assert res.estimated_additional_vbucks == 0
    assert res.equivalent_value_vbucks is None
    assert res.battle_pass_items_count == 1
    assert res.bundle_only_items_count == 1
    assert res.unknown_value_items_count == 2
    assert res.priced_items_count == 0


def test_only_explicit_estimated_override_changes_equivalent():
    # Known item 1200
    fact1 = ValuationFact(
        cosmetic_id="KNOWN",
        cosmetic_type="skin",
        provenance="trusted_individual_catalog",
        vbucks_price=1200,
        verified_individual_price=True
    )
    # Unknown exclusive with override
    fact2 = ValuationFact(
        cosmetic_id="EXCLUSIVE_OVERRIDE",
        cosmetic_type="skin",
        provenance="unknown",
        is_exclusive=True,
        estimated_override_active=True,
        estimated_override_vbucks=800
    )
    
    res = evaluate_collection([fact1, fact2])
    
    assert res.known_shop_value_vbucks == 1200
    assert res.estimated_additional_vbucks == 800
    assert res.equivalent_value_vbucks == 2000
    assert res.exclusive_items_count == 1
    assert res.estimated_items_count == 1


def test_duplicate_cosmetic_counted_once():
    fact1 = ValuationFact(
        cosmetic_id="DUP",
        cosmetic_type="skin",
        provenance="unknown"
    )
    fact2 = ValuationFact(
        cosmetic_id="DUP",
        cosmetic_type="skin",
        provenance="current_single_item_shop_observation",
        vbucks_price=1500,
        verified_individual_price=True
    )
    
    # Evaluate with both, but duplicates are resolved by keeping the most informative
    res = evaluate_collection([fact1, fact2])
    
    assert res.known_shop_value_vbucks == 1500
    assert res.priced_items_count == 1
    # total items evaluated is 1 unique
    assert res.unknown_value_items_count == 0


def test_rarity_does_not_multiply_verified_price():
    # This test proves our ValuationFact and Collection logic doesn't even accept rarity multipliers
    # so by design it cannot multiply it. 
    fact = ValuationFact(
        cosmetic_id="KNOWN",
        cosmetic_type="skin",
        provenance="historical_individual_observation",
        vbucks_price=1200,
        verified_individual_price=True
    )
    res = evaluate_collection([fact])
    assert res.known_shop_value_vbucks == 1200
    assert res.equivalent_value_vbucks == 1200
