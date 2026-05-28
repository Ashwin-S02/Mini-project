import os
import json
import math
import csv
import re
import uuid
import urllib.parse
import threading
import time
import requests
import logging
import sys
import warnings
from logging.handlers import RotatingFileHandler
from bs4 import BeautifulSoup
import gspread
from gspread_formatting import *
from flask import Flask, request, jsonify
from flask_cors import CORS
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor, as_completed

from zone_manager import get_zones, format_query, is_large_area

# Suppress BeautifulSoup XML parsing warnings
warnings.filterwarnings("ignore", category=UserWarning, module='bs4')

# ---------------------------------------------------------------------------
# Global Logging Configuration
# ---------------------------------------------------------------------------
os.makedirs('logs', exist_ok=True)

logger = logging.getLogger('BrainMapLogger')
logger.setLevel(logging.DEBUG)

# Formatter for logs
log_formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# 1. Console Handler (INFO level) - UNBUFFERED for cloud deployment
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(log_formatter)
sys.stdout.reconfigure(line_buffering=True)  # Force line buffering
logger.addHandler(console_handler)

# 2. General File Handler (DEBUG level - logs everything)
file_handler = RotatingFileHandler(os.path.join('logs', 'scraper_activity.log'), maxBytes=10*1024*1024, backupCount=5)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)

# 3. Error File Handler (ERROR level - logs only errors with full tracebacks)
error_file_handler = RotatingFileHandler(os.path.join('logs', 'scraper_errors.log'), maxBytes=10*1024*1024, backupCount=5)
error_file_handler.setLevel(logging.ERROR)
error_file_handler.setFormatter(log_formatter)
logger.addHandler(error_file_handler)


app = Flask(__name__)
# Suppress Werkzeug default request logging unless there's an error
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
_allowed_origins_raw = os.environ.get("ALLOWED_ORIGINS", "*")
_allowed_origins = [o.strip() for o in _allowed_origins_raw.split(",")] if "," in _allowed_origins_raw else _allowed_origins_raw
CORS(app, resources={r"/*": {"origins": _allowed_origins}})

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------
jobs = {}
jobs_lock = threading.Lock()

# Configuration
MAX_BROWSER_WORKERS = int(os.environ.get("MAX_BROWSER_WORKERS", 2))

# Global cap on concurrent Chromium instances across ALL jobs.
# Each Chromium takes ~200MB; 2Gi container = max ~8 instances before OOM.
# Keep it at 4 to leave headroom for the Flask worker and enrichment threads.
_browser_semaphore = threading.Semaphore(4)
MAX_SCROLL_ATTEMPTS = 45          # Max scrolls before giving up
SCROLL_PAUSE_MS = 900             # Base pause between scrolls
PLACE_CLICK_TIMEOUT_MS = 4000     # Max wait for detail panel after click
MAX_RESULTS_PER_ZONE = 120        # Google Maps rarely shows more than this

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def clean_google_url(google_url):
    if not google_url:
        return ""
    if "google.com/url?q=" in google_url:
        try:
            return urllib.parse.unquote(google_url.split('q=')[1].split('&')[0])
        except Exception:
            return google_url
    return google_url

def strip_icons(text):
    return re.sub(r'[\uE000-\uF8FF]', '', text or '').strip()

def normalize_name(name):
    if not name:
        return ""
    n = name.lower().strip()
    for suffix in [' pvt ltd', ' pvt. ltd.', ' pvt. ltd', ' private limited',
                   ' limited', ' ltd', ' ltd.', ' llp', ' inc', ' inc.',
                   ' corp', ' corp.', ' co.', ' & co', ' & co.']:
        n = n.replace(suffix, '')
    n = re.sub(r'[^a-z0-9\s]', '', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n

def _sanitize_sheet_tab(name):
    """Validate and sanitize a Google Sheets tab name.
    Google Sheets restrictions: max 100 chars, no \\ / ? * : [ ] characters.
    """
    if not name:
        return ""
    name = re.sub(r'[\\/?*:\[\]]', '', str(name)).strip()
    return name[:100]

def extract_website_data(url):
    data = {"emails": set(), "linkedin": "", "facebook": "", "instagram": "", "twitter": ""}
    email_regex = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
    social_patterns = {
        "linkedin": re.compile(r'linkedin\.com/(?:company|in)/[a-zA-Z0-9_-]+'),
        "facebook": re.compile(r'facebook\.com/[a-zA-Z0-9._-]+'),
        "instagram": re.compile(r'instagram\.com/[a-zA-Z0-9._-]+'),
        "twitter": re.compile(r'(?:twitter\.com|x\.com)/[a-zA-Z0-9._-]+')
    }
    
    if not url.startswith('http'):
        url = 'https://' + url
        
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'en-US,en;q=0.9'
    }
    
    base_url = f"{urllib.parse.urlparse(url).scheme}://{urllib.parse.urlparse(url).netloc}"
    paths_to_check = ['', '/contact', '/contact-us']
    urls_to_check = [urllib.parse.urljoin(base_url, p) for p in paths_to_check]

    visited = set()
    for target_url in urls_to_check:
        if target_url in visited: continue
        visited.add(target_url)
        try:
            res = requests.get(target_url, headers=headers, timeout=5)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                text_content = soup.get_text()
                for a in soup.find_all('a', href=True):
                    href = a['href'].lower()
                    if href.startswith('mailto:'):
                        clean_email = urllib.parse.unquote(a['href'].replace('mailto:', '').split('?')[0]).strip()
                        if email_regex.match(clean_email):
                            data["emails"].add(clean_email)
                    for platform, pattern in social_patterns.items():
                        if not data[platform]:
                            match = pattern.search(href)
                            if match:
                                data[platform] = "https://" + match.group(0)
                for match in email_regex.findall(text_content):
                    data["emails"].add(match)
        except requests.RequestException as e:
            logger.debug(f"[Enrich] Request failed for {target_url}: {e}")
            
    invalid_domains = ['example.com', 'domain.com', 'sentry.io', 'wix.com', 'yourdomain.com']
    invalid_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.js', '.css')
    
    valid_emails = set()
    for e in data["emails"]:
        e = e.lower()
        if not any(domain in e for domain in invalid_domains) and not e.endswith(invalid_extensions):
            valid_emails.add(e)
            
    data["emails"] = ", ".join(valid_emails)
    return data

