"""Meta-tests for the MPC persistent-worker harness (PartyPool).

Verifies: normal completion, crash detection, exception propagation,
timeout, channel busy protection, and auto-rebuild after failures.
"""

import os
import time

import pytest

from conftest import (
    PartyPool, PartyCrashError, PartyTimeoutError,
    _setup_add2, _setup_rep3,
)


class TestNormalCompletion:
    """Normal path — all workers finish cleanly."""

    def test_small_result_add2(self):
        with PartyPool(2, _setup_add2, protocol="add2", timeout=10) as pool:
            results = pool.run(lambda pid, channels: f"ok_from_{pid}")
            assert results == ["ok_from_0", "ok_from_1"]

    def test_none_result(self):
        """None is a legitimate return value."""
        with PartyPool(2, _setup_add2, protocol="add2", timeout=10) as pool:
            results = pool.run(lambda pid, channels: None)
            assert results == [None, None]

    def test_int_result_rss3(self):
        with PartyPool(3, _setup_rep3, protocol="rss3", timeout=10) as pool:
            results = pool.run(lambda pid, channels: pid * 10)
            assert results == [0, 10, 20]


class TestCrashDetection:
    """Any worker dying unexpectedly → instant PartyCrashError."""

    def test_os_exit_hard_crash(self):
        def party(pid, channels):
            if pid == 0:
                os._exit(1)
            else:
                return "survivor"

        with PartyPool(2, _setup_add2, protocol="add2", timeout=10) as pool:
            start = time.monotonic()
            with pytest.raises(PartyCrashError, match="Party 0"):
                pool.run(party)
            elapsed = time.monotonic() - start
            assert elapsed < 2.0, f"crash detection took {elapsed:.3f}s"

    def test_sys_exit(self):
        def party(pid, channels):
            if pid == 0:
                import sys
                sys.exit(2)
            else:
                return "survivor"

        with PartyPool(2, _setup_add2, protocol="add2", timeout=10) as pool:
            start = time.monotonic()
            with pytest.raises(PartyCrashError, match="Party 0"):
                pool.run(party)
            elapsed = time.monotonic() - start
            assert elapsed < 2.0


class TestExceptionPropagation:
    """A worker raising an exception → kill all → PartyCrashError."""

    def test_value_error(self):
        def party(pid, channels):
            if pid == 0:
                raise ValueError("test error in party 0")
            else:
                time.sleep(5)  # should be killed before this finishes
                return "survivor"

        with PartyPool(2, _setup_add2, protocol="add2", timeout=10) as pool:
            start = time.monotonic()
            with pytest.raises(PartyCrashError, match="Party 0"):
                pool.run(party)
            elapsed = time.monotonic() - start
            assert elapsed < 2.0, f"exception kill took {elapsed:.3f}s"


class TestTimeout:
    """Deadlock / stuck processes → PartyTimeoutError."""

    def test_deadlock_timeout(self):
        def party(pid, channels):
            time.sleep(30)

        with PartyPool(2, _setup_add2, protocol="add2", timeout=2) as pool:
            with pytest.raises(PartyTimeoutError, match="timed out"):
                pool.run(party)


class TestAutoRebuild:
    """After a crash, pool auto-rebuilds and subsequent tests succeed."""

    def test_rebuild_after_crash(self):
        pool = PartyPool(2, _setup_add2, protocol="add2", timeout=10)
        try:
            # First run: crash
            def crashy(pid, channels):
                if pid == 0:
                    raise RuntimeError("boom")
                return "ok"

            with pytest.raises(PartyCrashError):
                pool.run(crashy)

            # Second run: should auto-rebuild and succeed
            results = pool.run(lambda pid, channels: f"after_rebuild_{pid}")
            assert results == ["after_rebuild_0", "after_rebuild_1"]
        finally:
            pool.shutdown()


class TestChannelReuse:
    """Channels can be reused across multiple protocol instances.

    acquire() is idempotent — it just flushes and resets counters without
    locking.  Multiple protocol instances can be constructed and destroyed
    on the same channels in sequence.
    """

    def test_repeated_construction(self):
        """Construct, use, and destroy RSS3 instances on the same channels
        multiple times — verifies channels are idempotent."""
        def party(pid, channels):
            import mpmt
            ell = 4
            for _ in range(3):
                inst = mpmt.ShrRep3(ell, pid)(channels["prev"], channels["next"])
                # crng uses local correlated randomness, no network
                v = inst.crng()
                # inst goes out of scope → destructor runs → Aby3 destroyed
            return "ok"

        with PartyPool(3, _setup_rep3, protocol="rss3", timeout=10) as pool:
            results = pool.run(party)
            assert results == ["ok", "ok", "ok"]
