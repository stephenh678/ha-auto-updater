"""Tests for the 1.4.0 features: cooldown, blocking entities, dry run, notification buttons, repairs."""
import asyncio
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.ha_auto_updater import coordinator as coord_mod
from custom_components.ha_auto_updater.const import (
    CONF_ACTIONABLE_NOTIFICATIONS,
    CONF_AUTO_RESTART,
    CONF_BACKUP_BEFORE_UPDATE,
    CONF_BLOCKING_ENTITIES,
    CONF_EXCLUDED_ENTITIES,
    CONF_MAX_UPDATES_PER_RUN,
    CONF_MIN_RELEASE_AGE_DAYS,
    CONF_NOTIFY_FAILURE,
    CONF_NOTIFY_SERVICE,
    CONF_PRE_NOTIFY_DELAY,
    CONF_STAGGER_DELAY,
    DOMAIN,
)
from custom_components.ha_auto_updater.coordinator import AutoUpdaterCoordinator

HA_INSTALLED = isinstance(sys.modules.get("homeassistant"), types.ModuleType)


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


def _entity(entity_id, state="on", **attrs):
    st = _state(state, **attrs)
    st.entity_id = entity_id
    return st


def _install_calls(hass):
    return [
        c.args[2]["entity_id"] for c in hass.services.async_call.call_args_list
        if c.args[:2] == ("update", "install")
    ]


def _quiet(coord):
    """Stub out persistence and notifications a run touches."""
    coord._save_run_state = AsyncMock()
    coord._append_and_save_history = AsyncMock()
    coord._send_success_notification = MagicMock()
    coord._send_status_notification = MagicMock()
    coord._send_pre_update_notification = MagicMock()
    coord._call_notify_service = MagicMock()


# ---------------------------------------------------------------------------
# Release cooldown
# ---------------------------------------------------------------------------

class TestReleaseCooldown(unittest.TestCase):

    def test_track_versions_records_resets_and_drops(self):
        coord, hass = _make_coord()
        a = _entity("update.a", installed_version="1.0", latest_version="1.1")
        hass.states.async_all.return_value = [a]

        self.assertTrue(coord._track_versions())
        self.assertEqual(coord._seen_versions["update.a"]["version"], "1.1")
        self.assertFalse(coord._track_versions())  # nothing changed

        a.attributes["latest_version"] = "1.2"
        self.assertTrue(coord._track_versions())  # newer version restarts the clock
        self.assertEqual(coord._seen_versions["update.a"]["version"], "1.2")

        a.state = "unavailable"  # e.g. integration still loading after a restart
        self.assertFalse(coord._track_versions())
        self.assertIn("update.a", coord._seen_versions)

        a.state = "off"  # installed or skipped
        self.assertTrue(coord._track_versions())
        self.assertNotIn("update.a", coord._seen_versions)

    def test_new_version_held_back_until_old_enough(self):
        coord, hass = _make_coord({CONF_MIN_RELEASE_AGE_DAYS: 3})
        a = _entity("update.a", title="A", installed_version="1.0", latest_version="1.1")
        hass.states.async_all.return_value = [a]
        coord._get_update_source = MagicMock(return_value="Add-on")
        coord._track_versions()

        available, skipped = coord._classify_updates()
        self.assertEqual(available, [])
        self.assertEqual(skipped[0]["reason"], "cooldown")
        self.assertIn("available_after", skipped[0])

        old = datetime.now(timezone.utc) - timedelta(days=4)
        coord._seen_versions["update.a"]["first_seen"] = old.isoformat()
        available, skipped = coord._classify_updates()
        self.assertEqual(available, [a])
        self.assertEqual(skipped, [])

    def test_cooldown_off_by_default(self):
        coord, hass = _make_coord()
        a = _entity("update.a", installed_version="1.0", latest_version="1.1")
        hass.states.async_all.return_value = [a]
        coord._get_update_source = MagicMock(return_value="Add-on")
        coord._track_versions()
        self.assertEqual(coord._classify_updates()[0], [a])

    def test_scan_exposes_cooling_down_updates(self):
        coord, hass = _make_coord({CONF_MIN_RELEASE_AGE_DAYS: 2})
        a = _entity("update.a", title="A", installed_version="1.0", latest_version="1.1")
        hass.states.async_all.return_value = [a]
        hass.states.get.return_value = a
        coord._get_update_source = MagicMock(return_value="Add-on")

        asyncio.run(coord._async_scan_pending(None))

        self.assertEqual(coord.pending_count, 0)
        self.assertEqual(coord.cooldown_updates[0]["entity_id"], "update.a")
        self.assertIsNotNone(coord.cooldown_updates[0]["available_after"])

    def test_seen_versions_survive_reload(self):
        coord, _ = _make_coord()
        coord._read_json_file = AsyncMock(return_value={
            "update.a": {"version": "1.1", "first_seen": "2026-09-01T00:00:00+00:00"},
            "garbage": "not a dict",
        })
        asyncio.run(coord._load_seen_versions())
        self.assertEqual(list(coord._seen_versions), ["update.a"])


