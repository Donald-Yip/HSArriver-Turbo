# Layered Architecture

## Goal

Create a one-game MVP with stable domain, application, and adapter boundaries while retaining the legacy implementation until explicit cutover.

## Layers

### Domain

Pure game and action semantics. No I/O, no external libraries, no legacy imports.

### Application

Use cases, ports, plan generation, validation, execution coordination, stop control, journaling, and typed operation results.

### Adapters

Implement application ports by wrapping HSAng capture/OCR, Power.log parsing, lobby recognition, mouse input, configuration, card metadata, and persistence.

### Plugins

Static card/action extensions. They depend on domain and a narrow plugin contract only, return declarative plans, and have no side effects.

### Bootstrap

The only composition root. Creates real adapters, plugins, runtime controls, and use cases.

### Entrypoints

CLI and future delivery adapters. They translate external input into application commands.

## MVP Vertical Flow

```text
CLI -> bootstrap -> RunOneGame
  -> EnterBattle
  -> Mulligan
  -> PlayTurn loop
  -> FinishBattle
  -> stop after one game
```

## Legacy Strategy

- Existing `web_ui.py`, `FSM_action.py`, `manual_controller.py`, and root adapters remain in place initially.
- New code must not import their internals.
- Adapters may wrap stable legacy functions.
- Old code is retired only after the new entrypoint passes headless and native one-game acceptance.
