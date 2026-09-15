# HA Auto Updater

A custom Home Assistant integration that automatically installs available updates on a schedule, with notifications, backup protection, pre-flight safety guards, auto-quarantine, event bus hooks, and full dashboard control.

> **Version:** 1.4.0 | **Requires:** Home Assistant 2023.1 or newer

---

## Features

- **Scheduled updates** — runs automatically at a configurable time each day; run time is editable directly from the device page
- **Granular category switches** — toggle auto-updates independently for Add-ons, HACS integrations, device firmware (any update entity with `device_class: firmware` — Shelly, Tasmota, WLED, Zigbee2MQTT, ESPHome, Z-Wave JS, Matter, UniFi, …), and HA Core/OS/Supervisor
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
- **Auto restart** — optionally restarts HA after installing HACS updates, which only load on restart (add-on, firmware and system updates never trigger it)
- **Interrupted-run recovery** — a run cut short by a Core/OS restart is written to history on startup and the remaining updates run in a follow-up pass
- **Release cooldown** — hold back each new version until it has been available a set number of days
- **Blocking entities** — skip automatic runs while guest, vacation or party mode (or any on/off entity) is on
- **Preview next run** — a button and a `dry_run` service that show what would install and why anything is skipped
- **Notification buttons** — Install now, Skip this run or Snooze from the pre-update notification on your phone
- **Repairs integration** — quarantined updates and repeatedly failing backups appear under Settings → System → Repairs
- **Release notes links** — pending list and notifications link straight to each update's release notes when available
- **Run history & status sensor** — stores recent runs and updates dedicated text sensor (`Running`, `Success`, `Success (deferred)`, `Partial failure`, `All failed`, `No updates`, `Interrupted`, `Aborted (Low Storage)`, `Aborted (Safe Mode)`, `Aborted (Backup Failed)`, `Aborted (Backup Timeout)`, `Aborted (Cancelled)`, `Skipped (Blocked)`, `Skipped (from notification)`, `Snoozed (from notification)`, `Aborted`, or `Never run`)

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
| Only install versions older than | `0` days | Release cooldown: hold back each new version until it has been available this many days (`0` = off). |
| Skip automatic runs while any of these is on | _(none)_ | Blocking entities: scheduled runs and follow-up passes are skipped while any of them is on. |

---

## Entities

### Switches (Feature Controls)

| Entity ID | Description |
|-----------|-------------|
| `switch.ha_auto_updater_auto_updater` | Master on/off — enables or disables scheduled updates |
| `switch.ha_auto_updater_auto_update_add_ons` | Include Home Assistant Add-ons in auto-updates |
| `switch.ha_auto_updater_auto_update_hacs_integrations` | Include HACS custom components in auto-updates |
| `switch.ha_auto_updater_auto_update_device_firmware` | Include device firmware (`device_class: firmware` update entities — Shelly, Tasmota, WLED, Zigbee2MQTT, ESPHome, Z-Wave JS, Matter, UniFi, …) in auto-updates |
| `switch.ha_auto_updater_auto_update_core_os` | Include Home Assistant Core, OS, and Supervisor in auto-updates |
| `switch.ha_auto_updater_auto_quarantine_failing_updates` | Auto-snooze entities failing 3 consecutive runs for 7 days |
| `switch.ha_auto_updater_backup_before_updating` | Create a full backup before installing updates |
| `switch.ha_auto_updater_auto_purge_old_backups` | Auto-delete pre-update backups created past retention period |
| `switch.ha_auto_updater_restart_after_updates` | Restart HA after installing HACS updates (add-on, firmware and Core/OS/Supervisor updates never trigger a restart) |
| `switch.ha_auto_updater_skip_beta_rc_versions` | Skip beta and release-candidate versions |
| `switch.ha_auto_updater_debug_logging` | Enable verbose debug logging |
| `switch.ha_auto_updater_notify_on_success` | Send notification when updates succeed |
| `switch.ha_auto_updater_notify_on_failure` | Send notification when updates fail |
| `switch.ha_auto_updater_weekly_digest` | Send weekly summary every 7 days |
| `switch.ha_auto_updater_notify_on_new_updates` | Push notification when background scan detects new updates |
| `switch.ha_auto_updater_notification_action_buttons` | Send the pre-update heads-up to your phone with Install now / Skip / Snooze buttons (needs a `notify.mobile_app_*` service) |