# ---------------------------------------------------------------------------
# Blocking entities
# ---------------------------------------------------------------------------

class TestBlockingEntities(unittest.TestCase):

    def _run(self, manual, blocker_state):
        coord, hass = _make_coord({
            CONF_BACKUP_BEFORE_UPDATE: False,
            CONF_PRE_NOTIFY_DELAY: 0,
            CONF_STAGGER_DELAY: 0,
            CONF_BLOCKING_ENTITIES: ["input_boolean.guests"],
        })
        upd = _entity("update.addon_ssh", title="SSH", installed_version="1.0", latest_version="1.1")
        guests = _state(blocker_state, friendly_name="Guests")
        hass.states.async_all.return_value = [upd]
        hass.states.get.side_effect = lambda eid: {
            "update.addon_ssh": upd, "input_boolean.guests": guests,
        }.get(eid)
        coord._get_update_source = MagicMock(return_value="Add-on")
        _quiet(coord)
        asyncio.run(coord._async_run_updates_inner(manual=manual))
        return coord, hass

    def test_automatic_run_skipped_while_blocker_on(self):
        coord, hass = self._run(manual=False, blocker_state="on")
        self.assertEqual(_install_calls(hass), [])
        self.assertEqual(coord.last_run_status, "Skipped (Blocked)")
        self.assertEqual(
            coord._send_status_notification.call_args.kwargs["notification_id"],
            "ha_auto_updater_blocked",
        )
        self.assertIn("Guests", coord._send_status_notification.call_args.args[1])

    def test_manual_run_ignores_blocker(self):
        _, hass = self._run(manual=True, blocker_state="on")
        self.assertEqual(_install_calls(hass), ["update.addon_ssh"])

    def test_blocker_off_lets_run_proceed(self):
        _, hass = self._run(manual=False, blocker_state="off")
        self.assertEqual(_install_calls(hass), ["update.addon_ssh"])


# ---------------------------------------------------------------------------
# Notification buttons
# ---------------------------------------------------------------------------