def calculate_score(lead):
    score = 0
    if not lead.get("website"): score += 50
    if not lead.get("facebook"): score += 15
    if not lead.get("instagram"): score += 15
    if not lead.get("linkedin"): score += 10
    if not lead.get("twitter"): score += 10
    if not lead.get("emails"): score += 20
    
    rating_str = lead.get("rating", "")
    rating_val = 0
    try:
        if rating_str: rating_val = float(rating_str)
        if rating_val and rating_val < 4.0:
            score += 20
    except (ValueError, TypeError): pass
    
    has_social = any([lead.get("facebook"), lead.get("instagram"), lead.get("linkedin"), lead.get("twitter")])
    if lead.get("website") and lead.get("emails") and has_social and rating_val > 4.0:
        return 100
        
    return score

# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

def _get_sheet_client(tab_name=None):
    creds_json = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS_JSON')
    sheet_name = os.environ.get('GOOGLE_SHEET_NAME', 'int')
    if not creds_json:
        creds_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'credentials.json')
        if os.path.exists(creds_file):
            with open(creds_file, 'r') as f:
                creds_json = f.read()
        else:
            return None, None
    creds_dict = json.loads(creds_json)
    gc = gspread.service_account_from_dict(creds_dict)
    try:
        sh = gc.open_by_key("1BCEot7vu1-H0XQ7EE9C6P3uHJdtABD8VEO87N_-WPOc")
    except Exception as e:
        logger.warning(f"[Sheets] Failed to open sheet by key, falling back to name '{sheet_name}': {e}")
        sh = gc.open(sheet_name)
    if tab_name:
        try:
            worksheet = sh.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sh.add_worksheet(title=tab_name, rows="1000", cols="20")
            logger.info(f"[Sheets] Created new tab '{tab_name}'")
    else:
        worksheet = sh.get_worksheet(0)
    return gc, worksheet

def get_sheet_url():
    return "https://docs.google.com/spreadsheets/d/1BCEot7vu1-H0XQ7EE9C6P3uHJdtABD8VEO87N_-WPOc/edit?gid=0#gid=0"

def fetch_existing_sheet_keys(tab_name=None, export_mode="csv"):
    existing_keys = set()
    row_count = 0
    if export_mode == "sheet":
        try:
            _, worksheet = _get_sheet_client(tab_name)
            if worksheet is not None:
                all_rows = worksheet.get_all_values()
                if len(all_rows) > 1:
                    for row in all_rows[1:]:
                        if len(row) >= 14:
                            row_count += 1
                            name = normalize_name(row[0])
                            phone = row[4].strip().lstrip("'") if row[4] else ""
                            maps_raw = row[13] if len(row) > 13 else ""
                            maps_match = re.search(r'HYPERLINK\("([^"]+)"', maps_raw)
                            maps_url = maps_match.group(1) if maps_match else maps_raw
                            if maps_url:
                                existing_keys.add(("url", maps_url.strip()))
                            if name:
                                if phone:
                                    existing_keys.add(("name_phone", name, phone))
                                existing_keys.add(("name", name))
        except Exception as e:
            logger.warning(f"[Sheet] Warning: Could not fetch existing keys from Sheets: {e}")
    
    # Try loading from local CSV as well
    csv_name = tab_name if tab_name else "leads"
    csv_path = os.path.join("scraped_leads", f"{csv_name}.csv")
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    if len(row) >= 14:
                        row_count += 1
                        name = normalize_name(row[0])
                        phone = row[4].strip().lstrip("'") if row[4] else ""
                        maps_raw = row[13] if len(row) > 13 else ""
                        maps_match = re.search(r'HYPERLINK\("([^"]+)"', maps_raw)
                        maps_url = maps_match.group(1) if maps_match else maps_raw
                        if maps_url:
                            existing_keys.add(("url", maps_url.strip()))
                        if name:
                            if phone:
                                existing_keys.add(("name_phone", name, phone))
                            existing_keys.add(("name", name))
        except Exception as e:
            logger.warning(f"[Local CSV] Warning: Could not read local keys from {csv_path}: {e}")
            
    logger.info(f"[Dedup] Loaded {row_count} existing rows ({len(existing_keys)} dedup keys) for deduplication.")
    return existing_keys

