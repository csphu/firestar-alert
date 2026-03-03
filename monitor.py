#!/usr/bin/env python3
"""
Firestar XP Boiler Monitor
Scrapes the local Firestar XP status page, checks Furnace Status, Water Temp,
and Fire Temp. Sends push notifications via ntfy.sh when thresholds are violated.

Alert backoff schedule:
  - 1st alert: immediately on detection, then poll every 10 minutes
  - 2nd alert: after 10 min, then poll every 30 minutes
  - 3rd+ alerts: after 30 min, then poll every 60 minutes
  - Stops alerting after 6 hours of continuous error
  - Sends a recovery notification when the boiler returns to normal
"""

import re
import time
import json
import logging
import configparser
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.ini"
STATE_FILE  = SCRIPT_DIR / "state.json"
LOG_FILE    = SCRIPT_DIR / "monitor.log"

# ---------------------------------------------------------------------------
# Logging — writes to both file and stdout (picked up by journald)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config / state helpers
# ---------------------------------------------------------------------------
def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_FILE}")
    cfg.read(CONFIG_FILE)
    return cfg


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        log.error("Could not save state: %s", e)


# ---------------------------------------------------------------------------
# Boiler scraper
# ---------------------------------------------------------------------------
def get_boiler_data(url: str) -> tuple[str, float | None, float | None]:
    """
    Fetch the Firestar XP status page and return
    (furnace_status, water_temp, fire_temp).

    Furnace status is embedded in the page JS as:
        (parseInt('N')>0) ? "ON" : "OFF"
    Water Temp / Fire Temp are in <span class='ContentText'> cells.
    """
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    # --- Furnace Status (parsed from inline JavaScript) ---
    furnace_status = "Unknown"
    match = re.search(r"parseInt\('(-?\d+)'\)\s*>\s*0", html)
    if match:
        furnace_status = "ON" if int(match.group(1)) > 0 else "OFF"
    else:
        # Fallback: check for isNaN with a non-numeric value → Unknown
        isnan_match = re.search(r"isNaN\('([^']*)'\)", html)
        if isnan_match and isnan_match.group(1) == "":
            furnace_status = "Unknown"

    # --- Water Temp and Fire Temp (parsed from HTML table) ---
    water_temp: float | None = None
    fire_temp:  float | None = None

    for row in soup.find_all("tr"):
        row_text = row.get_text()
        if "Water Temp:" in row_text:
            spans = row.find_all("span", class_="ContentText")
            if spans:
                try:
                    water_temp = float(spans[0].get_text().strip())
                except ValueError:
                    log.warning("Could not parse Water Temp value: %r", spans[0].get_text())
        elif "Fire Temp:" in row_text:
            spans = row.find_all("span", class_="ContentText")
            if spans:
                try:
                    fire_temp = float(spans[0].get_text().strip())
                except ValueError:
                    log.warning("Could not parse Fire Temp value: %r", spans[0].get_text())

    # If either temp is still None, log a snippet of the raw HTML to help
    # diagnose whether the page structure changed or the response was malformed.
    if water_temp is None or fire_temp is None:
        snippet = html[:3000].replace("\n", " ").replace("\r", "")
        log.warning(
            "Could not parse all temp values (water=%s, fire=%s). "
            "Raw HTML snippet (first 3000 chars): %s",
            water_temp, fire_temp, snippet,
        )

    return furnace_status, water_temp, fire_temp


def get_boiler_data_with_retry(
    url: str,
    retries: int = 3,
    delay: int = 10,
) -> tuple[str, float | None, float | None]:
    """Call get_boiler_data, retrying if either temp value is unreadable."""
    for attempt in range(1, retries + 1):
        furnace_status, water_temp, fire_temp = get_boiler_data(url)
        if water_temp is not None and fire_temp is not None:
            return furnace_status, water_temp, fire_temp
        if attempt < retries:
            log.warning(
                "Temp(s) unreadable on attempt %d/%d "
                "(water=%s, fire=%s) — retrying in %ds.",
                attempt, retries, water_temp, fire_temp, delay,
            )
            time.sleep(delay)
    # Return whatever we have after all retries
    log.warning("Temp(s) still unreadable after %d attempts.", retries)
    return furnace_status, water_temp, fire_temp


