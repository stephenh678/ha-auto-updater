"""Regression tests for the full code review (scheduler, titles, deferred queue, retries)."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from custom_components.ha_auto_updater import (
    _async_options_updated,
    async_unload_entry,
)
from custom_components.ha_auto_updater import coordinator as coord_mod
from custom_components.ha_auto_updater.config_flow import _build_schema, _update_entity_map
from custom_components.ha_auto_updater.const import (
    CONF_BACKUP_BEFORE_UPDATE,
    CONF_EXCLUDED_ENTITIES,
    CONF_NOTIFY_SERVICE,
    CONF_PRE_NOTIFY_DELAY,
    DATA_COORDINATOR,
    DOMAIN,
)
from custom_components.ha_auto_updater.coordinator import AutoUpdaterCoordinator
from tests.test_run_bookkeeping import _make_coord, _state


class TestScheduleSurvivesFailures(unittest.TestCase):

    def test_failed_run_still_reschedules(self):
        coord, _ = _make_coord()
        coord.async_run_updates = AsyncMock(side_effect=RuntimeError("boom"))
        coord._async_reschedule = AsyncMock()
        asyncio.run(coord._async_fire(None))
        coord._async_reschedule.assert_awaited_once()

    def test_unloaded_coordinator_does_not_rearm_timer(self):
        coord, _ = _make_coord()
        coord._unloaded = True
        with patch.object(coord_mod, "async_track_point_in_time") as track:
            asyncio.run(coord._async_reschedule())
        track.assert_not_called()

    def test_setup_clears_unloaded_flag(self):
        coord, _ = _make_coord()
        coord._unloaded = True
        for name in (
            "_load_history", "_load_digest_state", "_load_snooze", "_load_backup_state",
            "_load_seen_versions", "_async_reconcile_interrupted_run",
            "_async_reschedule", "_async_scan_pending",
        ):
            setattr(coord, name, AsyncMock())
        with patch.object(coord_mod, "async_track_time_interval"):
            asyncio.run(coord.async_setup())
        self.assertFalse(coord.unloaded)

    def test_options_dialog_reenable_restores_scheduler(self):
        coord, hass = _make_coord()
        coord._unloaded = True
        coord.async_setup = AsyncMock()
        coord._async_reschedule = AsyncMock()
        coord._async_scan_pending = AsyncMock()
        coord._apply_log_level = MagicMock()
        hass.data = {DOMAIN: {"abc": {DATA_COORDINATOR: coord}}}
        entry = MagicMock()
        entry.entry_id = "abc"
        asyncio.run(_async_options_updated(hass, entry))
        coord.async_setup.assert_awaited_once()
        coord._async_reschedule.assert_not_awaited()

    def test_entry_stays_intact_when_platforms_fail_to_unload(self):
        coord, hass = _make_coord()
        coord.async_unload = AsyncMock()
        hass.data = {DOMAIN: {"abc": {DATA_COORDINATOR: coord}}}
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
        entry = MagicMock()
        entry.entry_id = "abc"
        self.assertFalse(asyncio.run(async_unload_entry(hass, entry)))
        coord.async_unload.assert_not_awaited()
        self.assertIn("abc", hass.data[DOMAIN])


class TestRunFixes(unittest.TestCase):

    def test_update_without_title_does_not_break_the_heads_up(self):
        coord, hass = _make_coord({
            CONF_BACKUP_BEFORE_UPDATE: False,
            CONF_PRE_NOTIFY_DELAY: 1,
            CONF_NOTIFY_SERVICE: "notify.mobile_app_phone",
        })
        ent = MagicMock()
        ent.entity_id = "update.z2m_firmware"
        ent.state = "on"
        ent.attributes = {"title": None, "installed_version": "1.0", "latest_version": "1.1"}
        hass.states.async_all.return_value = [ent]
        coord._save_run_state = AsyncMock()
        coord._call_notify_service = MagicMock()
        # Disabled during the wait, so the run stops right after the heads-up.
        with patch.object(
            AutoUpdaterCoordinator, "enabled", new_callable=PropertyMock, return_value=False
        ):
            asyncio.run(coord._async_run_updates_inner())
        messages = [c.args[0]["message"] for c in coord._call_notify_service.call_args_list]
        self.assertTrue(any("update.z2m_firmware" in m for m in messages))

    def test_empty_followup_pass_clears_deferred_queue(self):
        coord, hass = _make_coord()
        hass.states.async_all.return_value = []
        coord._deferred = [{"entity_id": "update.x", "title": "X"}]
        coord.last_run_duration = 99
        coord._save_run_state = AsyncMock()
        coord._append_and_save_history = AsyncMock()
        asyncio.run(coord._async_run_updates_inner(resume=True))
        self.assertEqual(coord._deferred, [])
        self.assertEqual(coord.last_run_duration, 0)
        coord._save_run_state.assert_awaited_with(None)

    def test_single_system_install_is_recorded_before_it_starts(self):
        coord, hass = _make_coord({CONF_BACKUP_BEFORE_UPDATE: False})
        hass.states.get.return_value = _state(
            "on", title="Core", installed_version="2025.1.0", latest_version="2025.2.0",
            release_url=None,
        )
        hass.states.async_all.return_value = []
        coord._save_run_state = AsyncMock()
        coord._append_and_save_history = AsyncMock()
        coord._async_scan_pending = AsyncMock()
        coord._install_with_retry = AsyncMock(return_value="pending")
        asyncio.run(coord.async_install_single("update.home_assistant_core"))
        first = coord._save_run_state.await_args_list[0].args[0]
        self.assertEqual(first["current"]["entity_id"], "update.home_assistant_core")
        self.assertIsNone(coord._save_run_state.await_args_list[-1].args[0])
        self.assertEqual(len(coord._pending_verification), 1)

    def test_single_addon_install_writes_no_marker(self):
        coord, hass = _make_coord({CONF_BACKUP_BEFORE_UPDATE: False})
        hass.states.get.return_value = _state(
            "on", title="Add-on", installed_version="1.0", latest_version="1.1", release_url=None,
        )
        hass.states.async_all.return_value = []
        coord._save_run_state = AsyncMock()
        coord._append_and_save_history = AsyncMock()
        coord._async_scan_pending = AsyncMock()
        coord._install_with_retry = AsyncMock(return_value="success")
        asyncio.run(coord.async_install_single("update.some_addon"))
        coord._save_run_state.assert_not_awaited()


class TestInstallRetry(unittest.TestCase):

    def test_retry_waits_for_install_that_outlived_the_timeout(self):
        coord, hass = _make_coord()
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("timed out"))
        states = [
            _state("on", in_progress=True, installed_version="1.0"),   # checked after the failure
            _state("on", in_progress=True, installed_version="1.0"),   # first poll
            _state("off", in_progress=False, installed_version="1.1"),  # finished
        ]
        hass.states.get.side_effect = states
        with patch.object(coord_mod, "SYSTEM_UPDATE_POLL_SECONDS", 0):
            outcome = asyncio.run(
                coord._install_with_retry("update.addon", "Add-on", "1.1", False, 0)
            )
        self.assertEqual(outcome, "success")
        self.assertEqual(hass.services.async_call.await_count, 1)

    def test_retry_still_happens_when_nothing_is_running(self):
        coord, hass = _make_coord()
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("rejected"))
        hass.states.get.return_value = _state("on", in_progress=False, installed_version="1.0")
        outcome = asyncio.run(
            coord._install_with_retry("update.addon", "Add-on", "1.1", False, 0)
        )
        self.assertEqual(outcome, "failed")
        self.assertEqual(hass.services.async_call.await_count, 2)


class TestBackupTracking(unittest.TestCase):

    def test_find_backup_skips_ids_already_tracked(self):
        def backup(backup_id):
            b = MagicMock()
            b.name = "pre_update_20260101_0200"
            b.backup_id = backup_id
            return b

        manager = MagicMock()
        manager.async_get_backups = AsyncMock(
            return_value=({"a": backup("a"), "b": backup("b")}, {})
        )
        find = AutoUpdaterCoordinator._find_backup_id_by_name
        self.assertEqual(asyncio.run(find(manager, "pre_update_20260101_0200")), "a")
        self.assertEqual(asyncio.run(find(manager, "pre_update_20260101_0200", {"a"})), "b")
        self.assertIsNone(asyncio.run(find(manager, "pre_update_20260101_0200", {"a", "b"})))


class TestConfigFlowHelpers(unittest.TestCase):

    def test_missing_title_is_labelled_with_the_entity_id(self):
        hass = MagicMock()
        titled, untitled = MagicMock(), MagicMock()
        titled.entity_id, titled.attributes = "update.a", {"title": "Alpha"}
        untitled.entity_id, untitled.attributes = "update.b", {"title": None}
        hass.states.async_all.return_value = [titled, untitled]
        self.assertEqual(_update_entity_map(hass), {"update.a": "Alpha", "update.b": "update.b"})

    def test_removed_excluded_entity_does_not_block_saving(self):
        schema = _build_schema({CONF_EXCLUDED_ENTITIES: ["update.gone"]}, {"update.a": "A"})
        self.assertIn(CONF_EXCLUDED_ENTITIES, schema({}))


if __name__ == "__main__":
    unittest.main()


class TestVerifyGrace(unittest.TestCase):

    def _pending(self, coord, hass, age_seconds):
        from datetime import datetime, timedelta, timezone
        ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
        coord._append_and_save_history = AsyncMock()
        coord._save_run_state = AsyncMock()
        coord._handle_failures = AsyncMock()
        coord._pending_verification = [
            {"entity_id": "update.home_assistant_core", "title": "Core", "from": "1", "to": "2", "triggered_at": ts}
        ]
        hass.states.get.return_value = _state("on", installed_version="1", latest_version="2", in_progress=False)

    def test_fresh_trigger_stays_pending_during_grace(self):
        coord, hass = _make_coord()
        self._pending(coord, hass, 5)
        asyncio.run(coord._async_verify_pending(grace_seconds=600))
        self.assertEqual(len(coord._pending_verification), 1)
        coord._handle_failures.assert_not_awaited()

    def test_old_trigger_fails_even_with_grace(self):
        coord, hass = _make_coord()
        self._pending(coord, hass, 3600)
        asyncio.run(coord._async_verify_pending(grace_seconds=600))
        self.assertEqual(coord._pending_verification, [])
        coord._handle_failures.assert_awaited_once()

    def test_no_grace_keeps_immediate_failure(self):
        coord, hass = _make_coord()
        self._pending(coord, hass, 5)
        asyncio.run(coord._async_verify_pending())
        coord._handle_failures.assert_awaited_once()


class TestLateBackupAdoption(unittest.TestCase):

    def _manager(self, backup_id=None):
        manager = MagicMock()
        found = {}
        if backup_id:
            b = MagicMock()
            b.name = "pre_update_20260101_0200"
            b.backup_id = backup_id
            found[backup_id] = b
        manager.async_get_backups = AsyncMock(return_value=(found, {}))
        return manager

    def test_finished_backup_is_tracked_for_purge(self):
        coord, _ = _make_coord()
        coord._save_backup_state = AsyncMock()
        coord._unrecorded_backups = [
            {"name": "pre_update_20260101_0200", "created": coord_mod.dt_util.now().isoformat()}
        ]
        asyncio.run(coord._adopt_unrecorded_backups(self._manager("abc123")))
        self.assertEqual([b["ref"] for b in coord._tracked_backups], ["abc123"])
        self.assertEqual(coord._unrecorded_backups, [])

    def test_still_running_backup_is_looked_for_again(self):
        coord, _ = _make_coord()
        coord._save_backup_state = AsyncMock()
        coord._unrecorded_backups = [
            {"name": "pre_update_20260101_0200", "created": coord_mod.dt_util.now().isoformat()}
        ]
        asyncio.run(coord._adopt_unrecorded_backups(self._manager()))
        self.assertEqual(coord._tracked_backups, [])
        self.assertEqual(len(coord._unrecorded_backups), 1)

    def test_old_unfound_backup_is_dropped(self):
        from datetime import timedelta
        coord, _ = _make_coord()
        coord._save_backup_state = AsyncMock()
        old = (coord_mod.dt_util.now() - timedelta(days=5)).isoformat()
        coord._unrecorded_backups = [{"name": "pre_update_20260101_0200", "created": old}]
        asyncio.run(coord._adopt_unrecorded_backups(self._manager()))
        self.assertEqual(coord._unrecorded_backups, [])


class TestNoUpdateHistory(unittest.TestCase):

    def _entry(self, ts):
        return {"timestamp": ts, "updated": [], "failed": [], "total_updated": 0,
                "total_failed": 0, "duration_seconds": 0, "note": coord_mod.NO_UPDATES_NOTE}

    def test_consecutive_empty_runs_share_one_entry(self):
        coord, _ = _make_coord()
        coord._save_history = AsyncMock()
        for ts in ("t1", "t2", "t3"):
            asyncio.run(coord._append_no_update_entry(self._entry(ts)))
        self.assertEqual([e["timestamp"] for e in coord.history], ["t3"])

    def test_empty_run_after_a_real_run_gets_its_own_entry(self):
        coord, _ = _make_coord()
        coord._save_history = AsyncMock()
        real = {"timestamp": "t0", "total_updated": 1, "total_failed": 0, "note": None}
        coord.history = [real]
        asyncio.run(coord._append_no_update_entry(self._entry("t1")))
        asyncio.run(coord._append_no_update_entry(self._entry("t2")))
        self.assertEqual([e["timestamp"] for e in coord.history], ["t0", "t2"])


class TestBackupCreation(unittest.TestCase):

    def _coord(self):
        coord, hass = _make_coord()
        coord._record_backup = AsyncMock()
        coord._purge_old_backups = AsyncMock()
        coord._adopt_unrecorded_backups = AsyncMock()
        hass.services.has_service.return_value = True
        return coord, hass

    def test_purge_runs_before_a_backup_that_then_fails(self):
        coord, _ = self._coord()
        coord._get_backup_manager = MagicMock(return_value=None)
        coord._call_backup_service = AsyncMock(side_effect=RuntimeError("disk full"))
        self.assertFalse(asyncio.run(coord._create_backup()))
        coord._purge_old_backups.assert_awaited_once()

    def test_error_after_the_backup_started_does_not_start_a_second_one(self):
        coord, _ = self._coord()
        coord._get_backup_manager = MagicMock(return_value=MagicMock())
        coord._create_backup_via_manager = AsyncMock(side_effect=AttributeError("late"))
        coord._call_backup_service = AsyncMock()
        self.assertFalse(asyncio.run(coord._create_backup()))
        coord._call_backup_service.assert_not_awaited()

    def test_manager_api_mismatch_before_start_uses_the_service(self):
        coord, _ = self._coord()
        coord._get_backup_manager = MagicMock(return_value=MagicMock())
        coord._create_backup_via_manager = AsyncMock(side_effect=NotImplementedError("no agent"))
        coord._call_backup_service = AsyncMock(return_value=None)
        self.assertTrue(asyncio.run(coord._create_backup()))
        coord._call_backup_service.assert_awaited_once()

    def test_signature_mismatch_is_reported_as_not_started(self):
        coord, _ = self._coord()
        manager = MagicMock()
        manager.local_backup_agents = {"backup.local": object()}
        manager.state = None
        manager.async_create_backup = MagicMock(side_effect=TypeError("unexpected keyword"))
        with self.assertRaises(NotImplementedError):
            asyncio.run(coord._create_backup_via_manager(manager, "pre_update_x"))

    def test_service_fallback_finds_the_new_backups_id(self):
        coord, _ = self._coord()
        manager = MagicMock()

        def backup(backup_id):
            b = MagicMock()
            b.backup_id = backup_id
            return b

        manager.async_get_backups = AsyncMock(side_effect=[
            ({"old": backup("old")}, {}),
            ({"old": backup("old"), "new": backup("new")}, {}),
        ])
        coord._get_backup_manager = MagicMock(return_value=manager)
        coord._create_backup_via_manager = AsyncMock(side_effect=NotImplementedError("no agent"))
        coord._call_backup_service = AsyncMock(return_value=None)
        self.assertTrue(asyncio.run(coord._create_backup()))
        self.assertEqual(coord._record_backup.await_args.args[2], "new")

    def test_ambiguous_new_backups_are_left_untracked(self):
        coord, _ = self._coord()
        manager = MagicMock()
        b = lambda i: MagicMock(backup_id=i)  # noqa: E731
        manager.async_get_backups = AsyncMock(side_effect=[
            ({}, {}),
            ({"a": b("a"), "b": b("b")}, {}),
        ])
        coord._get_backup_manager = MagicMock(return_value=manager)
        coord._create_backup_via_manager = AsyncMock(side_effect=NotImplementedError("no agent"))
        coord._call_backup_service = AsyncMock(return_value=None)
        asyncio.run(coord._create_backup())
        self.assertIsNone(coord._record_backup.await_args.args[2])


class TestBackupAlignment(unittest.TestCase):

    def test_core_install_uses_the_core_local_agent(self):
        manager = MagicMock()
        manager.local_backup_agents = {"backup.local": object()}
        manager.backup_agents = {"backup.local": object(), "cloud.cloud": object()}
        self.assertEqual(AutoUpdaterCoordinator._local_backup_agent_ids(manager), ["backup.local"])

    def test_supervised_install_uses_only_hassio_local_not_network_mounts(self):
        manager = MagicMock()
        manager.local_backup_agents = {}
        manager.backup_agents = {
            "hassio.local": object(), "hassio.nas": object(), "cloud.cloud": object(),
        }
        self.assertEqual(AutoUpdaterCoordinator._local_backup_agent_ids(manager), ["hassio.local"])

    def test_no_local_agent_means_no_agent(self):
        manager = MagicMock()
        manager.local_backup_agents = {}
        manager.backup_agents = {"hassio.nas": object()}
        self.assertEqual(AutoUpdaterCoordinator._local_backup_agent_ids(manager), [])

    def test_waits_for_a_running_backup_before_starting_ours(self):
        coord, _ = _make_coord()
        manager = MagicMock()
        manager.local_backup_agents = {"backup.local": object()}
        states = iter(["create_backup", "create_backup", "idle", "idle", "idle"])
        type(manager).state = PropertyMock(side_effect=lambda: next(states))
        manager.async_create_backup = AsyncMock()
        manager.async_get_backups = AsyncMock(return_value=({}, {}))
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        with patch.object(coord_mod.asyncio, "sleep", fake_sleep):
            asyncio.run(coord._create_backup_via_manager(manager, "pre_update_x"))
        self.assertGreaterEqual(len(sleeps), 1)
        manager.async_create_backup.assert_awaited_once()


# ---------------------------------------------------------------------------
# 1.5.0: quiet hours, skipped versions, health check
# ---------------------------------------------------------------------------

from datetime import datetime  # noqa: E402

from custom_components.ha_auto_updater.const import CONF_QUIET_END, CONF_QUIET_START  # noqa: E402


class TestQuietHours(unittest.TestCase):

    def _coord(self, start, end):
        coord, _ = _make_coord({CONF_QUIET_START: start, CONF_QUIET_END: end})
        return coord

    def test_off_when_unset_or_equal_or_invalid(self):
        now = datetime(2026, 1, 1, 12, 0)
        for start, end in ((None, None), ("", ""), ("10:00", "10:00"), ("x", "12:00"), ("25:00", "26:00")):
            self.assertFalse(self._coord(start, end)._in_quiet_hours(now), (start, end))

    def test_same_day_window(self):
        coord = self._coord("09:00", "17:00:00")
        self.assertTrue(coord._in_quiet_hours(datetime(2026, 1, 1, 9, 0)))
        self.assertTrue(coord._in_quiet_hours(datetime(2026, 1, 1, 16, 59)))
        self.assertFalse(coord._in_quiet_hours(datetime(2026, 1, 1, 17, 0)))
        self.assertFalse(coord._in_quiet_hours(datetime(2026, 1, 1, 8, 59)))

    def test_window_crossing_midnight(self):
        coord = self._coord("22:00", "06:00")
        self.assertTrue(coord._in_quiet_hours(datetime(2026, 1, 1, 23, 30)))
        self.assertTrue(coord._in_quiet_hours(datetime(2026, 1, 1, 5, 59)))
        self.assertFalse(coord._in_quiet_hours(datetime(2026, 1, 1, 6, 0)))
        self.assertFalse(coord._in_quiet_hours(datetime(2026, 1, 1, 12, 0)))

    def test_automatic_run_skipped_inside_window(self):
        coord = self._coord("00:00", "23:59")
        coord._send_status_notification = MagicMock()
        coord._notify_listeners = MagicMock()
        self.assertTrue(coord._skip_if_blocked())
        self.assertEqual(coord.last_run_status, "Skipped (Quiet hours)")
        self.assertIn("quiet hours", coord._send_status_notification.call_args.args[1])

    def test_not_skipped_outside_window(self):
        coord = self._coord(None, None)
        self.assertFalse(coord._skip_if_blocked())


class TestSkippedVersions(unittest.TestCase):

    def test_skip_applies_to_that_version_only(self):
        coord, _ = _make_coord()
        coord._skipped_versions = {"update.a": "2.0"}
        self.assertTrue(coord._is_version_skipped("update.a", "2.0"))
        self.assertFalse(coord._is_version_skipped("update.a", "2.1"))
        self.assertNotIn("update.a", coord._skipped_versions)   # newer version ends the skip

    def test_skip_version_records_current_latest(self):
        coord, hass = _make_coord()
        hass.states.get.return_value = _state("on", latest_version="2.0")
        coord._save_skipped_versions = AsyncMock()
        coord._async_scan_pending = AsyncMock()
        asyncio.run(coord.async_skip_version("update.a"))
        self.assertEqual(coord._skipped_versions, {"update.a": "2.0"})
        coord._save_skipped_versions.assert_awaited_once()

    def test_skip_version_without_pending_version_errors(self):
        coord, hass = _make_coord()
        hass.states.get.return_value = None
        with self.assertRaises(ValueError):
            asyncio.run(coord.async_skip_version("update.a"))

    def test_clear_one_and_all(self):
        coord, _ = _make_coord()
        coord._save_skipped_versions = AsyncMock()
        coord._async_scan_pending = AsyncMock()
        coord._skipped_versions = {"update.a": "1", "update.b": "2"}
        asyncio.run(coord.async_clear_skipped_version("update.a"))
        self.assertEqual(coord._skipped_versions, {"update.b": "2"})
        asyncio.run(coord.async_clear_skipped_version())
        self.assertEqual(coord._skipped_versions, {})


class TestHealthCheck(unittest.TestCase):

    def _coord_with_states(self, states):
        coord, hass = _make_coord()
        hass.states.get.side_effect = lambda eid: states.get(eid)
        return coord

    def test_unavailable_entity_is_unhealthy(self):
        coord = self._coord_with_states({
            "sensor.a": _state("unavailable"),
            "sensor.b": _state("5"),
        })
        self.assertEqual(
            coord._unhealthy_entities({"sensor.a": "1", "sensor.b": "4"}), ["sensor.a"]
        )

    def test_missing_entity_is_unhealthy(self):
        coord = self._coord_with_states({})
        self.assertEqual(coord._unhealthy_entities({"sensor.a": "1"}), ["sensor.a"])

    def test_running_sensor_going_off_is_unhealthy(self):
        coord = self._coord_with_states({
            "binary_sensor.addon_running": _state("off", device_class="running"),
            "binary_sensor.door": _state("off", device_class="door"),
        })
        snapshot = {"binary_sensor.addon_running": "on", "binary_sensor.door": "on"}
        self.assertEqual(coord._unhealthy_entities(snapshot), ["binary_sensor.addon_running"])

    def test_recovery_within_window_is_healthy(self):
        coord, hass = _make_coord()
        answers = iter([_state("unavailable"), _state("unavailable"), _state("1")])
        hass.states.get.side_effect = lambda eid: next(answers)
        with patch.object(coord_mod.asyncio, "sleep", new=AsyncMock()):
            bad = asyncio.run(coord._async_health_check({"sensor.a": "1"}))
        self.assertEqual(bad, [])

    def test_never_recovering_is_reported_after_the_window(self):
        coord, hass = _make_coord()
        hass.states.get.return_value = _state("unavailable")
        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)

        with patch.object(coord_mod.asyncio, "sleep", new=fake_sleep):
            bad = asyncio.run(coord._async_health_check({"sensor.a": "1"}))
        self.assertEqual(bad, ["sensor.a"])
        self.assertLessEqual(sum(sleeps), 30 + 180)

    def test_unhealthy_notification_names_the_entity(self):
        coord, _ = _make_coord()
        coord._send_status_notification = MagicMock()
        coord._send_push_notification = MagicMock()
        coord._send_unhealthy_notification(
            [{"title": "SSH", "from": "1", "to": "2", "unhealthy": ["binary_sensor.ssh_running"]}]
        )
        self.assertIn("binary_sensor.ssh_running", coord._send_status_notification.call_args.args[1])
        coord._send_push_notification.assert_called_once()
