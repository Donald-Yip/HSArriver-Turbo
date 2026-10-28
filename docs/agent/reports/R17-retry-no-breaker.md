# R17 Retry-Without-Breaker Follow-up

## Diagnosis

- The flow counted consecutive failures for the same recommendation and after
  ten failures returned `OBSERVE: recommendation_stuck`.
- That state suppressed all later retries for the recommendation even when the
  actual cause was a long opponent end-turn animation or a temporarily stale
  validator snapshot.
- The suppression could consume the player's turn without executing anything.

## Change

- Removed the same-recommendation counter, retry-limit constructor argument,
  stuck signature/state, warning, and `recommendation_stuck` UI mapping.
- All parse/adapt/validation/execution failures now return `RETRY`.
- Kept OCR raw-evidence logging once per unreadable episode.
- Kept turn deadlines, explicit stop controls, and liveness/watchdog checks.

## Verification

- New retry regression suite: 7/7.
- Related recommendation-flow suite: 60/60.
- Full headless suite: 1556/1556.
- Opt-in suite: 20/20.
- Architecture, self-test, card-rule, and compile gates pass.
