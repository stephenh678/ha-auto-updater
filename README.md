# HA Auto Updater

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![HACS Validation](https://github.com/stephenh678/ha-auto-updater/actions/workflows/hacs.yaml/badge.svg)](https://github.com/stephenh678/ha-auto-updater/actions/workflows/hacs.yaml)
[![Validate with hassfest](https://github.com/stephenh678/ha-auto-updater/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/stephenh678/ha-auto-updater/actions/workflows/hassfest.yaml)
[![GitHub Release](https://img.shields.io/github/v/release/stephenh678/ha-auto-updater)](https://github.com/stephenh678/ha-auto-updater/releases)
[![License](https://img.shields.io/github/license/stephenh678/ha-auto-updater)](LICENSE)

A custom Home Assistant integration that automatically installs available updates on a schedule — with backup protection, automatic backup cleanup, notifications, per-update snoozing, and full dashboard control.

> **Requires:** Home Assistant 2023.1 or newer

---

## Why?

Home Assistant surfaces updates but won't install them for you. **HA Auto Updater** does — safely and on your terms:

- Takes a full backup *right before* installing (only when there's actually something to install — never on a wasteful schedule)
- Skips major version bumps and beta/RC releases by default
- Lets you snooze a specific update for a few days when a release looks risky
- Cleans up its own old pre-update backups so they don't pile up
- Tells you what happened — persistent notifications, optional mobile push, and a weekly digest

---

## Features

- **Scheduled updates** — runs hourly, daily, or weekly; the run time is editable right from the device page
- **Backup before update** — triggers a full HA backup before installing (only when updates are pending)
- **Backup auto-purge** — optionally deletes the pre-update backups *it* created once they pass a configurable age (your manual backups are never touched)
- **Major-version protection** — skips major bumps by default (HA Core/OS/Supervisor calendar versions are handled correctly and never filtered)
- **Beta/RC skipping** — optionally skips pre-release versions
- **Per-update snooze** — temporarily skip a specific update for N days via a service call
- **Auto restart** — optionally restarts HA after installing updates that require it (HACS / custom components)
- **Release-notes links** — pending list and success notifications link to each update's changelog when available
- **Notifications** — persistent (and optional mobile push) on success/failure, new-update detection, and a weekly digest
- **Repeated-failure escalation** — flags any component that fails several runs in a row
- **Rich sensors** — pending count, failed count, last-run status/duration/count, next run, history, and an updates-available binary sensor

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

Full configuration options, every entity and service, how it works, and troubleshooting are documented here:

➡️ **[Detailed documentation](custom_components/ha_auto_updater/README.md)**

A few quick pointers:

- **"How often to check for updates" is not a backup schedule** — it controls how often Auto Updater *looks for and installs* updates. Backups only happen as a step inside an actual install.
- Snooze an update: call `ha_auto_updater.snooze_update` with an `entity_id` and optional `days`.
- All feature toggles (backup, auto-purge, notifications, etc.) are switches on the device page; scheduling/limits are in the integration's **Configure** dialog.

---

## Contributing

Issues and pull requests are welcome. Please use the [issue tracker](https://github.com/stephenh678/ha-auto-updater/issues) for bugs and feature requests.

## License

Released under the [MIT License](LICENSE).