def append_to_sheet(rows, tab_name=None):
    if not rows: return
    try:
        _, worksheet = _get_sheet_client(tab_name)
        if worksheet is None:
            logger.warning("[Sheets] Warning: No credentials found. Skipping sheet append.")
            return

        existing_data = worksheet.get_all_values()
        if not existing_data:
            header = ["Business Name", "Category", "Lead Score", "Rating", "Phone", "Website", "Website Link", "Email", "LinkedIn", "Facebook", "Instagram", "Twitter", "Address", "Maps"]
            worksheet.append_row(header)
            header_fmt = CellFormat(
                textFormat=TextFormat(bold=True, foregroundColor=Color(1, 1, 1)),
                backgroundColor=Color(0.2, 0.2, 0.2),
                horizontalAlignment='LEFT',
                verticalAlignment='MIDDLE'
            )
            format_cell_range(worksheet, 'A1:N1', header_fmt)
            set_frozen(worksheet, rows=1)
            logger.info("[Sheets] Created header row and applied frozen row.")

        formatted_rows = []
        for row in rows:
            name, cat, score, rating, phone, website, email, linkedin, fb, ig, tw, address, maps_url = row
            formatted_phone = f"'{phone}" if phone and phone.startswith('+') else phone
            website_link = f'=HYPERLINK("{website}", "Visit Website")' if website else ""
            maps_link = f'=HYPERLINK("{maps_url}", "View on Maps")' if maps_url else ""
            formatted_rows.append([name, cat, score, rating, formatted_phone, website, website_link, email, linkedin, fb, ig, tw, address, maps_link])

        worksheet.append_rows(formatted_rows, value_input_option='USER_ENTERED')
        total_rows = len(worksheet.get_all_values())
        table_fmt = CellFormat(
            borders=Borders(
                top=Border('SOLID', Color(0.8, 0.8, 0.8)),
                bottom=Border('SOLID', Color(0.8, 0.8, 0.8)),
                left=Border('SOLID', Color(0.8, 0.8, 0.8)),
                right=Border('SOLID', Color(0.8, 0.8, 0.8))
            ),
            verticalAlignment='MIDDLE',
            wrapStrategy='WRAP'
        )
        format_cell_range(worksheet, f'A2:N{total_rows}', table_fmt)
        widths = [200, 150, 90, 70, 150, 200, 100, 200, 150, 150, 150, 150, 300, 100]
        for i, width in enumerate(widths):
            set_column_width(worksheet, chr(65 + i), width)
        logger.info(f"[Sheets] Successfully saved {len(rows)} leads to Google Sheets.")
    except Exception as e:
        logger.exception(f"[Sheets] Error appending to Google Sheet: {e}")

def append_to_local_csv(rows, filename):
    if not rows: return
    try:
        os.makedirs('scraped_leads', exist_ok=True)
        csv_path = os.path.join('scraped_leads', f"{filename}.csv")
        file_exists = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0
        
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                header = ["Business Name", "Category", "Lead Score", "Rating", "Phone", "Website", "Website Link", "Email", "LinkedIn", "Facebook", "Instagram", "Twitter", "Address", "Maps"]
                writer.writerow(header)
            
            formatted_rows = []
            for row in rows:
                name, cat, score, rating, phone, website, email, linkedin, fb, ig, tw, address, maps_url = row
                formatted_phone = f"'{phone}" if phone and phone.startswith('+') else phone
                website_link = f'=HYPERLINK("{website}", "Visit Website")' if website else ""
                maps_link = f'=HYPERLINK("{maps_url}", "View on Maps")' if maps_url else ""
                formatted_rows.append([name, cat, score, rating, formatted_phone, website, website_link, email, linkedin, fb, ig, tw, address, maps_link])
            
            writer.writerows(formatted_rows)
        logger.info(f"[Local CSV] Successfully saved {len(rows)} leads to local file: {csv_path}")
    except Exception as e:
        logger.error(f"[Local CSV] Error saving to CSV: {e}")

# ---------------------------------------------------------------------------
# Core scraper — with adaptive scrolling
# ---------------------------------------------------------------------------