### Sensors

| Entity ID | Description |
|-----------|-------------|
| `sensor.ha_auto_updater_auto_updater_pending_updates` | Count of available updates. Attributes: `updates`, `release_notes`, `snoozed`, `cooling_down`. |
| `sensor.ha_auto_updater_auto_updater_failed_updates` | Number of updates that failed on the last run |
| `sensor.ha_auto_updater_auto_updater_last_run` | Timestamp of the last run |
| `sensor.ha_auto_updater_auto_updater_next_run` | Timestamp of the next scheduled run |
| `sensor.ha_auto_updater_auto_updater_last_run_count` | Number of updates installed on last run |
| `sensor.ha_auto_updater_auto_updater_last_run_duration` | Duration of last run in seconds |
| `sensor.ha_auto_updater_auto_updater_last_run_status` | Status: `Running`, `Success`, `Success (deferred)`, `Partial failure`, `All failed`, `No updates`, `Interrupted`, `Aborted (Low Storage)`, `Aborted (Safe Mode)`, `Aborted (Backup Failed)`, `Aborted (Backup Timeout)`, `Aborted (Cancelled)`, `Skipped (Blocked)`, `Skipped (from notification)`, `Snoozed (from notification)`, `Aborted`, or `Never run`. |
| `sensor.ha_auto_updater_auto_updater_history` | Last 10 run logs in `recent_runs` attribute |

### Binary Sensors & Selects & Buttons

| Entity ID | Type | Description |
|-----------|------|-------------|
| `binary_sensor.ha_auto_updater_updates_available` | Binary Sensor | `on` when updates are pending |
| `select.ha_auto_updater_run_time` | Select | Pick scheduled run time (12:00 AM – 11:00 PM) |
| `button.ha_auto_updater_run_updates_now` | Button | Immediately execute update process |
| `button.ha_auto_updater_scan_for_updates` | Button | Refresh pending list without installing |
| `button.ha_auto_updater_preview_next_run` | Button | Show what a run would install and skip, without installing anything |

---

## Event Bus Hooks

Auto Updater fires custom events on `hass.bus` throughout the update run lifecycle:

| Event Name | Payload Attributes | Description |
|------------|-------------------|-------------|
| `ha_auto_updater_start` | `available_count`, `titles` | Fired when an update run starts |
| `ha_auto_updater_backup_start` | `{}` | Fired when backup process begins |
| `ha_auto_updater_backup_complete` | `success` (`True`/`False`) | Fired when backup finishes |
| `ha_auto_updater_item_complete` | `entity_id`, `title`, `success`, `pending_verification`, `from`, `to` | Fired after each individual item finishes installing. `pending_verification` is `True` for a Core/OS/Supervisor install that was triggered but whose result is confirmed on a later scan. |
| `ha_auto_updater_finished` | `total_updated`, `total_failed`, `total_pending_verification`, `total_deferred`, `duration_seconds` | Fired when update run completes |

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
| `ha_auto_updater.run_updates` | `respect_blocking_entities` (opt, default false) | Immediately check for and install all available updates |
| `ha_auto_updater.install_single` | `entity_id` (req) | Install one update with the same safety checks and pre-update backup |
| `ha_auto_updater.dry_run` | _(none)_ | Preview what a run would do; returns the report as response data, or shows a notification |
| `ha_auto_updater.snooze_update` | `entity_id` (req), `days` (opt, default 7) | Temporarily skip an update entity |
| `ha_auto_updater.clear_snooze` | `entity_id` (opt) | Clear snooze for one entity or all snoozes |

