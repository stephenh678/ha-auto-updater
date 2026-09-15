"""Tests for run bookkeeping: verification, interrupted runs, entity-keyed failures."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.ha_auto_updater import coordinator as coord_mod
from custom_components.ha_auto_updater.coordinator import AutoUpdaterCoordinator
from custom_components.ha_auto_updater.const import (
    CONF_BACKUP_BEFORE_UPDATE,
    CONF_PRE_NOTIFY_DELAY,
    CONF_STAGGER_DELAY,
)


def _make_coord(options=None):
    hass = MagicMock()
    hass.config.config_dir = "."
    hass.config.safe_mode = False
    hass.config.path.side_effect = lambda name: name
    hass.services.async_call = AsyncMock()
    hass.async_add_executor_job = AsyncMock()
    hass.async_create_task.side_effect = lambda coro: coro.close()
    entry = MagicMock()
    entry.data = {}
    entry.options = options or {}
    return AutoUpdaterCoordinator(hass, entry), hass


def _state(state, **attrs):
    st = MagicMock()
    st.state = state
    st.attributes = attrs
    return st


class TestEntityKeyedFailures(unittest.TestCase):

    def test_consecutive_failures_prefers_entity_id(self):
        coord, _ = _make_coord()
        coord.history = [
            {"updated": [], "failed": ["Shelly Plug"],
             "updated_entities": [], "failed_entities": [{"entity_id": "update.plug_a", "title": "Shelly Plug"}]},
            {"updated": [], "failed": ["Shelly Plug"],
             "updated_entities": [], "failed_entities": [{"entity_id": "update.plug_b", "title": "Shelly Plug"}]},
            {"updated": [], "failed": ["Shelly Plug"],
             "updated_entities": [], "failed_entities": [{"entity_id": "update.plug_a", "title": "Shelly Plug"}]},
        ]
        # Two devices share a title — counts must not bleed into each other
        self.assertEqual(coord._consecutive_failures("Shelly Plug", "update.plug_a"), 2)
        self.assertEqual(coord._consecutive_failures("Shelly Plug", "update.plug_b"), 1)
        # Title-only lookup (legacy behaviour) still counts every entry
        self.assertEqual(coord._consecutive_failures("Shelly Plug"), 3)

    def test_consecutive_failures_success_breaks_streak(self):
        coord, _ = _make_coord()
        coord.history = [
            {"updated": [], "failed": ["X"],
             "updated_entities": [], "failed_entities": [{"entity_id": "update.x", "title": "X"}]},
            {"updated": ["X (1 → 2)"], "failed": [],
             "updated_entities": [{"entity_id": "update.x", "title": "X"}], "failed_entities": []},
            {"updated": [], "failed": ["X"],
             "updated_entities": [], "failed_entities": [{"entity_id": "update.x", "title": "X"}]},
            # legacy entry without entity ids is still honoured
            {"updated": [], "failed": ["X"]},
        ]
        self.assertEqual(coord._consecutive_failures("X", "update.x"), 2)

    def test_build_history_entry_shape(self):
        now = datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc)
        entry = AutoUpdaterCoordinator._build_history_entry(
            now,
            [{"entity_id": "update.a", "title": "A", "from": "1", "to": "2"}],
            [{"entity_id": "update.b", "title": "B", "from": "1", "to": "3"}],
            42,
            triggered=[{"entity_id": "update.home_assistant_core", "title": "Core", "from": "2026.8", "to": "2026.9"}],
            deferred=[{"entity_id": "update.home_assistant_operating_system", "title": "OS"}],
            note="n",
        )
        self.assertEqual(entry["updated"], ["A (1 → 2)"])
        self.assertEqual(entry["failed"], ["B"])
        self.assertEqual(entry["updated_entities"][0]["entity_id"], "update.a")
        self.assertEqual(entry["failed_entities"][0]["entity_id"], "update.b")
        self.assertEqual(entry["total_updated"], 1)
        self.assertEqual(entry["total_failed"], 1)
        self.assertEqual(entry["pending_verification"], ["Core (2026.8 → 2026.9)"])
        self.assertEqual(entry["deferred"], ["OS"])
        self.assertEqual(entry["note"], "n")

    def test_load_history_restores_failed_entity_ids(self):
        coord, hass = _make_coord()
        recent = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        coord._read_json_file = AsyncMock(return_value=[{
            "timestamp": recent,
            "updated": [], "failed": ["B"],
            "failed_entities": [{"entity_id": "update.b", "title": "B"}],
            "total_updated": 0, "total_failed": 1, "duration_seconds": 1,
        }])
        asyncio.run(coord._load_history())
        self.assertEqual(coord.failed_updates, [{"title": "B", "entity_id": "update.b"}])


class TestFirmwareDetection(unittest.TestCase):

    def _source_for(self, platform, device_class):
        coord, hass = _make_coord()
        registry = MagicMock()
        reg_entry = MagicMock()
        reg_entry.platform = platform
        registry.async_get.return_value = reg_entry
        hass.states.get.return_value = _state("on", device_class=device_class)
        with patch.object(coord_mod.er, "async_get", return_value=registry):
            return coord._get_update_source("update.some_device")

    def test_device_class_firmware_wins_for_unknown_platform(self):
        self.assertEqual(self._source_for("mqtt", "firmware"), "Firmware")
        self.assertEqual(self._source_for("shelly", "firmware"), "Firmware")

    def test_known_firmware_platform_without_device_class(self):
        self.assertEqual(self._source_for("esphome", None), "Firmware")
        self.assertEqual(self._source_for("zwave_js", None), "Firmware")

    def test_other_platforms(self):
        self.assertEqual(self._source_for("hassio", "firmware"), "Add-on")
        self.assertEqual(self._source_for("hacs", None), "HACS")
        self.assertEqual(self._source_for("some_cloud_thing", None), "Custom")


class TestSystemUpdateVerification(unittest.TestCase):

    def test_watch_returns_success_when_version_lands(self):
        coord, hass = _make_coord()
        hass.states.get.side_effect = [
            _state("on", installed_version="2026.8.0", latest_version="2026.9.0"),
            _state("on", installed_version="2026.9.0", latest_version="2026.9.0"),
        ]
        with patch.object(coord_mod, "SYSTEM_UPDATE_POLL_SECONDS", 0):
            outcome = asyncio.run(coord._watch_system_update("update.home_assistant_core", "2026.9.0"))
        self.assertEqual(outcome, "success")

    def test_watch_returns_pending_when_entity_vanishes(self):
        coord, hass = _make_coord()
        hass.states.get.return_value = None
        outcome = asyncio.run(coord._watch_system_update("update.home_assistant_core", "2026.9.0"))
        self.assertEqual(outcome, "pending")

    def test_watch_returns_pending_on_timeout(self):
        coord, hass = _make_coord()
        hass.states.get.return_value = _state("on", installed_version="1", latest_version="2", in_progress=False)
        with patch.object(coord_mod, "SYSTEM_UPDATE_POLL_SECONDS", 0), \
             patch.object(coord_mod, "SYSTEM_UPDATE_WATCH_SECONDS", 0):
            outcome = asyncio.run(coord._watch_system_update("update.home_assistant_core", "2"))
        self.assertEqual(outcome, "pending")

    def test_verify_pending_resolves_success_and_failure(self):
        coord, hass = _make_coord()
        coord._append_and_save_history = AsyncMock()
        coord._save_run_state = AsyncMock()
        coord._handle_failures = AsyncMock()
        coord._send_success_notification = MagicMock()
        ts = datetime.now(timezone.utc).isoformat()
        coord._pending_verification = [
            {"entity_id": "update.home_assistant_core", "title": "Core", "from": "1", "to": "2", "triggered_at": ts},
            {"entity_id": "update.home_assistant_operating_system", "title": "OS", "from": "10", "to": "11", "triggered_at": ts},
            {"entity_id": "update.home_assistant_supervisor", "title": "Sup", "from": "5", "to": "6", "triggered_at": ts},
            {"entity_id": "update.addon_x", "title": "X", "from": "1.0", "to": "1.1", "triggered_at": ts},
        ]
        states = {
            "update.home_assistant_core": _state("off", installed_version="2", latest_version="2"),
            "update.home_assistant_operating_system": _state("on", installed_version="10", latest_version="11", in_progress=False),
            "update.home_assistant_supervisor": None,  # not loaded yet
            # Landed on a newer release than the one we triggered: still a success
            "update.addon_x": _state("on", installed_version="1.2", latest_version="1.3", in_progress=False),
        }
        hass.states.get.side_effect = lambda eid: states[eid]

        asyncio.run(coord._async_verify_pending())

        entry = coord._append_and_save_history.call_args.args[0]
        self.assertEqual(
            [u["entity_id"] for u in entry["updated_entities"]],
            ["update.home_assistant_core", "update.addon_x"],
        )
        self.assertEqual(entry["failed_entities"][0]["entity_id"], "update.home_assistant_operating_system")
        self.assertEqual([p["entity_id"] for p in coord._pending_verification], ["update.home_assistant_supervisor"])
        coord._handle_failures.assert_awaited_once()
        coord._send_success_notification.assert_called_once()
        self.assertEqual(coord.failed_updates[0]["entity_id"], "update.home_assistant_operating_system")

    def test_verify_pending_noop_when_empty(self):
        coord, _ = _make_coord()
        coord._append_and_save_history = AsyncMock()
        asyncio.run(coord._async_verify_pending())
        coord._append_and_save_history.assert_not_awaited()


class TestInterruptedRun(unittest.TestCase):

    def test_finalize_marker_reconstructs_history(self):
        coord, _ = _make_coord()
        coord._append_and_save_history = AsyncMock()
        coord._save_run_state = AsyncMock()
        coord._handle_failures = AsyncMock()
        marker = {
            "started": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "items": [
                {"entity_id": "update.a", "title": "A"},
                {"entity_id": "update.b", "title": "B"},
                {"entity_id": "update.c", "title": "C"},
                {"entity_id": "update.d", "title": "D"},
            ],
            "updated": [{"entity_id": "update.a", "title": "A", "from": "1", "to": "2"}],
            "failed": [{"entity_id": "update.b", "title": "B", "from": "1", "to": "2"}],
            "triggered": [],
            "deferred": [],
            "current": {"entity_id": "update.c", "title": "C", "from": "1", "to": "2"},
        }
        remaining = asyncio.run(
            coord._async_finalize_run_marker(note="interrupted", marker=marker)
        )
        self.assertEqual([r["entity_id"] for r in remaining], ["update.d"])
        entry = coord._append_and_save_history.call_args.args[0]
        self.assertEqual(entry["total_updated"], 1)
        self.assertEqual(entry["total_failed"], 1)
        self.assertEqual(entry["deferred"], ["D"])
        self.assertEqual(entry["note"], "interrupted")
        # The item that was mid-install is verified later, not guessed
        self.assertEqual(coord._pending_verification[0]["entity_id"], "update.c")
        self.assertEqual(coord.last_run_status, "Interrupted")
        self.assertEqual(coord.failed_updates, [{"title": "B", "entity_id": "update.b"}])
        coord._handle_failures.assert_awaited_once()
        coord._save_run_state.assert_awaited_with(None)
        # Never-attempted items are queued for the follow-up pass
        self.assertEqual([d["entity_id"] for d in coord._deferred], ["update.d"])

    def test_reconcile_without_marker_is_noop(self):
        coord, _ = _make_coord()
        coord._load_run_state = AsyncMock(return_value=None)
        coord._async_finalize_run_marker = AsyncMock()
        asyncio.run(coord._async_reconcile_interrupted_run())
        coord._async_finalize_run_marker.assert_not_awaited()

    def test_reconcile_schedules_resume_for_remaining(self):
        coord, _ = _make_coord()
        coord._load_run_state = AsyncMock(return_value={"items": []})

        async def _finalize(note, marker=None):
            coord._deferred = [{"entity_id": "update.d", "title": "D"}]
            return coord._deferred

        coord._async_finalize_run_marker = AsyncMock(side_effect=_finalize)
        coord._schedule_resume_run = MagicMock()
        asyncio.run(coord._async_reconcile_interrupted_run())
        coord._schedule_resume_run.assert_called_once_with([{"entity_id": "update.d", "title": "D"}])

    def test_reconcile_resumes_persisted_deferred_without_marker(self):
        """A completed run that deferred items, then HA restarted: the queue survives."""
        coord, _ = _make_coord()
        coord._read_json_file = AsyncMock(return_value={
            "run": None,
            "pending_verification": [],
            "deferred": [{"entity_id": "update.home_assistant_operating_system", "title": "OS"}],
        })
        coord._async_finalize_run_marker = AsyncMock()
        coord._schedule_resume_run = MagicMock()
        asyncio.run(coord._async_reconcile_interrupted_run())
        coord._async_finalize_run_marker.assert_not_awaited()
        coord._schedule_resume_run.assert_called_once_with(
            [{"entity_id": "update.home_assistant_operating_system", "title": "OS"}]
        )

    def test_resume_callback_honours_enabled_and_running(self):
        coord, _ = _make_coord({"enabled": False})
        captured = {}

        def _fake_call_later(hass, delay, action):
            captured["action"] = action
            return MagicMock()

        coord.async_run_updates = AsyncMock()
        deferred = [{"entity_id": "update.d", "title": "D"}]
        with patch.object(coord_mod, "async_call_later", side_effect=_fake_call_later):
            # Disabled at schedule time: nothing is armed at all
            coord._schedule_resume_run(deferred)
            self.assertNotIn("action", captured)

            coord.entry.options = {"enabled": True}
            coord._schedule_resume_run(deferred)
            action = captured["action"]

            # Disabled after scheduling: callback must not run updates
            coord.entry.options = {"enabled": False}
            asyncio.run(action(None))
            coord.async_run_updates.assert_not_awaited()

            # Another run in flight: re-armed instead of dropped
            coord.entry.options = {"enabled": True}
            coord._is_running = True
            captured.clear()
            asyncio.run(action(None))
            coord.async_run_updates.assert_not_awaited()
            self.assertIn("action", captured)

            # Normal path
            coord._is_running = False
            asyncio.run(action(None))
            coord.async_run_updates.assert_awaited_once_with(resume=True)

    def test_load_run_state_merges_pending_verification(self):
        coord, _ = _make_coord()
        coord._pending_verification = [{"entity_id": "update.x"}]
        coord._read_json_file = AsyncMock(return_value={
            "run": None,
            "pending_verification": [{"entity_id": "update.x"}, {"entity_id": "update.y"}],
        })
        marker = asyncio.run(coord._load_run_state())
        self.assertIsNone(marker)
        self.assertEqual([p["entity_id"] for p in coord._pending_verification], ["update.x", "update.y"])


class TestRunLoopSystemUpdates(unittest.TestCase):

    def test_system_update_defers_rest_and_records_pending(self):
        coord, hass = _make_coord({
            CONF_BACKUP_BEFORE_UPDATE: False,
            CONF_PRE_NOTIFY_DELAY: 0,
            CONF_STAGGER_DELAY: 0,
        })
        addon = _state("on", title="SSH", installed_version="1.0", latest_version="1.1", restart_required=False)
        addon.entity_id = "update.addon_ssh"
        core = _state("on", title="Core", installed_version="2026.8.0", latest_version="2026.9.0")
        core.entity_id = "update.home_assistant_core"
        os_ = _state("on", title="OS", installed_version="16.0", latest_version="16.1")
        os_.entity_id = "update.home_assistant_operating_system"
        hass.states.async_all.return_value = [core, addon, os_]
        hass.states.get.side_effect = lambda eid: {
            "update.addon_ssh": addon, "update.home_assistant_core": core,
            "update.home_assistant_operating_system": os_,
        }[eid]

        coord._save_run_state = AsyncMock()
        coord._append_and_save_history = AsyncMock()
        coord._watch_system_update = AsyncMock(return_value="pending")
        coord._schedule_resume_run = MagicMock()
        coord._handle_failures = AsyncMock()

        asyncio.run(coord._async_run_updates_inner())

        install_calls = [
            c.args[2]["entity_id"] for c in hass.services.async_call.call_args_list
            if len(c.args) >= 2 and c.args[0] == "update" and c.args[1] == "install"
        ]
        # Add-on first, then core; OS never attempted this run
        self.assertEqual(install_calls, ["update.addon_ssh", "update.home_assistant_core"])
        entry = coord._append_and_save_history.call_args.args[0]
        self.assertEqual(entry["updated"], ["SSH (1.0 → 1.1)"])
        self.assertEqual(entry["pending_verification"], ["Core (2026.8.0 → 2026.9.0)"])
        self.assertEqual(entry["deferred"], ["OS"])
        self.assertEqual(coord._pending_verification[0]["entity_id"], "update.home_assistant_core")
        coord._schedule_resume_run.assert_called_once()
        self.assertEqual(coord.last_run_status, "Success (deferred)")
        # Marker cleared at end of run, deferred queue persisted for after a restart
        coord._save_run_state.assert_awaited_with(None)
        self.assertEqual([d["entity_id"] for d in coord._deferred], ["update.home_assistant_operating_system"])
        # No explicit HA restart when a system update was triggered
        restart_calls = [
            c for c in hass.services.async_call.call_args_list
            if len(c.args) >= 2 and c.args[0] == "homeassistant" and c.args[1] == "restart"
        ]
        self.assertEqual(restart_calls, [])

    def test_failed_item_goes_to_handle_failures_with_entity_id(self):
        coord, hass = _make_coord({
            CONF_BACKUP_BEFORE_UPDATE: False,
            CONF_PRE_NOTIFY_DELAY: 0,
            CONF_STAGGER_DELAY: 0,
            "retry_delay": 0,
        })
        addon = _state("on", title="Broken", installed_version="1.0", latest_version="1.1")
        addon.entity_id = "update.addon_broken"
        hass.states.async_all.return_value = [addon]
        hass.states.get.return_value = addon
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("boom"))
        coord._save_run_state = AsyncMock()
        coord._append_and_save_history = AsyncMock()
        coord._handle_failures = AsyncMock()

        asyncio.run(coord._async_run_updates_inner())

        coord._handle_failures.assert_awaited_once()
        failed = coord._handle_failures.call_args.args[0]
        self.assertEqual(failed[0]["entity_id"], "update.addon_broken")
        self.assertEqual(coord.failed_updates, [{"title": "Broken", "entity_id": "update.addon_broken"}])
        self.assertEqual(coord.last_run_status, "All failed")


if __name__ == "__main__":
    unittest.main()