def _scroll_feed_to_end(page, max_scrolls=MAX_SCROLL_ATTEMPTS):
    prev_count = 0
    stale_rounds = 0
    MAX_STALE = 4
    for scroll_num in range(max_scrolls):
        if page.locator('div[role="feed"]').count() == 0:
            return 0
        page.evaluate('''(selector) => {
            const feed = document.querySelector(selector);
            if (feed) feed.scrollTop = feed.scrollHeight;
        }''', 'div[role="feed"]')
        page.wait_for_timeout(SCROLL_PAUSE_MS)
        
        end_of_list = page.locator('span.HlvSq').count() > 0
        if not end_of_list:
            end_text = page.evaluate('''() => {
                const feed = document.querySelector('div[role="feed"]');
                if (!feed) return false;
                const lastChild = feed.lastElementChild;
                if (lastChild && lastChild.textContent.includes("You've reached the end")) return true;
                return false;
            }''')
            end_of_list = end_text
            
        current_count = page.locator('a[href*="/maps/place/"]').count()
        if end_of_list:
            logger.debug(f"[Scroll] End of list detected at scroll {scroll_num + 1}. Total: {current_count} places.")
            break
        if current_count == prev_count:
            stale_rounds += 1
            if stale_rounds >= MAX_STALE:
                logger.debug(f"[Scroll] No new results after {MAX_STALE} scrolls. Total: {current_count} places.")
                break
        else:
            stale_rounds = 0
        prev_count = current_count
        if scroll_num % 5 == 4:
            logger.debug(f"[Scroll] #{scroll_num + 1}: {current_count} places loaded so far...")
    return page.locator('a[href*="/maps/place/"]').count()

def extract_place_details(page, maps_url):
    """Refactored helper to extract business details from the current detail panel."""
    try:
        # Extract Name
        name_loc = page.locator('h1.fontHeadlineLarge')
        name = strip_icons(name_loc.first.inner_text()) if name_loc.count() > 0 else ""
        if not name or name == "Results":
            for h1 in page.locator('h1').all():
                t = strip_icons(h1.inner_text())
                if t and t != "Results":
                    name = t
                    break
        if not name: name = "Unknown"
        
        # Extract Address
        address_loc = page.locator('button[data-item-id="address"]')
        address = ""
        if address_loc.count() > 0:
            addr_lines = [strip_icons(l) for l in address_loc.inner_text().split('\n') if strip_icons(l)]
            address = addr_lines[-1] if addr_lines else ""
            
        # Extract Website
        website_loc = page.locator('a[data-item-id="authority"]')
        if website_loc.count() == 0:
            website_loc = page.locator('button[data-item-id="authority"]')
        raw_website = website_loc.get_attribute("href") if website_loc.count() > 0 else ""
        website = clean_google_url(raw_website)
        
        # Extract Phone
        phone_loc = page.locator('button[data-item-id^="phone:"]')
        phone = ""
        if phone_loc.count() > 0:
            phone_lines = [strip_icons(l) for l in phone_loc.inner_text().split('\n') if strip_icons(l)]
            phone = phone_lines[-1] if phone_lines else ""
        else:
            for text in page.locator('.fontBodyMedium').all_inner_texts():
                clean_t = strip_icons(text)
                m = re.search(r'(\+?\d[\d\s\-]{8,}\d)', clean_t)
                if m:
                    phone = m.group(1).strip()
                    break
                    
        # Extract Category
        NON_CATEGORY = {"learn more", "open", "closed", "directions", "save", "share", "send to phone", "website", "call", "overview", "reviews", "photos", "about", "menu", "updates"}
        def looks_like_category(text):
            t = strip_icons(text).strip()
            if not t or len(t) > 50: return False
            if t.lower() in NON_CATEGORY: return False
            if re.match(r'^[\d.,() \+\-]+$', t): return False
            if re.search(r'\d{5,}', t): return False
            if t.startswith("?") or t.startswith("$"): return False
            return True
        category = ""
        # 1. Try span.mgr77e (modern span selector)
        cat_span = page.locator('span.mgr77e').first
        if cat_span.count() > 0:
            t = strip_icons(cat_span.inner_text())
            if looks_like_category(t):
                category = t
                
        # 2. Try button.DkEaL (legacy button selector)
        if not category:
            cat_btn = page.locator('button.DkEaL').first
            if cat_btn.count() > 0:
                t = strip_icons(cat_btn.inner_text())
                if looks_like_category(t): category = t
                
        # 3. Try any button/element with jsaction containing category
        if not category:
            for btn in page.locator('button[jsaction*="category"]').all():
                t = strip_icons(btn.inner_text())
                if looks_like_category(t):
                    category = t
                    break
                    
        # 4. Sibling/parent inner text parsing next to rating
        if not category:
            try:
                rating_box = page.locator('div.F7nice').first
                if rating_box.count() > 0:
                    parent = rating_box.locator('..')
                    if parent.count() > 0:
                        text_parts = parent.inner_text().split('\n')
                        for part in text_parts:
                            clean = strip_icons(part).strip()
                            if clean and looks_like_category(clean):
                                category = clean
                                break
            except Exception:
                pass
                    
        # Extract Rating
        rating = ""
        try:
            panel = page.locator('div[role="main"]')
            stars_loc = panel.locator('div.F7nice span[aria-label*="stars"]').first
            if stars_loc.count() > 0:
                label = stars_loc.get_attribute("aria-label")
                if label:
                    m = re.search(r'(\d+\.?\d*)', label)
                    if m: rating = m.group(1)
        except Exception: pass
        
        return {
            "name": name, "category": category, "rating": rating,
            "phone": phone, "website": website, "address": address, "maps_url": maps_url
        }
    except Exception as e:
        logger.error(f"[Extract] Error in helper: {e}", exc_info=True)
        return None