---

## Release Cooldown, Blocking Entities & Notification Buttons

### Release cooldown

Set **Only install versions older than** to hold back each new version until it has been available that many days. Most broken releases get a hotfix within a few days, so a short cooldown avoids installing them.

- Update entities don't report release dates, so the cooldown counts from when Auto Updater first saw the version. Tracking always runs, even with the cooldown at `0`, so turning it on later uses real dates.
- A newer version replaces the one being held back and starts its own cooldown.
- Held-back updates are listed in the `cooling_down` attribute of the pending updates sensor, with when each becomes eligible.
- **Install Single Update** ignores the cooldown.

### Blocking entities

Pick entities under **Skip automatic runs while any of these is on**: a guests, vacation or party-mode toggle, a binary sensor, a switch, a schedule or a calendar.

- Scheduled runs and follow-up passes are skipped while any selected entity is on. The status becomes `Skipped (Blocked)` and a notification names the entity.
- The check repeats right before installs start, so turning a blocker on during the heads-up delay still stops the run.
- **Run updates now**, **Install Single Update** and `run_updates` ignore blockers. Call `run_updates` with `respect_blocking_entities: true` from an automation that should honour them.

### Preview next run

Press **Preview next run**, or call `ha_auto_updater.dry_run`, to see what a run would do right now without backing up or installing anything. The report lists what would install and in what order, what is skipped and why, anything that would stop the run, and whether a backup or restart would happen.

```yaml
action: ha_auto_updater.dry_run
response_variable: preview
```

Called with a response variable, the service returns the report as data. Without one, it shows a persistent notification.

### Notification buttons

When the notify service is a Home Assistant Companion app service (`notify.mobile_app_*`) and the pre-notify delay is above `0`, the heads-up also goes to your phone with three buttons:

| Button | Effect |
|--------|--------|
| Install now | Ends the wait and starts installing |
| Skip this run | Cancels this run (`Skipped (from notification)`); updates are tried again next run |
| Snooze 7 days | Snoozes every update in this run for 7 days (`Snoozed (from notification)`) |

Buttons only act on the run that sent them, so tapping one on an old notification does nothing. Turn off **Notification Action Buttons** to stop the phone heads-up.

### Repairs

Two problems are raised under **Settings → System → Repairs**:

- **Update quarantined:** an update was auto-quarantined after failing 3 runs in a row. Use **Fix** to clear the quarantine so the next run retries it. The issue also clears when the snooze ends or the update gets installed.
- **Pre-update backups are failing:** the backup failed on 2 runs in a row. The issue clears after the next successful backup.

---

## System Updates, Interrupted Runs & Verification

Core, OS and Supervisor updates restart Home Assistant (or the host) part-way through installing, so they are handled differently from add-ons, HACS and firmware:

- **Recognised by the entity registry.** Core, OS and Supervisor update entities are identified by their registry unique ID, so they are handled correctly whether their entity IDs end in `_update` (current HA), predate that naming, or were renamed.
- **Installed last, one per run.** System updates are sorted to the end of the queue. As soon as one is triggered, the remaining updates are **deferred** and a follow-up pass runs automatically about 10 minutes later (or 10 minutes after HA comes back up). The follow-up pass skips the backup and the pre-update notice, since both already happened.
- **Outcome is verified, not assumed.** A system install is triggered non-blocking, watched for a few minutes, and then listed as *pending verification*. On the next background scan the entity's `installed_version` is checked: a matching version becomes a success entry in history, an unchanged one becomes a failure (and counts toward auto-quarantine). Until then it is not reported as a success.
- **Interrupted runs are reconstructed.** A run marker (`ha_auto_updater_run.json`) is written before each install. If HA restarts mid-run, the next startup writes a history entry from it (`Interrupted` status), verifies whatever was mid-install by entity state, and schedules the follow-up pass for anything not yet attempted.
- **Reloading the integration cancels a run cleanly.** A run waiting in the pre-update delay, a stagger delay or a retry wait is cancelled on unload and its partial results are saved (`Aborted (Cancelled)`).

