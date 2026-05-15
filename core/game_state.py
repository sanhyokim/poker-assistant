"""GameState data structures for per-frame recognition results."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PlayerState:
    """Individual player state.

    Attributes:
        name: Player name from OCR, or None on failure.
        stack: Stack amount. Empty seats use None.
        bet: Current-street bet amount. No bet uses 0.
        is_seated: Whether the player is seated.
        cards_visible: Whether opponent hole cards are visible this frame.
        in_current_hand: Whether the player joined the current hand.
    """

    name: str | None = None
    stack: int | None = None
    bet: int = 0
    is_seated: bool = False
    cards_visible: bool = False
    in_current_hand: bool = False


@dataclass
class HeroState:
    """Hero state.

    Attributes:
        seat: Hero seat number. Always 1.
        position: Position name, or None when dealer is unknown.
        cards: Hero hole cards, or None before recognition.
        cards_visible: Whether hero hole cards are visible this frame.
        stack: Hero stack amount.
        bet: Current-street hero bet amount.
        is_my_turn: Whether hero is currently to act.
        in_current_hand: Whether hero is still in the current hand.
        has_folded: Whether hero has folded in the current hand.
    """

    seat: int = 1
    position: str | None = None
    cards: list[str] | None = None
    cards_visible: bool = False
    stack: int | None = None
    bet: int = 0
    is_my_turn: bool = False
    in_current_hand: bool = False
    has_folded: bool = False


@dataclass
class ButtonState:
    """Action button state, populated only when hero can act.

    Attributes:
        fold: Whether the fold button is visible.
        call_or_check: Either call, check, or None.
        raise_or_bet: Either raise, bet, or None.
        bet_size: Bet-size input value.
    """

    fold: bool = True
    call_or_check: str | None = None
    raise_or_bet: str | None = None
    bet_size: int | None = None


@dataclass
class ActionRecord:
    """One detected action.

    Attributes:
        seat: Acting player seat number.
        action: Action type.
        amount: Chip amount. FOLD and CHECK use 0.
        confidence: Detection confidence, high or low.
    """

    seat: int = 0
    action: str = ""
    amount: int = 0
    confidence: str = "high"


@dataclass
class GameState:
    """Complete game state for one polling frame.

    Attributes:
        timestamp: ISO 8601 timestamp.
        frame_number: Frame sequence number.
        phase: Game phase.
        hand_id: Current hand ID, or None while waiting.
        table_visible: Whether this frame appears to be a valid poker table.
        hero: Hero state.
        board: Board card list.
        board_card_count: Number of visible board cards.
        pot: Pot amount.
        players: Seat string to PlayerState mapping for seats 2 through 6.
        dealer_seat: Dealer button seat, or None if unknown.
        active_player_count: Number of players in the current hand.
        buttons: Button state when hero can act, otherwise None.
        actions_since_last_frame: Actions detected since the previous frame.
        current_street_actions: Cumulative actions on the current street.
        preflop_actions: Cumulative preflop actions for the current hand.
        hero_action: Hero action after execution, otherwise None.
        game_event: Game lifecycle event, or None.
        suppress_phase_fast_forward: Whether hand-start phase fast-forward
            should be skipped for this frame.
        strategy_defer_reason: Reason to skip strategy calculation for this
            frame, or None when strategy may run.
        hero_cards_unstable_reason: Reason hero cards are unsafe for strategy
            or saving, or None when hero cards are stable.
    """

    timestamp: str = ""
    frame_number: int = 0
    phase: str = "waiting"
    hand_id: int | None = None
    table_visible: bool = False

    hero: HeroState = field(default_factory=HeroState)
    board: list[str] = field(default_factory=list)
    board_card_count: int = 0
    pot: int = 0

    players: dict[str, PlayerState] = field(default_factory=dict)
    dealer_seat: int | None = None
    active_player_count: int = 0

    buttons: ButtonState | None = None
    actions_since_last_frame: list[ActionRecord] = field(default_factory=list)
    current_street_actions: list[ActionRecord] = field(default_factory=list)
    preflop_actions: list[ActionRecord] = field(default_factory=list)
    hero_action: ActionRecord | None = None
    game_event: str | None = None
    suppress_phase_fast_forward: bool = False
    strategy_defer_reason: str | None = None
    hero_cards_unstable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the GameState to a JSON-serializable dictionary.

        Returns:
            Recursively converted dictionary.
        """
        return asdict(self)

    @staticmethod
    def create_default_players() -> dict[str, PlayerState]:
        """Create default PlayerState entries for seats 2 through 6.

        Returns:
            Mapping from seat string to default PlayerState.
        """
        return {str(i): PlayerState() for i in range(2, 7)}


