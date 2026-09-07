"""
Fortnite Locker Evaluator.

Analytical module for matching player locker items against the cosmetic catalog
database and calculating account value, category breakdowns, and generating
HTML reports for Telegram.
"""

from __future__ import annotations

import datetime
import html
import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from collection_valuation import ValuationFact, ValuationResult, evaluate_collection
from rarity_engine import RarityContext, calculate_rarity
from cosmetics_service import CosmeticDatabase, CosmeticItem
from price_repository import get_fx_fact, get_price_facts
from regional_pricing import RegionalPricingResult, compute_regional_price
from decimal import Decimal

logger = logging.getLogger(__name__)

DEFAULT_CATEGORY_KEYS = (
    "AthenaCharacter",
    "AthenaPickaxe",
    "AthenaGlider",
    "AthenaDance",
    "AthenaBackpack",
    "Other",
)

CATEGORY_DISPLAY_INFO: dict[str, tuple[str, str]] = {
    "AthenaCharacter": ("👕", "Скины"),
    "AthenaPickaxe": ("⛏️", "Кирки"),
    "AthenaGlider": ("🪂", "Дельтапланы"),
    "AthenaDance": ("💃", "Эмоции/Танцы"),
    "AthenaBackpack": ("🎒", "Рюкзаки"),
    "Other": ("📦", "Прочее"),
}


def _create_default_categories() -> dict[str, CategorySummary]:
    """Create default categories dictionary with zeroed summaries."""
    return {key: CategorySummary(count=0, total_vbucks=0) for key in DEFAULT_CATEGORY_KEYS}


def format_vbucks(amount: int) -> str:
    """Format integer V-Bucks amount with space thousand separators (e.g. 24 500 V-Bucks)."""
    formatted_num = f"{amount:,}".replace(",", " ")
    return f"{formatted_num} V-Bucks"


def format_number(amount: int) -> str:
    """Format integer with space thousand separators (e.g. 1 000)."""
    return f"{amount:,}".replace(",", " ")


class CategorySummary(BaseModel):
    """
    Summary metrics for a specific cosmetic category.

    Attributes:
        count: Number of unique items in this category.
        total_vbucks: Total value of items in this category in V-Bucks.
    """

    model_config = ConfigDict(extra="ignore")

    count: int = 0
    total_vbucks: int = 0


class LockerEvaluation(BaseModel):
    """
    Comprehensive evaluation of a player's Fortnite locker.
    """

    model_config = ConfigDict(extra="ignore")

    total_items: int = 0
    valuation_result: ValuationResult
    regional_pricing: Optional[RegionalPricingResult] = None
    categories: dict[str, CategorySummary] = Field(default_factory=_create_default_categories)
    top_valuable_items: list[tuple[CosmeticItem, Decimal]] = Field(default_factory=list)
    unindexed_items_count: int = 0


