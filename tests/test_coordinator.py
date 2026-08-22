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

        mock_hass = MagicMock()
        mock_hass.config.config_dir = "."
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.version = 1
        mock_entry.options = {"test_opt": True}

        coord = AutoUpdaterCoordinator(mock_hass, mock_entry)
        mock_hass.data = {"ha_auto_updater": {"test_entry": {"coordinator": coord}}}

        diag = asyncio.run(async_get_config_entry_diagnostics(mock_hass, mock_entry))
        self.assertEqual(diag["entry_id"], "test_entry")
        self.assertIn("system_info", diag)
        self.assertIn("coordinator_state", diag)


if __name__ == "__main__":
    unittest.main()
