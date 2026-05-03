# Action Sequence Fixtures

These fixtures are synthetic GameState transition sequences based on patterns
observed in PoC v2 auto-capture data. They are not raw captured frame dumps.

Each JSON file contains consecutive simplified GameState frames and the expected
`ActionEstimator.estimate()` result for each transition. Only fields required by
the current action-estimation core are included.

Notes:
- `hand_001_preflop_actions.json`: NEW_HAND, CALL, RAISE, FOLD.
- `hand_002_postflop_fold.json`: NEW_STREET, BET, FOLD.
- `hand_003_allin_showdown.json`: BET, ALL_IN, no BETS_COLLECTED when the pot
  value has already updated before bets clear, then NEW_STREET.
- `hand_004_check_and_ocr_skip.json`: Hero CHECK, three-frame OCR None
  confirmation for FOLD, and one-frame pot-spike filtering.
