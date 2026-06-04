import requests
from ics import Calendar, Event
from datetime import datetime, date
import json
import os

ARCHIVE_FILE = "archive.json"

def load_archive():
    # Load previously saved events from the local JSON archive file
    if os.path.exists(ARCHIVE_FILE):
        print(f"Loading existing archive from '{ARCHIVE_FILE}'")
        try:
            with open(ARCHIVE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading archive file, starting fresh: {e}")
            return {}
    print("No archive file found. Creating a new database structure.")
    return {}

def save_archive(archive_data):
    # Persist the updated master dataset back to the local file system
    try:
        with open(ARCHIVE_FILE, 'w', encoding='utf-8') as f:
            json.dump(archive_data, f, ensure_ascii=False, indent=2)
        print(f"Archive database successfully updated in '{ARCHIVE_FILE}'")
    except Exception as e:
        print(f"Failed to save archive file: {e}")

def fetch_bike_jesus_events():
    # Ingest the dynamic event list from the live endpoint
    url = "https://bikejesus.com/events"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }

    print(f"Requesting live schedule from: {url}")
    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Error: API responded with status code {response.status_code}")
            return []
        return response.json()
    except Exception as e:
        print(f"Critical error during API network request: {e}")
        return []

def merge_and_sync(archive, live_events):
    # Step 1: Insert or update all live events into the master archive dictionary
    current_date_str = date.today().isoformat()
    live_ids = set()

    for item in live_events:
        event_id = str(item.get("id"))
        if not event_id:
            continue
        live_ids.add(event_id)
        # Store or overwrite the event details with latest data from API
        archive[event_id] = item

    # Step 2: Clean up future events that were cancelled/removed from the official API
    # We only delete an archived event if it is scheduled for TODAY or FUTURE, and missing from live API.
    # Past events (HISTORY) are preserved forever.
    ids_to_remove = []
    for ev_id, ev_data in archive.items():
        ev_date_raw = ev_data.get("Date", "")
        if ev_date_raw:
            ev_date_clean = ev_date_raw.split("T")[0]
            # If the archived event is in the future/present but no longer returned by the API
            if ev_date_clean >= current_date_str and ev_id not in live_ids:
                print(f"Removing cancelled/hidden future event ID {ev_id}: '{ev_data.get('Title')}'")
                ids_to_remove.append(ev_id)

    for ev_id in ids_to_remove:
        del archive[ev_id]

    return archive

def create_ical_calendar(archive_data):
    cal = Calendar()

    for ev_id, item in archive_data.items():
        try:
            event = Event()

            raw_title = item.get("Title") or "Untitled Event"
            raw_subtitle = item.get("Subtitle")
            raw_desc = item.get("Description")
            raw_link = item.get("Link")
            raw_tickets = item.get("Tickets")
            raw_date = item.get("Date")

            clean_title = raw_title.strip()

            if raw_subtitle and raw_subtitle.strip():
                event.name = f"Bike Jesus: {clean_title} | {raw_subtitle.strip()}"
            else:
                event.name = f"Bike Jesus: {clean_title}"

            description_parts = []
            if raw_desc and raw_desc.strip():
                description_parts.append(raw_desc.strip())

            if raw_link and raw_link.strip():
                description_parts.append(f"Facebook Event: {raw_link.strip()}")

            if raw_tickets and raw_tickets.strip():
                description_parts.append(f"Tickets: {raw_tickets.strip()}")

            description_parts.append(f"Website Info: https://bikejesus.com/#event-{ev_id}")

            event.description = "\n\n".join(description_parts)
            event.location = "Bike Jesus, Ostrov Štvanice 1125, Prague, Czech republic"

            if raw_date:
                clean_date_str = raw_date.split("T")[0]
                parsed_date = datetime.strptime(clean_date_str, "%Y-%m-%d")

                event.begin = parsed_date
                event.make_all_day()
                cal.events.add(event)

        except Exception as event_err:
            print(f"Skipped parsing archived event item {ev_id} due to processing error: {event_err}")
            continue

    # Tailored to write into calendar.ics as requested
    output_filename = 'calendar.ics'
    with open(output_filename, 'w', encoding='utf-8') as f:
        f.writelines(cal.serialize_iter())

    print(f"Success: Dynamic iCal database compiled and dumped to '{output_filename}'")

if __name__ == "__main__":
    print("[START] Running Bike Jesus Persistent History Sync Engine...")

    # 1. Load historical database from GitHub storage
    master_archive = load_archive()

    # 2. Fetch fresh rolling timeline from the API
    live_api_data = fetch_bike_jesus_events()
    print(f"[INFO] Total live records fetched from API: {len(live_api_data)}")

    # 3. Merge data, preserve history, drop dropped upcoming shows
    updated_archive = merge_and_sync(master_archive, live_api_data)
    print(f"[INFO] Total master archive size after synchronization: {len(updated_archive)}")

    # 4. Save the updated persistent database back
    save_archive(updated_archive)

    # 5. Build the complete calendar file
    create_ical_calendar(updated_archive)

    print("[END] Operational lifecycle closed.")