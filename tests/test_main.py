"""Tests for main.py application glue."""

import os
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.game_state import GameState, create_empty_game_state
from main import GameLoopWorker


class _FakeHandManager:
    """Minimal hand manager double for GameLoopWorker tests."""

    @property
    def last_saved_hand_id(self) -> int | None:
        """Return no newly saved hand."""
        return None


class _FakeGameLoop:
    """GameLoop double that records GUI worker processing order."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._polling_interval = 0.0
        self._hand_manager = _FakeHandManager()
        self._state = create_empty_game_state()
        self._state.phase = "preflop"
        self._recommendation = object()

    def process_one_frame(self) -> GameState | None:
        """Return a single test GameState."""
        self.calls.append("process_one_frame")
        return self._state

    def process_game_state_after_frame(self, game_state: GameState) -> None:
        """Record canonical post-frame processing and mutate emitted state."""
        self.calls.append("process_game_state_after_frame")
        game_state.hero.position = "BB"

    @property
    def current_recommendation(self) -> object:
        """Return the recommendation after post-frame processing."""
        self.calls.append("current_recommendation")
        return self._recommendation

    @property
    def capture_failed(self) -> bool:
        """Stop the worker after one processed frame."""
        return "process_game_state_after_frame" in self.calls


def test_game_loop_worker_uses_canonical_post_frame_processing() -> None:
    """GUI worker emits the GameState after canonical post-frame processing."""
    game_loop = _FakeGameLoop()
    worker = GameLoopWorker(game_loop)
    emitted_states: list[Any] = []
    emitted_recommendations: list[Any] = []
    emitted_phases: list[str] = []

    worker.game_state_ready.connect(emitted_states.append)
    worker.recommendation_ready.connect(emitted_recommendations.append)
    worker.phase_changed.connect(emitted_phases.append)

    worker.run()

    assert game_loop.calls == [
        "process_one_frame",
        "process_game_state_after_frame",
        "current_recommendation",
    ]
    assert emitted_states == [game_loop._state]
    assert emitted_states[0].hero.position == "BB"
    assert emitted_recommendations == [game_loop._recommendation]
    assert emitted_phases == ["preflop"]


def test_game_loop_worker_emits_pre_hand_phase_status() -> None:
    """GUI worker uses hand_start_status for PRE-HAND phase display."""
    game_loop = _FakeGameLoop()
    game_loop._state.phase = "waiting"
    game_loop._state.hand_start_status = "PRE-HAND"
    worker = GameLoopWorker(game_loop)
    emitted_phases: list[str] = []

    worker.phase_changed.connect(emitted_phases.append)

    worker.run()

    assert emitted_phases == ["PRE-HAND"]
