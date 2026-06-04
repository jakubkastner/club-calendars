import urllib.request
import json
import os
import re
from datetime import datetime
from ics import Calendar, Event

ARCHIVE_FILE = "fuchs2-archive.json"
CALENDAR_FILE = "fuchs2-calendar.ics"
TARGET_URL = "https://www.fuchs2.cz/shows"

def load_archive():
    if os.path.exists(ARCHIVE_FILE):
        print(f"Loading Fuchs2 archive from '{ARCHIVE_FILE}'")
        try:
            with open(ARCHIVE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading archive, starting fresh: {e}")
            return {}
    print("No archive found for Fuchs2. Creating a new local history file.")
    return {}

def save_archive(archive_data):
    try:
        with open(ARCHIVE_FILE, 'w', encoding='utf-8') as f:
            json.dump(archive_data, f, ensure_ascii=False, indent=2)
        print(f"Archive successfully updated in '{ARCHIVE_FILE}'")
    except Exception as e:
        print(f"Failed to save archive: {e}")

def fetch_html_content(url):
    print(f"Downloading live HTML from {url}...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html_data = response.read().decode('utf-8', errors='ignore')
            print(f"[DEBUG] Downloaded HTML size: {len(html_data)} characters.")
            return html_data
    except Exception as e:
        print(f"[ERROR] Failed to download HTML content: {e}")
        return ""

def parse_wix_events(html_content):
    if not html_content:
        print("[ERROR] Cannot parse events: HTML content is completely empty.")
        return []

    print("Analyzing target HTML markup for internal Wix application state data layers...")
    extracted_items = []

    warmup_match = re.search(r'<script[^>]*id="wix-warmup-data"[^>]*>(.*?)</script>', html_content, re.DOTALL)
    if warmup_match:
        print("[DEBUG] Step 1 Success: Found <script id=\"wix-warmup-data\"> block.")
        raw_json_str = warmup_match.group(1).strip()

        try:
            parsed_warmup = json.loads(raw_json_str)
            print("[DEBUG] Step 2 Success: Safely decoded raw string into a valid JSON object.")

            matched_nodes_count = 0

            def search_nodes(node):
                nonlocal matched_nodes_count
                if isinstance(node, dict):
                    has_id = "_id" in node or "id" in node
                    has_title = "title" in node or "name" in node or "eventName" in node

                    has_any_date = False
                    for dict_key in node.keys():
                        key_lower = str(dict_key).lower()
                        if "date" in key_lower or "start" in key_lower:
                            has_any_date = True
                            break

                    if has_id and has_title and has_any_date:
                        if isinstance(node.get("title") or node.get("name") or node.get("eventName"), str) and len(str(node.get("_id") or node.get("id"))) > 5:
                            extracted_items.append(node)
                            matched_nodes_count += 1

                    for key, val in node.items():
                        search_nodes(val)
                elif isinstance(node, list):
                    for item in node:
                        search_nodes(item)

            search_nodes(parsed_warmup)
            print(f"[DEBUG] Step 3 Completed: JSON tree traversal finished. Found {matched_nodes_count} flexible event components.")

        except json.JSONDecodeError as je:
            print(f"[ERROR] Step 2 Failed: JSON structure unparsable. Error: {je}")
        except Exception as e:
            print(f"[ERROR] Step 3 Failed: Exception encountered during JSON tree traversal: {e}")
    else:
        print("[WARNING] Step 1 Failed: Could not locate `<script id=\"wix-warmup-data\">` tag.")

    valid_events = []
    for item in extracted_items:
        if isinstance(item, dict):
            event_id = item.get("_id") or item.get("id")
            if event_id and event_id not in [e.get("_id") or e.get("id") for e in valid_events]:
                if "title" not in item and "eventName" in item:
                    item["title"] = item["eventName"]
                elif "title" not in item and "name" in item:
                    item["title"] = item["name"]
                valid_events.append(item)

    print(f"[DEBUG] Validation complete. Total valid unique events prepared: {len(valid_events)}")
    return valid_events

def discover_rich_descriptions(archive_data):
    """Deep scans event detail pages to fetch and cache full rich descriptions."""
    print("Initiating deep scan pass for missing rich event descriptions...")
    updated_count = 0

    for show_id, item in archive_data.items():
        # Skip crawling if we already cached a proper rich description for this event
        if item.get("scraped_rich_description"):
            continue

        slug = item.get("slug")
        if not slug:
            continue

        detail_url = f"https://www.fuchs2.cz/events/{slug}"
        print(f"[DEEP SCAN] Fetching full event details from: {detail_url}")
        detail_html = fetch_html_content(detail_url)

        if not detail_html:
            continue

        # Parse the details using the same warmup data algorithm to find the full description
        detail_events = parse_wix_events(detail_html)
        rich_desc = None

        if detail_events:
            for d_ev in detail_events:
                rich_desc = d_ev.get("description") or d_ev.get("about") or d_ev.get("shortDescription")
                if rich_desc and len(rich_desc) > 5:
                    break

        # Fallback to direct regex if JSON extraction is non-standard on specific event detail
        if not rich_desc:
            desc_match = re.search(r'<p[^>]* class="[^"]*event-description[^"]*"[^>]*>(.*?)</p>', detail_html, re.DOTALL)
            if desc_match:
                rich_desc = desc_match.group(1).strip()

        if rich_desc:
            # Safe text formatting: remove heavy HTML markup, clean trailing artifacts
            clean_text = re.sub('<[^<]+?>', '', str(rich_desc)).strip()
            # Replace common HTML entity residues if any
            clean_text = clean_text.replace('&nbsp;', ' ').replace('&amp;', '&')

            print(f"[DEEP SCAN] Rich description successfully extracted and cached.")
            archive_data[show_id]["scraped_rich_description"] = clean_text
            updated_count += 1

    if updated_count > 0:
        print(f"[INFO] Deep scan completed. Enhanced {updated_count} event records with descriptions.")
    else:
        print("[INFO] Deep scan completed. No missing descriptions found.")
    return archive_data

def merge_and_sync(archive, live_events):
    for show in live_events:
        show_id = str(show.get("_id") or show.get("id") or "")
        if not show_id:
            continue

        if show_id in archive and "scraped_rich_description" in archive[show_id]:
            show["scraped_rich_description"] = archive[show_id]["scraped_rich_description"]

        archive[show_id] = show
    return archive

def create_ical_calendar(archive_data):
    cal = Calendar()
    parsed_count = 0

    for show_id, item in archive_data.items():
        try:
            event = Event()
            title = item.get("title") or "Fuchs2 Event"
            slug = item.get("slug") or ""

            # Prioritize our newly cached deep-scraped rich summary text
            description = item.get("scraped_rich_description") or item.get("description") or item.get("about") or ""

            scheduling = item.get("scheduling", {})
            config = scheduling.get("config", {}) if isinstance(scheduling, dict) else {}
            raw_date = config.get("startDate") if isinstance(config, dict) else None

            if not raw_date:
                def find_iso_date(node):
                    if isinstance(node, str) and re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', node):
                        return node
                    elif isinstance(node, dict):
                        for val in node.values():
                            res = find_iso_date(val)
                            if res: return res
                    elif isinstance(node, list):
                        for val in node:
                            res = find_iso_date(val)
                            if res: return res
                    return None
                raw_date = find_iso_date(item)

            if not raw_date:
                continue

            try:
                clean_date_str = str(raw_date).split("T")[0]
                parsed_date = datetime.strptime(clean_date_str, "%Y-%m-%d")
            except Exception:
                continue

            event.name = f"{str(title).strip()}"

            description_parts = []
            clean_desc = str(description).strip()

            # Insert the entire extracted text block into the iCal description array
            if clean_desc and clean_desc != "None":
                description_parts.append(clean_desc)

            if slug:
                description_parts.append(f"Event link: https://www.fuchs2.cz/events/{slug}")

            event.description = "\n\n".join(description_parts)
            event.location = "Fuchs2, Ostrov Štvanice 1125, Prague, Czech republic"
            event.begin = parsed_date
            event.make_all_day()

            cal.events.add(event)
            parsed_count += 1

        except Exception as err:
            print(f"[ERROR] Skipped parsing iCal event ID {show_id}: {err}")
            continue

    with open(CALENDAR_FILE, 'w', encoding='utf-8') as f:
        f.writelines(cal.serialize_iter())
    print(f"Success: iCal file compiled with {parsed_count} events into '{CALENDAR_FILE}'")

if __name__ == "__main__":
    print("[START] Running Fuchs2 Automated Online Scraper Engine...")

    master_archive = load_archive()
    live_html = fetch_html_content(TARGET_URL)
    parsed_wix_data = parse_wix_events(live_html)

    print(f"[INFO] Successfully fetched and parsed {len(parsed_wix_data)} items from live website.")

    if parsed_wix_data:
        master_archive = merge_and_sync(master_archive, parsed_wix_data)
        print(f"[INFO] Master archive now contains {len(master_archive)} records.")

    if master_archive:
        # Run deep HTML scan pass to automatically populate and cache descriptions
        master_archive = discover_rich_descriptions(master_archive)
        save_archive(master_archive)
        create_ical_calendar(master_archive)

    print("[END] Processing completed.")