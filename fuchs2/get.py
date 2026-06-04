import requests
import json
import os
import re
from datetime import datetime, date
from html.parser import HTMLParser
from ics import Calendar, Event

ARCHIVE_FILE = "archive.json"
CALENDAR_FILE = "calendar.ics"

class WixWarmupParser(HTMLParser):
    """Simple HTML parser to locate Wix warmup data script tag."""
    def __init__(self):
        super().__init__()
        self.in_warmup_script = False
        self.json_content = None

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            attr_dict = dict(attrs)
            # Wix places dynamic application data in scripts containing warmup-data
            if attr_dict.get('id') == 'wix-warmup-data':
                self.in_warmup_script = True

    def handle_data(self, data):
        if self.in_warmup_script:
            self.json_content = data

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_warmup_script = False

def load_archive():
    if os.path.exists(ARCHIVE_FILE):
        print(f"Loading Fuchs2 archive from '{ARCHIVE_FILE}'")
        try:
            with open(ARCHIVE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading archive, starting fresh: {e}")
            return {}
    print("No archive found for Fuchs2. Creating fresh timeline database.")
    return {}

def save_archive(archive_data):
    try:
        with open(ARCHIVE_FILE, 'w', encoding='utf-8') as f:
            json.dump(archive_data, f, ensure_ascii=False, indent=2)
        print(f"Archive successfully updated in '{ARCHIVE_FILE}'")
    except Exception as e:
        print(f"Failed to save archive: {e}")

def fetch_wix_events():
    url = "https://www.fuchs2.cz/shows"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    print(f"Scraping live Fuchs2 interface from: {url}")
    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Error: Server responded with status code {response.status_code}")
            return []

        # Parse the HTML content to extract the raw json payload
        parser = WixWarmupParser()
        parser.feed(response.text)

        if not parser.json_content:
            print("Critical: Wix warmup data blocks were not discovered in the HTML body.")
            return []

        data = json.loads(parser.json_content)

        # Navigate through deep Wix structure to extract the dynamic event listings
        # Usually stored inside appsData -> tpaComponents or general component descriptors
        raw_shows = []
        apps_data = data.get("appsData", {})

        for app_id, app_content in apps_data.items():
            for instance_id, instance_content in app_content.items():
                if isinstance(instance_content, dict) and "shows" in instance_content:
                    raw_shows = instance_content["shows"]
                    break
            if raw_shows:
                break

        # Fallback inspection if structure shifts slightly
        if not raw_shows and "state" in data:
            # Look inside generic app states if primary mapping fails
            for key, val in data.get("state", {}).items():
                if isinstance(val, dict) and "shows" in val:
                    raw_shows = val["shows"]
                    break

        return raw_shows if isinstance(raw_shows, list) else []
    except Exception as e:
        print(f"Network processing or parsing failure: {e}")
        return []

def merge_and_sync(archive, live_events):
    current_date_str = date.today().isoformat()
    live_ids = set()

    for show in live_events:
        # Wix typically identifies items via 'id' or 'id_' key strings
        show_id = str(show.get("id") or show.get("id_") or "")
        if not show_id:
            continue
        live_ids.add(show_id)
        archive[show_id] = show

    # Since Wix loads things dynamically over multiple pages, we ONLY clean up cancelled
    # events if they are explicitly returned in the active block but formatted as deleted.
    # We do NOT drop unreturned upcoming events because they might just be on page 2.
    return archive

def create_ical_calendar(archive_data):
    cal = Calendar()

    for show_id, item in archive_data.items():
        try:
            event = Event()

            # Wix fields usually capitalize or structure names cleanly
            title = item.get("title") or item.get("Title") or "Fuchs2 Event"
            subtitle = item.get("subtitle") or item.get("Subtitle") or ""
            description = item.get("description") or item.get("Description") or ""

            # Extract clean date (Format usually: 2026-06-15T00:00:00.000Z or similar)
            raw_date = item.get("date") or item.get("Date")
            if not raw_date:
                continue

            clean_date_str = raw_date.split("T")[0]
            parsed_date = datetime.strptime(clean_date_str, "%Y-%m-%d")

            if subtitle.strip():
                event.name = f"Fuchs2: {title.strip()} | {subtitle.strip()}"
            else:
                event.name = f"Fuchs2: {title.strip()}"

            # Clean HTML tags out of description if Wix left them inside
            clean_desc = re.sub('<[^<]+?>', '', description).strip()

            description_parts = []
            if clean_desc:
                description_parts.append(clean_desc)

            # Append permanent link helper
            description_parts.append(f"More info: https://www.fuchs2.cz/shows")
            event.description = "\n\n".join(description_parts)

            event.location = "Fuchs2, Ostrov Štvanice 1125, Prague, Czechia"
            event.begin = parsed_date
            event.make_all_day()

            cal.events.add(event)

        except Exception as err:
            print(f"Skipped parsing event ID {show_id}: {err}")
            continue

    with open(CALENDAR_FILE, 'w', encoding='utf-8') as f:
        f.writelines(cal.serialize_iter())
    print(f"Success: iCal file compiled into '{CALENDAR_FILE}'")

if __name__ == "__main__":
    print("[START] Running Fuchs2 Dynamic HTML Ingestion Engine...")

    master_archive = load_archive()
    live_wix_data = fetch_wix_events()
    print(f"[INFO] Fetched {len(live_wix_data)} current front-page records from Wix.")

    updated_archive = merge_and_sync(master_archive, live_wix_data)
    print(f"[INFO] Master archive contains {len(updated_archive)} total historical records.")

    save_archive(updated_archive)
    create_ical_calendar(updated_archive)

    print("[END] Operational lifecycle closed.")