# ---------------------------------------------------------------------------
# Threshold checks
# ---------------------------------------------------------------------------
def check_thresholds(
    cfg: configparser.ConfigParser,
    furnace_status: str,
    water_temp: float | None,
    fire_temp: float | None,
) -> list[str]:
    """Return a list of human-readable issue strings for any failed threshold."""
    water_min = float(cfg["boiler"]["water_temp_min"])
    fire_min  = float(cfg["boiler"]["fire_temp_min"])
    issues: list[str] = []

    if furnace_status != "ON":
        issues.append(f"Furnace Status: {furnace_status} (expected ON)")

    if water_temp is None:
        issues.append("Water Temp: unreadable")
    elif water_temp < water_min:
        issues.append(f"Water Temp: {water_temp}\u00b0F (min {water_min}\u00b0F)")

    if fire_temp is None:
        issues.append("Fire Temp: unreadable")
    elif fire_temp < fire_min:
        issues.append(f"Fire Temp: {fire_temp}\u00b0F (min {fire_min}\u00b0F)")

    return issues


# ---------------------------------------------------------------------------
# Push notification via ntfy.sh
# ---------------------------------------------------------------------------
def send_sms(cfg: configparser.ConfigParser, subject: str, body: str) -> None:
    """Send a push notification via ntfy.sh."""
    server = cfg["ntfy"]["server"].rstrip("/")
    topic  = cfg["ntfy"]["topic"]
    url    = f"{server}/{topic}"

    # Emoji prefix so alerts stand out from recovery messages
    priority = "urgent" if "ALERT" in subject else "default"
    tag = "fire" if "ALERT" in subject else "white_check_mark" if "RECOVERED" in subject else "bell"

    resp = requests.post(
        url,
        data=body.encode("utf-8"),
        headers={
            "Title":    subject,
            "Priority": priority,
            "Tags":     tag,
        },
        timeout=10,
    )
    resp.raise_for_status()
    log.info("ntfy notification sent \u2192 %s/%s | subject: %s", server, topic, subject)


