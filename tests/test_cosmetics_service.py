"""Unit and integration tests for cosmetics_service."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from cosmetics_service import (
    CosmeticDatabase,
    CosmeticItem,
    calculate_item_price_and_exclusivity,
    get_cosmetics_db,
)


@pytest.fixture(autouse=True)
def reset_db_singleton():
    """Ensure every test starts with a clean CosmeticDatabase singleton."""
    CosmeticDatabase.reset_instance()
    yield
    CosmeticDatabase.reset_instance()


class TestPriceAndExclusivityLogic:
    def test_base_price_greater_than_zero(self):
        raw = {"id": "CID_1", "name": "Item", "price": 1500}
        price, is_exclusive = calculate_item_price_and_exclusivity(raw)
        assert price == 1500
        assert is_exclusive is False

    def test_base_price_zero_with_shop_history(self):
        raw = {
            "id": "CID_2",
            "name": "Item",
            "price": 0,
            "shopHistory": [
                {"date": "2023-01-01", "vbucks": 800},
                {"date": "2023-06-01", "vbucks": 1200},
            ],
        }
        price, is_exclusive = calculate_item_price_and_exclusivity(raw)
        assert price == 1200
        assert is_exclusive is False

    def test_base_price_missing_with_shop_history(self):
        raw = {
            "id": "CID_3",
            "name": "Item",
            "shopHistory": [{"vbucks": 500}],
        }
        price, is_exclusive = calculate_item_price_and_exclusivity(raw)
        assert price == 500
        assert is_exclusive is False

    def test_no_price_and_no_shop_history_is_exclusive(self):
        raw = {"id": "CID_4", "name": "Battle Pass Item"}
        price, is_exclusive = calculate_item_price_and_exclusivity(raw)
        assert price == 0
        assert is_exclusive is True

    def test_base_price_zero_and_empty_shop_history_is_exclusive(self):
        raw = {"id": "CID_5", "name": "Secret Skin", "price": 0, "shopHistory": []}
        price, is_exclusive = calculate_item_price_and_exclusivity(raw)
        assert price == 0
        assert is_exclusive is True

    def test_shop_history_without_vbucks_is_exclusive(self):
        raw = {
            "id": "CID_6",
            "name": "Item",
            "price": 0,
            "shopHistory": [{"date": "2023-01-01"}],
        }
        price, is_exclusive = calculate_item_price_and_exclusivity(raw)
        assert price == 0
        assert is_exclusive is True


class TestCosmeticItemModel:
    def test_model_normalization_nested_fields(self):
        raw = {
            "id": "cid_028_athena_commando_m_scrapper",
            "name": "Scrapper",
            "type": {"value": "outfit", "displayValue": "Outfit"},
            "rarity": {"value": "rare", "displayValue": "Rare"},
            "shopHistory": [{"vbucks": 1200}],
        }
        item = CosmeticItem.model_validate(raw)
        assert item.id == "CID_028_ATHENA_COMMANDO_M_SCRAPPER"
        assert item.name == "Scrapper"
        assert item.type == "skin"  # Normalized from outfit
        assert item.rarity == "rare"
        assert item.price == 1200
        assert item.is_exclusive_or_bp is False

    def test_model_with_direct_strings(self):
        raw = {
            "id": "Pickaxe_Lockjaw",
            "name": "Lockjaw Axe",
            "type": "pickaxe",
            "rarity": "epic",
            "price": 800,
        }
        item = CosmeticItem.model_validate(raw)
        assert item.id == "PICKAXE_LOCKJAW"
        assert item.name == "Lockjaw Axe"
        assert item.type == "pickaxe"
        assert item.rarity == "epic"
        assert item.price == 800
        assert item.is_exclusive_or_bp is False

    def test_model_exclusive_bp_item(self):
        raw = {
            "id": "cid_bp_black_knight",
            "name": "Black Knight",
            "type": {"value": "outfit"},
            "rarity": {"value": "legendary"},
        }
        item = CosmeticItem.model_validate(raw)
        assert item.id == "CID_BP_BLACK_KNIGHT"
        assert item.price == 0
        assert item.is_exclusive_or_bp is True


class TestCosmeticDatabase:
    def test_singleton_behavior(self):
        db1 = CosmeticDatabase()
        db2 = CosmeticDatabase()
        assert db1 is db2

        db3 = CosmeticDatabase.get_instance()
        assert db1 is db3

        db4 = get_cosmetics_db()
        assert db1 is db4

    def test_get_item_case_insensitive(self):
        db = CosmeticDatabase()
        item = CosmeticItem(
            id="CID_028_ATHENA_COMMANDO_M_SCRAPPER",
            name="Scrapper",
            type="skin",
            rarity="rare",
            price=1200,
            is_exclusive_or_bp=False,
        )
        db._cache[item.id] = item

        # Exact match
        assert db.get_item("CID_028_ATHENA_COMMANDO_M_SCRAPPER") == item
        # Lowercase
        assert db.get_item("cid_028_athena_commando_m_scrapper") == item
        # Mixed case with whitespace
        assert db.get_item("  Cid_028_Athena_Commando_M_Scrapper ") == item
        # Non-existent
        assert db.get_item("NON_EXISTENT") is None
        # Empty / None
        assert db.get_item("") is None

    @pytest.mark.asyncio
    async def test_load_cosmetics_success(self, caplog):
        db = CosmeticDatabase()

        mock_payload = {
            "status": 200,
            "data": [
                {
                    "id": "CID_001",
                    "name": "Skin One",
                    "type": {"value": "outfit"},
                    "rarity": {"value": "rare"},
                    "price": 1200,
                },
                {
                    "id": "PICKAXE_002",
                    "name": "Axe Two",
                    "type": {"value": "pickaxe"},
                    "rarity": {"value": "uncommon"},
                    "price": 0,
                    "shopHistory": [{"vbucks": 500}],
                },
                {
                    "id": "CID_BP_003",
                    "name": "BP Skin",
                    "type": {"value": "outfit"},
                    "rarity": {"value": "legendary"},
                },
            ],
        }

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_payload)

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.get.return_value.__aenter__.return_value = mock_response

        with caplog.at_level(logging.INFO):
            await db.load_cosmetics(mock_session)

        assert len(db) == 3
        assert "CID_001" in db
        assert "PICKAXE_002" in db
        assert "CID_BP_003" in db

        item1 = db.get_item("CID_001")
        assert item1 is not None
        assert item1.price == 1200
        assert item1.type == "skin"
        assert item1.is_exclusive_or_bp is False

        item2 = db.get_item("pickaxe_002")
        assert item2 is not None
        assert item2.price == 500
        assert item2.type == "pickaxe"
        assert item2.is_exclusive_or_bp is False

        item3 = db.get_item("cid_bp_003")
        assert item3 is not None
        assert item3.price == 0
        assert item3.type == "skin"
        assert item3.is_exclusive_or_bp is True

        # Check that log message confirmed the item count
        assert "Successfully loaded and cached 3 cosmetics items" in caplog.text

    @pytest.mark.asyncio
    async def test_load_cosmetics_network_error_handled(self, caplog):
        db = CosmeticDatabase()

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.get.side_effect = aiohttp.ClientConnectionError("Connection refused")

        with caplog.at_level(logging.ERROR):
            # By default, error is gracefully logged and not raised
            await db.load_cosmetics(mock_session)

        assert "Network error while loading cosmetics" in caplog.text
        assert len(db) == 0

    @pytest.mark.asyncio
    async def test_load_cosmetics_network_error_re_raised_when_requested(self):
        db = CosmeticDatabase()

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.get.side_effect = aiohttp.ClientConnectionError("Connection refused")

        with pytest.raises(aiohttp.ClientError):
            await db.load_cosmetics(mock_session, raise_on_error=True)

    @pytest.mark.asyncio
    async def test_load_cosmetics_timeout_handled(self, caplog):
        db = CosmeticDatabase()

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.get.side_effect = TimeoutError()

        with caplog.at_level(logging.ERROR):
            await db.load_cosmetics(mock_session)

        assert "Request timeout" in caplog.text

    @pytest.mark.asyncio
    async def test_load_cosmetics_non_200_status(self, caplog):
        db = CosmeticDatabase()

        mock_response = AsyncMock()
        mock_response.status = 503

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_session.get.return_value.__aenter__.return_value = mock_response

        with caplog.at_level(logging.ERROR):
            await db.load_cosmetics(mock_session)

        assert "Fortnite API returned non-200 status code: 503" in caplog.text
        assert len(db) == 0
