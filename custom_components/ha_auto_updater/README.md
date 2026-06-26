# HA Auto Updater

A custom Home Assistant integration that automatically installs available updates on a schedule, with notifications, backup protection, and full dashboard control.

> **Version:** 1.1.0 | **Requires:** Home Assistant 2023.1 or newer

---

## Features

- **Scheduled updates** — runs automatically at a configurable time each day; run time is editable directly from the device page
- **Backup before update** — optionally triggers a full HA backup before installing anything
- **Backup auto-purge** — optionally deletes pre-update backups it created once they pass a configurable age, so they don't accumulate (your manual backups are never touched)
- **Pending update scan** — background scan every 30 minutes keeps the count accurate without waiting for the next scheduled run
- **Major version protection** — skips major version bumps by default (calendar-versioned packages like HA Core/OS/Supervisor are handled correctly and are never filtered)
- **Beta/RC skipping** — optionally skips pre-release versions
- **Per-update snooze** — temporarily skip a specific update for a set number of days (e.g. wait out a buggy release) via a service call
- **Auto restart** — optionally restarts HA after installing updates that require it (HACS / custom components)
- **Release notes links** — pending list and success notifications link straight to each update's release notes when available
- **Persistent notifications** — success/failure notifications survive HA restarts and are restored on reboot
- **New-update alerts** — optional push notification when new updates are detected during a background scan
- **Repeated-failure escalation** — if a component fails several runs in a row, the failure notification flags it for manual attention
- **Critical toggle alerts** — persistent notifications appear when key controls (backup, auto-restart, etc.) are changed
- **Run history** — stores the last 10 update runs with timestamps, counts, durations, and per-update results
- **Run status sensor** — dedicated text sensor showing the outcome of the last run (`Running`, `Success`, `Partial failure`, `All failed`, `No updates`, `Aborted`, `Never run`) for easy use in automations
- **Updates-available binary sensor** — simple on/off sensor for dashboard badges and automations
- **Long-term statistics** — pending, failed, last-run-count, and run-duration sensors participate in HA's built-in statistics tracking
- **Install timeout & retry** — each update gets a 5-minute timeout and one automatic retry (retry delay is configurable); a stuck install no longer blocks the rest of the queue
- **Atomic history writes** — history file is written safely so a crash mid-save cannot corrupt it

---

## Requirements

| Requirement | Details |
|-------------|---------|
| Home Assistant | 2023.1 or newer |
| Installation method | Manual (custom component) |
| HACS | Not required, but compatible |
| External dependencies | None |

---

## Installation

### Manual Installation

1. Download or clone this repository.
2. Copy the `ha_auto_updater` folder into your `/config/custom_components/` directory on your HA server. The final path should be:
   ```
   /config/custom_components/ha_auto_updater/__init__.py
   ```
