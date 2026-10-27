# Agent Task Board

Updated: 2026-10-27

| ID | Status | Branch | Depends | Notes |
|---|---|---|---|---|
| R00 | done | feature/R00-governance | - | Governance baseline |
| R01 | done | feature/R01-uv-baseline | R00 | Uv-only environment |
| R02A | done | feature/R02A-test-inventory | R01 | Test inventory and classification |
| R02B | done | feature/R02B-ci-gates | R02A | Architecture, scope, CI, and PR gates |
| R02C1 | done | feature/R02C1-safe-click-contracts | R02B | Safe click behavior and CLI contracts |
| R02C2 | done | feature/R02C2-overlay-tests | R02C1 | Overlay source-string replacement |
| R02C3 | done | feature/R02C3-non-overlay-tests | R02C2 | Non-overlay source-string replacement |
| R02C4 | done | feature/R02C4-native-node | R02C3 | Native and Node isolation |
| R02C5 | done | feature/R02C5-headless-final | R02C4 | Private-patch rewrite and final headless gate |
| R03 | done | feature/R03-domain-contracts | R02C5 | Domain models and rules |
| R04 | done | feature/R04-application-ports | R03 | Ports, runtime controls, fakes |
| R05 | done | feature/R05-plan-execution | R04 | Plan and execution pipeline fix round 2 complete |
| R06 | done | feature/R06-data-adapters | R05 | Config, cards, journal, artifacts |
| R07 | done | feature/R07-external-adapters | R06 | HsAng, Power.log, lobby, input; fix round 1 ready |
| R08 | done | feature/R08-enter-battle | R07 | Enter-battle slice approved |
| R02D | done | feature/R02D-test-portfolio | R08 | Test portfolio audit committed |
| R02E | in_progress | feature/R02E-test-cleanup | R02D | Stage 1+B6 approved; stage 2B B9/B8/B7 in progress |
| R09 | planned | feature/R09-mulligan | R02E | Mulligan slice |
| R10 | planned | feature/R10-turn-loop | R09 | Turn loop and reconciliation |
| R11 | planned | feature/R11-settlement-stop | R10 | Settlement and stop |
| R12 | planned | feature/R12-one-game-cli | R11 | CLI and fake full game |
| R13 | planned | feature/R13-native-cutover | R12 | Native one game and main cutover |
| R14 | planned | feature/R14-plugin-registry | R05 | Static plugin expansion |
| R15 | planned | feature/R15-control-security | R13 | Control-plane security |
| R16 | planned | feature/R16-legacy-retirement | R15 | Legacy cleanup |

## WIP Limit

One implementer and one reviewer. Parallel implementation only when interfaces are stable and files are disjoint.
