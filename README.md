# firestar-alert

[![Release](https://img.shields.io/github/v/release/csphu/firestar-alert?style=flat-square&color=blue)](https://github.com/csphu/firestar-alert/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-C51A4A?style=flat-square&logo=raspberry-pi&logoColor=white)](https://www.raspberrypi.com/)
[![Notifications](https://img.shields.io/badge/notifications-ntfy.sh-orange?style=flat-square)](https://ntfy.sh)

A lightweight Raspberry Pi monitor for the **Central Boiler Firestar XP** wood-fired outdoor boiler.

> **Note:** This project is not affiliated with, endorsed by, or in any way connected to Central Boiler. It reads data from the local HTTP status page the Firestar XP controller already exposes on your home network — the same page viewable in any browser. No proprietary protocols are reversed, no cloud services are accessed, and no Central Boiler systems are modified.

## Background

Central Boiler offers a paid cloud subscription service that provides remote monitoring, advanced alerts, and historical data for the Firestar XP. If you just want to know whether your fire has gone out or the boiler has shut off unexpectedly — and you'd rather not pay a monthly fee — this project does exactly that using the local web interface the boiler already exposes on your network.

The Firestar XP controller hosts a small status page at a static IP on your home network. This project runs a Python script on a Raspberry Pi that polls that page every minute, parses the Furnace Status, Water Temp, and Fire Temp values, and sends a push notification to your phone if anything falls outside acceptable limits.

---

## How It Works

The Firestar XP serves a plain HTML/JavaScript status page on your local network. The page embeds the furnace state as an integer in inline JavaScript and renders Water Temp and Fire Temp as plain text in an HTML table. This script fetches that page, extracts those values with a regex and BeautifulSoup, compares them against configurable thresholds, and fires a push notification via ntfy.sh when a threshold is violated.

No login, no cloud API, no proprietary protocol — just a local HTTP GET.

---

## Features

- Polls `http://192.168.1.113` every minute under normal conditions
- Sends **push notifications via [ntfy.sh](https://ntfy.sh)** (free, no account needed)
- Detects and alerts on:
  - Furnace Status ≠ ON (fire out, fault condition, etc.)
  - Water Temp < 165 °F
  - Fire Temp < 200 °F
- **Retries up to 3 times** before alerting if temp values are unreadable
- **Smart alert backoff** — avoids spamming you if the problem persists:

  | Alerts sent | Poll interval |
  |---|---|
  | 0 (normal) | 1 minute |
  | 1st alert sent | 10 minutes |
  | 2nd alert sent | 30 minutes |
  | 3rd+ alert sent | 60 minutes |
  | 6 hours in error | Stop alerting; check hourly for recovery |

- Sends a **recovery notification** when the boiler returns to normal
- Sends a **startup notification** with current temps and active config on every service start
- **Restarting the service resets all alert backoff state**
- Runs as a **systemd service** — starts automatically at boot, restarts on crash
- All thresholds and intervals are configurable via `config.ini` without editing code
- Logs to the systemd journal and a local `monitor.log` file

---

## Future Ideas

### Temperature History

The boiler page doesn't expose a history API, but since this script already polls every minute, it's straightforward to extend it to log readings to a local database or CSV file. That data could then be visualized with tools like:

- **[Grafana](https://grafana.com/) + [InfluxDB](https://www.influxdata.com/)** — the standard self-hosted time-series dashboard stack. Runs well on a Pi.
- **[Home Assistant](https://www.home-assistant.io/)** — if you already use it, the monitor could push readings as sensors.
- A simple CSV log + Python/spreadsheet for a lightweight option.

If you'd like temperature history tracking added to this project, open an issue or submit a PR.

---

## Requirements

- Raspberry Pi (any model with a network connection)
- Python 3.9+
- Your Firestar XP controller assigned a static IP on your local network (default used here: `192.168.1.113`)
- The [ntfy](https://ntfy.sh) app installed on your phone (iOS or Android)

---

## Project Structure

```
firestar-alert/
├── monitor.py               # Main polling and alerting script
├── config.ini               # Configuration — edit this before deploying
├── requirements.txt         # Python dependencies
├── firestar-alert.service   # systemd unit file
├── install.sh               # Installer script for the Raspberry Pi
├── monitor.log              # Created at runtime — rolling log file
└── state.json               # Created at runtime — cleared on every service restart
```

---

## Installation

### 1. Copy the project to your Raspberry Pi

```bash
scp -r firestar-alert <user>@<pi-ip-address>:~/firestar-alert
```

Or clone directly on the Pi:

```bash
git clone https://github.com/csphu/firestar-alert.git
cd firestar-alert
```

### 2. Edit the configuration

SSH into the Pi, then:

```bash
cd ~/firestar-alert
nano config.ini
```

Fill in the following values (see [Configuration Reference](#configuration-reference) below):

- `[boiler]` → `url` — the local IP of your Firestar XP (e.g. `http://192.168.1.113`)
- `[ntfy]` → `topic` — the topic name you subscribed to in the ntfy app

### 3. Run the installer

```bash
chmod +x install.sh
./install.sh
```

The installer will:
1. Create a Python virtual environment at `~/firestar-alert/venv`
2. Install `requests` and `beautifulsoup4`
3. Register and enable the systemd service

> **Note:** If you run `install.sh` from within the project directory (the normal case after `scp`), it will detect that and skip the file copy step automatically.

### 4. Start the service

```bash
sudo systemctl start firestar-alert
```

### 5. Verify it's running

```bash
sudo systemctl status firestar-alert
```

### 6. Watch live logs

```bash
journalctl -u firestar-alert -f
```

---

## Configuration Reference

All settings live in `config.ini`.

### `[boiler]`

| Key | Default | Description |
|---|---|---|
| `url` | `http://192.168.1.113` | URL of the Firestar XP status page |
| `water_temp_min` | `165` | Alert threshold for Water Temp (°F) |
| `fire_temp_min` | `200` | Alert threshold for Fire Temp (°F) |

### `[ntfy]`

| Key | Default | Description |
|---|---|---|
| `topic` | `csphu-boiler-alert` | Your ntfy topic name — must match what you subscribed to in the app. Make it unique. |
| `server` | `https://ntfy.sh` | ntfy server URL. Change only if self-hosting. |

### `[alerts]`

| Key | Default | Description |
|---|---|---|
| `normal_interval` | `1` | Polling interval when everything is OK (minutes) |
| `interval_after_first_alert` | `10` | Polling interval after 1st alert (minutes) |
| `interval_after_second_alert` | `30` | Polling interval after 2nd alert (minutes) |
| `interval_after_third_alert` | `60` | Polling interval after 3rd+ alert (minutes) |
| `max_error_hours` | `6` | Hours before alerts stop (checks hourly for recovery) |

---

## ntfy.sh Setup

[ntfy](https://ntfy.sh) is a free, open-source push notification service. No account is needed.

1. Install the **ntfy** app on your phone ([iOS](https://apps.apple.com/us/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy))
2. Tap **+** and subscribe to a topic name you make up (e.g. `my-boiler-alerts`)
3. Test it from any terminal: `curl -d "test" ntfy.sh/my-boiler-alerts`
4. Set that topic name in `config.ini` under `[ntfy]`

> **Security tip:** Make your topic name long and hard to guess — anyone who knows it can subscribe and receive your notifications.

---

## Service Management

```bash
# Start / stop / restart
sudo systemctl start firestar-alert
sudo systemctl stop firestar-alert
sudo systemctl restart firestar-alert

# Enable / disable autostart at boot
sudo systemctl enable firestar-alert
sudo systemctl disable firestar-alert

# View status
sudo systemctl status firestar-alert

# Follow live logs
journalctl -u firestar-alert -f

# View all logs
journalctl -u firestar-alert --no-pager
```

---

## Alert Examples

Alerts appear as push notifications in the ntfy app with a � tag on startup, 🔥 for problems, and ✅ for recovery.

**Startup notification (ntfy title):** `MONITOR STARTED`
```
Time: 2026-03-03 11:49
Furnace: ON
Water:   186.4°F
Fire:    197°F
────────────────────
Water min: 165°F
Fire min:  200°F
Interval:  1m
```

**Problem alert (ntfy title):** `BOILER ALERT`
```
Firestar XP Alert
Time: 2026-03-03 02:14
────────────────────
  • Furnace Status: OFF (expected ON)
  • Fire Temp: 143.0°F (min 200°F)
────────────────────
Furnace: OFF
Water:   158.2°F
Fire:    143.0°F
```

**Recovery notification (ntfy title):** `BOILER RECOVERED`
```
Firestar XP recovered.
Time: 2026-03-03 03:45
Furnace: ON
Water:   187.5°F
Fire:    312.0°F
```

---

## Disclaimer

This project is not affiliated with or endorsed by Central Boiler Inc. "Firestar XP" is a product of Central Boiler. This software reads data from the local HTTP interface the device exposes on your own home network and does not interact with any Central Boiler cloud service, API, or server. Use at your own risk.

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

---

## License

MIT — see [LICENSE](LICENSE).