def scrape_single_zone(query, target_results=MAX_RESULTS_PER_ZONE):
    leads = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                viewport={'width': 1280, 'height': 900},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                locale='en-US'
            )
            page = context.new_page()
            try:
                encoded_query = urllib.parse.quote_plus(query)
                page.goto(f"https://www.google.com/maps/search/{encoded_query}", timeout=30000)
                # Wait for multiple possibilities: Feed (List), H1 (Single result), or "No results" message
                selectors = ['div[role="feed"]', 'h1.fontHeadlineLarge', 'div.Q2vSnd', 'div.fontBodyMedium']
                try:
                    page.wait_for_selector(", ".join(selectors), timeout=15000)
                except Exception:
                    # If it times out, we check if at least one place exists or if "No results" text is visible
                    pass
                
                # Check for "No results" message by text content if the selector didn't match perfectly
                if page.locator('text="Google Maps can\'t find"').count() > 0 or page.locator('div.Q2vSnd').count() > 0:
                    logger.info(f"[Maps] No results found for query: \"{query}\"")
                    return []

                # Case 1: Multiple results (Feed exists)
                if page.locator('div[role="feed"]').count() > 0:
                    total_loaded = _scroll_feed_to_end(page)
                    logger.info(f"[Maps] Query: \"{query}\" -> {total_loaded} results loaded. Extracting up to {target_results}...")
                    places = page.locator('a[href*="/maps/place/"]').all()
                
                # Case 2: Single result redirect (H1 exists, no feed)
                elif page.locator('h1.fontHeadlineLarge').count() > 0:
                    logger.info(f"[Maps] Single result redirect for query: \"{query}\"")
                    lead = extract_place_details(page, page.url)
                    if lead:
                        leads.append(lead)
                    return leads
                
                # Case 3: Fallback if everything else fails but some results might be there
                else:
                    places = page.locator('a[href*="/maps/place/"]').all()
                    if not places:
                        logger.info(f"[Maps] No identifiable results for query: \"{query}\"")
                        return []
                    logger.info(f"[Maps] Extracted {len(places)} results via fallback locator.")

                # Step 1: Collect all place URLs while the list page is open.
                # Do NOT click — clicking reshuffles the DOM and makes later
                # locators stale. We only need the hrefs, which are stable.
                place_urls = []
                for place in places:
                    if len(place_urls) >= target_results * 2:
                        break  # generous buffer for sponsored/skipped entries
                    try:
                        place_text = place.inner_text(timeout=3000)
                        if "Sponsored" in place_text:
                            continue
                        url = place.get_attribute('href', timeout=3000) or ""
                        if url:
                            place_urls.append(url)
                    except Exception:
                        continue

                # Step 2: Navigate to each place URL directly — no panel-opening jank,
                # no stale locator issues, reliable h1 on every place page.
                processed = 0
                for maps_url in place_urls:
                    if processed >= target_results:
                        break
                    try:
                        page.goto(maps_url, timeout=15000, wait_until='domcontentloaded')
                        try:
                            page.wait_for_selector('h1.fontHeadlineLarge', timeout=5000)
                        except Exception:
                            pass
                        lead = extract_place_details(page, maps_url)
                        if lead:
                            leads.append(lead)
                            processed += 1
                            if processed % 10 == 0:
                                logger.debug(f"[Extract] {processed} leads extracted...")
                    except Exception as e:
                        logger.error(f"[Zone] Failed to extract {maps_url}: {e}")
                logger.info(f"[Maps] Extracted {processed} leads from query: \"{query}\"")
            except Exception as e:
                logger.exception(f"[Zone] Scraping error for \"{query}\": {e}")
            finally:
                context.close()
                browser.close()
    except Exception as e:
        logger.exception(f"[Zone] Playwright launch error: {e}")
    return leads

# ---------------------------------------------------------------------------
# Deduplication engine
# ---------------------------------------------------------------------------

