# HA Auto Updater

A custom Home Assistant integration that automatically installs available updates on a schedule, with notifications, backup protection, pre-flight safety guards, auto-quarantine, event bus hooks, and full dashboard control.

> **Version:** 1.2.0 | **Requires:** Home Assistant 2023.1 or newer

---

## Features

- **Scheduled updates** — runs automatically at a configurable time each day; run time is editable directly from the device page
- **Granular category switches** — toggle auto-updates independently for Add-ons, HACS integrations, device firmware (ESPHome/Z-Wave/Matter), and HA Core/OS/Supervisor
- **Pre-flight disk space guard** — checks available storage (`min_disk_space_gb`) before backups or installs and aborts safely with a notification if free space is low
- **Safe Mode guard** — automatically skips update runs if Home Assistant is running in Safe Mode
- **Auto-Quarantine** — automatically snoozes components failing 3 consecutive runs for 7 days to prevent repeated installation loops
- **Custom Event Bus hooks** — fires structured events (`ha_auto_updater_start`, `_backup_start`, `_backup_complete`, `_item_complete`, `_finished`) on `hass.bus` for Node-RED and external automations
- **AwesomeVersion integration** — robust SemVer and CalVer version comparison engine
- **Backup before update** — optionally triggers a full HA backup before installing anything
- **Backup auto-purge** — deletes pre-update backups it created once they pass a configurable age (your manual backups are never touched)
- **Pending update scan** — background scan every 30 minutes keeps the count accurate without waiting for the next scheduled run
- **Major version protection** — skips major version bumps by default (calendar-versioned packages like HA Core/OS/Supervisor are handled correctly and are never filtered)
- **Beta/RC skipping** — optionally skips pre-release versions
- **Per-update snooze** — temporarily skip a specific update for a set number of days via service call
- **Auto restart** — optionally restarts HA after installing updates that require it
- **Release notes links** — pending list and notifications link straight to each update's release notes when available
- **Run history & status sensor** — stores recent runs and updates dedicated text sensor (`Running`, `Success`, `Partial failure`, `All failed`, `No updates`, `Aborted (Low Storage)`, `Aborted (Safe Mode)`, `Aborted`, or `Never run`)

---

## Requirements

| Requirement | Details |
|-------------|---------|
| Home Assistant | 2023.1 or newer |
| Installation method | HACS or Manual (custom component) |
| HACS | Compatible, optional |
| External dependencies | `awesomeversion` (built into Home Assistant) |

---

## Installation

### Manual Installation

1. Copy the `ha_auto_updater` folder into your `/config/custom_components/` directory. The final path should be:
   ```
   /config/custom_components/ha_auto_updater/__init__.py
   ```
2. Restart Home Assistant fully (**Settings → System → Restart**).
3. Go to **Settings → Devices & Services → Add Integration**.
4. Search for **HA Auto Updater** and complete setup.

---

## Configuration

All options can be changed anytime via **Settings → Devices & Services → HA Auto Updater → Configure**.

| Option | Default | Description |
|--------|---------|-------------|
| How often to check for updates | Daily | How often Auto Updater looks for and installs updates: `Hourly`, `Daily`, or `Weekly`. |
| Time of day to run | `02:00` | Scheduled time for Daily and Weekly runs. Editable on device page. |
| Day of week | Monday | Scheduled day for Weekly runs. |
| Pre-notify delay | `5` min | Minutes to wait (with a heads-up notification) before installing once updates are found. |
| Stagger delay | `30` sec | Seconds to wait between individual update installs. |
| Retry delay | `60` sec | Seconds to wait before retrying a failed install. |
| Minimum free disk space | `1.5` GB | Runs abort if host free storage drops below this threshold. |
| Include major versions | Off | Install updates that bump major version numbers. (Core/OS calendar versions are never filtered). |
| Skip beta/RC | On | Skip pre-release versions. |
| Backup keep days | `7` | Days to retain pre-update backups when auto-purge is enabled. |
| Notify service | _(none)_ | Optional `domain.service` to forward notifications to (e.g. `notify.mobile_app_my_phone`). |
| Max updates per run | `0` | Cap on updates per run (`0` = unlimited). |
| Weekly digest | Off | Send a weekly summary of update activity every 7 days. |

---

## Entities

### Switches (Feature Controls)

| Entity ID | Description |
|-----------|-------------|
| `switch.ha_auto_updater_auto_updater` | Master on/off — enables or disables scheduled updates |
| `switch.ha_auto_updater_auto_update_add_ons` | Include Home Assistant Add-ons in auto-updates |
| `switch.ha_auto_updater_auto_update_hacs_integrations` | Include HACS custom components in auto-updates |
| `switch.ha_auto_updater_auto_update_device_firmware` | Include ESPHome, Z-Wave JS, Matter firmware in auto-updates |
| `switch.ha_auto_updater_auto_update_core_os` | Include Home Assistant Core, OS, and Supervisor in auto-updates |
| `switch.ha_auto_updater_auto_quarantine_failing_updates` | Auto-snooze entities failing 3 consecutive runs for 7 days |
| `switch.ha_auto_updater_backup_before_updating` | Create a full backup before installing updates |
| `switch.ha_auto_updater_auto_purge_old_backups` | Auto-delete pre-update backups created past retention period |
| `switch.ha_auto_updater_restart_after_updates` | Restart HA after installing updates that require it |
| `switch.ha_auto_updater_skip_beta_rc_versions` | Skip beta and release-candidate versions |
| `switch.ha_auto_updater_debug_logging` | Enable verbose debug logging |
| `switch.ha_auto_updater_notify_on_success` | Send notification when updates succeed |
| `switch.ha_auto_updater_notify_on_failure` | Send notification when updates fail |
| `switch.ha_auto_updater_weekly_digest` | Send weekly summary every 7 days |
| `switch.ha_auto_updater_notify_on_new_updates` | Push notification when background scan detects new updates |