class TestNotificationActions(unittest.TestCase):

    def test_buttons_only_for_mobile_app_services(self):
        coord, _ = _make_coord({CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"})
        self.assertTrue(coord._actionable_push_available())
        coord, _ = _make_coord({CONF_NOTIFY_SERVICE: "notify.telegram"})
        self.assertFalse(coord._actionable_push_available())
        coord, _ = _make_coord({
            CONF_NOTIFY_SERVICE: "notify.mobile_app_phone", CONF_ACTIONABLE_NOTIFICATIONS: False,
        })
        self.assertFalse(coord._actionable_push_available())

    def test_push_payload_carries_run_token(self):
        coord, _ = _make_coord({CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"})
        coord._call_notify_service = MagicMock()
        coord._send_pre_update_push(["A", "B"], 5, "tok123")
        payload = coord._call_notify_service.call_args.args[0]
        actions = [a["action"] for a in payload["data"]["actions"]]
        self.assertEqual(actions, [
            "HA_AUTO_UPDATER_INSTALL_NOW_tok123",
            "HA_AUTO_UPDATER_SKIP_RUN_tok123",
            "HA_AUTO_UPDATER_SNOOZE_tok123",
        ])
        self.assertEqual(payload["data"]["tag"], "ha_auto_updater_pre_update")

    def test_handler_accepts_only_the_waiting_runs_token(self):
        coord, _ = _make_coord()

        async def scenario():
            coord._action_token = "abc"
            coord._action_event = asyncio.Event()
            for action in ("HA_AUTO_UPDATER_SKIP_RUN_old", "SOMETHING_ELSE", "HA_AUTO_UPDATER_BOGUS_abc"):
                await coord._async_handle_notification_action(MagicMock(data={"action": action}))
                self.assertIsNone(coord._requested_action, action)
            await coord._async_handle_notification_action(
                MagicMock(data={"action": "HA_AUTO_UPDATER_SKIP_RUN_abc"})
            )
            self.assertEqual(coord._requested_action, "SKIP_RUN")
            self.assertTrue(coord._action_event.is_set())

        asyncio.run(scenario())

    def _run_with_action(self, action):
        coord, hass = _make_coord({
            CONF_BACKUP_BEFORE_UPDATE: False,
            CONF_PRE_NOTIFY_DELAY: 1,
            CONF_STAGGER_DELAY: 0,
            CONF_NOTIFY_SERVICE: "notify.mobile_app_phone",
        })
        upd = _entity("update.addon_ssh", title="SSH", installed_version="1.0", latest_version="1.1")
        hass.states.async_all.return_value = [upd]
        hass.states.get.return_value = upd
        coord._get_update_source = MagicMock(return_value="Add-on")
        _quiet(coord)
        coord._save_snooze = AsyncMock()
        coord._async_scan_pending = AsyncMock()

        def fake_push(titles, delay, token):
            # Simulate the phone: the button press arrives as a bus event.
            event = MagicMock(data={"action": f"HA_AUTO_UPDATER_{action}_{token}"})
            asyncio.get_running_loop().create_task(coord._async_handle_notification_action(event))

        coord._send_pre_update_push = fake_push
        asyncio.run(asyncio.wait_for(coord._async_run_updates_inner(), timeout=10))
        return coord, hass

    def test_skip_this_run(self):
        coord, hass = self._run_with_action("SKIP_RUN")
        self.assertEqual(_install_calls(hass), [])
        self.assertEqual(coord.last_run_status, "Skipped (from notification)")
        coord._call_notify_service.assert_called_with(
            {"message": "clear_notification", "data": {"tag": "ha_auto_updater_pre_update"}}
        )

    def test_snooze(self):
        coord, hass = self._run_with_action("SNOOZE")
        self.assertEqual(_install_calls(hass), [])
        self.assertEqual(coord.last_run_status, "Snoozed (from notification)")
        self.assertIn("update.addon_ssh", coord._snoozed)

    def test_install_now_ends_the_wait(self):
        # The pre-notify delay is a full minute; the 10 s timeout proves the
        # button ended the wait instead of it running out.
        coord, hass = self._run_with_action("INSTALL_NOW")
        self.assertEqual(_install_calls(hass), ["update.addon_ssh"])
        self.assertIsNone(coord._action_token)


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

class TestDryRun(unittest.TestCase):

    def _coord(self, options, entities, sources, extra_states=None):
        coord, hass = _make_coord(options)
        hass.states.async_all.return_value = entities
        states = {e.entity_id: e for e in entities}
        states.update(extra_states or {})
        hass.states.get.side_effect = lambda eid: states.get(eid)
        coord._get_update_source = MagicMock(side_effect=lambda eid: sources[eid])
        return coord, hass

    def test_report_orders_explains_and_installs_nothing(self):
        core = _entity("update.home_assistant_core", title="Core", installed_version="2026.8.0", latest_version="2026.9.0")
        hacs = _entity("update.hacs_thing", title="Thing", installed_version="1.0", latest_version="1.1")
        addon = _entity("update.addon_ssh", title="SSH", installed_version="1.0", latest_version="1.1")
        excluded = _entity("update.excluded", title="Ex", installed_version="1.0", latest_version="1.1")
        coord, hass = self._coord(
            {
                CONF_EXCLUDED_ENTITIES: ["update.excluded"],
                CONF_BLOCKING_ENTITIES: ["input_boolean.guests"],
                CONF_AUTO_RESTART: True,
            },
            [core, hacs, addon, excluded],
            {
                "update.home_assistant_core": "HA System", "update.hacs_thing": "HACS",
                "update.addon_ssh": "Add-on", "update.excluded": "Add-on",
            },
            {"input_boolean.guests": _state("on", friendly_name="Guests")},
        )

        report = asyncio.run(coord.async_dry_run())

        self.assertEqual(
            [w["entity_id"] for w in report["would_install"]],
            ["update.hacs_thing", "update.addon_ssh", "update.home_assistant_core"],
        )
        by_id = {w["entity_id"]: w for w in report["would_install"]}
        self.assertTrue(by_id["update.hacs_thing"]["needs_restart"])
        self.assertTrue(by_id["update.home_assistant_core"]["system_update"])
        self.assertFalse(any(w["follow_up_pass"] for w in report["would_install"]))
        self.assertFalse(report["restart_after"])  # a system update is in this pass
        self.assertEqual([(s["entity_id"], s["reason"]) for s in report["skipped"]], [("update.excluded", "excluded")])
        self.assertIn("blocking_entity", [b["reason"] for b in report["blockers"]])
        self.assertTrue(report["backup_before_update"])
        self.assertEqual(_install_calls(hass), [])
        hass.services.async_call.assert_not_called()

    def test_follow_up_pass_and_run_cap(self):
        addon = _entity("update.addon_ssh", title="SSH", installed_version="1.0", latest_version="1.1")
        core = _entity("update.home_assistant_core", title="Core", installed_version="2026.8.0", latest_version="2026.9.0")
        os_ = _entity("update.home_assistant_operating_system", title="OS", installed_version="16.0", latest_version="16.1")
        sources = {
            "update.addon_ssh": "Add-on", "update.home_assistant_core": "HA System",
            "update.home_assistant_operating_system": "HA System",
        }

        coord, _ = self._coord({}, [addon, core, os_], sources)
        report = asyncio.run(coord.async_dry_run())
        follow = {w["entity_id"]: w["follow_up_pass"] for w in report["would_install"]}
        self.assertEqual(follow, {
            "update.addon_ssh": False,
            "update.home_assistant_core": False,
            "update.home_assistant_operating_system": True,
        })

        coord, _ = self._coord({CONF_MAX_UPDATES_PER_RUN: 2}, [addon, core, os_], sources)
        report = asyncio.run(coord.async_dry_run())
        self.assertEqual(len(report["would_install"]), 2)
        self.assertEqual(report["skipped"][-1]["reason"], "run_cap")

    def test_restart_after_hacs_only_run(self):
        hacs = _entity("update.hacs_thing", title="Thing", installed_version="1.0", latest_version="1.1")
        coord, _ = self._coord({CONF_AUTO_RESTART: True}, [hacs], {"update.hacs_thing": "HACS"})
        self.assertTrue(asyncio.run(coord.async_dry_run())["restart_after"])

    def test_notification_lists_items_and_reasons(self):
        coord, _ = _make_coord()
        report = {
            "would_install": [{
                "entity_id": "update.a", "title": "A", "source": "HACS", "from": "1.0", "to": "1.1",
                "system_update": False, "needs_restart": True, "follow_up_pass": False,
            }],
            "skipped": [{"entity_id": "update.b", "title": "B", "reason": "cooldown", "detail": "too new"}],
            "blockers": [{"reason": "blocking_entity", "detail": "Guests is on.", "automatic_runs_only": True}],
            "backup_before_update": True,
            "pre_update_delay_minutes": 5,
            "restart_after": True,
        }
        with patch.object(coord_mod, "async_create") as create:
            coord.send_dry_run_notification(report)
        message = create.call_args.args[1]
        for text in ("Would install (1)", "A (1.0 → 1.1) [HACS]", "B — too new", "Guests is on.", "backup is taken first"):
            self.assertIn(text, message)
        self.assertEqual(create.call_args.kwargs["notification_id"], "ha_auto_updater_dry_run")


# ---------------------------------------------------------------------------
# Repairs
# ---------------------------------------------------------------------------

class TestRepairsIssues(unittest.TestCase):

    def test_quarantine_raises_fixable_issue(self):
        coord, _ = _make_coord({CONF_NOTIFY_FAILURE: False})
        coord._consecutive_failures = MagicMock(return_value=3)
        coord.async_snooze_update = AsyncMock()
        with patch.object(coord_mod, "ir") as ir_mock:
            asyncio.run(coord._handle_failures([{"entity_id": "update.x", "title": "X"}]))
        ir_mock.async_create_issue.assert_called_once()
        call = ir_mock.async_create_issue.call_args
        self.assertEqual(call.args[1:3], (DOMAIN, "quarantined_update.x"))
        self.assertTrue(call.kwargs["is_fixable"])
        self.assertEqual(call.kwargs["translation_key"], "update_quarantined")
        self.assertEqual(call.kwargs["data"]["entity_id"], "update.x")

    def test_clearing_a_snooze_clears_its_issue(self):
        coord, hass = _make_coord()
        coord._snoozed = {"update.x": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()}
        coord._save_snooze = AsyncMock()
        coord._async_scan_pending = AsyncMock()
        with patch.object(coord_mod, "ir") as ir_mock:
            asyncio.run(coord.async_clear_snooze("update.x"))
        ir_mock.async_delete_issue.assert_called_with(hass, DOMAIN, "quarantined_update.x")

    def test_reconcile_drops_only_stale_quarantine_issues(self):
        coord, hass = _make_coord()
        coord._snoozed = {"update.b": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()}
        hass.states.get.return_value = _state("on")
        with patch.object(coord_mod, "ir") as ir_mock:
            ir_mock.async_get.return_value.issues = {
                (DOMAIN, "quarantined_update.a"): object(),   # snooze gone
                (DOMAIN, "quarantined_update.b"): object(),   # still snoozed
                (DOMAIN, "backup_failing"): object(),
                ("other", "quarantined_update.c"): object(),
            }
            coord._reconcile_quarantine_issues()
        deleted = [c.args[2] for c in ir_mock.async_delete_issue.call_args_list]
        self.assertEqual(deleted, ["quarantined_update.a"])

    def test_backup_failure_streak_raises_and_clears_issue(self):
        coord, _ = _make_coord()
        coord._save_backup_state = AsyncMock()
        with patch.object(coord_mod, "ir") as ir_mock:
            asyncio.run(coord._async_note_backup_result(False))
            ir_mock.async_create_issue.assert_not_called()
            asyncio.run(coord._async_note_backup_result(False))
            ir_mock.async_create_issue.assert_called_once()
            self.assertEqual(ir_mock.async_create_issue.call_args.args[2], "backup_failing")
            self.assertEqual(ir_mock.async_create_issue.call_args.kwargs["translation_placeholders"], {"count": "2"})
            asyncio.run(coord._async_note_backup_result(True))
            ir_mock.async_delete_issue.assert_called_with(coord.hass, DOMAIN, "backup_failing")
        self.assertEqual(coord._backup_failure_streak, 0)

    def test_backup_failure_streak_persists(self):
        coord, _ = _make_coord()
        coord._read_json_file = AsyncMock(return_value={"backups": [], "consecutive_failures": 3})
        asyncio.run(coord._load_backup_state())
        self.assertEqual(coord._backup_failure_streak, 3)


@unittest.skipUnless(HA_INSTALLED, "needs the real homeassistant package")
class TestRepairFlow(unittest.TestCase):

    def test_fix_flow_clears_quarantine(self):
        from custom_components.ha_auto_updater import repairs

        flow = asyncio.run(repairs.async_create_fix_flow(
            MagicMock(), "quarantined_update.x",
            {"entity_id": "update.x", "title": "X", "failures": "3", "days": "7"},
        ))
        self.assertIsInstance(flow, repairs.ClearQuarantineRepairFlow)

        coordinator = MagicMock()
        coordinator.async_clear_snooze = AsyncMock()
        flow.hass = MagicMock()
        flow.hass.data = {DOMAIN: {"entry": {"coordinator": coordinator}}}
        flow.flow_id = "test_flow"
        flow.handler = DOMAIN

        form = asyncio.run(flow.async_step_init())
        self.assertEqual(form["type"], "form")
        self.assertEqual(form["step_id"], "confirm")
        self.assertEqual(form["description_placeholders"]["title"], "X")

        done = asyncio.run(flow.async_step_confirm({}))
        coordinator.async_clear_snooze.assert_awaited_once_with("update.x")
        self.assertEqual(done["type"], "create_entry")

    def test_unknown_issue_gets_plain_confirm_flow(self):
        from homeassistant.components.repairs import ConfirmRepairFlow
        from custom_components.ha_auto_updater import repairs

        flow = asyncio.run(repairs.async_create_fix_flow(MagicMock(), "backup_failing", None))
        self.assertIsInstance(flow, ConfirmRepairFlow)


class TestConfigFlowOptions(unittest.TestCase):

    def test_schema_has_new_options(self):
        from custom_components.ha_auto_updater.config_flow import _build_schema

        keys = [k.schema for k in _build_schema({}, {}).schema.keys() if hasattr(k, "schema")]
        self.assertIn(CONF_MIN_RELEASE_AGE_DAYS, keys)
        self.assertIn(CONF_BLOCKING_ENTITIES, keys)


if __name__ == "__main__":
    unittest.main()
