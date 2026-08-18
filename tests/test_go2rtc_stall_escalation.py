"""Unit tests for the go2rtc bytes-stall escalation state machine.

Tests the pure function ``check_bytes_stall`` in
``app/wyzebridge/go2rtc_sidecar_helpers.py`` which decides whether to
do nothing, restart a single alias, or escalate to a full go2rtc
process restart after repeated bytes-stall failures.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPERS = ROOT / "app" / "wyzebridge"
sys.path.insert(0, str(HELPERS))

from go2rtc_sidecar_helpers import check_bytes_stall, quarantine_peer  # noqa: E402


class TestBytesStallEscalation(unittest.TestCase):
    """Verify the stall-escalation state machine behaves correctly."""

    # ── Healthy stream ───────────────────────────────────────────────

    def test_healthy_stream_no_action(self):
        """Bytes increasing → action is 'none', state resets."""
        state = {"prev_bytes": 1000, "stall_since": 0, "restart_count": 0}
        new_state, action, log = check_bytes_stall("cam", 2000, 100, state)
        self.assertEqual(action, "none")
        self.assertEqual(new_state["prev_bytes"], 2000)
        self.assertEqual(new_state["stall_since"], 0)
        self.assertEqual(new_state["restart_count"], 0)
        self.assertIsNone(log)

    def test_first_seen_no_action(self):
        """First time seeing the stream (prev_bytes=-1) → no action."""
        state = {"prev_bytes": -1, "stall_since": 0, "restart_count": 0}
        new_state, action, log = check_bytes_stall("cam", 500, 100, state)
        self.assertEqual(action, "none")
        self.assertEqual(new_state["prev_bytes"], 500)
        self.assertEqual(new_state["restart_count"], 0)
        self.assertIsNone(log)

    # ── Stall detection ──────────────────────────────────────────────

    def test_first_stall_cycle_silent(self):
        """Bytes stop increasing → first cycle records stall, no action."""
        state = {"prev_bytes": 1000, "stall_since": 0, "restart_count": 0}
        new_state, action, log = check_bytes_stall("cam", 1000, 100, state)
        self.assertEqual(action, "none")
        self.assertEqual(new_state["stall_since"], 100)
        self.assertEqual(new_state["restart_count"], 0)
        self.assertIsNone(log)

    def test_stall_within_timeout_no_action(self):
        """Stall not yet exceeded 45s → keep waiting."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 0}
        new_state, action, log = check_bytes_stall("cam", 1000, 130, state)
        self.assertEqual(action, "none")
        self.assertIsNone(log)

    # ── Alias restart ────────────────────────────────────────────────

    def test_stall_timeout_triggers_alias_restart(self):
        """Stall exceeds 45s → first alias restart (1/3)."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 0}
        new_state, action, log = check_bytes_stall("cam", 1000, 150, state)
        self.assertEqual(action, "restart_alias")
        self.assertEqual(new_state["restart_count"], 1)
        self.assertEqual(new_state["stall_since"], 0)
        self.assertIsNotNone(log)
        self.assertIn("alias restart (1/3)", log)

    def test_second_alias_restart(self):
        """Second consecutive stall → second alias restart (2/3)."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 1}
        new_state, action, log = check_bytes_stall("cam", 1000, 150, state)
        self.assertEqual(action, "restart_alias")
        self.assertEqual(new_state["restart_count"], 2)
        self.assertIn("alias restart (2/3)", log)

    def test_third_alias_restart(self):
        """Third consecutive stall → third alias restart (3/3)."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 2}
        new_state, action, log = check_bytes_stall("cam", 1000, 150, state)
        self.assertEqual(action, "restart_alias")
        self.assertEqual(new_state["restart_count"], 3)
        self.assertIn("alias restart (3/3)", log)

    # ── Process restart escalation ───────────────────────────────────

    def test_escalation_after_max_restarts(self):
        """After 3 failed alias restarts, escalate to process restart."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 3}
        new_state, action, log = check_bytes_stall("cam", 1000, 150, state)
        self.assertEqual(action, "restart_process")
        self.assertEqual(new_state["restart_count"], 0)
        self.assertIsNotNone(log)
        self.assertIn("escalating to process restart", log)

    # ── Recovery resets escalation ───────────────────────────────────

    def test_recovery_resets_restart_count(self):
        """Bytes increase after alias restarts → restart_count resets to 0."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 2}
        new_state, action, log = check_bytes_stall("cam", 5000, 200, state)
        self.assertEqual(action, "none")
        self.assertEqual(new_state["restart_count"], 0)
        self.assertEqual(new_state["stall_since"], 0)

    def test_recovery_logs_after_long_stall(self):
        """Recovery after >30s stall logs the recovery message."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 1}
        new_state, action, log = check_bytes_stall("cam", 5000, 200, state)
        self.assertEqual(action, "none")
        self.assertIsNotNone(log)
        self.assertIn("bytes flowing again", log)

    def test_recovery_silent_after_short_stall(self):
        """Recovery after <30s stall doesn't log (avoid noise)."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 0}
        new_state, action, log = check_bytes_stall("cam", 2000, 115, state)
        self.assertEqual(action, "none")
        self.assertIsNone(log)

    # ── Full simulation: repeated stalls → escalation → recovery ─────

    def test_full_simulation_escalation_then_recovery(self):
        """Simulate the exact live scenario: TUTK wedge with repeated stalls.

        After each alias restart, stall_since resets to 0, so the next
        same-byte sample starts a *new* silent stall window that must
        run for >45s (strictly greater) before the next restart triggers.

        Timeline (15s cycles):
          t=0:   bytes=1000, first seen
          t=15:  bytes=1000, stall starts (stall_since=15)
          t=61:  bytes=1000, stall >45s → alias restart 1/3 (stall_since→0)
          t=76:  bytes=1000, new stall starts (stall_since=76)
          t=122: bytes=1000, stall >45s → alias restart 2/3 (stall_since→0)
          t=137: bytes=1000, new stall starts (stall_since=137)
          t=183: bytes=1000, stall >45s → alias restart 3/3 (stall_since→0)
          t=198: bytes=1000, new stall starts (stall_since=198)
          t=244: bytes=1000, stall >45s → PROCESS RESTART
          t=259: bytes=5000, recovered → reset
        """
        state = {"prev_bytes": -1, "stall_since": 0, "restart_count": 0}
        actions = []

        # t=0: first seen
        state, action, _ = check_bytes_stall("cam", 1000, 0, state)
        actions.append(action)
        self.assertEqual(action, "none")

        # t=15: stall begins
        state, action, _ = check_bytes_stall("cam", 1000, 15, state)
        actions.append(action)
        self.assertEqual(action, "none")

        # t=61: stall >45s (61-15=46) → restart 1/3
        state, action, log = check_bytes_stall("cam", 1000, 61, state)
        actions.append(action)
        self.assertEqual(action, "restart_alias")
        self.assertEqual(state["restart_count"], 1)
        self.assertEqual(state["stall_since"], 0)

        # t=76: new stall window starts (restart didn't help)
        state, action, _ = check_bytes_stall("cam", 1000, 76, state)
        actions.append(action)
        self.assertEqual(action, "none")
        self.assertEqual(state["stall_since"], 76)

        # t=122: stall >45s (122-76=46) → restart 2/3
        state, action, log = check_bytes_stall("cam", 1000, 122, state)
        actions.append(action)
        self.assertEqual(action, "restart_alias")
        self.assertEqual(state["restart_count"], 2)

        # t=137: new stall window starts
        state, action, _ = check_bytes_stall("cam", 1000, 137, state)
        actions.append(action)
        self.assertEqual(action, "none")
        self.assertEqual(state["stall_since"], 137)

        # t=183: stall >45s (183-137=46) → restart 3/3
        state, action, log = check_bytes_stall("cam", 1000, 183, state)
        actions.append(action)
        self.assertEqual(action, "restart_alias")
        self.assertEqual(state["restart_count"], 3)

        # t=198: new stall window starts
        state, action, _ = check_bytes_stall("cam", 1000, 198, state)
        actions.append(action)
        self.assertEqual(action, "none")
        self.assertEqual(state["stall_since"], 198)

        # t=244: stall >45s (244-198=46) → PROCESS RESTART
        state, action, log = check_bytes_stall("cam", 1000, 244, state)
        actions.append(action)
        self.assertEqual(action, "restart_process")
        self.assertIn("escalating to process restart", log)

        # t=259: bytes increase → recovery, state resets
        state, action, log = check_bytes_stall("cam", 5000, 259, state)
        actions.append(action)
        self.assertEqual(action, "none")
        self.assertEqual(state["restart_count"], 0)
        self.assertEqual(state["stall_since"], 0)

        # Verify the full action sequence
        self.assertEqual(
            actions,
            [
                "none",  # t=0
                "none",  # t=15
                "restart_alias",  # t=61  (1/3)
                "none",  # t=76  (new stall window)
                "restart_alias",  # t=122 (2/3)
                "none",  # t=137 (new stall window)
                "restart_alias",  # t=183 (3/3)
                "none",  # t=198 (new stall window)
                "restart_process",  # t=244 (escalation)
                "none",  # t=259 (recovery)
            ],
        )

    # ── Custom parameters ────────────────────────────────────────────

    def test_custom_max_restarts(self):
        """max_restarts=1 escalates after just one failed alias restart."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 0}
        _, action, _ = check_bytes_stall("cam", 1000, 150, state, max_restarts=1)
        self.assertEqual(action, "restart_alias")

        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 1}
        _, action, _ = check_bytes_stall("cam", 1000, 150, state, max_restarts=1)
        self.assertEqual(action, "restart_process")

    def test_custom_timeout(self):
        """alias_timeout=30 triggers restart sooner than default 45."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 0}
        _, action, _ = check_bytes_stall("cam", 1000, 135, state, alias_timeout=30)
        self.assertEqual(action, "restart_alias")


class TestQuarantineBackoff(unittest.TestCase):
    """Verify per-alias quarantine after process restart escalation."""

    def test_process_restart_sets_quarantine(self):
        """After 3 failed alias restarts, process restart quarantines the alias."""
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 3}
        new_state, action, log = check_bytes_stall("cam", 1000, 200, state)
        self.assertEqual(action, "restart_process")
        self.assertGreater(new_state["quarantined_until"], 200)
        self.assertEqual(new_state["quarantine_count"], 1)
        self.assertEqual(new_state["restart_count"], 0)
        self.assertIn("quarantine", log)

    def test_quarantined_alias_skipped(self):
        """While quarantined, the helper returns 'none' regardless of bytes."""
        state = {
            "prev_bytes": 1000,
            "stall_since": 100,
            "restart_count": 2,
            "quarantined_until": 500,
            "quarantine_count": 1,
        }
        # now=300 < quarantined_until=500 → skip
        new_state, action, log = check_bytes_stall("cam", 1000, 300, state)
        self.assertEqual(action, "none")
        self.assertIsNone(log)
        # Quarantine state preserved
        self.assertEqual(new_state["quarantined_until"], 500)
        self.assertEqual(new_state["quarantine_count"], 1)
        # Volatile state reset during quarantine
        self.assertEqual(new_state["restart_count"], 0)
        self.assertEqual(new_state["stall_since"], 0)

    def test_quarantine_recovery_clears_quarantine(self):
        """After quarantine expires, bytes increasing clears quarantine."""
        state = {
            "prev_bytes": 1000,
            "stall_since": 0,
            "restart_count": 0,
            "quarantined_until": 500,
            "quarantine_count": 1,
        }
        # now=600 > quarantined_until=500, bytes increased → recovery
        new_state, action, log = check_bytes_stall("cam", 5000, 600, state)
        self.assertEqual(action, "none")
        self.assertEqual(new_state["quarantined_until"], 0)
        self.assertEqual(new_state["quarantine_count"], 0)
        self.assertIn("recovered after quarantine", log)

    def test_quarantine_expired_still_stalled_re_escalates(self):
        """After quarantine expires with no recovery, a new stall cycle begins."""
        state = {
            "prev_bytes": 1000,
            "stall_since": 0,
            "restart_count": 0,
            "quarantined_until": 500,
            "quarantine_count": 1,
        }
        # now=600 > quarantined_until=500, bytes still 1000
        # Should start a new stall cycle (stall_since=600, action=none)
        new_state, action, _ = check_bytes_stall("cam", 1000, 600, state)
        self.assertEqual(action, "none")
        self.assertEqual(new_state["stall_since"], 600)
        self.assertEqual(new_state["quarantined_until"], 0)
        self.assertEqual(new_state["quarantine_count"], 1)  # preserved

    def test_quarantine_exponential_backoff(self):
        """Each successive quarantine doubles the duration, capped at 3600."""
        # First escalation: 300s quarantine
        state = {"prev_bytes": 1000, "stall_since": 100, "restart_count": 3}
        new_state, _, _ = check_bytes_stall("cam", 1000, 200, state)
        self.assertEqual(new_state["quarantined_until"], 200 + 300)
        self.assertEqual(new_state["quarantine_count"], 1)

        # Second escalation: 600s quarantine
        state = {
            "prev_bytes": 1000,
            "stall_since": 100,
            "restart_count": 3,
            "quarantined_until": 0,
            "quarantine_count": 1,
        }
        new_state, _, _ = check_bytes_stall("cam", 1000, 600, state)
        self.assertEqual(new_state["quarantined_until"], 600 + 600)
        self.assertEqual(new_state["quarantine_count"], 2)

        # Third escalation: 1200s quarantine
        state = {
            "prev_bytes": 1000,
            "stall_since": 100,
            "restart_count": 3,
            "quarantined_until": 0,
            "quarantine_count": 2,
        }
        new_state, _, _ = check_bytes_stall("cam", 1000, 1200, state)
        self.assertEqual(new_state["quarantined_until"], 1200 + 1200)
        self.assertEqual(new_state["quarantine_count"], 3)

        # Fourth escalation: 2400s quarantine
        state = {
            "prev_bytes": 1000,
            "stall_since": 100,
            "restart_count": 3,
            "quarantined_until": 0,
            "quarantine_count": 3,
        }
        new_state, _, _ = check_bytes_stall("cam", 1000, 2400, state)
        self.assertEqual(new_state["quarantined_until"], 2400 + 2400)
        self.assertEqual(new_state["quarantine_count"], 4)

        # Fifth escalation: capped at 3600s
        state = {
            "prev_bytes": 1000,
            "stall_since": 100,
            "restart_count": 3,
            "quarantined_until": 0,
            "quarantine_count": 4,
        }
        new_state, _, _ = check_bytes_stall("cam", 1000, 5000, state)
        self.assertEqual(new_state["quarantined_until"], 5000 + 3600)
        self.assertEqual(new_state["quarantine_count"], 5)

    def test_dead_camera_does_not_flap_healthy_cameras(self):
        """Full simulation: dead camera quarantined after one escalation cycle.

        A permanently dead camera should trigger exactly ONE process restart,
        then be quarantined.  While quarantined it returns 'none' and cannot
        trigger another process restart, so healthy cameras are not flapped.
        """
        state = {"prev_bytes": -1, "stall_since": 0, "restart_count": 0}
        actions = []

        # Escalation cycle: 3 alias restarts → process restart
        # t=0: first seen
        state, a, _ = check_bytes_stall("dead", 0, 0, state)
        actions.append(a)

        # t=15: stall begins
        state, a, _ = check_bytes_stall("dead", 0, 15, state)
        actions.append(a)

        # t=61: stall >45s → restart 1/3
        state, a, _ = check_bytes_stall("dead", 0, 61, state)
        actions.append(a)

        # t=76: new stall window
        state, a, _ = check_bytes_stall("dead", 0, 76, state)
        actions.append(a)

        # t=122: restart 2/3
        state, a, _ = check_bytes_stall("dead", 0, 122, state)
        actions.append(a)

        # t=137: new stall window
        state, a, _ = check_bytes_stall("dead", 0, 137, state)
        actions.append(a)

        # t=183: restart 3/3
        state, a, _ = check_bytes_stall("dead", 0, 183, state)
        actions.append(a)

        # t=198: new stall window
        state, a, _ = check_bytes_stall("dead", 0, 198, state)
        actions.append(a)

        # t=244: PROCESS RESTART + quarantine (300s, until t=544)
        state, a, log = check_bytes_stall("dead", 0, 244, state)
        actions.append(a)
        self.assertEqual(a, "restart_process")
        self.assertEqual(state["quarantined_until"], 244 + 300)
        self.assertEqual(state["quarantine_count"], 1)

        # t=300: still quarantined (300 < 544) → 'none', no flap
        state, a, _ = check_bytes_stall("dead", 0, 300, state)
        actions.append(a)
        self.assertEqual(a, "none")

        # t=400: still quarantined → 'none'
        state, a, _ = check_bytes_stall("dead", 0, 400, state)
        actions.append(a)
        self.assertEqual(a, "none")

        # t=544: quarantine expired, bytes still 0 → new stall cycle starts
        state, a, _ = check_bytes_stall("dead", 0, 544, state)
        actions.append(a)
        self.assertEqual(a, "none")
        self.assertEqual(state["stall_since"], 544)
        self.assertEqual(state["quarantine_count"], 1)  # preserved

        # Verify: only ONE process restart in the entire sequence
        self.assertEqual(actions.count("restart_process"), 1)
        self.assertEqual(
            actions,
            [
                "none",  # t=0
                "none",  # t=15
                "restart_alias",  # t=61  (1/3)
                "none",  # t=76
                "restart_alias",  # t=122 (2/3)
                "none",  # t=137
                "restart_alias",  # t=183 (3/3)
                "none",  # t=198
                "restart_process",  # t=244 (escalation + quarantine)
                "none",  # t=300 (quarantined)
                "none",  # t=400 (quarantined)
                "none",  # t=544 (quarantine expired, new stall)
            ],
        )


class TestQuarantinePeer(unittest.TestCase):
    """Verify peer quarantine when another alias triggers a process restart."""

    def test_peer_at_max_restarts_gets_quarantined(self):
        """A peer alias at restart_count >= max_restarts is quarantined."""
        state = {"prev_bytes": 0, "stall_since": 100, "restart_count": 3}
        result = quarantine_peer(state, 200)
        self.assertIsNotNone(result)
        self.assertEqual(result["quarantined_until"], 200 + 300)
        self.assertEqual(result["quarantine_count"], 1)

    def test_peer_below_max_restarts_not_quarantined(self):
        """A peer alias below max_restarts is NOT quarantined."""
        state = {"prev_bytes": 0, "stall_since": 100, "restart_count": 2}
        result = quarantine_peer(state, 200)
        self.assertIsNone(result)

    def test_peer_with_prior_quarantine_uses_backoff(self):
        """A peer that was previously quarantined gets exponential backoff."""
        state = {"prev_bytes": 0, "stall_since": 100, "restart_count": 3, "quarantined_until": 0, "quarantine_count": 1}
        result = quarantine_peer(state, 200)
        self.assertIsNotNone(result)
        self.assertEqual(result["quarantined_until"], 200 + 600)
        self.assertEqual(result["quarantine_count"], 2)

    def test_peer_no_quarantine_file_uses_first_backoff(self):
        """A peer with no prior quarantine file (count=0) gets 300s."""
        state = {"prev_bytes": 0, "stall_since": 100, "restart_count": 3}
        result = quarantine_peer(state, 200)
        self.assertIsNotNone(result)
        self.assertEqual(result["quarantined_until"], 200 + 300)
        self.assertEqual(result["quarantine_count"], 1)

    def test_multiple_dead_peers_all_quarantined(self):
        """When one alias triggers process restart, all peers at max_restarts
        get quarantined in the same cycle — no serial flapping."""
        peers = [
            {"prev_bytes": 0, "stall_since": 100, "restart_count": 3},
            {"prev_bytes": 0, "stall_since": 100, "restart_count": 3},
            {"prev_bytes": 0, "stall_since": 100, "restart_count": 2},  # healthy
        ]
        results = [quarantine_peer(s, 200) for s in peers]
        self.assertIsNotNone(results[0])
        self.assertIsNotNone(results[1])
        self.assertIsNone(results[2])  # below max_restarts
        # Both dead peers quarantined for 300s
        self.assertEqual(results[0]["quarantined_until"], 200 + 300)
        self.assertEqual(results[1]["quarantined_until"], 200 + 300)


if __name__ == "__main__":
    unittest.main()