# ---------------------------------------------------------------------------
# Interval helper
# ---------------------------------------------------------------------------
def get_sleep_seconds(cfg: configparser.ConfigParser, alert_count: int) -> int:
    """
    Returns how many seconds to sleep before the next check.

    alert_count  sleep interval
    -----------  -----------------------
    0            normal_interval (1 min)
    1            interval_after_first_alert  (10 min)
    2            interval_after_second_alert (30 min)
    3+           interval_after_third_alert  (60 min)
    """
    steps = [
        int(cfg["alerts"]["normal_interval"])              * 60,
        int(cfg["alerts"]["interval_after_first_alert"])   * 60,
        int(cfg["alerts"]["interval_after_second_alert"])  * 60,
        int(cfg["alerts"]["interval_after_third_alert"])   * 60,
    ]
    return steps[min(alert_count, len(steps) - 1)]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    log.info("Firestar XP monitor starting.")
    cfg = load_config()

    # Always start with a clean slate on startup so a service restart
    # clears any backoff / stopped state.
    state = {
        "in_error":        False,
        "alert_count":     0,
        "error_start":     None,
        "last_alert_time": None,
        "stopped":         False,
    }
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        log.info("State reset on startup.")

    # ----------------------------------------------------------------
    # Send a startup notification with current temps and config
    # ----------------------------------------------------------------
    try:
        url = cfg["boiler"]["url"]
        furnace_status, water_temp, fire_temp = get_boiler_data_with_retry(url)
        now = datetime.now()
        startup_body = (
            f"Time: {now.strftime('%Y-%m-%d %H:%M')}\n"
            f"Furnace: {furnace_status}\n"
            f"Water:   {water_temp}°F\n"
            f"Fire:    {fire_temp}°F\n"
            f"────────────────────\n"
            f"Water min: {cfg['boiler']['water_temp_min']}°F\n"
            f"Fire min:  {cfg['boiler']['fire_temp_min']}°F\n"
            f"Interval:  {cfg['alerts']['normal_interval']}m"
        )
        send_sms(cfg, "MONITOR STARTED", startup_body)
        log.info("Startup notification sent.")
    except Exception as e:
        log.warning("Could not send startup notification: %s", e)

    while True:
        sleep_secs = get_sleep_seconds(cfg, state.get("alert_count", 0))

        # ----------------------------------------------------------------
        # If we previously stopped due to 6-hour error limit, keep
        # checking every hour in case the boiler recovers.
        # ----------------------------------------------------------------
        if state.get("stopped"):
            log.info("Alert limit reached (6h error). Checking for recovery in 1h.")
            time.sleep(3600)
            state["stopped"] = False   # re-enter normal check on next pass

        try:
            url = cfg["boiler"]["url"]
            furnace_status, water_temp, fire_temp = get_boiler_data_with_retry(url)
            log.info(
                "Furnace=%s  Water=%.1f°F  Fire=%.0f°F",
                furnace_status,
                water_temp if water_temp is not None else 0.0,
                fire_temp  if fire_temp  is not None else 0.0,
            )

            issues = check_thresholds(cfg, furnace_status, water_temp, fire_temp)
            now    = datetime.now()

            # ---- ERROR STATE ----
            if issues:
                if not state["in_error"]:
                    state["in_error"]    = True
                    state["error_start"] = now.isoformat()
                    state["alert_count"] = 0

                error_start    = datetime.fromisoformat(state["error_start"])
                hours_in_error = (now - error_start).total_seconds() / 3600
                max_hours      = int(cfg["alerts"]["max_error_hours"])

                if hours_in_error >= max_hours:
                    log.warning(
                        "Boiler has been in error for %.1f hours. "
                        "Stopping alerts. Will re-check hourly.",
                        hours_in_error,
                    )
                    state["stopped"] = True
                    save_state(state)
                    time.sleep(3600)
                    continue

                # Decide whether it's time to send the next alert
                last_alert = state.get("last_alert_time")
                time_since_last = (
                    (now - datetime.fromisoformat(last_alert)).total_seconds()
                    if last_alert else float("inf")
                )

                if time_since_last >= sleep_secs:
                    subject = "BOILER ALERT"
                    body = (
                        f"Firestar XP Alert\n"
                        f"Time: {now.strftime('%Y-%m-%d %H:%M')}\n"
                        f"{'\u2500' * 20}\n"
                        + "\n".join(f"  \u2022 {issue}" for issue in issues)
                        + f"\n{'\u2500' * 20}\n"
                        f"Furnace: {furnace_status}\n"
                        f"Water:   {water_temp}\u00b0F\n"
                        f"Fire:    {fire_temp}\u00b0F"
                    )
                    try:
                        send_sms(cfg, subject, body)
                        state["last_alert_time"] = now.isoformat()
                        state["alert_count"]     += 1
                    except Exception as e:
                        log.error("Failed to send alert SMS: %s", e)

                    # Recalculate sleep using updated alert_count
                    sleep_secs = get_sleep_seconds(cfg, state["alert_count"])

            # ---- RECOVERY ----
            else:
                if state["in_error"]:
                    log.info("Boiler recovered. Sending recovery notification.")
                    try:
                        send_sms(
                            cfg,
                            "BOILER RECOVERED",
                            f"Firestar XP recovered.\n"
                            f"Time: {now.strftime('%Y-%m-%d %H:%M')}\n"
                            f"Furnace: {furnace_status}\n"
                            f"Water:   {water_temp}\u00b0F\n"
                            f"Fire:    {fire_temp}\u00b0F",
                        )
                    except Exception as e:
                        log.error("Failed to send recovery SMS: %s", e)

                # Reset all error state
                state = {
                    "in_error":        False,
                    "alert_count":     0,
                    "error_start":     None,
                    "last_alert_time": None,
                    "stopped":         False,
                }
                sleep_secs = get_sleep_seconds(cfg, 0)

        except requests.RequestException as e:
            log.warning("Could not reach boiler at %s: %s", cfg["boiler"]["url"], e)
            # Don't treat a network blip as a boiler error; retry at normal interval
            sleep_secs = int(cfg["alerts"]["normal_interval"]) * 60

        except Exception as e:
            log.exception("Unexpected error: %s", e)

        save_state(state)
        log.debug("Sleeping %d seconds.", sleep_secs)
        time.sleep(sleep_secs)


if __name__ == "__main__":
    main()