History entries record both titles (for display) and entity ids (`updated_entities`, `failed_entities`), so auto-quarantine keys on the entity rather than the title. Two devices that share a title no longer share a failure count.

### Backups on Home Assistant 2025.1 and newer

On HA 2025.1+ the pre-update backup goes through the backup manager rather than the `backup.create` service. That lets the integration name the backup (`pre_update_YYYYMMDD_HHMM`), learn its id, and delete it later when **Auto-purge old backups** is on. On HA OS / Supervised the backup includes all add-ons; on Container it covers Home Assistant and its database. The backup is stored on the local backup agent only. Older HA versions fall back to `backup.create` / `hassio.backup_full` as before.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Run aborted with storage warning | Free disk space below `min_disk_space_gb` | Free up disk space or lower threshold in Configure |
| Run aborted with Safe Mode warning | Home Assistant is running in Safe Mode | Resolve safe mode issues and restart HA |
| Add-on or HACS update skipped | Category switch is OFF | Turn ON `switch.ha_auto_updater_auto_update_add_ons` or `switch.ha_auto_updater_auto_update_hacs_integrations` |
| Failing update snoozed automatically | Auto-Quarantine triggered after 3 failures | Check update error; call `ha_auto_updater.clear_snooze` when resolved |
| Status shows `Interrupted` | HA restarted mid-run (usually a Core/OS update) | Nothing to do — the partial run was reconstructed and a follow-up pass is scheduled |
| Status shows `Aborted (Backup Timeout)` | The pre-update backup took longer than 30 minutes and may still be running | The run is skipped even without strict backup mode, so nothing installs mid-backup. Updates retry on the next run. |
| Zigbee firmware never installs | Before 1.3.1, hex firmware versions such as `0x1b000045` were mistaken for betas | Fixed in 1.3.1 |
| Update never installs and is listed under `cooling_down` | The release cooldown is holding back a version that is still new | Wait, lower the cooldown, or use Install Single Update |
| Status shows `Skipped (Blocked)` | A blocking entity was on when the run started | Turn it off, or run updates manually |
| No buttons on the phone notification | The notify service isn't `notify.mobile_app_*`, the pre-notify delay is `0`, or Notification Action Buttons is off | Check those three settings |
| Core/OS update listed as "pending verification" | Result is confirmed on the next 30-minute scan | Wait for the next scan, or press **Scan for updates** |

---

## Diagnostics Platform

HA Auto Updater supports Home Assistant's native Diagnostics platform. You can download a full, sanitized diagnostic JSON payload containing config entry options, current coordinator status, disk space metrics, and recent history logs directly from:

**Settings → Devices & Services → HA Auto Updater → Download Diagnostics**

---

## State Files

The integration keeps its own state in small JSON files in the Home Assistant config directory. They are safe to delete while the integration is unloaded; deleting them only resets the listed data.

| File | Contents |
|------|----------|
| `ha_auto_updater_history.json` | Last 50 runs (feeds the history sensor, weekly digest and failure counting) |
| `ha_auto_updater_run.json` | Run currently in progress, system updates awaiting verification, and updates deferred to the follow-up pass. Removed automatically when nothing is outstanding. |
| `ha_auto_updater_snooze.json` | Per-entity snooze expiry timestamps (manual snoozes and auto-quarantine) |
| `ha_auto_updater_backups.json` | Pre-update backups the integration created, for auto-purge, and the count of consecutive backup failures |
| `ha_auto_updater_seen.json` | When each pending version was first seen, for the release cooldown |
| `ha_auto_updater_digest.json` | When the last weekly digest was sent |

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