def create_empty_game_state() -> GameState:
    """Create an empty initialized GameState.

    Returns:
        Waiting-state GameState with seats 2 through 6 initialized.
    """
    return GameState(players=GameState.create_default_players())


@dataclass
class StateDiff:
    """Difference between two GameState frames.

    Attributes:
        pot_changed: Whether pot changed.
        pot_prev: Previous pot value.
        pot_curr: Current pot value.
        board_count_changed: Whether board card count changed.
        board_count_prev: Previous board card count.
        board_count_curr: Current board card count.
        hero_stack_changed: Whether hero stack changed.
        hero_stack_prev: Previous hero stack.
        hero_stack_curr: Current hero stack.
        hero_bet_changed: Whether hero bet changed.
        hero_bet_prev: Previous hero bet.
        hero_bet_curr: Current hero bet.
        is_my_turn_changed: Whether hero turn state changed.
        is_my_turn_prev: Previous hero turn state.
        is_my_turn_curr: Current hero turn state.
        player_changes: Seat string to player change information.
        max_bet_prev: Previous maximum bet across hero and players.
        max_bet_curr: Current maximum bet across hero and players.
        any_change: Whether any tracked value changed.
    """

    pot_changed: bool = False
    pot_prev: int = 0
    pot_curr: int = 0

    board_count_changed: bool = False
    board_count_prev: int = 0
    board_count_curr: int = 0

    hero_stack_changed: bool = False
    hero_stack_prev: int | None = None
    hero_stack_curr: int | None = None

    hero_bet_changed: bool = False
    hero_bet_prev: int = 0
    hero_bet_curr: int = 0

    is_my_turn_changed: bool = False
    is_my_turn_prev: bool = False
    is_my_turn_curr: bool = False

    player_changes: dict[str, dict[str, Any]] = field(default_factory=dict)

    max_bet_prev: int = 0
    max_bet_curr: int = 0

    any_change: bool = False


def compute_state_diff(prev: GameState, curr: GameState) -> StateDiff:
    """Compute the difference between two GameState frames.

    Args:
        prev: Previous GameState.
        curr: Current GameState.

    Returns:
        StateDiff containing tracked value changes.
    """
    diff = StateDiff()

    diff.pot_prev = prev.pot
    diff.pot_curr = curr.pot
    diff.pot_changed = prev.pot != curr.pot

    diff.board_count_prev = prev.board_card_count
    diff.board_count_curr = curr.board_card_count
    diff.board_count_changed = prev.board_card_count != curr.board_card_count

    diff.hero_stack_prev = prev.hero.stack
    diff.hero_stack_curr = curr.hero.stack
    diff.hero_stack_changed = prev.hero.stack != curr.hero.stack

    diff.hero_bet_prev = prev.hero.bet
    diff.hero_bet_curr = curr.hero.bet
    diff.hero_bet_changed = prev.hero.bet != curr.hero.bet

    diff.is_my_turn_prev = prev.hero.is_my_turn
    diff.is_my_turn_curr = curr.hero.is_my_turn
    diff.is_my_turn_changed = prev.hero.is_my_turn != curr.hero.is_my_turn

    all_bets_prev = [prev.hero.bet]
    all_bets_curr = [curr.hero.bet]

    for seat_key in ["2", "3", "4", "5", "6"]:
        prev_player = prev.players.get(seat_key, PlayerState())
        curr_player = curr.players.get(seat_key, PlayerState())

        player_change: dict[str, Any] = {
            "stack_prev": prev_player.stack,
            "stack_curr": curr_player.stack,
            "stack_changed": prev_player.stack != curr_player.stack,
            "bet_prev": prev_player.bet,
            "bet_curr": curr_player.bet,
            "bet_changed": prev_player.bet != curr_player.bet,
        }
        diff.player_changes[seat_key] = player_change

        all_bets_prev.append(prev_player.bet)
        all_bets_curr.append(curr_player.bet)

    diff.max_bet_prev = max(all_bets_prev)
    diff.max_bet_curr = max(all_bets_curr)

    diff.any_change = (
        diff.pot_changed
        or diff.board_count_changed
        or diff.hero_stack_changed
        or diff.hero_bet_changed
        or diff.is_my_turn_changed
        or any(
            player_change["stack_changed"] or player_change["bet_changed"]
            for player_change in diff.player_changes.values()
        )
    )

    return diff