3. Restart Home Assistant fully (**Settings → System → Restart**).
4. Go to **Settings → Devices & Services → Add Integration**.
5. Search for **HA Auto Updater** and click it.
6. Complete the setup flow (see [Configuration](#configuration) below).

### Verifying the Installation

After restarting, the integration should appear under **Settings → Devices & Services**. If it shows as **Not loaded**, check **Settings → System → Logs** for errors. Common causes:

- Files not copied to the correct path
- `manifest.json` saved with a UTF-8 BOM (re-save the file as UTF-8 without BOM)
- A syntax error in one of the `.py` files

### Updating the Integration

1. Copy the updated files to `/config/custom_components/ha_auto_updater/` on your HA server, overwriting the existing ones.
2. Go to **Settings → Devices & Services → HA Auto Updater** and click **Reload**, or do a full HA restart.

---

## Configuration

The setup flow runs the first time you add the integration. All options can be changed afterward via **Settings → Devices & Services → HA Auto Updater → Configure**.

> **Important — what "frequency" means:** The frequency setting controls **how often Auto Updater checks for and installs updates** — it is *not* a backup schedule. A backup is only ever taken as a step **inside an install run, and only when there are actually updates to install**. If a scheduled check finds nothing, nothing installs and **no backup is made**. So with "Daily," a week with no updates produces zero backups. See [Backups & Auto-Purge](#backups--auto-purge).

| Option | Default | Description |
|--------|---------|-------------|
| How often to check for updates | Daily | How often Auto Updater looks for and installs updates: `Hourly`, `Daily`, or `Weekly`. **Hourly** runs at the top of every hour and ignores the time/day below. **Daily** runs once a day at the set time. **Weekly** runs once a week on the set day and time. (A background scan also refreshes the pending count every 30 minutes regardless of this setting, but never installs.) |
| Time of day to run | `02:00` | Time of day the run happens (24-hour format). Used for **Daily** and **Weekly**; ignored for **Hourly**. Can also be changed via the **Run Time** entity on the device page. |
| Day of week | Monday | Day the run happens. Only used when frequency is set to **Weekly**. |
| Pre-notify delay | `5` min | Minutes to wait (with a heads-up notification) before installing once updates are found |
| Stagger delay | `30` sec | Seconds to wait between each individual update install |
| Retry delay | `60` sec | Seconds to wait before the single automatic retry of a failed install |
| Include major versions | Off | Install updates that bump the major version number. HA Core/OS/Supervisor are always updated regardless of this setting. |
| Skip beta/RC | On | Skip pre-release and release-candidate versions |
| Backup before update | On | Take a full HA backup before installing any updates |
| Backup keep days | `7` | When **Auto-Purge Old Backups** is on, delete pre-update backups older than this many days |
| Auto restart | Off | Restart HA automatically after installing updates that require it (e.g. HACS integrations, custom components) |
| Notify on success | On | Show a persistent notification when updates install successfully |
| Notify on failure | On | Show a persistent notification when one or more updates fail |
| Notify on new updates | Off | Send a push notification (via Notify service) when a scan finds newly available updates |
| Notify service | _(none)_ | Optional `domain.service` to forward notifications to (e.g. `notify.mobile_app_my_phone`) |
| Max updates per run | `0` | Cap on how many updates to install per run. `0` means unlimited. |
| Weekly digest | Off | Send a weekly summary of update activity every 7 days |
| Debug logging | Off | Write verbose output to the HA log |

---

## Entities

### Sensors

| Entity ID | Description |
|-----------|-------------|
| `sensor.ha_auto_updater_auto_updater_pending_updates` | Number of updates currently available. Updates every 30 minutes automatically. Attributes: `updates` (each pending update with source `HA System` / `Add-on` / `HACS` / `Custom`), `release_notes` (title + URL per update), and `snoozed` (currently snoozed entities). |
| `sensor.ha_auto_updater_auto_updater_failed_updates` | Number of updates that failed on the last run |
| `sensor.ha_auto_updater_auto_updater_last_run` | Timestamp of the last update run |
| `sensor.ha_auto_updater_auto_updater_next_run` | Timestamp of the next scheduled run |
| `sensor.ha_auto_updater_auto_updater_last_run_count` | How many updates were installed on the last run |
| `sensor.ha_auto_updater_auto_updater_last_run_duration` | How long the last run took, in seconds |
| `sensor.ha_auto_updater_auto_updater_last_run_status` | Outcome of the last run: `Running`, `Success`, `Partial failure`, `All failed`, `No updates`, `Aborted`, or `Never run`. Icon changes to match each state. |
| `sensor.ha_auto_updater_auto_updater_history` | Full run history stored as the `recent_runs` attribute (last 10 runs) |

### Binary Sensors

| Entity ID | Description |
|-----------|-------------|
| `binary_sensor.ha_auto_updater_updates_available` | On when one or more updates are pending. Cleaner for dashboard badges and automations than comparing the pending-count sensor to zero. |

### Switches

| Entity ID | Description |
|-----------|-------------|
| `switch.ha_auto_updater_auto_updater` | Master on/off — enables or disables all scheduled updates |
| `switch.ha_auto_updater_backup_before_updating` | Create a full backup before installing updates |
| `switch.ha_auto_updater_auto_purge_old_backups` | Auto-delete pre-update backups it created once they pass the configured age |
| `switch.ha_auto_updater_restart_after_updates` | Restart HA after installing updates that require it |
| `switch.ha_auto_updater_skip_beta_rc_versions` | Skip beta and release-candidate versions |
| `switch.ha_auto_updater_debug_logging` | Enable verbose debug logging |
| `switch.ha_auto_updater_notify_on_success` | Send a notification when updates succeed |
| `switch.ha_auto_updater_notify_on_failure` | Send a notification when updates fail |
| `switch.ha_auto_updater_weekly_digest` | Send a weekly summary of update activity every 7 days |
| `switch.ha_auto_updater_notify_on_new_updates` | Send a push notification when new updates are detected during a background scan |

### Selects

| Entity ID | Description |
|-----------|-------------|
| `select.ha_auto_updater_run_time` | Scheduled run time — pick any hour from 12:00 AM to 11:00 PM. Changes take effect immediately without opening Configure. |

### Buttons

| Entity ID | Description |
|-----------|-------------|
| `button.ha_auto_updater_run_updates_now` | Immediately run the full update process |
| `button.ha_auto_updater_scan_for_updates` | Scan for pending updates without installing anything |

### Services

| Service | Description |
|---------|-------------|
| `ha_auto_updater.run_updates` | Immediately check for and install all available updates |
| `ha_auto_updater.snooze_update` | Temporarily skip one update entity. Fields: `entity_id` (required), `days` (optional, default 7) |
| `ha_auto_updater.clear_snooze` | Clear the snooze for one `entity_id`, or all snoozes if none is given |

**Example — snooze HA Core for 14 days:**
```yaml
service: ha_auto_updater.snooze_update
data:
  entity_id: update.home_assistant_core_update
  days: 14
```

---

## How It Works

### Scheduled Run

At the configured time each day, the integration:

1. Checks all `update.*` entities in HA for available updates (`state = "on"`)
2. Filters out excluded entities, snoozed entities, major version bumps, and beta/RC releases (per your settings)
3. Optionally triggers a full HA backup and waits for it to complete (then auto-purges old pre-update backups if enabled)
4. Re-checks each entity's state immediately before installing (skips anything already up-to-date)
5. Installs each update one at a time using `update.install`, with a 5-minute timeout and one automatic retry per update
6. Records the result (success or failure) and run duration for each update
7. Saves the run to history (written atomically to prevent file corruption)
8. Updates the `last_run_status` sensor
9. Sends persistent notifications if enabled (flagging any component that has failed several runs in a row)
10. Optionally restarts HA if any installed updates require it

### Backups & Auto-Purge

This integration does not implement its own backup logic — it calls Home Assistant's **built-in backup services** (`backup.create` on modern HA, falling back to the Supervisor's `hassio.backup_full` on HA OS / Supervised). The backups it produces are standard full HA backups, identical to pressing **Create backup** under **Settings → System → Backups**, and they land wherever your HA backup configuration sends them.

**Why a separate purge is needed:** HA's built-in retention policy ("keep last N" / "keep for N days") only applies to the **automatic backups** created by HA's own backup *schedule*. Backups made on-demand via a service call — which is how this integration takes its pre-update backup right before installing — are treated as **manual** backups and are **not** covered by that policy. HA keeps manual backups indefinitely, so without help they would accumulate over time.

When **Auto-Purge Old Backups** is enabled, after each new pre-update backup the integration deletes any backups *it previously created* that are older than the **Backup keep days** setting. It only tracks and removes its own `pre_update_*` backups — your manual backups, HA's scheduled automatic backups, and those made by other tools are never touched. Backup deletion relies on a backup-delete service being available in your HA version; if cleanup isn't working, check the logs for `no delete service available`.

### Snoozing an Update

Use the `ha_auto_updater.snooze_update` service to skip a specific update for a number of days — handy when a release is known-buggy and you want to wait for a patch without permanently excluding the component. Snoozes persist across restarts and appear in the pending sensor's `snoozed` attribute. Clear them with `ha_auto_updater.clear_snooze`.

### Background Scan

Every 30 minutes, a lightweight scan checks all `update.*` entities and updates the pending count sensor. This keeps the count current between scheduled runs without installing anything.

### System Update Handling

Home Assistant Core, Supervisor, and Operating System updates are treated differently from other updates:

- They **always bypass the major version filter** — HA uses calendar versioning (e.g. `2026.4.0`) which would otherwise be incorrectly flagged as a major bump.
- They use **non-blocking service calls** — the Supervisor and OS updates trigger a restart mid-install, which would cause a blocking call to time out. Non-blocking calls handle this correctly.

### Notification Restoration

If HA restarts within 2 hours of a completed update run, any persistent notifications from that run are automatically re-posted with a *(Restored after restart)* note, so you don't miss update results.

---

## Notifications

Persistent notifications appear in the HA notification panel in the following situations:

| Trigger | Notification |
|---------|-------------|
| Updates installed successfully | Lists all updated components, with release-notes links where available |
| One or more updates failed | Lists failed components; flags any that have failed several runs in a row |
| New updates detected | (Push, if **Notify on new updates** is on) Sent when a scan finds newly available updates |
| Backup started | "Creating backup…" — dismissed automatically when complete |
| Backup completed | Confirms backup succeeded before installs begin |
| Auto Updater switched off | Warning that no updates will run |
| Backup Before Update switched off | Warning that updates will run without a backup |
| Auto-Purge Old Backups switched on | Confirms old pre-update backups will be deleted past the retention period |
| Auto Restart switched on | Reminder that HA may restart after updates |
| Debug Logging switched on | Reminder to turn off when done |
| Weekly Digest switched on | Confirms weekly summaries are enabled |

---

## Automations & Advanced Use

The integration's sensors and buttons are standard HA entities and can be used in automations.

**Example — notify if updates are pending for more than 24 hours:**
```yaml
trigger:
  - platform: numeric_state
    entity_id: sensor.ha_auto_updater_auto_updater_pending_updates
    above: 0
    for:
      hours: 24
action:
  - service: notify.mobile_app
    data:
      message: "{{ states('sensor.ha_auto_updater_auto_updater_pending_updates') }} HA updates have been pending for over 24 hours."
```

**Example — alert when an update run partially or fully fails:**
```yaml
trigger:
  - platform: state
    entity_id: sensor.ha_auto_updater_last_run_status
    to:
      - "Partial failure"
      - "All failed"
action:
  - service: notify.mobile_app
    data:
      message: "Auto Updater run result: {{ states('sensor.ha_auto_updater_last_run_status') }}. Check HA logs for details."
```

**Example — run updates after a backup completes:**
```yaml
trigger:
  - platform: state
    entity_id: sensor.backup_manager_last_backup
action:
  - service: button.press
    target:
      entity_id: button.ha_auto_updater_run_updates_now
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Integration shows "Not loaded" | Import error or bad manifest.json | Check HA logs; re-save manifest.json as UTF-8 without BOM |
| Pending count shows 0 but updates exist | `update.*` entities reporting unexpected state | Press **Scan for Updates** to force a refresh |
| Supervisor update not installing | Service call timing out | This is handled automatically with non-blocking calls in v1.5+ |
| Notifications disappear after reboot | Expected HA behavior | Notifications are restored automatically if the run was within 2 hours |
| "None" appears as update name | Update entity has no `title` attribute | Fixed in v1.2+ — update to latest version |
| Major update skipped unexpectedly | Include major versions is off | Enable **Include major versions** in configuration, or press **Run Updates Now** after enabling |
| An update is being skipped every run | It may be snoozed | Check the pending sensor's `snoozed` attribute; clear it with `ha_auto_updater.clear_snooze` |
| Old pre-update backups not deleted | Auto-purge off, or no delete service on your HA version | Enable **Auto-Purge Old Backups**; check logs for `no delete service available` |

---

## File Structure

```
custom_components/ha_auto_updater/
├── __init__.py          # Integration setup and unload
├── manifest.json        # Integration metadata and version
├── const.py             # Constants and default values
├── config_flow.py       # Setup and options UI flow
├── coordinator.py       # Core update logic and scheduling
├── sensor.py            # Sensor entities
├── binary_sensor.py     # Updates-available binary sensor
├── switch.py            # Switch (control) entities
├── select.py            # Select (dropdown) entities
├── button.py            # Button entities
├── services.yaml        # Service definitions
├── strings.json         # UI strings
├── icons.json           # MDI icon declarations for HA device page
├── icon.png             # Integration icon fallback (256x256, transparent PNG)
├── brands/
│   └── icon.png         # Icon shown on the Integrations card (256x256, transparent PNG)
└── translations/        # Localization files
    └── en.json
```
