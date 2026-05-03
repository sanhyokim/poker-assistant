"""Position calculator for poker table seats."""

import logging

logger = logging.getLogger(__name__)

ALL_SEATS: list[int] = [1, 2, 3, 4, 5, 6]

POSITION_NAMES: dict[int, list[str]] = {
    6: ["BTN", "SB", "BB", "UTG", "MP", "CO"],
    5: ["BTN", "SB", "BB", "UTG", "CO"],
    4: ["BTN", "SB", "BB", "CO"],
    3: ["BTN", "SB", "BB"],
    2: ["BTN", "BB"],
}

_last_dealer_warning: str | None = None


def _seats_clockwise_from(start_seat: int, active_seats: list[int]) -> list[int]:
    """Return active seats ordered in CoinPoker action order from a start seat.

    Args:
        start_seat: Seat number used as the start of the action order.
        active_seats: Active seat numbers.

    Returns:
        Active seats in descending seat-number order from start_seat.
    """
    full_order = [
        ((start_seat - 1 - offset) % len(ALL_SEATS)) + 1
        for offset in range(len(ALL_SEATS))
    ]
    active_set = set(active_seats)
    return [seat for seat in full_order if seat in active_set]


def calculate_positions(
    dealer_seat: int | None,
    active_seats: list[int],
) -> dict[int, str]:
    """Assign poker positions to active seats.

    CoinPoker action order runs clockwise on screen, which corresponds to
    descending seat numbers with wraparound.

    Args:
        dealer_seat: Dealer button seat number from 1 to 6.
        active_seats: Seats participating in the current hand.

    Returns:
        Mapping from seat number to position name. Invalid inputs return an
        empty dictionary.
    """
    if dealer_seat is None:
        logger.warning("Dealer seat is None, cannot calculate positions")
        return {}

    if not active_seats:
        logger.warning("No active seats, cannot calculate positions")
        return {}

    if dealer_seat not in ALL_SEATS:
        logger.warning("Invalid dealer seat: %s", dealer_seat)
        return {}

    invalid_seats = [seat for seat in active_seats if seat not in ALL_SEATS]
    if invalid_seats:
        logger.warning("Invalid active seats: %s", invalid_seats)
        return {}

    unique_active_seats = sorted(set(active_seats), key=ALL_SEATS.index)
    player_count = len(unique_active_seats)

    if player_count < 2:
        logger.warning(
            "Only %d active seat(s), need at least 2 for positions",
            player_count,
        )
        return {}

    if player_count > 6:
        logger.warning("Too many active seats (%d), max 6 supported", player_count)
        return {}

    position_names = POSITION_NAMES.get(player_count)
    if position_names is None:
        logger.error("No position mapping for %d players", player_count)
        return {}

    if dealer_seat not in unique_active_seats:
        _log_dealer_not_active_once(dealer_seat, unique_active_seats)
    ordered_seats = _seats_clockwise_from(dealer_seat, unique_active_seats)
    positions: dict[int, str] = {}
    for index, seat in enumerate(ordered_seats):
        if index < len(position_names):
            positions[seat] = position_names[index]

    logger.debug(
        "Positions calculated: dealer=%d, players=%d, %s",
        dealer_seat,
        player_count,
        positions,
    )
    logger.debug(
        "Position assignment: dealer=%s, active_seats=%s, positions=%s",
        dealer_seat,
        unique_active_seats,
        positions,
    )
    return positions


def _log_dealer_not_active_once(dealer_seat: int, active_seats: list[int]) -> None:
    global _last_dealer_warning

    warning_key = f"{dealer_seat}-{active_seats}"
    if warning_key == _last_dealer_warning:
        return
    logger.debug(
        "Dealer seat %d not in active seats %s, using physical button position",
        dealer_seat,
        active_seats,
    )
    _last_dealer_warning = warning_key


def get_hero_position(positions: dict[int, str], hero_seat: int = 1) -> str | None:
    """Return the hero position from a position mapping.

    Args:
        positions: Mapping returned by calculate_positions().
        hero_seat: Hero seat number.

    Returns:
        Hero position name, or None if hero has no assigned position.
    """
    return positions.get(hero_seat)