class DeduplicationEngine:
    def __init__(self, existing_keys=None):
        self._lock = threading.Lock()
        self._seen_urls = set()
        self._seen_name_phone = set()
        self._seen_name_addr = set()
        self._seen_names = set()
        if existing_keys:
            for key in existing_keys:
                if key[0] == "url":
                    self._seen_urls.add(key[1])
                elif key[0] == "name_phone":
                    self._seen_name_phone.add((key[1], key[2]))
                elif key[0] == "name":
                    self._seen_names.add(key[1])

    def is_duplicate(self, lead):
        maps_url = lead.get("maps_url", "").strip()
        name = normalize_name(lead.get("name", ""))
        phone = lead.get("phone", "").strip()
        address = lead.get("address", "").strip().lower()
        
        with self._lock:
            # Exact URL match (strongest)
            if maps_url and maps_url in self._seen_urls: 
                return True
            
            # Name + Phone match (very strong)
            if name and phone:
                if (name, phone) in self._seen_name_phone: 
                    return True
            
            # Name + Address match (strong - catches same business at same location)
            if name and address:
                if (name, address) in self._seen_name_addr: 
                    return True
            
            # Name only match (weak - only if no phone/address)
            if name and not phone and not address:
                if name in self._seen_names: 
                    return True
            
            # Store for future comparisons
            if maps_url: 
                self._seen_urls.add(maps_url)
            if name and phone: 
                self._seen_name_phone.add((name, phone))
            if name and address: 
                self._seen_name_addr.add((name, address))
            if name: 
                self._seen_names.add(name)
            
            return False

# ---------------------------------------------------------------------------
# Enrich leads
# ---------------------------------------------------------------------------

def enrich_leads(leads):
    if not leads: return leads
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_lead = {executor.submit(extract_website_data, l["website"]): l for l in leads if l.get("website")}
        for future in as_completed(future_to_lead):
            lead = future_to_lead[future]
            try:
                web_data = future.result(timeout=30)
                lead.update(web_data)
            except Exception as e:
                logger.debug(f"[Enrich] Error enriching {lead.get('website')}: {e}")
                lead.update({"emails": "", "linkedin": "", "facebook": "", "instagram": "", "twitter": ""})
    for lead in leads:
        for key in ["emails", "linkedin", "facebook", "instagram", "twitter"]:
            if key not in lead: lead[key] = ""
        lead["score"] = calculate_score(lead)
    return leads

def leads_to_sheet_rows(leads):
    return [[l["name"], l["category"], l["score"], l["rating"], l["phone"], l["website"], l["emails"], l["linkedin"], l["facebook"], l["instagram"], l["twitter"], l["address"], l["maps_url"]] for l in leads]

# ---------------------------------------------------------------------------
# Background job processor
# ---------------------------------------------------------------------------

def _scrape_zone_worker(query, target_results):
    # Acquire the global semaphore BEFORE launching Chromium.
    # This caps total concurrent browser instances across all parallel jobs,
    # preventing OOM when multiple jobs run at the same time.
    with _browser_semaphore:
        # Only retry on exceptions (network errors), not on empty results.
        # Retrying empty results doubles the zone time for no benefit.
        for attempt in range(2):
            try:
                return scrape_single_zone(query, target_results)
            except Exception as e:
                logger.error(f"[Worker] Error on attempt {attempt + 1} for '{query}': {e}", exc_info=True)
                if attempt < 1:
                    time.sleep(2)
    return []

