# BrainMap AI — Google Maps Lead Scraper Project Documentation

This file serves as a comprehensive, centralized knowledge base for the **BrainMap AI Lead Scraper** project. It details the system architecture, file structures, core algorithms, configuration guidelines, and deployment strategies.

---

## 1. Project Overview

**BrainMap AI** is an advanced, high-performance web scraping and enrichment platform designed to extract high-volume B2B leads from Google Maps and save them directly to Google Sheets. 

Traditional Google Maps searches are capped at **60 to 120 results** per query. BrainMap AI overcomes this limitation by dynamically subdividing target locations into non-overlapping sub-regions (zones) and querying them in parallel, allowing users to collect hundreds or thousands of high-quality leads from any city, state, or country on Earth.

### Primary Workflow
```
[User Input Query] 
       │ (e.g., "plumbers" in "california", target 500 leads)
       ▼
[Zone Manager (OSM / Grid)] 
       │ (Splits California into 25 major cities)
       ▼
[Parallel Scraping Engine] 
       │ (Dispatches up to 3 Playwright Chrome instances)
       ▼
[Deduplication Engine] 
       │ (Checks duplicates using URL, name/phone, and existing Google Sheet rows)
       ▼
[Website Enricher & Scoring] 
       │ (Scans business websites for emails, socials, and computes lead scores)
       ▼
[Google Sheets / Live UI Feed]
       └─► Appends rows in batches & updates frontend activity feed in real time
```

---

## 2. Technical Architecture & Core Subsystems

### A. Dynamic Zone Discovery (`zone_manager.py`)
To scrape thousands of leads without hitting Google's truncation limits, the scraper breaks down locations using free APIs from OpenStreetMap (OSM):
1. **Geocoding (Nominatim API)**: Resolves the search location to a boundary box and central coordinate points.
2. **Sub-Region Lookup (Overpass API)**:
   - For **large areas** (e.g. states/countries), it fetches major cities in the boundary box.
   - For **medium areas** (e.g. cities), it fetches local neighborhoods/suburbs.
3. **Grid-Based Fallback**: If OSM APIs fail or return fewer than 3 regions, it divides the boundary box mathematically into a coordinate-based grid.
4. **Adaptive Scaling**: Automatically limits search zones to match the requested quantity of leads (using a $2.0\times$ coverage multiplier, minimum 25 zones).

### B. Asynchronous Execution & Job Orchestration (`main.py`)
To prevent HTTP timeouts when scraping massive datasets (which can take 15–60 minutes), the system uses an asynchronous architecture:
- Requests targeting **>50 results** are spawned in background worker threads.
- The server returns a unique `job_id` instantly, allowing the client to poll progress.
- Cleanups are automatically handled, and browser worker pools are limited by a global semaphore (`_browser_semaphore = 4`) to prevent Out Of Memory (OOM) crashes on standard host containers.
- If a target lead count is satisfied, the background threads terminate early to save API calls and bandwidth.

### C. Deduplication Engine
Prevents scraping or saving duplicate leads across different zones or execution runs:
- **First-tier Check**: Compares the unique canonical Google Maps place URL.
- **Second-tier Check**: Compares normalized business name + telephone number.
- **Third-tier Check**: Compares normalized business name + address.
- **Cross-Run Protection**: Downloads all existing URL keys from the active Google Sheet tab at the start of a scrape job to skip already harvested leads.

### D. Parsing, Enrichment, and Scoring
- **Web Contact Scraper**: Uses `BeautifulSoup` to scan business websites (`/`, `/contact`, `/contact-us`) for contact emails and social media platforms (LinkedIn, Facebook, Instagram, Twitter).
- **Lead Scoring**: Assigns a quality rating from `0` to `100` based on:
  - Missing website: $+50$ (indicates high potential for outreach/agency sales)
  - Missing social handles: $+10$ to $+15$ each
  - Missing email contact: $+20$
  - Review rating $< 4.0$: $+20$ (bad reviews indicating service improvement opportunities)
  - Perfect website/social presence: score of $100$

---

## 3. Directory & File Map

```
mini_pro/
└── brain_map/
    ├── main.py                     # Core Flask API backend & job scheduler
    ├── zone_manager.py             # Geographic splitting & OpenStreetMap client
    ├── check_gs_access.py          # Script verifying GCP sheets connection
    ├── get_sheet.py                # Helper reading active sheet titles
    ├── run_scraper.py              # CLI interactive script utilizing backend APIs
    ├── start_frontend.py           # Local HTTP server serving UI on port 3000
    ├── start_all.bat / .sh         # One-click script starting backend & frontend
    ├── Dockerfile                  # Production container configuration
    ├── requirements.txt            # Python dependencies list
    ├── .env                        # Environment variables (Google Sheets name/credentials)
    │
    ├── frontend/                   # UI Assets
    │   ├── index.html              # Main scraping interface
    │   ├── script.js               # Polling & Session restoration logic
    │   ├── style.css               # Modern glassmorphism & visual layer styling
    │   ├── config.js               # Routing configuration for Local/VM/Cloud Run
    │   ├── guide.html              # Interactive usage field guide
    │   └── assets/                 # App logos and watermark assets
    │
    ├── documentation/              # Architecture documents
    │   ├── ARCHITECTURE.md         # Dataflow flowcharts & API schemas
    │   └── ZONE_MANAGER_APPROACH.md# Details on Overpass OSM queries
    │
    └── logs/                       # Log files (automatically generated)
        ├── scraper_activity.log    # General debug & activity tracer
        └── scraper_errors.log      # Stack trace records of failed operations
```