class LockerEvaluator:
    """Analytical engine for matching locker items against CosmeticDatabase."""

    # Map cosmetic types to standard Athena category keys
    TYPE_TO_CATEGORY: dict[str, str] = {
        "skin": "AthenaCharacter",
        "outfit": "AthenaCharacter",
        "character": "AthenaCharacter",
        "athenacharacter": "AthenaCharacter",
        "pickaxe": "AthenaPickaxe",
        "harvestingtool": "AthenaPickaxe",
        "athenapickaxe": "AthenaPickaxe",
        "glider": "AthenaGlider",
        "athenaglider": "AthenaGlider",
        "emote": "AthenaDance",
        "dance": "AthenaDance",
        "emoji": "AthenaDance",
        "spray": "AthenaDance",
        "toy": "AthenaDance",
        "athenadance": "AthenaDance",
        "backpack": "AthenaBackpack",
        "backbling": "AthenaBackpack",
        "athenabackpack": "AthenaBackpack",
    }

    # ID prefix heuristics for resolving category when item type is generic or missing
    ID_PREFIX_TO_CATEGORY: dict[str, str] = {
        "CID_": "AthenaCharacter",
        "CHARACTER_": "AthenaCharacter",
        "PICKAXE_ID_": "AthenaPickaxe",
        "SKID_": "AthenaPickaxe",
        "GLIDER_ID_": "AthenaGlider",
        "GLIDER_": "AthenaGlider",
        "EID_": "AthenaDance",
        "EMOTE_": "AthenaDance",
        "BID_": "AthenaBackpack",
        "BACKPACK_": "AthenaBackpack",
    }

    @classmethod
    def _resolve_category(cls, item: CosmeticItem, raw_id: str = "") -> str:
        """
        Determine canonical category key for a cosmetic item.
        """
        if item.type:
            clean_type = item.type.strip().lower()
            if clean_type in cls.TYPE_TO_CATEGORY:
                return cls.TYPE_TO_CATEGORY[clean_type]

        if ":" in raw_id:
            prefix, _, _ = raw_id.partition(":")
            clean_prefix = prefix.strip().lower()
            if clean_prefix in cls.TYPE_TO_CATEGORY:
                return cls.TYPE_TO_CATEGORY[clean_prefix]

        item_id_upper = item.id.upper()
        for id_prefix, cat in cls.ID_PREFIX_TO_CATEGORY.items():
            if item_id_upper.startswith(id_prefix):
                return cat

        return "Other"

    @classmethod
    def evaluate(
        cls,
        raw_item_ids: list[str],
        db: CosmeticDatabase,
        account_country: str = "US",
    ) -> LockerEvaluation:
        """
        Match player locker items with the cosmetic database and calculate valuation.
        """
        seen_clean_ids: set[str] = set()
        unique_entries: list[tuple[str, str]] = []

        for raw_id in raw_item_ids:
            if not isinstance(raw_id, str):
                continue
            stripped = raw_id.strip()
            if not stripped:
                continue

            clean_id = stripped.partition(":")[2] if ":" in stripped else stripped
            clean_id = clean_id.strip().upper()
            if not clean_id:
                clean_id = stripped.upper()

            if clean_id not in seen_clean_ids:
                seen_clean_ids.add(clean_id)
                unique_entries.append((stripped, clean_id))

        unindexed_items_count = 0
        categories = _create_default_categories()
        scored_items: list[tuple[CosmeticItem, Decimal]] = []
        valuation_facts: list[ValuationFact] = []
        
        now = datetime.datetime.now(datetime.timezone.utc)

        for raw_id, clean_id in unique_entries:
            item = db.get_item(clean_id)
            if item is None and ":" in raw_id:
                item = db.get_item(raw_id.strip().upper())

            if item is None:
                unindexed_items_count += 1
                continue

            category = cls._resolve_category(item, raw_id)

            cat_summary = categories.get(category)
            if cat_summary is None:
                cat_summary = CategorySummary(count=0, total_vbucks=0)
                categories[category] = cat_summary

            cat_summary.count += 1
            cat_summary.total_vbucks += item.price

            # Build ValuationFact for the new engine
            provenance = "unknown"
            if item.price > 0:
                provenance = "historical_individual_observation"

            fact = ValuationFact(
                cosmetic_id=clean_id,
                cosmetic_type=category,
                provenance=provenance,
                vbucks_price=item.price if item.price > 0 else None,
                verified_individual_price=(item.price > 0),
                is_battle_pass=item.is_exclusive_or_bp,
                is_exclusive=False,
            )
            valuation_facts.append(fact)
            
            # Compute Rarity
            days_since = None
            if item.last_seen:
                try:
                    # e.g., "2023-01-01T00:00:00Z"
                    dt = datetime.datetime.strptime(item.last_seen[:10], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
                    days_since = max(0, (now - dt).days)
                except ValueError:
                    pass

            rarity_ctx = RarityContext(
                cosmetic_id=clean_id,
                cosmetic_type=category,
                is_shop=True if item.price > 0 or item.last_seen else (False if item.is_exclusive_or_bp else None),
                days_since_last_seen=days_since,
                is_battle_pass=item.is_exclusive_or_bp,
                introduction_chapter=item.introduction_chapter
            )
            rarity_res = calculate_rarity(rarity_ctx)
            # Use 1.0 as base if final_multiplier is None
            mult = rarity_res.final_multiplier or Decimal("1.0")
            scored_items.append((item, mult))

        val_result = evaluate_collection(valuation_facts)

        regional = None
        if val_result.equivalent_value_vbucks is not None:
            now = datetime.datetime.now(datetime.timezone.utc)
            # Default to US/USD if country not explicitly supported by our mock DB
            # Our mock DB has TR and US.
            country = account_country if account_country in ("TR", "US") else "US"
            currency = "TRY" if country == "TR" else "USD"
            
            regional = compute_regional_price(
                requested_vbucks=val_result.equivalent_value_vbucks,
                country=country,
                selected_channel="standard_pack",
                selected_platform="epic_pc_web",
                price_facts=get_price_facts(),
                reference_time=now,
                value_basis="known_shop_value",
                fx_fact=get_fx_fact(currency)
            )

        top_valuable_items = sorted(
            scored_items,
            key=lambda x: (x[1], x[0].price),
            reverse=True,
        )[:5]

        total_items = len(unique_entries)

        return LockerEvaluation(
            total_items=total_items,
            valuation_result=val_result,
            regional_pricing=regional,
            categories=categories,
            top_valuable_items=top_valuable_items,
            unindexed_items_count=unindexed_items_count,
        )


def format_telegram_report(
    evaluation: LockerEvaluation,
    display_name: str | None = None,
) -> str:
    """
    Format locker evaluation results into a clean Telegram HTML report.
    """
    lines: list[str] = []
    val = evaluation.valuation_result
    reg = evaluation.regional_pricing

    # Header section
    if display_name and display_name.strip():
        safe_name = html.escape(display_name.strip())
        lines.append(f"🎮 <b>Шкафчик игрока:</b> <code>{safe_name}</code>")
    else:
        lines.append("🎮 <b>Оценка шкафчика Fortnite</b>")

    lines.append("")
    # Total valuation section
    vbucks_val = val.equivalent_value_vbucks or 0
    lines.append(f"💰 <b>Общая стоимость:</b> <code>{format_vbucks(vbucks_val)}</code>")
    
    # Regional Pricing Output
    if reg and reg.available:
        if reg.rub_value:
            lines.append(f"🌍 <b>Примерная цена (RUB):</b> ~<code>{reg.rub_value} ₽</code> <i>(через {reg.currency})</i>")
        else:
            lines.append(f"🌍 <b>Примерная цена:</b> <code>{reg.total_local_price} {reg.currency}</code>")
            
    lines.append(f"📦 <b>Всего предметов:</b> {format_number(evaluation.total_items)}")
    lines.append(f"💳 <b>Куплено за V-Bucks:</b> {format_number(val.priced_items_count)}")
    lines.append(f"🏆 <b>Эксклюзивы / БП:</b> {format_number(val.exclusive_items_count + val.battle_pass_items_count)}")

    if evaluation.unindexed_items_count > 0:
        lines.append(
            f"⚠️ <b>Не распознано в базе:</b> {format_number(evaluation.unindexed_items_count)}"
        )

    # Categories breakdown section
    lines.append("")
    lines.append("📊 <b>Сводка по категориям:</b>")

    for cat_key, (emoji, title) in CATEGORY_DISPLAY_INFO.items():
        summary = evaluation.categories.get(cat_key)
        count = summary.count if summary else 0
        vbucks = summary.total_vbucks if summary else 0

        if cat_key == "Other" and count == 0:
            continue

        lines.append(
            f" • {emoji} {title}: <b>{format_number(count)}</b> (<i>{format_vbucks(vbucks)}</i>)"
        )

    for cat_key, summary in evaluation.categories.items():
        if cat_key not in CATEGORY_DISPLAY_INFO and summary.count > 0:
            lines.append(
                f" • 📦 {html.escape(cat_key)}: <b>{format_number(summary.count)}</b> "
                f"(<i>{format_vbucks(summary.total_vbucks)}</i>)"
            )

    # Top-5 valuable items section
    lines.append("")
    lines.append("🔥 <b>Топ-5 самых ценных/редких предметов:</b>")
    if evaluation.top_valuable_items:
        for idx, (item, mult) in enumerate(evaluation.top_valuable_items, start=1):
            safe_item_name = html.escape(item.name or item.id)
            price_str = format_vbucks(item.price) if item.price > 0 else "N/A"
            rarity_str = f" [Редкость: x{mult:.1f}]" if mult > Decimal("1.0") else ""
            lines.append(
                f" {idx}. <b>{safe_item_name}</b> — <code>{price_str}</code>{rarity_str}"
            )
    else:
        lines.append(" <i>Предметы отсутствуют</i>")

    return "\n".join(lines)
