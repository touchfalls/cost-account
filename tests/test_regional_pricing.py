import datetime
from decimal import Decimal

from regional_pricing import (
    FxFact,
    PriceFact,
    compute_regional_price,
)


def _dt(days_ago: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 9, 7, 12, 0, 0, tzinfo=datetime.timezone.utc) - datetime.timedelta(days=days_ago)


def test_optimizer_chooses_cheapest_valid_combination():
    facts = [
        PriceFact(
            country="US", currency="USD", vbucks_amount=1000, local_price=Decimal("8.99"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="epic",
            observed_at=_dt(1)
        ),
        PriceFact(
            country="US", currency="USD", vbucks_amount=2800, local_price=Decimal("22.99"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="epic",
            observed_at=_dt(1)
        ),
        PriceFact(
            country="US", currency="USD", vbucks_amount=5000, local_price=Decimal("36.99"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="epic",
            observed_at=_dt(1)
        ),
        PriceFact(
            country="US", currency="USD", vbucks_amount=13500, local_price=Decimal("89.99"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="epic",
            observed_at=_dt(1)
        ),
    ]
    
    # We want 3000 vbucks.
    # 3x 1000 = 3000 for 26.97
    # 1x 2800 + 1x 1000 = 3800 for 31.98
    # 1x 5000 = 5000 for 36.99
    # The cheapest is 3x 1000 for 26.97.
    res = compute_regional_price(
        requested_vbucks=3000,
        country="US",
        selected_channel="standard_pack",
        selected_platform="epic_pc_web",
        price_facts=facts,
        reference_time=_dt(0),
        value_basis="known_shop_value"
    )
    
    assert res.available
    assert res.purchased_vbucks == 3000
    assert res.total_local_price == Decimal("26.97")
    assert len(res.selected_packs) == 1
    assert res.selected_packs[0].pack.vbucks_amount == 1000
    assert res.selected_packs[0].count == 3


def test_channels_and_platforms_never_mixed():
    facts = [
        PriceFact(
            country="US", currency="USD", vbucks_amount=1000, local_price=Decimal("8.99"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="epic", observed_at=_dt()
        ),
        PriceFact(
            country="US", currency="USD", vbucks_amount=2800, local_price=Decimal("22.99"),
            purchase_channel="gift_card", platform="retail", source="retail", observed_at=_dt()
        ),
    ]
    
    res = compute_regional_price(
        requested_vbucks=2000,
        country="US",
        selected_channel="standard_pack",
        selected_platform="epic_pc_web",
        price_facts=facts,
        reference_time=_dt(0),
        value_basis="known_shop_value"
    )
    
    # Should only use standard_pack
    assert res.available
    assert res.purchased_vbucks == 2000
    assert res.total_local_price == Decimal("17.98")
    assert res.selected_packs[0].pack.vbucks_amount == 1000


def test_exact_amount_rejected_for_whole_collection_valuation():
    res = compute_regional_price(
        requested_vbucks=1200,
        country="US",
        selected_channel="exact_amount",
        selected_platform="epic_pc_web",
        price_facts=[],
        reference_time=_dt(0),
        value_basis="estimated_collection_equivalent"
    )
    assert not res.available
    assert res.unavailable_reason == "exact_amount_not_valid_for_collection_valuation"


def test_stale_pack_behavior():
    facts = [
        PriceFact(
            country="US", currency="USD", vbucks_amount=1000, local_price=Decimal("8.99"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="epic",
            observed_at=_dt(50) # 50 days ago is > 45 max age
        )
    ]
    
    res = compute_regional_price(
        requested_vbucks=1000,
        country="US",
        selected_channel="standard_pack",
        selected_platform="epic_pc_web",
        price_facts=facts,
        reference_time=_dt(0),
        value_basis="known_shop_value",
        allow_stale=False
    )
    assert not res.available
    assert res.unavailable_reason == "regional_prices_stale"
    
    res2 = compute_regional_price(
        requested_vbucks=1000,
        country="US",
        selected_channel="standard_pack",
        selected_platform="epic_pc_web",
        price_facts=facts,
        reference_time=_dt(0),
        value_basis="known_shop_value",
        allow_stale=True
    )
    assert res2.available
    assert res2.stale
    assert "Using stale regional prices." in res2.warnings


def test_direct_fx_conversion_and_rounding():
    facts = [
        PriceFact(
            country="TR", currency="TRY", vbucks_amount=1000, local_price=Decimal("150.00"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="epic", observed_at=_dt(1)
        )
    ]
    fx_fact = FxFact(
        from_currency="TRY", rate_to_rub=Decimal("3.1234"), source="cbr", observed_at=_dt(1)
    )
    
    res = compute_regional_price(
        requested_vbucks=1000,
        country="TR",
        selected_channel="standard_pack",
        selected_platform="epic_pc_web",
        price_facts=facts,
        reference_time=_dt(0),
        value_basis="known_shop_value",
        fx_fact=fx_fact
    )
    
    assert res.available
    assert res.total_local_price == Decimal("150.00")
    # 150 * 3.1234 = 468.51
    assert res.rub_value == Decimal("468.51")


def test_no_fx_leaves_local_result_valid():
    facts = [
        PriceFact(
            country="TR", currency="TRY", vbucks_amount=1000, local_price=Decimal("150.00"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="epic", observed_at=_dt(1)
        )
    ]
    
    res = compute_regional_price(
        requested_vbucks=1000,
        country="TR",
        selected_channel="standard_pack",
        selected_platform="epic_pc_web",
        price_facts=facts,
        reference_time=_dt(0),
        value_basis="known_shop_value",
        fx_fact=None
    )
    
    assert res.available
    assert res.total_local_price == Decimal("150.00")
    assert res.rub_value is None
    assert "No FX rate provided, RUB conversion unavailable." in res.warnings