def _process_job(job_id, term, location, max_results, zone_split=True, sheet_tab=None):
    job = jobs[job_id]
    
    def log_activity(message, level="info"):
        """Add human-readable activity to job log (thread-safe)."""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        with jobs_lock:
            job["activity_log"].append({"time": timestamp, "message": message, "level": level})
            if len(job["activity_log"]) > 20:
                job["activity_log"].pop(0)
        console_message = message.encode('ascii', 'ignore').decode('ascii')
        if level == "error":
            logger.error(f"[Activity] {console_message}")
        elif level == "warning":
            logger.warning(f"[Activity] {console_message}")
        else:
            logger.info(f"[Activity] {console_message}")
        sys.stdout.flush()
        sys.stderr.flush()
    
    try:
        log_activity(f"Starting scrape for '{term}' in '{location}'")
        job["status"] = "running"
        
        if zone_split and is_large_area(location):
            zone_info = get_zones(location, max_results)
            zones = zone_info["zones"]
            parent_city = zone_info["parent_city"]
            strategy = zone_info["strategy"]
            log_activity(f"Split into {len(zones)} zones for better coverage")
        else:
            zones, parent_city, strategy = [location], location, "single"
            log_activity(f"Searching single area: {location}")
        
        job["total_zones"] = len(zones)
        
        logger.info(f"JOB {job_id}: '{term}' in '{location}' | Target: {max_results} unique | Zones: {len(zones)} | Strategy: {strategy}")
        
        log_activity("Checking for existing data to avoid duplicates...")
        existing_keys = fetch_existing_sheet_keys(sheet_tab, job.get("export_mode", "csv"))
        dedup = DeduplicationEngine(existing_keys)
        
        all_leads = []
        num_workers = min(MAX_BROWSER_WORKERS, len(zones))
        
        # SMART APPROACH: Keep scraping zones until we have enough unique leads
        zone_idx = 0
        batch_size = num_workers
        
        while len(all_leads) < max_results and zone_idx < len(zones):
            if job.get("cancelled"):
                job["status"] = "cancelled"
                job["completed_at"] = time.time()
                log_activity("Scrape cancelled by user", "warning")
                logger.info(f"JOB {job_id} cancelled by user.")
                return
            
            batch_zones = zones[zone_idx:zone_idx + batch_size]
            queries = []
            for zone in batch_zones:
                q = f"{term} in {zone}" if strategy == "single" else format_query(term, zone, parent_city, strategy)
                queries.append((zone, q))
            
            job["current_zone"] = f"{batch_zones[0]} (+{len(batch_zones)-1} more)" if len(batch_zones) > 1 else batch_zones[0]
            
            remaining_needed = max_results - len(all_leads)
            log_activity(f"Scanning {len(batch_zones)} zones... (need {remaining_needed} more unique leads)")

            # Budget each parallel zone: spread the remaining target across workers,
            # add a 60% dupe buffer so deduplication doesn't leave us short.
            # Cap at MAX_RESULTS_PER_ZONE; floor at 20 so tiny searches still work.
            per_zone_target = max(
                min(remaining_needed + 5, 20),  # floor scales with need, caps at 20
                min(MAX_RESULTS_PER_ZONE, math.ceil(remaining_needed * 1.6 / len(batch_zones)))
            )

            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                future_to_zone = {executor.submit(_scrape_zone_worker, q, per_zone_target): zone_name for zone_name, q in queries}
                for future in as_completed(future_to_zone):
                    zone_name = future_to_zone[future]
                    try:
                        zone_leads = future.result(timeout=300)
                        if zone_leads:
                            log_activity(f"Found {len(zone_leads)} results in {zone_name}")
                        _process_zone_results(zone_leads, dedup, all_leads, max_results, job, log_activity)
                        
                        # Stop processing remaining zones if target reached
                        if len(all_leads) >= max_results:
                            log_activity(f"✅ Target reached! Stopping zone processing")
                            # Cancel remaining futures
                            for f in future_to_zone:
                                if not f.done():
                                    f.cancel()
                            break
                    except Exception as e:
                        log_activity(f"Error in {zone_name}: {str(e)[:50]}", "error")
                        logger.exception(f"[Zone] {zone_name} failed: {e}")
                            
            zone_idx += batch_size
            job["zones_completed"] = min(zone_idx, len(zones))
            
            # Check if we have enough unique leads
            if len(all_leads) >= max_results:
                log_activity(f"Reached target! Got {len(all_leads)} unique leads")
                break
            
            # Check if we've run out of zones
            if zone_idx >= len(zones):
                log_activity(f"Completed all zones. Got {len(all_leads)} unique leads (target was {max_results})", "warning")
                break
                
        job["status"] = "completed"
        job["completed_at"] = time.time()
        job["zones_completed"] = len(zones)
        log_activity(f"Scrape complete! Found {len(all_leads)} unique leads")
        if job.get("export_mode") == "sheet":
            log_activity(f"All data saved to Google Sheets")
        else:
            log_activity(f"All data saved to CSV")
        logger.info(f"JOB {job_id} COMPLETED: {len(all_leads)} unique leads scraped.")
    except Exception as e:
        job["status"] = "failed"
        job["completed_at"] = time.time()
        job["errors"].append(str(e))
        log_activity(f"Scrape failed: {str(e)[:100]}", "error")
        logger.exception(f"[Job] FAILED: {e}")

def _process_zone_results(zone_leads, dedup, all_leads, max_results, job, log_activity=None):
    if not zone_leads: return
    unique_leads = []
    dupes_skipped = 0
    for lead in zone_leads:
        if not dedup.is_duplicate(lead):
            unique_leads.append(lead)
        else:
            dupes_skipped += 1
            
    if dupes_skipped > 0:
        logger.info(f"[Dedup] Skipped {dupes_skipped} duplicates, {len(unique_leads)} unique in this zone.")
        if log_activity:
            dupe_pct = round((dupes_skipped / (dupes_skipped + len(unique_leads))) * 100)
            log_activity(f"⚡ Filtered {dupes_skipped} duplicates ({dupe_pct}% overlap)")
        
    if unique_leads:
        remaining = max_results - len(all_leads)
        trimmed = unique_leads[:remaining]  # trim before enriching — no point enriching leads we'll discard
        if log_activity:
            log_activity(f"🔄 Enriching {len(trimmed)} leads (finding emails, social links)...")
        enriched = enrich_leads(trimmed)
        batch = enriched
        if log_activity:
            log_activity(f"💾 Saving {len(batch)} leads...")
        
        if job.get("export_mode") == "sheet":
            append_to_sheet(leads_to_sheet_rows(batch), job.get("sheet_tab"))
        
        # Save to job-specific local CSV
        append_to_local_csv(leads_to_sheet_rows(batch), f"leads_{job.get('job_id') or 'current'}")
        # Save to tab-specific consolidated CSV
        if job.get("sheet_tab"):
            append_to_local_csv(leads_to_sheet_rows(batch), job.get("sheet_tab"))
            
        all_leads.extend(batch)
        job["leads_found"] = len(all_leads)
        job["leads"].extend(batch)
        if log_activity:
            log_activity(f"📊 Total leads collected: {len(all_leads)}")

