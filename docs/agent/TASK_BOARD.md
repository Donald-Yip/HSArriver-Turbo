# Agent Task Board

Updated: 2026-10-27

| ID | Status | Branch | Depends | Notes |
|---|---|---|---|---|
| R00 | done | feature/R00-governance | - | Governance baseline |
| R01 | done | feature/R01-uv-baseline | R00 | Uv-only environment |
| R02 | planned | feature/R02-contracts-tests-ci | R01 | Contracts, golden replay, CI, test rationalization |
| R03 | planned | feature/R03-domain-contracts | R02 | Domain models and rules |
| R04 | planned | feature/R04-application-ports | R03 | Ports, runtime controls, fakes |
| R05 | planned | feature/R05-plan-execution | R04 | Plan and execution pipeline |
| R06 | planned | feature/R06-data-adapters | R05 | Config, cards, journal, artifacts |
| R07 | planned | feature/R07-external-adapters | R06 | HsAng, Power.log, lobby, input |
| R08 | planned | feature/R08-enter-battle | R07 | Enter battle slice |
| R09 | planned | feature/R09-mulligan | R08 | Mulligan slice |
| R10 | planned | feature/R10-turn-loop | R09 | Turn loop and reconciliation |
| R11 | planned | feature/R11-settlement-stop | R10 | Settlement and stop |
| R12 | planned | feature/R12-one-game-cli | R11 | CLI and fake full game |
| R13 | planned | feature/R13-native-cutover | R12 | Native one game and main cutover |
| R14 | planned | feature/R14-plugin-registry | R05 | Static plugin expansion |
| R15 | planned | feature/R15-control-security | R13 | Control-plane security |
| R16 | planned | feature/R16-legacy-retirement | R15 | Legacy cleanup |

## WIP Limit

One implementer and one reviewer. Parallel implementation only when interfaces are stable and files are disjoint.
