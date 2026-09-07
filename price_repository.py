"""
Price Repository.

Static repository providing V-Bucks pack prices and FX rates for regional pricing.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from regional_pricing import FxFact, PriceFact

# Use a reference time close to now for validity
_NOW = datetime.datetime.now(datetime.timezone.utc)

def get_price_facts() -> list[PriceFact]:
    """Return a mock set of price facts for TR and US regions."""
    return [
        # Turkey (TRY) Packs
        PriceFact(
            country="TR", currency="TRY", vbucks_amount=1000, local_price=Decimal("150.00"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="mock",
            observed_at=_NOW
        ),
        PriceFact(
            country="TR", currency="TRY", vbucks_amount=2800, local_price=Decimal("380.00"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="mock",
            observed_at=_NOW
        ),
        PriceFact(
            country="TR", currency="TRY", vbucks_amount=5000, local_price=Decimal("600.00"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="mock",
            observed_at=_NOW
        ),
        PriceFact(
            country="TR", currency="TRY", vbucks_amount=13500, local_price=Decimal("1500.00"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="mock",
            observed_at=_NOW
        ),
        
        # United States (USD) Packs
        PriceFact(
            country="US", currency="USD", vbucks_amount=1000, local_price=Decimal("8.99"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="mock",
            observed_at=_NOW
        ),
        PriceFact(
            country="US", currency="USD", vbucks_amount=2800, local_price=Decimal("22.99"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="mock",
            observed_at=_NOW
        ),
        PriceFact(
            country="US", currency="USD", vbucks_amount=5000, local_price=Decimal("36.99"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="mock",
            observed_at=_NOW
        ),
        PriceFact(
            country="US", currency="USD", vbucks_amount=13500, local_price=Decimal("89.99"),
            purchase_channel="standard_pack", platform="epic_pc_web", source="mock",
            observed_at=_NOW
        ),
    ]

def get_fx_fact(currency: str) -> FxFact | None:
    """Return mock FX rate to RUB for a given currency."""
    if currency == "TRY":
        return FxFact(
            from_currency="TRY", rate_to_rub=Decimal("2.65"), source="cbr_mock", observed_at=_NOW
        )
    if currency == "USD":
        return FxFact(
            from_currency="USD", rate_to_rub=Decimal("89.50"), source="cbr_mock", observed_at=_NOW
        )
    return None
