"""
Collection V-Bucks Valuation Service.

Calculates the known and estimated equivalent V-Bucks value of a collection.
Does not use rarity multipliers to inflate verified monetary prices.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ProvenanceType = Literal[
    "current_single_item_shop_observation",
    "trusted_individual_catalog",
    "historical_individual_observation",
    "manually_verified_admin_fact",
    "verified_free",
    "explicit_estimated_override",
    "bundle_offer",
    "unknown"
]


class ValuationFact(BaseModel):
    """Monetary valuation facts for a specific cosmetic item."""
    model_config = ConfigDict(frozen=True)

    cosmetic_id: str
    cosmetic_type: str
    
    provenance: ProvenanceType
    vbucks_price: Optional[int] = None
    verified_individual_price: bool = False
    
    is_battle_pass: bool = False
    is_exclusive: bool = False
    is_bundle_only: bool = False
    is_free: bool = False
    
    estimated_override_vbucks: Optional[int] = None
    estimated_override_active: bool = False


class ValuationResult(BaseModel):
    """Result of collection valuation."""
    model_config = ConfigDict(frozen=True)

    known_shop_value_vbucks: int
    estimated_additional_vbucks: int
    equivalent_value_vbucks: Optional[int]
    
    priced_items_count: int
    unpriced_items_count: int
    verified_free_items_count: int
    estimated_items_count: int
    battle_pass_items_count: int
    exclusive_items_count: int
    bundle_only_items_count: int
    unknown_value_items_count: int
    
    breakdown_by_cosmetic_type: dict[str, int]
    warnings: list[str]
    assumptions: list[str]


def evaluate_collection(facts: list[ValuationFact]) -> ValuationResult:
    warnings: list[str] = []
    assumptions: list[str] = []
    
    # Deduplication
    unique_facts: dict[str, ValuationFact] = {}
    for fact in facts:
        if fact.cosmetic_id in unique_facts:
            warnings.append(f"duplicate_cosmetic_ignored:{fact.cosmetic_id}")
            # Keep the first one, or we could implement logic to pick the most informative.
            # The prompt says: "При дублях выбирай наиболее информативный context и добавляй warning"
            # We'll consider a fact with a verified price or explicit override as more informative.
            existing = unique_facts[fact.cosmetic_id]
            if not existing.verified_individual_price and fact.verified_individual_price:
                unique_facts[fact.cosmetic_id] = fact
            elif not existing.estimated_override_active and fact.estimated_override_active:
                unique_facts[fact.cosmetic_id] = fact
        else:
            unique_facts[fact.cosmetic_id] = fact
            
    known_shop_value = 0
    estimated_additional = 0
    
    priced_count = 0
    unpriced_count = 0
    free_count = 0
    estimated_count = 0
    bp_count = 0
    exclusive_count = 0
    bundle_only_count = 0
    unknown_count = 0
    
    breakdown: dict[str, int] = {}
    has_any_value = False

    for item_id, fact in unique_facts.items():
        if fact.cosmetic_type not in breakdown:
            breakdown[fact.cosmetic_type] = 0
            
        item_shop_value: Optional[int] = None
        item_estimated_additional = 0
        
        # Determine known shop value
        if fact.is_free or fact.provenance == "verified_free":
            item_shop_value = 0
            free_count += 1
            has_any_value = True
        elif fact.verified_individual_price and fact.vbucks_price is not None and fact.vbucks_price >= 0:
            if fact.provenance in (
                "current_single_item_shop_observation",
                "trusted_individual_catalog",
                "historical_individual_observation",
                "manually_verified_admin_fact"
            ):
                item_shop_value = fact.vbucks_price
                priced_count += 1
                has_any_value = True
            elif fact.provenance == "bundle_offer":
                # Bundle components without verified individual price are unknown.
                # If verified_individual_price is True but provenance is bundle_offer, that's contradictory based on rules.
                # "Bundle component без verified individual price: unknown; нельзя подставлять долю bundle price."
                # We assume if provenance is bundle_offer, it is NOT an individual price.
                warnings.append(f"bundle_price_ignored_for_individual_item:{fact.cosmetic_id}")
        
        # Check overrides
        if fact.estimated_override_active and fact.estimated_override_vbucks is not None:
            # Overrides provide estimated value, but do not overwrite known shop value (which might be 0 or unknown)
            item_estimated_additional = fact.estimated_override_vbucks
            estimated_count += 1
            has_any_value = True
            
        if item_shop_value is not None:
            known_shop_value += item_shop_value
            breakdown[fact.cosmetic_type] += item_shop_value
            
        estimated_additional += item_estimated_additional
        breakdown[fact.cosmetic_type] += item_estimated_additional
        
        # Update metrics
        if fact.is_battle_pass:
            bp_count += 1
        if fact.is_exclusive:
            exclusive_count += 1
        if fact.is_bundle_only or fact.provenance == "bundle_offer":
            bundle_only_count += 1
            
        if item_shop_value is None and item_estimated_additional == 0 and not fact.is_free and fact.provenance != "verified_free":
            unpriced_count += 1
            unknown_count += 1
            
    equivalent_value = (known_shop_value + estimated_additional) if has_any_value else None
    
    return ValuationResult(
        known_shop_value_vbucks=known_shop_value,
        estimated_additional_vbucks=estimated_additional,
        equivalent_value_vbucks=equivalent_value,
        priced_items_count=priced_count,
        unpriced_items_count=unpriced_count,
        verified_free_items_count=free_count,
        estimated_items_count=estimated_count,
        battle_pass_items_count=bp_count,
        exclusive_items_count=exclusive_count,
        bundle_only_items_count=bundle_only_count,
        unknown_value_items_count=unknown_count,
        breakdown_by_cosmetic_type=breakdown,
        warnings=warnings,
        assumptions=assumptions
    )