---

## 4. API Endpoints

### 1. Synchronous Scrape (Legacy)
* **Endpoint**: `POST /`
* **Use Case**: Quick scraping for targets $\le 50$ results.
* **Payload**:
  ```json
  {
    "term": "cafes",
    "location": "Indiranagar, Bengaluru",
    "max_results": 20,
    "zone_split": false,
    "sheet_tab": "CafesTab"
  }
  ```

### 2. Start Asynchronous Scrape
* **Endpoint**: `POST /scrape`
* **Use Case**: Production scraping for targets $> 50$ results.
* **Payload**:
  ```json
  {
    "term": "software companies",
    "location": "California",
    "max_results": 300,
    "zone_split": true,
    "sheet_tab": "Software_CA"
  }
  ```
* **Response**:
  ```json
  {
    "job_id": "8f8b8a5d-6c1c-4b68-8d2a-436f9bcf0407",
    "status": "started"
  }
  ```

### 3. Poll Job Status
* **Endpoint**: `GET /status/<job_id>`
* **Response**:
  ```json
  {
    "job_id": "8f8b8a5d-6c1c-4b68-8d2a-436f9bcf0407",
    "status": "running",
    "progress_pct": 28,
    "total_zones": 25,
    "zones_completed": 7,
    "current_zone": "San Francisco",
    "leads_found": 84,
    "activity_log": [
      { "time": "12:04:12", "message": "Scanned San Jose... Found 14 leads", "level": "info" }
    ],
    "errors": []
  }
  ```

### 4. Cancel Scrape
* **Endpoint**: `POST /cancel/<job_id>`
* **Response**:
  ```json
  {
    "status": "cancelling"
  }
  ```

---

## 5. Local Setup & Execution

### Prerequisites
- Python 3.9+ installed
- A Google Cloud Platform Project with **Google Sheets API** and **Google Drive API** enabled
- A downloaded Service Account JSON key (`credentials.json`) shared with the target Google Sheet

### Quick Start (Automated)
Run the helper startup script to automatically launch both the API backend and the local UI server:
* **Windows**:
  ```powershell
  start_all.bat
  ```
* **Linux/Mac**:
  ```bash
  chmod +x start_all.sh
  ./start_all.sh
  ```

### Manual Execution

1. **Activate Virtual Environment & Install Dependencies**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # venv\Scripts\activate on Windows
   pip install -r requirements.txt
   playwright install chromium
   ```

2. **Setup environment configurations** in `.env`:
   ```env
   GOOGLE_SHEET_NAME=Web_scrp
   GOOGLE_APPLICATION_CREDENTIALS_JSON='{"type": "service_account", ...}'
   MAX_BROWSER_WORKERS=3
   PORT=8081
   ```

3. **Start the API Server**:
   ```bash
   python main.py
   ```

4. **Serve the Frontend**:
   ```bash
   python start_frontend.py
   ```
   Open your browser to `http://localhost:3000`.

---

## 6. Production Deployment Guidelines

### Backend Deployment (Google Cloud Run / GCE VM)
The backend is Dockerized and handles multi-threaded operations.

* **Recommended Spec**:
  - Memory: **2Gi** (Minimum required to run up to 3 Playwright instances in parallel without memory leaks).
  - CPU: **2 Cores**.
  - Timeout: **3600s (60 minutes)** (Necessary to prevent premature shutdown of high-volume scrapes).

* **GCP Cloud Run Deploy Command**:
  ```bash
  gcloud run deploy lead-scraper-backend \
    --source . \
    --platform managed \
    --region us-central1 \
    --memory 2Gi \
    --cpu 2 \
    --timeout 3600 \
    --concurrency 5 \
    --max-instances 3 \
    --set-env-vars GOOGLE_SHEET_NAME="YourSheet"
  ```

### Frontend Deployment (Vercel)
The UI is built with vanilla HTML/CSS/JS and is entirely static, making it ideal for free deployment platforms:
1. Connect your Github Repository to Vercel.
2. Select the `frontend` folder as the Root Directory.
3. Configure the backend URL in `frontend/config.js` by setting `CURRENT: 'VM'` or `CURRENT: 'CLOUD_RUN'` to match your deployed API address.
4. Deploy!

---

## 7. Diagnostics & Logging

If leads are not showing or requests fail:
1. **Google Sheets Auth**: Run `python check_gs_access.py` to confirm the service account can access and edit the sheet specified in `.env`.
2. **Scraper Activity Logs**: Check `logs/scraper_activity.log` for step-by-step progress and Playwright browser outputs.
3. **Scraper Errors**: Check `logs/scraper_errors.log` for full traceback dumps of any crashes or exceptions.