def _cleanup_old_jobs():
    """Remove completed/failed/cancelled jobs older than 1 hour to prevent memory growth."""
    cutoff = time.time() - 3600
    with jobs_lock:
        to_delete = [
            jid for jid, j in jobs.items()
            if j.get("status") in ("completed", "failed", "cancelled")
            and (j.get("completed_at") or 0) < cutoff
        ]
        for jid in to_delete:
            del jobs[jid]
            logger.debug(f"[Cleanup] Removed stale job {jid}")

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.route("/scrape", methods=["POST"])
def start_scrape():
    data = request.json
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # Validate and sanitize term
    term = str(data.get('term', '')).strip()
    if not term:
        return jsonify({"error": "Missing 'term'"}), 400
    if len(term) > 100:
        return jsonify({"error": "'term' must be at most 100 characters"}), 400

    # Validate and sanitize location
    location = str(data.get('location', '')).strip()
    if not location:
        return jsonify({"error": "Missing 'location'"}), 400
    if len(location) > 200:
        return jsonify({"error": "'location' must be at most 200 characters"}), 400

    # Validate max_results
    try:
        max_results = int(data.get('max_results', 15))
    except (ValueError, TypeError):
        max_results = 15
    max_results = max(1, min(max_results, 500))

    zone_split = bool(data.get('zone_split', True))
    sheet_tab = _sanitize_sheet_tab(data.get('sheet_tab', ''))
    export_mode = str(data.get('export_mode', 'csv')).strip().lower()
    if export_mode not in ('csv', 'sheet'):
        export_mode = 'csv'

    # Remove stale completed/failed jobs before creating new ones
    _cleanup_old_jobs()

    job_id = str(uuid.uuid4())[:8]
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "status": "starting", "term": term, "location": location,
            "max_results": max_results, "zone_split": zone_split, "sheet_tab": sheet_tab,
            "export_mode": export_mode,
            "total_zones": 0, "zones_completed": 0, "current_zone": "", "leads_found": 0,
            "leads": [], "errors": [], "activity_log": [], "completed_at": None,
        }
    threading.Thread(
        target=_process_job,
        args=(job_id, term, location, max_results, zone_split, sheet_tab),
        daemon=True
    ).start()
    return jsonify({"job_id": job_id, "status": "started"})

@app.route("/sheet-url", methods=["GET"])
def get_sheet_url_route():
    url = get_sheet_url()
    if url:
        return jsonify({"url": url})
    return jsonify({"url": "https://docs.google.com/spreadsheets/d/1BCEot7vu1-H0XQ7EE9C6P3uHJdtABD8VEO87N_-WPOc/edit?gid=0#gid=0"}), 200

@app.route("/status/<job_id>", methods=["GET"])
def get_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        # Snapshot all fields under the lock to avoid torn reads
        snap = {
            "status": job["status"],
            "total_zones": job["total_zones"],
            "zones_completed": job["zones_completed"],
            "current_zone": job["current_zone"],
            "leads_found": job["leads_found"],
            "errors": list(job["errors"][-3:]),
            "activity_log": list(job.get("activity_log", [])[-10:]),
            "export_mode": job.get("export_mode", "csv"),
        }
    total = snap["total_zones"] or 1
    completed = snap["zones_completed"]
    progress = 100 if snap["status"] == "completed" else min(round((completed / total) * 100), 100)
    return jsonify({
        "job_id": job_id, "status": snap["status"], "progress_pct": progress,
        "total_zones": snap["total_zones"], "zones_completed": completed,
        "current_zone": snap["current_zone"], "leads_found": snap["leads_found"],
        "errors": snap["errors"], "activity_log": snap["activity_log"],
        "export_mode": snap["export_mode"],
        "download_url": f"/download/leads_{job_id}.csv"
    })

@app.route("/cancel/<job_id>", methods=["POST"])
def cancel_job(job_id):
    with jobs_lock: job = jobs.get(job_id)
    if not job: return jsonify({"error": "Job not found"}), 404
    job["cancelled"] = True
    return jsonify({"status": "cancelling"})

@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    import flask
    if not re.match(r'^[a-zA-Z0-9_\-\.]+\.csv$', filename):
        return jsonify({"error": "Invalid filename"}), 400
    
    directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scraped_leads')
    if not os.path.exists(os.path.join(directory, filename)):
        return jsonify({"error": "File not found"}), 404
        
    return flask.send_from_directory(directory, filename, as_attachment=True)

@app.route("/", methods=["POST"])
def run_scraper_legacy():
    data = request.json
    if not data: return jsonify({"error": "No data"}), 400
    return start_scrape()

@app.route("/health", methods=["GET"])
def health():
    with jobs_lock:
        active = sum(1 for j in jobs.values() if j.get("status") == "running")
        total = len(jobs)
    return jsonify({"status": "healthy", "active_jobs": active, "total_jobs": total})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    # Force unbuffered output for cloud deployments
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    logger.info(f"Scraper backend starting on: http://localhost:{port}")
    logger.info(f"Python stdout buffering: {sys.stdout.line_buffering}")
    app.run(host="0.0.0.0", port=port, threaded=True)