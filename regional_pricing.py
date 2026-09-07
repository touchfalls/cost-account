"""
Regional V-Bucks Pricing Service.

Computes the cheapest combination of real-world fiat packs to meet a target V-Bucks sum.
Uses bounded dynamic programming optimization.
"""

from __future__ import annotations

import datetime
import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


PurchaseChannel = Literal["standard_pack", "gift_card", "exact_amount", "unknown"]
ValueBasis = Literal["known_shop_value", "estimated_collection_equivalent"]


class PriceFact(BaseModel):
    """A versioned fact about a V-Bucks pack price in a specific region."""
    model_config = ConfigDict(frozen=True)

    country: str  # ISO alpha-2
    currency: str # ISO alpha-3
    vbucks_amount: int
    local_price: Decimal
    purchase_channel: PurchaseChannel
    platform: str
    source: str
    
    valid_from: Optional[datetime.datetime] = None
    valid_to: Optional[datetime.datetime] = None
    observed_at: datetime.datetime
    active: bool = True


class FxFact(BaseModel):
    """A versioned fact about an FX rate from a local currency to RUB."""
    model_config = ConfigDict(frozen=True)

    from_currency: str
    rate_to_rub: Decimal
    source: str
    observed_at: datetime.datetime


class PackSelection(BaseModel):
    pack: PriceFact
    count: int


class RegionalPricingResult(BaseModel):
    """Output result of the regional pricing DP optimization."""
    model_config = ConfigDict(frozen=True)

    available: bool
    unavailable_reason: Optional[str] = None
    
    value_basis: ValueBasis
    requested_vbucks: int
    purchased_vbucks: int
    extra_vbucks: int
    
    total_local_price: Decimal
    currency: str
    country: str
    purchase_channel: PurchaseChannel
    platform: str
    
    selected_packs: list[PackSelection]
    
    price_source: Optional[str] = None
    price_observed_at: Optional[datetime.datetime] = None
    stale: bool = False
    
    rub_value: Optional[Decimal] = None
    fx_source: Optional[str] = None
    fx_observed_at: Optional[datetime.datetime] = None
    
    warnings: list[str] = Field(default_factory=list)


def _filter_and_validate_facts(
    facts: list[PriceFact],
    reference_time: datetime.datetime,
    country: str,
    channel: PurchaseChannel,
    platform: str
) -> tuple[list[PriceFact], list[str], bool, Optional[str]]:
    warnings: list[str] = []
    stale = False
    unavailable_reason = None
    
    if not country:
        return [], warnings, stale, "account_country_unknown"
        
    filtered = []
    for fact in facts:
        if not fact.active:
            continue
        if fact.country != country or fact.purchase_channel != channel or fact.platform != platform:
            continue
            
        # Time checks
        if fact.valid_from and fact.valid_from > reference_time:
            continue
        if fact.valid_to and fact.valid_to < reference_time:
            continue
        if fact.observed_at > reference_time:
            continue
            
        # Freshness check
        age = reference_time - fact.observed_at
        if age.total_seconds() > 45 * 86400:
            stale = True
            
        filtered.append(fact)
        
    # Pick the most recent applicable version per pack size
    unique_packs: dict[int, PriceFact] = {}
    for fact in sorted(filtered, key=lambda f: f.observed_at):
        # By sorting ascending and overwriting, we keep the newest
        unique_packs[fact.vbucks_amount] = fact
        
    final_facts = list(unique_packs.values())
    if not final_facts:
        unavailable_reason = "regional_prices_unavailable"
    elif stale:
        # Default behavior: reject stale prices unless overridden.
        # But instructions say "stale_result обязан иметь stale=true и warning". 
        # So we don't hard reject here, we let the caller pass an allow_stale flag if needed, 
        # or we just return it with the stale flag. Wait, "по умолчанию расчёт unavailable".
        unavailable_reason = "regional_prices_stale"
        warnings.append("Stale pack prices rejected by default.")
        
    return final_facts, warnings, stale, unavailable_reason


