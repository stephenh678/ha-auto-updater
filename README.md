# HA Auto Updater

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![HACS Validation](https://github.com/stephenh678/ha-auto-updater/actions/workflows/hacs.yaml/badge.svg)](https://github.com/stephenh678/ha-auto-updater/actions/workflows/hacs.yaml)
[![Validate with hassfest](https://github.com/stephenh678/ha-auto-updater/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/stephenh678/ha-auto-updater/actions/workflows/hassfest.yaml)
[![GitHub Release](https://img.shields.io/github/v/release/stephenh678/ha-auto-updater)](https://github.com/stephenh678/ha-auto-updater/releases)
[![License](https://img.shields.io/github/license/stephenh678/ha-auto-updater)](LICENSE)

A custom Home Assistant integration that automatically installs available updates on a schedule — with backup protection, pre-flight disk space guards, granular category switches, auto-quarantine, custom event hooks, and full dashboard control.

> **Version:** 1.2.0 | **Requires:** Home Assistant 2023.1 or newer

---

## Why?

Home Assistant surfaces updates but won't install them for you. **HA Auto Updater** does — safely and on your terms:

- **Pre-flight safety guards:** Verifies host disk space (`min_disk_space_gb`) and aborts if HA is in Safe Mode.
- **Granular category control:** Toggle updates independently for Add-ons, HACS integrations, device firmware, and HA Core/OS.
- **Backup before update:** Takes a full backup *right before* installing (only when there's actually something to install — never on a wasteful schedule).
- **Auto-Quarantine:** Automatically snoozes components that fail 3 consecutive runs for 7 days to prevent repeated failure loops.
- **Major-version & Beta protection:** Skips major version bumps and beta/RC releases by default.
- **Per-update snooze:** Lets you snooze a specific update for a few days when a release looks risky.
- **Automations & Event hooks:** Fires rich events (`ha_auto_updater_start`, `_finished`, etc.) on `hass.bus` for Node-RED and custom automations.
- **Notifications & Digest:** Persistent notifications, optional mobile push, and weekly summaries.

---

## Features

- **Scheduled updates** — runs hourly, daily, or weekly; run time is editable right from the device page
- **Category update toggles** — dedicated switches for Add-ons, HACS, Device Firmware, and Core/OS
- **Storage & Safe Mode guards** — aborts runs if free disk space is below minimum threshold or if HA is in Safe Mode
- **Auto-Quarantine** — automatically snoozes items failing 3 consecutive runs for 7 days
- **Backup before update** — triggers a full HA backup before installing (only when updates are pending)
- **Backup auto-purge** — deletes pre-update backups *it* created once they pass a configurable age
- **Major-version protection** — uses `AwesomeVersion` for strict SemVer and CalVer version checks
- **Beta/RC skipping** — optionally skips pre-release versions
- **Per-update snooze** — temporarily skip a specific update for N days via service call
- **Event Bus hooks** — fires structured events on `hass.bus` for external automations
- **Auto restart** — optionally restarts HA after installing updates that require it
- **Rich sensors** — pending count, failed count, last-run status/duration/count, next run, history, and binary sensor

---

## Installation

### Via HACS (recommended)

1. In Home Assistant, open **HACS → Integrations**.
2. Click the **⋮** menu (top right) → **Custom repositories**.
3. Add this repository:
   - **Repository:** `https://github.com/stephenh678/ha-auto-updater`
   - **Category:** `Integration`
4. Find **HA Auto Updater** in HACS, click **Download**, and restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration**, search for **HA Auto Updater**, and complete the setup.

### Manual

1. Copy the `custom_components/ha_auto_updater` folder into your HA `config/custom_components/` directory.
2. Restart Home Assistant.
3. Add the integration via **Settings → Devices & Services → Add Integration → HA Auto Updater**.

---

## Documentation

Full configuration options, every entity, service, event hook, and troubleshooting guide:

➡️ **[Detailed documentation](custom_components/ha_auto_updater/README.md)**

---

## Contributing

Issues and pull requests are welcome. Please use the [issue tracker](https://github.com/stephenh678/ha-auto-updater/issues) for bugs and feature requests.

## Contributors

- [@stephenh678](https://github.com/stephenh678) — Author & Lead Maintainer
- **Antigravity AI** — AI Coding Assistant & Co-Developer (Google DeepMind)

---

## License

Released under the [MIT License](LICENSE).
