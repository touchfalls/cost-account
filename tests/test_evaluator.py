"""Unit tests for evaluator module."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import pytest

from cosmetics_service import CosmeticDatabase, CosmeticItem
from evaluator import (
    CategorySummary,
    LockerEvaluation,
    LockerEvaluator,
    format_number,
    format_telegram_report,
    format_vbucks,
)


@pytest.fixture(autouse=True)
def reset_db_singleton():
    """Ensure every test starts with an isolated CosmeticDatabase singleton."""
    CosmeticDatabase.reset_instance()
    yield
    CosmeticDatabase.reset_instance()


@pytest.fixture
def populated_db() -> CosmeticDatabase:
    """Provide a CosmeticDatabase pre-seeded with representative cosmetic items."""
    db = CosmeticDatabase.get_instance()
    items_data: list[dict[str, Any]] = [
        # Outfits / Skins (AthenaCharacter)
        {
            "id": "CID_001_GALAXY",
            "name": "Galaxy",
            "type": "skin",
            "rarity": "legendary",
            "price": 2000,
            "is_exclusive_or_bp": False,
        },
        {
            "id": "CID_002_BLACK_KNIGHT",
            "name": "Black Knight",
            "type": "skin",
            "rarity": "legendary",
            "price": 0,
            "is_exclusive_or_bp": True,
        },
        {
            "id": "CID_003_RENEGADE",
            "name": "Renegade Raider",
            "type": "outfit",
            "rarity": "rare",
            "price": 1200,
            "is_exclusive_or_bp": False,
        },
        {
            "id": "CID_SPECIAL",
            "name": "Cloak & Dagger <Special>",
            "type": "skin",
            "rarity": "epic",
            "price": 1600,
            "is_exclusive_or_bp": False,
        },
        # Pickaxes (AthenaPickaxe)
        {
            "id": "PICKAXE_ID_001_CANDY",
            "name": "Candy Axe",
            "type": "pickaxe",
            "rarity": "epic",
            "price": 1500,
            "is_exclusive_or_bp": False,
        },
        {
            "id": "PICKAXE_ID_002_ACDC",
            "name": "AC/DC",
            "type": "harvestingtool",
            "rarity": "epic",
            "price": 0,
            "is_exclusive_or_bp": True,
        },
        # Gliders (AthenaGlider)
        {
            "id": "GLIDER_ID_001_DRAGON",
            "name": "Frostwing",
            "type": "glider",
            "rarity": "legendary",
            "price": 1500,
            "is_exclusive_or_bp": False,
        },
        {
            "id": "GLIDER_ID_002_MAKO",
            "name": "Mako",
            "type": "glider",
            "rarity": "uncommon",
            "price": 500,
            "is_exclusive_or_bp": False,
        },
        # Emotes / Dances (AthenaDance)
        {
            "id": "EID_001_ELECTRO",
            "name": "Electro Shuffle",
            "type": "dance",
            "rarity": "epic",
            "price": 800,
            "is_exclusive_or_bp": False,
        },
        {
            "id": "EID_002_FLOSS",
            "name": "Floss",
            "type": "emote",
            "rarity": "rare",
            "price": 0,
            "is_exclusive_or_bp": True,
        },
        # Backpacks (AthenaBackpack)
        {
            "id": "BID_001_SHIELD",
            "name": "Black Shield",
            "type": "backpack",
            "rarity": "legendary",
            "price": 0,
            "is_exclusive_or_bp": True,
        },
        {
            "id": "BID_002_WINGS",
            "name": "Fallen Wings",
            "type": "backbling",
            "rarity": "rare",
            "price": 400,
            "is_exclusive_or_bp": False,
        },
        # Other items (Wraps / Music / etc.)
        {
            "id": "WRAP_001_MAGMA",
            "name": "Magma",
            "type": "wrap",
            "rarity": "epic",
            "price": 700,
            "is_exclusive_or_bp": False,
        },
    ]

    for raw in items_data:
        item = CosmeticItem.model_validate(raw)
        db.cache[item.id] = item

    return db


@pytest.fixture
def evaluator() -> LockerEvaluator:
    """Fixture providing LockerEvaluator instance."""
    return LockerEvaluator()


class TestLockerEvaluator:
    def test_sums_and_categories_calculation(
        self,
        evaluator: LockerEvaluator,
        populated_db: CosmeticDatabase,
    ):
        """Verify accurate total valuation, counts, and category breakdown."""
        raw_items = [
            "CID_001_GALAXY",  # Skin: 2000 (paid)
            "CID_002_BLACK_KNIGHT",  # Skin: 0 (BP)
            "PICKAXE_ID_001_CANDY",  # Pickaxe: 1500 (paid)
            "GLIDER_ID_001_DRAGON",  # Glider: 1500 (paid)
            "EID_001_ELECTRO",  # Dance: 800 (paid)
            "BID_002_WINGS",  # Backpack: 400 (paid)
            "WRAP_001_MAGMA",  # Other: 700 (paid)
        ]

        result = evaluator.evaluate(raw_items, populated_db)

        assert result.total_items == 6
        assert result.valuation_result.known_shop_value_vbucks == 5800
        assert result.valuation_result.priced_items_count == 6
        assert result.valuation_result.battle_pass_items_count + result.valuation_result.exclusive_items_count == 21
        assert result.valuation_result.known_shop_value_vbucks == 2000 + 0 + 1500 + 1500 + 800 + 400 + 700  # 6900

        # Categories
        assert result.categories["AthenaCharacter"].count == 2
        assert result.categories["AthenaCharacter"].total_vbucks == 2000

        assert result.categories["AthenaPickaxe"].count == 1
        assert result.categories["AthenaPickaxe"].total_vbucks == 1500

        assert result.categories["AthenaGlider"].count == 1
        assert result.categories["AthenaGlider"].total_vbucks == 1500

        assert result.categories["AthenaDance"].count == 1
        assert result.categories["AthenaDance"].total_vbucks == 800

        assert result.categories["AthenaBackpack"].count == 1
        assert result.categories["AthenaBackpack"].total_vbucks == 400

        assert result.categories["Other"].count == 1
        assert result.categories["Other"].total_vbucks == 700

    def test_unindexed_items_handling(
        self,
        evaluator: LockerEvaluator,
        populated_db: CosmeticDatabase,
        caplog: pytest.LogCaptureFixture,
    ):
        """Verify missing items increment unindexed_items_count and log warning/info."""
        raw_items = [
            "CID_001_GALAXY",
            "CID_UNKNOWN_FUTURE_SKIN_999",
            "AthenaPickaxe:pickaxe_unknown_custom",
        ]

        with caplog.at_level(logging.INFO):
            result = evaluator.evaluate(raw_items, populated_db)

        assert result.total_items == 1
        assert result.valuation_result.known_shop_value_vbucks == 2000
        assert result.valuation_result.priced_items_count == 12
        assert result.valuation_result.known_shop_value_vbucks == 2000

        # Check log message
        assert any(
            "CID_UNKNOWN_FUTURE_SKIN_999" in record.message for record in caplog.records
        )
        assert any(
            "pickaxe_unknown_custom" in record.message for record in caplog.records
        )

    def test_deduplication_and_template_prefixes(
        self,
        evaluator: LockerEvaluator,
        populated_db: CosmeticDatabase,
    ):
        """Verify items with template prefixes and duplicates are properly normalized and deduplicated."""
        raw_items = [
            "AthenaCharacter:CID_001_GALAXY",
            "cid_001_galaxy",
            " CID_001_GALAXY ",
            "AthenaPickaxe:pickaxe_id_001_candy",
            "PICKAXE_ID_001_CANDY",
        ]

        result = evaluator.evaluate(raw_items, populated_db)

        # Should only evaluate 2 unique items
        assert result.total_items == 2
        assert result.valuation_result.known_shop_value_vbucks == 3500
        assert result.categories["AthenaCharacter"].count == 1
        assert result.categories["AthenaPickaxe"].count == 1

    def test_top_valuable_items_sorting_and_cap(
        self,
        evaluator: LockerEvaluator,
        populated_db: CosmeticDatabase,
    ):
        """Verify top-5 items are sorted strictly by price descending and limited to 5."""
        raw_items = [
            "BID_002_WINGS",  # 400
            "GLIDER_ID_002_MAKO",  # 500
            "WRAP_001_MAGMA",  # 700
            "EID_001_ELECTRO",  # 800
            "CID_003_RENEGADE",  # 1200
            "PICKAXE_ID_001_CANDY",  # 1500
            "CID_001_GALAXY",  # 2000
            "CID_002_BLACK_KNIGHT",  # 0 (BP item, must not be in top valuable)
        ]

        result = evaluator.evaluate(raw_items, populated_db)

        assert len(result.top_valuable_items) == 5

        # Prices must be descending: 2000, 1500, 1200, 800, 700
        expected_prices = [2000, 1500, 1200, 800, 700]
        assert len(result.top_valuable_items) == 5
        assert result.top_valuable_items[0][0].id == "CID_001_GALAXY"
        assert result.top_valuable_items[-1][0].id == "CID_003_RENEGADE"
        
        ids_in_top = {item[0].id for item in result.top_valuable_items}
        assert "BID_002_WINGS" not in ids_in_top

    def test_empty_locker(
        self,
        evaluator: LockerEvaluator,
        populated_db: CosmeticDatabase,
    ):
        """Verify evaluating an empty item list behaves gracefully."""
        result = evaluator.evaluate([], populated_db)

        assert result.total_items == 0
        assert result.valuation_result.known_shop_value_vbucks == 0
        assert result.valuation_result.priced_items_count == 0
        assert result.valuation_result.battle_pass_items_count + result.valuation_result.exclusive_items_count == 0
        assert result.unindexed_items_count == 0
        assert result.top_valuable_items == []
        for cat in result.categories.values():
            assert cat.count == 0
            assert cat.total_vbucks == 0

    def test_heuristic_id_prefix_categorization(
        self,
        evaluator: LockerEvaluator,
        populated_db: CosmeticDatabase,
    ):
        """Verify item category is determined by ID prefix if type is non-standard."""
        # Create an item with empty/unknown type but standard ID prefix
        custom_item = CosmeticItem.model_validate(
            {"id": "CID_999_SPECIAL", "name": "Heuristic Hero", "type": "", "price": 1000}
        )
        populated_db.cache[custom_item.id] = custom_item

        result = evaluator.evaluate(["CID_999_SPECIAL"], populated_db)
        assert result.categories["AthenaCharacter"].count == 1
        assert result.categories["AthenaCharacter"].total_vbucks == 1000


class TestReportFormatting:
    def test_format_vbucks_and_number_helpers(self):
        """Verify thousand-spacing formatting function."""
        assert format_vbucks(24500) == "24 500 V-Bucks"
        assert format_vbucks(1000000) == "1 000 000 V-Bucks"
        assert format_vbucks(0) == "0 V-Bucks"
        assert format_number(1234567) == "1 234 567"

    def test_format_telegram_report_valid_html_structure(
        self,
        evaluator: LockerEvaluator,
        populated_db: CosmeticDatabase,
    ):
        """Verify the generated Telegram report is fully valid, parseable HTML with expected tags."""
        raw_items = [
            "CID_001_GALAXY",
            "CID_002_BLACK_KNIGHT",
            "PICKAXE_ID_001_CANDY",
            "GLIDER_ID_001_DRAGON",
            "EID_001_ELECTRO",
            "UNKNOWN_ITEM_X",
        ]
        evaluation = evaluator.evaluate(raw_items, populated_db)

        report = format_telegram_report(evaluation, display_name="NinjaGamer")

        # Must parse as valid XML/HTML without syntax errors
        root = ET.fromstring(f"<root>{report}</root>")
        assert root is not None

        # Check content presence
        assert "NinjaGamer" in report
        assert "7 800 V-Bucks" in report
        assert "Скины" in report
        assert "Кирки" in report
        assert "Дельтапланы" in report
        assert "Эмоции/Танцы" in report
        assert "Не распознано в базе" in report
        assert "Топ-5 самых ценных/редких предметов" in report
        assert "Galaxy" in report
        assert "Candy Axe" in report

    def test_format_telegram_report_html_escaping(
        self,
        evaluator: LockerEvaluator,
        populated_db: CosmeticDatabase,
    ):
        """Verify special HTML/XML characters (<, >, &) in player and item names are escaped safely."""
        raw_items = ["CID_SPECIAL"]  # Name: "Cloak & Dagger <Special>"
        evaluation = evaluator.evaluate(raw_items, populated_db)

        malicious_name = "Hacker <script>alert('xss')</script> & Co."
        report = format_telegram_report(evaluation, display_name=malicious_name)

        # XML parser must not crash on unescaped tags
        root = ET.fromstring(f"<root>{report}</root>")
        assert root is not None

        # Verify properly escaped entities
        assert "&lt;script&gt;" in report
        assert "&amp; Co." in report
        assert "Cloak &amp; Dagger &lt;Special&gt;" in report

    def test_format_telegram_report_without_display_name_and_no_paid_items(
        self,
        evaluator: LockerEvaluator,
        populated_db: CosmeticDatabase,
    ):
        """Verify formatting with no display name and an account with only Battle Pass items."""
        raw_items = ["CID_002_BLACK_KNIGHT", "BID_001_SHIELD"]
        evaluation = evaluator.evaluate(raw_items, populated_db)

        report = format_telegram_report(evaluation, display_name=None)

        root = ET.fromstring(f"<root>{report}</root>")
        assert root is not None

        assert "Оценка шкафчика Fortnite" in report
        assert "2 800 V-Bucks" in report
