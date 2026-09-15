"""Unit tests for AutoUpdaterCoordinator helper functions."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from custom_components.ha_auto_updater.coordinator import AutoUpdaterCoordinator


class TestAutoUpdaterCoordinator(unittest.TestCase):

    def test_is_major_bump(self):
        """Test major bump detection across various version formats."""
        # Standard SemVer major bump -> True
        self.assertTrue(AutoUpdaterCoordinator._is_major_bump({
            "installed_version": "1.4.0",
            "latest_version": "2.0.0",
        }))

        # Standard SemVer minor bump -> False
        self.assertFalse(AutoUpdaterCoordinator._is_major_bump({
            "installed_version": "1.4.0",
            "latest_version": "1.5.0",
        }))

        # Standard SemVer patch bump -> False
        self.assertFalse(AutoUpdaterCoordinator._is_major_bump({
            "installed_version": "1.4.0",
            "latest_version": "1.4.1",
        }))

        # 'v' prefixed versions -> True
        self.assertTrue(AutoUpdaterCoordinator._is_major_bump({
            "installed_version": "v1.9.0",
            "latest_version": "v2.0.0",
        }))

        # CalVer (year >= 2000) -> False (always routine)
        self.assertFalse(AutoUpdaterCoordinator._is_major_bump({
            "installed_version": "2024.1.0",
            "latest_version": "2024.2.0",
        }))

        # Missing attributes or invalid -> False
        self.assertFalse(AutoUpdaterCoordinator._is_major_bump({}))
        self.assertFalse(AutoUpdaterCoordinator._is_major_bump({"installed_version": "?"}))

    def test_is_prerelease(self):
        """Test pre-release detection."""
        # Beta / RC / dev -> True
        self.assertTrue(AutoUpdaterCoordinator._is_prerelease("1.0.0b1"))
        self.assertTrue(AutoUpdaterCoordinator._is_prerelease("2.0.0-rc1"))
        self.assertTrue(AutoUpdaterCoordinator._is_prerelease("0.1.0.dev0"))
        self.assertTrue(AutoUpdaterCoordinator._is_prerelease("1.2.3-alpha"))

        # Stable releases -> False
        self.assertFalse(AutoUpdaterCoordinator._is_prerelease("1.0.0"))
        self.assertFalse(AutoUpdaterCoordinator._is_prerelease("v2.5.1"))
        self.assertFalse(AutoUpdaterCoordinator._is_prerelease("2026.3.0"))
        self.assertFalse(AutoUpdaterCoordinator._is_prerelease(""))
        self.assertFalse(AutoUpdaterCoordinator._is_prerelease("?"))

    def test_derive_status(self):
        """Test status string derivation for run completions."""
        self.assertEqual(AutoUpdaterCoordinator._derive_status(0, 0), "No updates")
        self.assertEqual(AutoUpdaterCoordinator._derive_status(3, 0), "Success")
        self.assertEqual(AutoUpdaterCoordinator._derive_status(0, 2), "All failed")
        self.assertEqual(AutoUpdaterCoordinator._derive_status(2, 1), "Partial failure")

    def test_consecutive_failures(self):
        """Test counting consecutive failures for escalation notifications."""
        mock_hass = MagicMock()
        mock_entry = MagicMock()
        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)

        coord.history = [
            {"updated": ["Component A (1.0 -> 1.1)"], "failed": []},
            {"updated": [], "failed": ["Component B"]},
            {"updated": [], "failed": ["Component B"]},
            {"updated": [], "failed": ["Component B"]},
        ]

        self.assertEqual(coord._consecutive_failures("Component B"), 3)
        self.assertEqual(coord._consecutive_failures("Component A"), 0)

    def test_snooze_logic(self):
        """Test snoozing and snooze expiry checks."""
        mock_hass = MagicMock()
        mock_entry = MagicMock()
        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)

        # Entity not snoozed
        self.assertFalse(coord._is_snoozed("update.test_entity"))

        # Set snooze for future ISO timestamp
        future_ts = (datetime.now() + timedelta(days=5)).isoformat()
        coord._snoozed["update.test_entity"] = future_ts
        self.assertTrue(coord._is_snoozed("update.test_entity"))

        # Expired snooze
        past_ts = (datetime.now() - timedelta(days=1)).isoformat()
        coord._snoozed["update.old_entity"] = past_ts
        self.assertFalse(coord._is_snoozed("update.old_entity"))
        # Lazy cleanup removed it from dictionary
    def test_time_conversion(self):
        """Test time label conversion preserving minutes."""
        from custom_components.ha_auto_updater.select import _24h_to_label, _label_to_24h

        self.assertEqual(_24h_to_label("02:00"), "2:00 AM")
        self.assertEqual(_24h_to_label("14:30"), "2:30 PM")
        self.assertEqual(_24h_to_label("00:15"), "12:15 AM")
        self.assertEqual(_24h_to_label("12:00"), "12:00 PM")

        self.assertEqual(_label_to_24h("2:00 AM"), "02:00")
        self.assertEqual(_label_to_24h("2:30 PM"), "14:30")
        self.assertEqual(_label_to_24h("12:15 AM"), "00:15")
        self.assertEqual(_label_to_24h("12:00 PM"), "12:00")

    def test_check_disk_space(self):
        """Test pre-flight disk space helper."""
        mock_hass = MagicMock()
        mock_hass.config.config_dir = "."
        mock_entry = MagicMock()
        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)

        is_ok, free_gb = coord._check_disk_space(0.001)
        self.assertTrue(is_ok)
        self.assertGreater(free_gb, 0.0)

    def test_category_source(self):
        """Test update source categorization."""
        mock_hass = MagicMock()
        mock_entry = MagicMock()
        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)

        self.assertEqual(coord._get_update_source("update.home_assistant_core"), "HA System")
        self.assertEqual(coord._get_update_source("update.home_assistant_operating_system"), "HA System")

    def test_diagnostics(self):
        """Test diagnostics payload generation."""
        import asyncio
        from custom_components.ha_auto_updater.diagnostics import async_get_config_entry_diagnostics
        from custom_components.ha_auto_updater.const import CONF_NOTIFY_SERVICE

        mock_hass = MagicMock()
        mock_hass.config.config_dir = "."
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.version = 1
        mock_entry.options = {
            "test_opt": True,
            CONF_NOTIFY_SERVICE: "notify.secret_webhook",
        }

        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)
        coord._snoozed = {"update.item1": "2026-12-31T00:00:00"}
        coord._tracked_backups = [{"ref": "b1", "domain": "backup", "name": "pre_update_1", "created": "2026-08-29"}]
        mock_hass.data = {"ha_auto_updater": {"test_entry": {"coordinator": coord}}}

        diag = asyncio.run(async_get_config_entry_diagnostics(mock_hass, mock_entry))
        self.assertEqual(diag["entry_id"], "test_entry")
        self.assertIn("system_info", diag)
        self.assertIn("coordinator_state", diag)
        self.assertEqual(diag["coordinator_state"]["snoozed"], {"update.item1": "2026-12-31T00:00:00"})
        self.assertEqual(diag["coordinator_state"]["tracked_backups"], coord._tracked_backups)
        # Redaction check
        self.assertEqual(diag["options"][CONF_NOTIFY_SERVICE], "**REDACTED**")

    def test_system_update_sorting(self):
        """Test that system updates (Core/OS/Supervisor) sort to the end."""
        from custom_components.ha_auto_updater.coordinator import _HA_SYSTEM_UPDATE_ENTITIES

        e_core = MagicMock(entity_id="update.home_assistant_core")
        e_hacs = MagicMock(entity_id="update.hacs_integration")
        e_os = MagicMock(entity_id="update.home_assistant_operating_system")
        e_addon = MagicMock(entity_id="update.addon_ssh")
        e_firmware = MagicMock(entity_id="update.shelly_device")

        items = [e_core, e_hacs, e_os, e_addon, e_firmware]
        items.sort(key=lambda e: 1 if e.entity_id in _HA_SYSTEM_UPDATE_ENTITIES else 0)

        # First 3 should be non-system
        self.assertEqual(items[0], e_hacs)
        self.assertEqual(items[1], e_addon)
        self.assertEqual(items[2], e_firmware)
        # Last 2 should be system updates
        self.assertEqual(items[3], e_core)
        self.assertEqual(items[4], e_os)

    def test_strict_backup_mode_abort(self):
        """Test that update run aborts when backup fails and strict backup mode is enabled."""
        import asyncio
        from unittest.mock import AsyncMock
        from custom_components.ha_auto_updater.const import (
            CONF_ABORT_ON_BACKUP_FAILURE,
            CONF_BACKUP_BEFORE_UPDATE,
            CONF_PRE_NOTIFY_DELAY,
        )

        mock_hass = MagicMock()
        mock_hass.config.config_dir = "."
        mock_hass.config.safe_mode = False
        mock_hass.services.async_call = AsyncMock()
        mock_hass.async_add_executor_job = AsyncMock()
        mock_hass.async_create_task.side_effect = lambda coro: coro.close()
        mock_entry = MagicMock()
        mock_entry.data = {}
        mock_entry.options = {
            CONF_BACKUP_BEFORE_UPDATE: True,
            CONF_ABORT_ON_BACKUP_FAILURE: True,
            CONF_PRE_NOTIFY_DELAY: 0,
        }

        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)

        # Mock an available update
        mock_update = MagicMock()
        mock_update.state = "on"
        mock_update.entity_id = "update.test_addon"
        mock_update.attributes = {"title": "Test Addon", "installed_version": "1.0", "latest_version": "1.1"}
        mock_hass.states.async_all.return_value = [mock_update]

        coord._create_backup = AsyncMock(return_value=False)
        coord._append_and_save_history = AsyncMock()

        asyncio.run(coord._async_run_updates_inner())

        self.assertEqual(coord.last_run_status, "Aborted (Backup Failed)")
        # update.install should NOT have been called
        install_calls = [
            c for c in mock_hass.services.async_call.call_args_list
            if len(c.args) >= 2 and c.args[0] == "update" and c.args[1] == "install"
        ]
        self.assertEqual(len(install_calls), 0)

    def test_next_run_time_with_seconds(self):
        """Test _next_run_time parsing of HH:MM:SS format from TimeSelector."""
        from custom_components.ha_auto_updater.const import CONF_FREQUENCY, CONF_TIME_OF_DAY, FREQ_DAILY

        mock_hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.data = {}
        mock_entry.options = {
            CONF_FREQUENCY: FREQ_DAILY,
            CONF_TIME_OF_DAY: "04:30:00",
        }
        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)
        next_run = coord._next_run_time()
        self.assertEqual(next_run.hour, 4)
        self.assertEqual(next_run.minute, 30)

    def test_async_install_single(self):
        """Test manual single entity install."""
        import asyncio
        from unittest.mock import AsyncMock
        from custom_components.ha_auto_updater.const import CONF_BACKUP_BEFORE_UPDATE

        mock_hass = MagicMock()
        mock_hass.config.config_dir = "."
        mock_hass.config.safe_mode = False
        mock_hass.services.async_call = AsyncMock()
        mock_hass.async_add_executor_job = AsyncMock()
        mock_hass.async_create_task.side_effect = lambda coro: coro.close()
        mock_entry = MagicMock()
        mock_entry.data = {}
        mock_entry.options = {
            CONF_BACKUP_BEFORE_UPDATE: False,
        }

        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)
        mock_state = MagicMock()
        mock_state.state = "on"
        mock_state.attributes = {
            "title": "Single Item",
            "installed_version": "1.0",
            "latest_version": "2.0",
            "release_url": None,
            "restart_required": False,
        }
        mock_hass.states.get.return_value = mock_state
        mock_hass.states.async_all.return_value = []
        coord._append_and_save_history = AsyncMock()
        coord._async_scan_pending = AsyncMock()

        success = asyncio.run(coord.async_install_single("update.single_item"))
        self.assertTrue(success)
        mock_hass.services.async_call.assert_called()

    def test_install_single_disk_space_guard(self):
        """Test that manual install aborts if disk space is insufficient."""
        import asyncio
        from custom_components.ha_auto_updater.const import CONF_MIN_DISK_SPACE_GB

        mock_hass = MagicMock()
        mock_hass.config.config_dir = "."
        mock_hass.config.safe_mode = False
        mock_entry = MagicMock()
        mock_entry.data = {}
        mock_entry.options = {CONF_MIN_DISK_SPACE_GB: 50.0}

        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)
        coord._check_disk_space = MagicMock(return_value=(False, 1.0))

        success = asyncio.run(coord.async_install_single("update.test"))
        self.assertFalse(success)

    def test_install_single_strict_backup_abort(self):
        """Test that manual install aborts if pre-update backup fails in strict backup mode."""
        import asyncio
        from unittest.mock import AsyncMock
        from custom_components.ha_auto_updater.const import (
            CONF_ABORT_ON_BACKUP_FAILURE,
            CONF_BACKUP_BEFORE_UPDATE,
        )

        mock_hass = MagicMock()
        mock_hass.config.config_dir = "."
        mock_hass.config.safe_mode = False
        mock_hass.services.async_call = AsyncMock()
        mock_hass.async_add_executor_job = AsyncMock()
        mock_entry = MagicMock()
        mock_entry.data = {}
        mock_entry.options = {
            CONF_BACKUP_BEFORE_UPDATE: True,
            CONF_ABORT_ON_BACKUP_FAILURE: True,
        }

        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)
        mock_state = MagicMock()
        mock_state.state = "on"
        mock_state.attributes = {"title": "Single Item"}
        mock_hass.states.get.return_value = mock_state
        coord._create_backup = AsyncMock(return_value=False)

        success = asyncio.run(coord.async_install_single("update.single_item"))
        self.assertFalse(success)

    def test_config_flow_schema(self):
        """Test that config flow schema contains TimeSelector and abort_on_backup_failure."""
        from custom_components.ha_auto_updater.config_flow import _build_schema
        from custom_components.ha_auto_updater.const import (
            CONF_ABORT_ON_BACKUP_FAILURE,
            CONF_TIME_OF_DAY,
        )

        schema = _build_schema({}, {})
        schema_keys = [k.schema for k in schema.schema.keys() if hasattr(k, "schema")]
        self.assertIn(CONF_TIME_OF_DAY, schema_keys)
        self.assertIn(CONF_ABORT_ON_BACKUP_FAILURE, schema_keys)


if __name__ == "__main__":
    unittest.main()
