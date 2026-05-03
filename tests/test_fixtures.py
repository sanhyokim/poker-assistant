"""Tests for shared fixtures and static fixture files."""

from typing import Any


def test_profile_coordinate_entries(profile: dict[str, Any]) -> None:
    """Verify CoinPoker profile contains required coordinate entries."""
    required_keys = [
        "hero_card_1",
        "hero_card_2",
        *[f"board_card_{i}" for i in range(1, 6)],
        "pot_display",
        "hero_stack",
        "hero_bet",
        *[f"player_stack_{i}" for i in range(2, 7)],
        *[f"player_bet_{i}" for i in range(2, 7)],
        *[f"dealer_btn_{i}" for i in range(1, 5)],
        "btn_fold",
        "btn_call_check",
        "btn_raise_bet",
        *[f"player_name_{i}" for i in range(1, 7)],
    ]

    for key in required_keys:
        assert key in profile
        assert set(profile[key]) == {"x", "y", "w", "h"}


def test_ground_truth_screenshot_entries(ground_truth: dict[str, Any]) -> None:
    """Verify CoinPoker ground truth contains current primary screenshots."""
    screenshots = ground_truth["screenshots"]

    for i in range(1, 14):
        assert f"cp_{i:02d}" in screenshots

    assert "cp_07b" in screenshots
    assert "cp_14" not in screenshots
    assert screenshots["cp_03"]["is_my_turn"] is False
    assert screenshots["cp_04"]["is_my_turn"] is True