### Sensors

| Entity ID | Description |
|-----------|-------------|
| `sensor.ha_auto_updater_auto_updater_pending_updates` | Count of available updates. Attributes: `updates`, `release_notes`, `snoozed`. |
| `sensor.ha_auto_updater_auto_updater_failed_updates` | Number of updates that failed on the last run |
| `sensor.ha_auto_updater_auto_updater_last_run` | Timestamp of the last run |
| `sensor.ha_auto_updater_auto_updater_next_run` | Timestamp of the next scheduled run |
| `sensor.ha_auto_updater_auto_updater_last_run_count` | Number of updates installed on last run |
| `sensor.ha_auto_updater_auto_updater_last_run_duration` | Duration of last run in seconds |
| `sensor.ha_auto_updater_auto_updater_last_run_status` | Status: `Running`, `Success`, `Partial failure`, `All failed`, `No updates`, `Aborted (Low Storage)`, `Aborted (Safe Mode)`, `Aborted`, or `Never run`. |
| `sensor.ha_auto_updater_auto_updater_history` | Last 10 run logs in `recent_runs` attribute |

### Binary Sensors & Selects & Buttons

| Entity ID | Type | Description |
|-----------|------|-------------|
| `binary_sensor.ha_auto_updater_updates_available` | Binary Sensor | `on` when updates are pending |
| `select.ha_auto_updater_run_time` | Select | Pick scheduled run time (12:00 AM – 11:00 PM) |
| `button.ha_auto_updater_run_updates_now` | Button | Immediately execute update process |
| `button.ha_auto_updater_scan_for_updates` | Button | Refresh pending list without installing |

---

## Event Bus Hooks

Auto Updater fires custom events on `hass.bus` throughout the update run lifecycle:

| Event Name | Payload Attributes | Description |
|------------|-------------------|-------------|
| `ha_auto_updater_start` | `available_count`, `titles` | Fired when an update run starts |
| `ha_auto_updater_backup_start` | `{}` | Fired when backup process begins |
| `ha_auto_updater_backup_complete` | `success` (`True`/`False`) | Fired when backup finishes |
| `ha_auto_updater_item_complete` | `entity_id`, `title`, `success`, `from`, `to` | Fired after each individual item finishes installing |
| `ha_auto_updater_finished` | `total_updated`, `total_failed`, `duration_seconds` | Fired when update run completes |

### Event Bus Automation Example
```yaml
trigger:
  - platform: event
    event_type: ha_auto_updater_finished
condition:
  - condition: numeric_state
    entity_id: trigger.event.data.total_failed
    above: 0
action:
  - service: notify.telegram
    data:
      message: "⚠️ Auto Updater finished with {{ trigger.event.data.total_failed }} failure(s)."
```

---

## Services

| Service | Fields | Description |
|---------|--------|-------------|
| `ha_auto_updater.run_updates` | _(none)_ | Immediately check for and install all available updates |
| `ha_auto_updater.snooze_update` | `entity_id` (req), `days` (opt, default 7) | Temporarily skip an update entity |
| `ha_auto_updater.clear_snooze` | `entity_id` (opt) | Clear snooze for one entity or all snoozes |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Run aborted with storage warning | Free disk space below `min_disk_space_gb` | Free up disk space or lower threshold in Configure |
| Run aborted with Safe Mode warning | Home Assistant is running in Safe Mode | Resolve safe mode issues and restart HA |
| Add-on or HACS update skipped | Category switch is OFF | Turn ON `switch.ha_auto_updater_auto_update_add_ons` or `switch.ha_auto_updater_auto_update_hacs_integrations` |
| Failing update snoozed automatically | Auto-Quarantine triggered after 3 failures | Check update error; call `ha_auto_updater.clear_snooze` when resolved |

---

## Diagnostics Platform

HA Auto Updater supports Home Assistant's native Diagnostics platform. You can download a full, sanitized diagnostic JSON payload containing config entry options, current coordinator status, disk space metrics, and recent history logs directly from:

**Settings → Devices & Services → HA Auto Updater → Download Diagnostics**

---

## File Structure

```
custom_components/ha_auto_updater/
├── __init__.py          # Integration setup and unload
├── manifest.json        # Integration metadata and version
├── const.py             # Constants and default values
├── config_flow.py       # Setup and options UI flow
├── coordinator.py       # Core update logic and scheduling
├── diagnostics.py       # Native Home Assistant diagnostics platform
├── sensor.py            # Sensor entities (categorized as DIAGNOSTIC)
├── binary_sensor.py     # Updates-available binary sensor
├── switch.py            # Switch (control & CONFIG) entities
├── select.py            # Select (dropdown) entities
├── button.py            # Button entities
├── services.yaml        # Service definitions
├── strings.json         # UI strings
├── icons.json           # MDI icon declarations for HA device page
└── translations/        # Localization files
    └── en.json
```

---

## Contributors

- [@stephenh678](https://github.com/stephenh678) — Author & Lead Maintainer
- **Antigravity AI** — AI Coding Assistant & Co-Developer (Google DeepMind)

---

## License

Released under the [MIT License](../../LICENSE).
