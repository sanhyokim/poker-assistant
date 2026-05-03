"""Phase 1 acceptance criteria verification tests."""

import importlib
import json
import pathlib
from typing import Any

import pytest
import yaml


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestConfigYaml:
    """Verify config.yaml is valid and contains all required sections."""

    def test_config_yaml_parseable(self) -> None:
        """config.yaml can be parsed by yaml.safe_load()."""
        config_path = PROJECT_ROOT / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert isinstance(config, dict)

    def test_config_yaml_has_all_sections(self) -> None:
        """config.yaml contains all required top-level sections."""
        config_path = PROJECT_ROOT / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        required_sections = [
            "capture",
            "profile",
            "game",
            "solver",
            "llm",
            "hud",
            "ocr",
            "recognition",
            "action_estimation",
            "logging",
            "replay",
            "preflop_chart",
        ]
        for section in required_sections:
            assert section in config, (
                f"Missing required section '{section}' in config.yaml"
            )


class TestPackageImports:
    """Verify all packages can be imported without errors."""

    @pytest.mark.parametrize(
        "package_name",
        ["capture", "recognition", "core", "strategy", "gui"],
    )
    def test_import_package(self, package_name: str) -> None:
        """Each project package can be imported."""
        module = importlib.import_module(package_name)
        assert module is not None


class TestCoordinateProfile:
    """Verify coordinate profile is valid and complete."""

    def test_profile_parseable(self) -> None:
        """coinpoker_6max.json can be parsed by json.load()."""
        profile_path = PROJECT_ROOT / "profiles" / "coinpoker_6max.json"
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
        assert isinstance(profile, dict)

    def test_profile_has_all_required_keys(self) -> None:
        """Profile contains all required region keys."""
        profile_path = PROJECT_ROOT / "profiles" / "coinpoker_6max.json"
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)

        required_keys = [
            "hero_card_1",
            "hero_card_2",
            "board_card_1",
            "board_card_2",
            "board_card_3",
            "board_card_4",
            "board_card_5",
            "pot_display",
            "hero_stack",
            "hero_bet",
            "player_stack_2",
            "player_stack_3",
            "player_stack_4",
            "player_stack_5",
            "player_stack_6",
            "player_bet_2",
            "player_bet_3",
            "player_bet_4",
            "player_bet_5",
            "player_bet_6",
            "dealer_btn_1",
            "dealer_btn_2",
            "dealer_btn_3",
            "dealer_btn_4",
            "btn_fold",
            "btn_call_check",
            "btn_raise_bet",
            "player_name_1",
            "player_name_2",
            "player_name_3",
            "player_name_4",
            "player_name_5",
            "player_name_6",
        ]
        for key in required_keys:
            assert key in profile, f"Missing required key '{key}' in profile"

    def test_profile_coordinate_format(self) -> None:
        """All coordinate entries use 'w'/'h' keys (not 'width'/'height')."""
        profile_path = PROJECT_ROOT / "profiles" / "coinpoker_6max.json"
        with open(profile_path, "r", encoding="utf-8") as f:
            profile: dict[str, Any] = json.load(f)

        for key, value in profile.items():
            if key.startswith("_") or key == "table_size":
                continue
            assert isinstance(value, dict), (
                f"'{key}' should be a dict, got {type(value)}"
            )
            assert set(value.keys()) == {"x", "y", "w", "h"}, (
                f"'{key}' should have exactly keys "
                f"{{'x', 'y', 'w', 'h'}}, got {set(value.keys())}"
            )


class TestGroundTruth:
    """Verify ground truth data is valid."""

    def test_ground_truth_parseable(self) -> None:
        """coinpoker.json can be parsed by json.load()."""
        gt_path = (
            PROJECT_ROOT
            / "tests"
            / "fixtures"
            / "ground_truth"
            / "coinpoker.json"
        )
        with open(gt_path, "r", encoding="utf-8") as f:
            gt = json.load(f)
        assert "screenshots" in gt

    def test_ground_truth_has_all_screenshots(self) -> None:
        """Ground truth contains entries for cp_01 through cp_13 and cp_07b."""
        gt_path = (
            PROJECT_ROOT
            / "tests"
            / "fixtures"
            / "ground_truth"
            / "coinpoker.json"
        )
        with open(gt_path, "r", encoding="utf-8") as f:
            gt = json.load(f)

        for i in range(1, 14):
            key = f"cp_{i:02d}"
            assert key in gt["screenshots"], f"Missing screenshot entry '{key}'"
        assert "cp_07b" in gt["screenshots"]
        assert "cp_14" not in gt["screenshots"]

    def test_cp03_cp04_swap(self) -> None:
        """cp_03 is NOT my turn, cp_04 IS my turn (filename swap)."""
        gt_path = (
            PROJECT_ROOT
            / "tests"
            / "fixtures"
            / "ground_truth"
            / "coinpoker.json"
        )
        with open(gt_path, "r", encoding="utf-8") as f:
            gt = json.load(f)

        cp03 = gt["screenshots"]["cp_03"]
        cp04 = gt["screenshots"]["cp_04"]
        assert cp03["is_my_turn"] is False, (
            "cp_03 should have is_my_turn=false (swapped)"
        )
        assert cp04["is_my_turn"] is True, (
            "cp_04 should have is_my_turn=true (swapped)"
        )