def compute_regional_price(
    requested_vbucks: int,
    country: str,
    selected_channel: PurchaseChannel,
    selected_platform: str,
    price_facts: list[PriceFact],
    reference_time: datetime.datetime,
    value_basis: ValueBasis,
    fx_fact: Optional[FxFact] = None,
    allow_stale: bool = False
) -> RegionalPricingResult:

    if selected_channel == "exact_amount" and value_basis == "estimated_collection_equivalent":
        return RegionalPricingResult(
            available=False,
            unavailable_reason="exact_amount_not_valid_for_collection_valuation",
            value_basis=value_basis,
            requested_vbucks=requested_vbucks,
            purchased_vbucks=0,
            extra_vbucks=0,
            total_local_price=Decimal(0),
            currency="",
            country=country,
            purchase_channel=selected_channel,
            platform=selected_platform,
            selected_packs=[]
        )

    facts, warnings, stale, unavailable_reason = _filter_and_validate_facts(
        price_facts, reference_time, country, selected_channel, selected_platform
    )
    
    if unavailable_reason and not (stale and allow_stale):
        return RegionalPricingResult(
            available=False,
            unavailable_reason=unavailable_reason,
            value_basis=value_basis,
            requested_vbucks=requested_vbucks,
            purchased_vbucks=0,
            extra_vbucks=0,
            total_local_price=Decimal(0),
            currency="",
            country=country,
            purchase_channel=selected_channel,
            platform=selected_platform,
            selected_packs=[],
            warnings=warnings
        )
        
    if stale and allow_stale:
        unavailable_reason = None
        warnings.append("Using stale regional prices.")
        
    # Check currency ambiguity
    currencies = {f.currency for f in facts}
    if len(currencies) > 1:
        return RegionalPricingResult(
            available=False,
            unavailable_reason="regional_price_currency_ambiguous",
            value_basis=value_basis,
            requested_vbucks=requested_vbucks,
            purchased_vbucks=0,
            extra_vbucks=0,
            total_local_price=Decimal(0),
            currency="",
            country=country,
            purchase_channel=selected_channel,
            platform=selected_platform,
            selected_packs=[],
            warnings=warnings
        )
        
    currency = list(currencies)[0]
    
    # DP Optimization
    # We want to find a combination of packs that sum to >= requested_vbucks
    # minimizing: 1) local price, 2) overshoot, 3) number of packs
    
    # Extract pack denominations
    denominations = sorted([f.vbucks_amount for f in facts])
    pack_map = {f.vbucks_amount: f for f in facts}
    
    # Compute GCD for scaling
    gcd = denominations[0]
    for d in denominations[1:]:
        gcd = math.gcd(gcd, d)
        
    scaled_target = math.ceil(requested_vbucks / gcd)
    scaled_largest = max(denominations) // gcd
    max_states = scaled_target + scaled_largest
    
    if max_states > 50000:
        warnings.append("DP state space too large, aborted.")
        return RegionalPricingResult(
            available=False,
            unavailable_reason="dp_state_space_too_large",
            value_basis=value_basis,
            requested_vbucks=requested_vbucks,
            purchased_vbucks=0,
            extra_vbucks=0,
            total_local_price=Decimal(0),
            currency=currency,
            country=country,
            purchase_channel=selected_channel,
            platform=selected_platform,
            selected_packs=[],
            warnings=warnings
        )
        
    # DP State: tuple(min_cost, min_overshoot, min_packs, selected_pack_amounts)
    # Actually, we can just compute min cost to reach exactly `i`
    # Initialize DP array
    # We will track: cost, packs_count, last_coin
    INF = Decimal('infinity')
    dp_cost = [INF] * max_states
    dp_count = [math.inf] * max_states
    dp_choice = [0] * max_states
    
    dp_cost[0] = Decimal(0)
    dp_count[0] = 0
    
    for i in range(max_states):
        if dp_cost[i] == INF:
            continue
        for d in denominations:
            s = d // gcd
            nxt = i + s
            if nxt < max_states:
                cost = dp_cost[i] + pack_map[d].local_price
                count = dp_count[i] + 1
                
                # We want to minimize cost. 
                # If cost is same, minimize packs.
                # If cost and packs same, deterministic (choice of larger coin first)
                if cost < dp_cost[nxt]:
                    dp_cost[nxt] = cost
                    dp_count[nxt] = count
                    dp_choice[nxt] = d
                elif cost == dp_cost[nxt]:
                    if count < dp_count[nxt]:
                        dp_count[nxt] = count
                        dp_choice[nxt] = d
                    elif count == dp_count[nxt]:
                        if d > dp_choice[nxt]:
                            dp_choice[nxt] = d
                            
    # Find the best valid target >= scaled_target
    best_target = -1
    best_cost = INF
    best_overshoot = math.inf
    best_count = math.inf
    
    for i in range(scaled_target, max_states):
        if dp_cost[i] < INF:
            overshoot = i - scaled_target
            cost = dp_cost[i]
            count = dp_count[i]
            
            # criteria: 1) min cost, 2) min overshoot, 3) min packs
            if cost < best_cost:
                best_cost = cost
                best_overshoot = overshoot
                best_count = count
                best_target = i
            elif cost == best_cost:
                if overshoot < best_overshoot:
                    best_overshoot = overshoot
                    best_count = count
                    best_target = i
                elif overshoot == best_overshoot:
                    if count < best_count:
                        best_count = count
                        best_target = i
                        
    if best_target == -1:
        return RegionalPricingResult(
            available=False,
            unavailable_reason="no_valid_combination_found",
            value_basis=value_basis,
            requested_vbucks=requested_vbucks,
            purchased_vbucks=0,
            extra_vbucks=0,
            total_local_price=Decimal(0),
            currency=currency,
            country=country,
            purchase_channel=selected_channel,
            platform=selected_platform,
            selected_packs=[],
            warnings=warnings
        )
        
    # Reconstruct packs
    curr = best_target
    counts: dict[int, int] = {}
    while curr > 0:
        d = dp_choice[curr]
        counts[d] = counts.get(d, 0) + 1
        curr -= (d // gcd)
        
    selected_packs = []
    for d, c in sorted(counts.items(), reverse=True): # Deterministic order (largest first)
        selected_packs.append(PackSelection(pack=pack_map[d], count=c))
        
    purchased = best_target * gcd
    extra = purchased - requested_vbucks
    
    # Freshness of FX
    rub_value: Optional[Decimal] = None
    fx_source = None
    fx_observed_at = None
    
    if currency == "RUB":
        rub_value = best_cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        if fx_fact:
            age = reference_time - fx_fact.observed_at
            if age.total_seconds() > 3 * 86400:
                warnings.append("FX rate is stale (older than 3 days).")
            elif fx_fact.from_currency != currency:
                warnings.append("FX rate currency mismatch.")
            else:
                rub_value = (best_cost * fx_fact.rate_to_rub).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                fx_source = fx_fact.source
                fx_observed_at = fx_fact.observed_at
        else:
            warnings.append("No FX rate provided, RUB conversion unavailable.")
            
    # Price source info (most recent)
    price_source = facts[-1].source
    price_observed = max(f.observed_at for f in facts)
            
    return RegionalPricingResult(
        available=True,
        value_basis=value_basis,
        requested_vbucks=requested_vbucks,
        purchased_vbucks=purchased,
        extra_vbucks=extra,
        total_local_price=best_cost,
        currency=currency,
        country=country,
        purchase_channel=selected_channel,
        platform=selected_platform,
        selected_packs=selected_packs,
        price_source=price_source,
        price_observed_at=price_observed,
        stale=stale,
        rub_value=rub_value,
        fx_source=fx_source,
        fx_observed_at=fx_observed_at,
        warnings=warnings
    )
