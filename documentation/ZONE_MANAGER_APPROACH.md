# BrainMap AI: Dynamic Zone Manager Architecture

This document explains the **Zone Manager** approach used by the BrainMap AI Lead Scraper to extract high-volume (500+) leads from any city worldwide. 

## The Problem It Solves

Google Maps has a hard limitation: searching for a broad query like *"Plumbers in Bengaluru"* will only return **around 60 to 120 results**, regardless of how many plumbers actually exist in the city. Google truncates the list to protect its API and encourage paid usage. 

To bypass this limit and extract thousands of leads from an entire city, we must **sub-divide the city into smaller neighborhoods** (e.g., *"Plumbers in Koramangala"*, *"Plumbers in Indiranagar"*).

**The Old Approach:** We used to rely on a hardcoded, manual list of neighborhoods for 6 Indian cities. This didn't scale. If a user searched for "Jaipur" or "Berlin," it either failed or generated poor results.

## The New Approach: Dynamic OpenStreetMap Integration

The scraper now dynamically "learns" the internal geography of **any location on Earth** entirely automatically, using free APIs from OpenStreetMap (OSM).

Here is the three-step flow of the Zone Manager algorithm:

### Step 1: Geocoding (Nominatim API)
When a user searches for a broad location (e.g., "Jaipur"):
1. The Zone Manager pings the **Nominatim Geocoding API**.
2. It fetches the exact GPS coordinates (Latitude/Longitude) and the spatial **Bounding Box** (North, South, East, West borders) of that city.
3. It standardizes the city name.

### Step 2: Locality Discovery (Overpass API)
Once we have the bounding box, the system needs the names of the neighborhoods inside it.
1. It queries the **Overpass API**, asking for all nodes and zones tagged as `suburb`, `neighbourhood`, `quarter`, or `village` within that bounding box.
2. The Overpass API returns a clean list of real, locally-recognized neighborhood names (e.g., "Malviya Nagar", "C Scheme").
3. **Google loves local names:** These exact, real-world neighborhood names are the highest quality search parameters to feed into Google Maps.

### Step 3: Adaptive Coverage & Grid Fallback
Sometimes, OpenStreetMap might not have well-defined neighborhoods for a remote area.
1. The Zone Manager intelligently assesses: *"Did OSM return enough zones to satisfy the user's lead target?"*
2. If OSM returns < 5 neighborhoods, the Zone Manager activates the **Grid Fallback Engine**.
3. It mathematically divides the city's bounding box into a 4x4 matrix of geographic coordinates (e.g., `12.97°N, 77.59°E`).
4. It formats queries using Google Maps coordinate syntax (`automotive near 12.97,77.59`), forcing Maps to scan that specific grid square.

## Orchestration & Deduplication

Once the zones are mapped out, here is how the scraping process operates:

* **Parallel Execution:** If a user wants 500 leads, the Zone Manager might select 9 neighborhoods. It dispatches **3 browser instances at the same time** (multi-threading), assigning different zones to each browser.
* **Auto-Scaling Results:** The scraper adaptively scrolls the feed until Google definitively says "You've reached the end." This extracts the absolute maximum available per neighborhood.
* **Strict Deduplication:** Because neighborhoods overlap, the same business might appear twice. The engine checks a comprehensive unique footprint:
  1. Primary: **Google Maps Place URL** (The most accurate fingerprint).
  2. Secondary: **Name + Phone Number**.
  3. Tertiary: **Name + Address**.

### Summary of Benefits 
* **Zero Configuration:** Works for any city in any country out of the box. No manual list maintenance.
* **Scalability:** Easily handles extra-large cities by automatically pulling hundreds of OSM localities if the lead quota is high.
* **Accuracy:** Uses official, localized OSM neighborhood definitions which align perfectly with Google Maps localized search algorithms.
