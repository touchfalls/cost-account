"""
Fortnite Cosmetics Service.

Asynchronous service for caching and querying the Fortnite cosmetics catalog
via Fortnite-API.com using aiohttp and Pydantic v2.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

FORTNITE_API_URL: str = "https://fortnite-api.com/v2/cosmetics/br"
DEFAULT_TIMEOUT_SECONDS: float = 20.0


def calculate_item_price_and_exclusivity(raw_item: dict[str, Any]) -> tuple[int, bool]:
    """
    Compute the item price and Battle Pass / exclusive flag based on business rules:

    1. If base `price > 0`, use it.
    2. If base `price == 0` or missing, inspect `shopHistory` array and take the price
       from the most recent appearance (`shopHistory[-1]["vbucks"]`).
    3. If no price is found anywhere, set `price = 0` and `is_exclusive_or_bp = True`.
    """
    price: int = 0
    is_exclusive_or_bp: bool = False

    # 1. Base price check
    base_price = raw_item.get("price")
    if base_price is not None:
        try:
            parsed_price = int(base_price)
            if parsed_price > 0:
                price = parsed_price
        except (ValueError, TypeError):
            price = 0

    # 2. Check shopHistory if base price is 0 or absent
    if price == 0:
        shop_history = raw_item.get("shopHistory")
        if isinstance(shop_history, list) and len(shop_history) > 0:
            last_entry = shop_history[-1]
            if isinstance(last_entry, dict):
                vbucks = last_entry.get("vbucks")
                if vbucks is not None:
                    try:
                        parsed_vbucks = int(vbucks)
                        if parsed_vbucks > 0:
                            price = parsed_vbucks
                    except (ValueError, TypeError):
                        pass
            elif isinstance(last_entry, (int, float)):
                try:
                    parsed_vbucks = int(last_entry)
                    if parsed_vbucks > 0:
                        price = parsed_vbucks
                except (ValueError, TypeError):
                    pass

    # 3. Estimate based on type and rarity if price is still 0
    if price == 0:
        type_str = ""
        raw_type = raw_item.get("type")
        if isinstance(raw_type, dict):
            type_str = str(raw_type.get("value") or "").strip().lower()
        elif raw_type is not None:
            type_str = str(raw_type).strip().lower()
            
        if type_str == "outfit":
            type_str = "skin"
            
        rarity_str = ""
        raw_rarity = raw_item.get("rarity")
        if isinstance(raw_rarity, dict):
            rarity_str = str(raw_rarity.get("value") or "").strip().lower()
        elif raw_rarity is not None:
            rarity_str = str(raw_rarity).strip().lower()
            
        prices = {
            "skin": {"uncommon": 800, "rare": 1200, "epic": 1500, "legendary": 2000},
            "pickaxe": {"uncommon": 500, "rare": 800, "epic": 1200, "legendary": 1500},
            "glider": {"uncommon": 500, "rare": 800, "epic": 1200, "legendary": 1500},
            "emote": {"uncommon": 200, "rare": 500, "epic": 800, "legendary": 800},
            "backpack": {"uncommon": 200, "rare": 400, "epic": 600, "legendary": 800},
            "wrap": {"uncommon": 300, "rare": 500, "epic": 700, "legendary": 700},
        }
        price = prices.get(type_str, {}).get(rarity_str, 0)
        
    # 4. Determine exclusivity based on presence of explicit flag from other sources
    is_exclusive_or_bp = bool(raw_item.get("is_exclusive_or_bp", False))
    if price == 0:
        is_exclusive_or_bp = True

    return price, is_exclusive_or_bp


class CosmeticItem(BaseModel):
    """
    Domain model for a Fortnite cosmetic item.

    Attributes:
        id: Item unique identifier in UPPERCASE (e.g. CID_028_ATHENA_COMMANDO_M_SCRAPPER).
        name: Item display name.
        type: Item category/type (skin, pickaxe, emote, wrap, etc.).
        rarity: Item rarity string (e.g. common, rare, epic, legendary).
        price: Price in V-Bucks (0 if not sold in the item shop).
        is_exclusive_or_bp: True if item is exclusive or Battle Pass only (cannot be purchased).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    type: str
    rarity: str
    price: int = Field(default=0, ge=0)
    is_exclusive_or_bp: bool = False
    last_seen: Optional[str] = None
    introduction_chapter: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_raw_data(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized: dict[str, Any] = dict(data)

        # 1. Normalize ID to UPPERCASE
        raw_id = normalized.get("id")
        normalized["id"] = str(raw_id).strip().upper() if raw_id is not None else ""

        # 2. Normalize Name
        raw_name = normalized.get("name")
        normalized["name"] = str(raw_name).strip() if raw_name is not None else ""

        # 3. Normalize Type: handles both raw API nested dicts {"value": "outfit"} and strings
        raw_type = normalized.get("type")
        type_str = ""
        if isinstance(raw_type, dict):
            type_str = str(raw_type.get("value") or raw_type.get("displayValue") or "")
        elif raw_type is not None:
            type_str = str(raw_type)

        type_str = type_str.strip().lower()
        # Map Fortnite internal designation "outfit" to conventional player term "skin"
        if type_str == "outfit":
            type_str = "skin"
        normalized["type"] = type_str

        # 4. Normalize Rarity: handles nested dicts {"value": "rare"} and strings
        raw_rarity = normalized.get("rarity")
        rarity_str = ""
        if isinstance(raw_rarity, dict):
            rarity_str = str(raw_rarity.get("value") or raw_rarity.get("displayValue") or "")
        elif raw_rarity is not None:
            rarity_str = str(raw_rarity)
        normalized["rarity"] = rarity_str.strip().lower()

        # 5. Price & exclusivity calculation
        computed_price, computed_exclusive = calculate_item_price_and_exclusivity(normalized)
        normalized["price"] = computed_price
        normalized["is_exclusive_or_bp"] = computed_exclusive
        
        # 6. Extract last seen date and chapter
        shop_history = normalized.get("shopHistory")
        if isinstance(shop_history, list) and len(shop_history) > 0:
            normalized["last_seen"] = str(shop_history[-1])
            
        intro = normalized.get("introduction")
        if isinstance(intro, dict):
            normalized["introduction_chapter"] = str(intro.get("chapter", ""))

        return normalized

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> "CosmeticItem":
        """Factory method to parse a raw Fortnite API dictionary into a CosmeticItem."""
        return cls.model_validate(data)


class CosmeticDatabase:
    """
    Asynchronous in-memory database and caching service for Fortnite cosmetics.

    Can function as a Singleton or be injected as a dependency.
    Provides fast O(1) item lookups by uppercase ID.
    """

    _instance: Optional["CosmeticDatabase"] = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "CosmeticDatabase":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, api_url: str = FORTNITE_API_URL) -> None:
        if getattr(self, "_initialized", False):
            return
        self.api_url: str = api_url
        self._cache: dict[str, CosmeticItem] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._initialized: bool = True

    @classmethod
    def get_instance(cls, api_url: str = FORTNITE_API_URL) -> "CosmeticDatabase":
        """Retrieve the singleton instance or create it if not yet initialized."""
        if cls._instance is None:
            cls._instance = cls(api_url=api_url)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (useful in unit testing)."""
        cls._instance = None

    @property
    def cache(self) -> dict[str, CosmeticItem]:
        """Access the underlying in-memory items dictionary."""
        return self._cache

    def __len__(self) -> int:
        """Return the number of currently cached items."""
        return len(self._cache)

    def __contains__(self, item_id: str) -> bool:
        """Check if an item ID exists in the database (case-insensitive)."""
        if not item_id:
            return False
        return item_id.strip().upper() in self._cache

    def clear(self) -> None:
        """Clear all cached items."""
        self._cache.clear()

    async def load_cosmetics(
        self,
        session: aiohttp.ClientSession,
        *,
        raise_on_error: bool = False,
    ) -> None:
        """
        Request cosmetics from Fortnite-API.com, parse them, and cache in memory.

        :param session: Active aiohttp.ClientSession instance.
        :param raise_on_error: If True, re-raises any caught network/timeout exceptions.
        """
        timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
        logger.info("Loading Fortnite cosmetics from %s...", self.api_url)

        try:
            async with session.get(self.api_url, timeout=timeout) as response:
                if response.status != 200:
                    logger.error(
                        "Fortnite API returned non-200 status code: %d",
                        response.status,
                    )
                    if raise_on_error:
                        response.raise_for_status()
                    return

                payload = await response.json()
        except TimeoutError as exc:
            logger.error(
                "Request timeout (%.1fs) while loading cosmetics from %s: %s",
                DEFAULT_TIMEOUT_SECONDS,
                self.api_url,
                exc,
            )
            if raise_on_error:
                raise
            return
        except aiohttp.ClientError as exc:
            logger.error(
                "Network error while loading cosmetics from %s: %s",
                self.api_url,
                exc,
            )
            if raise_on_error:
                raise
            return
        except Exception as exc:
            logger.error(
                "Unexpected error while loading cosmetics from %s: %s",
                self.api_url,
                exc,
                exc_info=True,
            )
            if raise_on_error:
                raise
            return

        # Extract items array from payload (supports {"data": [...]} or raw [...])
        raw_items: Any = payload.get("data", []) if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            logger.error("Unexpected payload structure: 'data' is not a list")
            return

        new_cache: dict[str, CosmeticItem] = {}
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            try:
                item = CosmeticItem.model_validate(raw_item)
                if item.id:
                    new_cache[item.id] = item
            except Exception as exc:
                logger.warning(
                    "Skipping invalid cosmetic item (%s): %s",
                    raw_item.get("id"),
                    exc,
                )

        async with self._lock:
            self._cache = new_cache

        logger.info(
            "Successfully loaded and cached %d cosmetics items",
            len(self._cache),
        )

    def get_item(self, item_id: str) -> Optional[CosmeticItem]:
        """
        Fast O(1) lookup of a cosmetic item by ID (case-insensitive).

        :param item_id: Item identifier (e.g. 'CID_028_Athena_Commando_M_Scrapper').
        :return: CosmeticItem instance if found, None otherwise.
        """
        if not item_id:
            return None
        return self._cache.get(item_id.strip().upper())


def get_cosmetics_db() -> CosmeticDatabase:
    """Dependency provider function for frameworks like FastAPI."""
    return CosmeticDatabase.get_instance()
