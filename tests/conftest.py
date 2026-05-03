"""Pytest shared fixtures for poker-assistant tests."""

import json
import pathlib
from typing import Any

import pytest
import yaml


# Project root directory
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def project_root() -> pathlib.Path:
    """Return the project root directory path."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def config() -> dict[str, Any]:
    """Load and return config.yaml as a dictionary.

    Returns:
        Parsed config.yaml contents.
    """
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def profile() -> dict[str, Any]:
    """Load and return the CoinPoker 6max coordinate profile.

    Returns:
        Parsed coordinate profile dictionary.
    """
    profile_path = PROJECT_ROOT / "profiles" / "coinpoker_6max.json"
    with open(profile_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def ground_truth() -> dict[str, Any]:
    """Load and return the CoinPoker ground truth test data.

    Returns:
        Parsed ground truth dictionary containing expected values
        for each test screenshot.
    """
    gt_path = (
        PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "ground_truth"
        / "coinpoker.json"
    )
    with open(gt_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def screenshots_dir() -> pathlib.Path:
    """Return the path to CoinPoker test screenshots directory.

    Returns:
        Path to tests/fixtures/screenshots/coinpoker/ directory.
    """
    return (
        PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "screenshots"
        / "coinpoker"
    )
