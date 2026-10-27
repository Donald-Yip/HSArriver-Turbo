from __future__ import annotations

import threading
import unittest
from collections.abc import Callable


from hslegend.application import (
    ActionGate,
    ActionLease,
    ActionLeaseHeld,
    ConfigSnapshot,
    NoActiveLease,
    OperationCancelled,
    RunContext,
    StaleActionLease,
    StopToken,
    WrongLeaseOwner,
)


def _run_barrier_workers(worker_count: int, worker: Callable[[int], None]) -> list[BaseException]:
    """Run workers that meet at one barrier, returning unexpected exceptions."""

    barrier = threading.Barrier(worker_count, timeout=2.0)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def guarded(index: int) -> None:
        try:
            barrier.wait()
            worker(index)
        except BaseException as exc:  # pragma: no cover - asserted below
            with errors_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=guarded, args=(index,), daemon=True)
        for index in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3.0)
    if any(thread.is_alive() for thread in threads):
        errors.append(AssertionError("concurrent worker did not finish"))
    return errors


class StopTokenTests(unittest.TestCase):
    def test_cancel_is_idempotent_and_first_reason_wins(self) -> None:
        token = StopToken()
        self.assertFalse(token.is_cancelled())
        self.assertTrue(token.cancel("user_requested"))
        self.assertFalse(token.cancel("later_reason"))
        self.assertTrue(token.is_cancelled())
        self.assertEqual("user_requested", token.reason)
        with self.assertRaisesRegex(OperationCancelled, "user_requested"):
            token.raise_if_cancelled()

    def test_concurrent_cancel_has_exactly_one_winner_many_iterations(self) -> None:
        workers = 8
        for iteration in range(64):
            token = StopToken()
            results: list[bool] = []
            results_lock = threading.Lock()
            reasons = tuple(f"reason-{iteration}-{index}" for index in range(workers))

            def cancel(index: int) -> None:
                won = token.cancel(reasons[index])
                with results_lock:
                    results.append(won)

            errors = _run_barrier_workers(workers, cancel)
            with self.subTest(iteration=iteration):
                self.assertEqual([], errors)
                self.assertEqual(1, sum(results))
                self.assertEqual(workers, len(results))
                self.assertIn(token.reason, reasons)
                self.assertFalse(token.cancel("late"))

    def test_active_waiter_observes_cancellation(self) -> None:
        token = StopToken()
        entered = threading.Event()
        observed: list[bool] = []

        def wait_for_stop() -> None:
            entered.set()
            observed.append(token.wait(2.0, poll_interval=0.005))

        waiter = threading.Thread(target=wait_for_stop, daemon=True)
        waiter.start()
        self.assertTrue(entered.wait(1.0))
        token.cancel("stop")
        waiter.join(1.0)

        self.assertFalse(waiter.is_alive())
        self.assertEqual([True], observed)

    def test_wait_timeout_returns_false(self) -> None:
        self.assertFalse(StopToken().wait(0.0))


class ActionGateTests(unittest.TestCase):
    def test_gate_enforces_single_lease_and_exact_owner_release(self) -> None:
        gate = ActionGate()
        lease = gate.acquire("worker-a", "operation-1")

        with self.assertRaises(ActionLeaseHeld):
            gate.acquire("worker-b", "operation-2")

        forged = ActionLease("worker-b", "operation-1", lease.lease_id, lease.expires_at)
        with self.assertRaises(WrongLeaseOwner):
            gate.release(forged)

        same_owner_stale = ActionLease(
            "worker-a", "operation-2", lease.lease_id, lease.expires_at
        )
        with self.assertRaises(StaleActionLease):
            gate.release(same_owner_stale)

        gate.release(lease)
        self.assertIsNone(gate.current_lease())
        with self.assertRaises(NoActiveLease):
            gate.release(lease)

    def test_concurrent_acquire_never_issues_two_leases_many_iterations(self) -> None:
        workers = 8
        for iteration in range(64):
            gate = ActionGate()
            if iteration % 2:
                gate.acquire(
                    "expired-owner",
                    "expired-operation",
                    timeout_seconds=1.0,
                    now=0.0,
                )
            leases: list[ActionLease] = []
            held = 0
            results_lock = threading.Lock()

            def acquire(index: int) -> None:
                nonlocal held
                try:
                    lease = gate.acquire(f"owner-{index}", f"operation-{index}", now=2.0)
                except ActionLeaseHeld:
                    with results_lock:
                        held += 1
                else:
                    with results_lock:
                        leases.append(lease)

            errors = _run_barrier_workers(workers, acquire)
            with self.subTest(iteration=iteration):
                self.assertEqual([], errors)
                self.assertEqual(1, len(leases))
                self.assertEqual(workers - 1, held)
                self.assertEqual(leases[0], gate.current_lease())
                gate.release(leases[0])

    def test_concurrent_release_rejects_wrong_and_stale_leases_safely(self) -> None:
        for iteration in range(32):
            gate = ActionGate()
            lease = gate.acquire("owner", "operation")
            stale = ActionLease("owner", "stale-operation", lease.lease_id, lease.expires_at)
            wrong = ActionLease("intruder", "operation", lease.lease_id, lease.expires_at)
            safe_failures = 0
            released = 0
            results_lock = threading.Lock()

            def correct_release() -> None:
                nonlocal released
                gate.release(lease)
                with results_lock:
                    released += 1

            def reject_stale() -> None:
                nonlocal safe_failures
                try:
                    gate.release(stale)
                except (StaleActionLease, NoActiveLease):
                    with results_lock:
                        safe_failures += 1

            def reject_wrong_owner() -> None:
                nonlocal safe_failures
                try:
                    gate.release(wrong)
                except (WrongLeaseOwner, NoActiveLease):
                    with results_lock:
                        safe_failures += 1

            workers = (correct_release, reject_stale, reject_wrong_owner)
            errors = _run_barrier_workers(
                len(workers),
                lambda index: workers[index](),
            )
            with self.subTest(iteration=iteration):
                self.assertEqual([], errors)
                self.assertEqual(1, released)
                self.assertEqual(2, safe_failures)
                self.assertIsNone(gate.current_lease())

    def test_expired_lease_is_reaped_before_next_acquire(self) -> None:
        gate = ActionGate()
        first = gate.acquire("worker-a", "operation-1", timeout_seconds=2.0, now=10.0)
        self.assertEqual(12.0, first.expires_at)

        with self.assertRaises(ActionLeaseHeld):
            gate.acquire("worker-b", "operation-2", now=11.9)

        second = gate.acquire("worker-b", "operation-2", now=12.0)
        self.assertEqual("worker-b", second.owner)
        self.assertEqual("lease-2", second.lease_id)
        self.assertFalse(gate.reap_expired(12.5))


class RunContextTests(unittest.TestCase):
    def test_context_contains_one_session_and_placeholder_registry(self) -> None:
        config = ConfigSnapshot({"deck": "deck-1"})
        context = RunContext(
            "session-1",
            config=config,
            plugin_registry=["baseline"],
        )

        self.assertEqual("session-1", context.session_id)
        self.assertEqual("session-1", context.correlation_id)
        self.assertEqual(("baseline",), context.plugin_registry)
        self.assertIs(config, context.config)
        self.assertIsInstance(context.stop_token, StopToken)
        self.assertIsInstance(context.action_gate, ActionGate)

    def test_context_stop_token_is_shared_by_the_run(self) -> None:
        context = RunContext("session-1")
        context.stop_token.cancel("shutdown")
        with self.assertRaises(OperationCancelled):
            context.raise_if_cancelled()


if __name__ == "__main__":
    unittest.main()
