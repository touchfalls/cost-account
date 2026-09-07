"""
Collector Rarity Engine.

Calculates the collectability/exclusivity index (multiplier) for Fortnite cosmetics
based on verifiable historical facts. Does not represent monetary resale value.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RarityOverride(BaseModel):
    """Manual override for rarity multiplier."""
    model_config = ConfigDict(frozen=True)

    multiplier: Decimal
    reason: str
    active: bool
    target: Literal["cosmetic", "variant"]


class RarityContext(BaseModel):
    """Input facts required to calculate rarity of a cosmetic and/or variant."""
    model_config = ConfigDict(frozen=True)

    cosmetic_id: str
    cosmetic_type: str
    
    is_shop: Optional[bool] = None
    days_since_last_seen: Optional[int] = None
    known_appearance_count: Optional[int] = None
    
    is_battle_pass: Optional[bool] = None
    is_old_battle_pass: Optional[bool] = None
    verified_battle_pass_chapter: Optional[int] = None
    
    introduction_chapter: Optional[str] = None
    introduction_season: Optional[str] = None
    
    cosmetic_is_exclusive: Optional[bool] = None
    is_legacy_exclusive: Optional[bool] = None
    
    owned_variant_id: Optional[str] = None
    owned_variant_is_exclusive: Optional[bool] = None
    
    cosmetic_override: Optional[RarityOverride] = None
    owned_variant_override: Optional[RarityOverride] = None
    
    shop_history_provenance: Optional[str] = None


class RarityResult(BaseModel):
    """Output result of the rarity computation."""
    model_config = ConfigDict(frozen=True)

    final_multiplier: Optional[Decimal]
    component_multipliers: dict[str, Decimal] = Field(default_factory=dict)
    override_multiplier: Optional[Decimal] = None
    capped: bool = False
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def calculate_rarity(ctx: RarityContext, old_bp_cutoff_chapter: Optional[int] = None) -> RarityResult:
    """
    Evaluate facts and output a capped rarity multiplier.
    """
    warnings: list[str] = []
    reasons: list[str] = []
    components: dict[str, Decimal] = {}

    # 1. Overrides
    if ctx.owned_variant_override and ctx.owned_variant_override.active and ctx.owned_variant_id:
        return _apply_override(ctx.owned_variant_override, warnings)
        
    if ctx.cosmetic_override and ctx.cosmetic_override.active:
        return _apply_override(ctx.cosmetic_override, warnings)

    # 2. Availability Signal
    if ctx.is_shop is True:
        if ctx.days_since_last_seen is not None:
            days = ctx.days_since_last_seen
            if days >= 1500:
                mult = Decimal("2.0")
                reasons.append(f"shop_item_not_seen_for_{days}_days")
            elif days >= 1000:
                mult = Decimal("1.5")
                reasons.append(f"shop_item_not_seen_for_{days}_days")
            elif days >= 500:
                mult = Decimal("1.2")
                reasons.append(f"shop_item_not_seen_for_{days}_days")
            else:
                mult = Decimal("1.0")
                reasons.append(f"shop_item_not_seen_for_{days}_days")
                
            if mult > Decimal("1.0"):
                components["availability"] = mult
                
            if ctx.shop_history_provenance == "third_party":
                warnings.append("availability_based_on_third_party_data_only")
        else:
            warnings.append("shop_item_last_seen_date_unknown")
    elif ctx.is_shop is None:
        warnings.append("shop_status_unknown")
        
    # 3. Other Signals
    is_old_bp = ctx.is_old_battle_pass
    if is_old_bp is None and ctx.is_battle_pass is True and ctx.verified_battle_pass_chapter is not None and old_bp_cutoff_chapter is not None:
        if ctx.verified_battle_pass_chapter <= old_bp_cutoff_chapter:
            is_old_bp = True
            
    if is_old_bp is True:
        components["old_battle_pass"] = Decimal("2.0")
        reasons.append("confirmed_old_battle_pass")
        
    if ctx.is_legacy_exclusive is True:
        components["legacy_exclusive"] = Decimal("3.0")
        reasons.append("confirmed_legacy_exclusive")
        
    if ctx.cosmetic_is_exclusive is True:
        components["cosmetic_exclusive"] = Decimal("4.0")
        reasons.append("confirmed_exclusive_cosmetic")
        
    if ctx.owned_variant_id and ctx.owned_variant_is_exclusive is True:
        components["variant_exclusive"] = Decimal("4.0")
        reasons.append("owned_variant_confirmed_exclusive")

    # If any fact is explicitly marked unknown (and it could have increased value), we should warn
    if ctx.is_battle_pass is None and ctx.is_shop is not True and ctx.is_shop is not False:
         warnings.append("source_of_item_unknown")

    # 4. Math combination
    if not components:
        return RarityResult(
            final_multiplier=Decimal("1.0"),
            component_multipliers={},
            reasons=reasons,
            warnings=warnings
        )
        
    premiums = [v - Decimal("1.0") for v in components.values()]
    premiums.sort(reverse=True)
    
    largest_premium = premiums[0]
    other_premiums = sum(premiums[1:]) if len(premiums) > 1 else Decimal("0.0")
    
    computed = Decimal("1.0") + largest_premium + (Decimal("0.5") * other_premiums)
    
    capped = False
    if computed > Decimal("6.0"):
        computed = Decimal("6.0")
        capped = True
        reasons.append("maximum_multiplier_cap_applied")
        
    return RarityResult(
        final_multiplier=computed,
        component_multipliers=components,
        capped=capped,
        reasons=reasons,
        warnings=warnings
    )


def _apply_override(override: RarityOverride, warnings: list[str]) -> RarityResult:
    capped = False
    mult = override.multiplier
    if mult > Decimal("6.0"):
        mult = Decimal("6.0")
        capped = True
        
    reasons = [f"manual_override:{override.reason}"]
    if capped:
        reasons.append("maximum_multiplier_cap_applied")
        
    return RarityResult(
        final_multiplier=mult,
        override_multiplier=override.multiplier,
        capped=capped,
        reasons=reasons,
        warnings=warnings
    )
