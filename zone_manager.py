"""
Zone Manager — Dynamic geographic coverage for large-area scraping.

Instead of hardcoded locality lists, this module dynamically discovers
sub-areas of ANY city using OpenStreetMap's free APIs:

  1. Nominatim  — geocodes the city name → bounding box + coordinates
  2. Overpass   — fetches all neighborhoods/suburbs within the bbox
  3. Grid split — if OSM doesn't return enough zones, subdivides the
                   bounding box into a geographic grid

Works for any city on Earth. No API keys. No hardcoded lists.
"""

import math
import re
import time
import requests

# ---------------------------------------------------------------------------
# In-memory cache so we don't re-query OSM for the same city twice in one run
# ---------------------------------------------------------------------------
_zone_cache = {}

# Nominatim requires a valid User-Agent (their usage policy)
_HEADERS = {
    "User-Agent": "BrainMapAI-LeadScraper/1.0 (contact: support@rnsbrains.com)"
}

_NOMINATIM_URL = "https://nominatim.openstreetmap.org"
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"


# ---------------------------------------------------------------------------
# Step 1: Geocode the location → get bounding box
# ---------------------------------------------------------------------------

def _geocode_location(location: str) -> dict | None:
    """
    Uses Nominatim to geocode a location string.
    Returns { 'lat', 'lon', 'bbox': [south, north, west, east], 'display_name' }
    or None if not found.
    """
    try:
        params = {
            "q": location,
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        }
        resp = requests.get(f"{_NOMINATIM_URL}/search", params=params,
                            headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None

        r = results[0]
        bbox = r.get("boundingbox", [])
        return {
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "bbox": [float(b) for b in bbox] if len(bbox) == 4 else None,
            "display_name": r.get("display_name", location),
            "type": r.get("type", ""),
            "class": r.get("class", ""),
        }
    except Exception as e:
        print(f"  [Geocode] Failed to geocode '{location}': {e}")
        return None


# ---------------------------------------------------------------------------
# Step 2: Fetch neighborhoods/suburbs from Overpass API
# ---------------------------------------------------------------------------

def _fetch_osm_neighborhoods(bbox: list, location_name: str) -> list[str]:
    """
    Queries Overpass API for neighborhoods, suburbs, quarters, towns.
    
    PRIORITIZES: suburbs, towns over tiny hamlets
    FILTERS: weird names, numbers, special characters

    bbox format: [south, north, west, east]
    Returns a list of zone name strings, prioritized by place type.
    """
    south, north, west, east = bbox

    # Query for named places: PRIORITIZE suburbs/towns
    query = f"""
    [out:json][timeout:90];
    (
      node["place"~"suburb|neighbourhood|quarter|town"]
        ({south},{west},{north},{east});
      way["place"~"suburb|neighbourhood|quarter"]
        ({south},{west},{north},{east});
      relation["place"~"suburb|neighbourhood|quarter"]
        ({south},{west},{north},{east});
    );
    out center tags;
    """

    try:
        resp = requests.post(_OVERPASS_URL, data={"data": query},
                             headers=_HEADERS, timeout=90)
        resp.raise_for_status()
        data = resp.json()

        # Separate zones by priority
        priority_zones = []  # suburbs, towns
        regular_zones = []   # neighbourhoods, quarters
        
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            name = tags.get("name:en") or tags.get("name", "")
            place_type = tags.get("place", "")
            
            if name and len(name) > 2 and len(name) < 50:
                # Filter out bad names
                if re.match(r'^\d+$', name):  # Skip pure numbers
                    continue
                if re.search(r'[^\w\s\-\']', name):  # Skip weird special chars
                    continue
                if name.lower() in ['lost city', 'ghost town', 'unnamed']:
                    continue
                    
                # Prioritize by place type
                if place_type in ['suburb', 'town']:
                    priority_zones.append(name)
                else:
                    regular_zones.append(name)

        # Combine: priority first, then regular
        all_zones = list(set(priority_zones)) + list(set(regular_zones) - set(priority_zones))
        
        print(f"  [OSM] Found {len(all_zones)} neighborhoods for '{location_name}' ({len(set(priority_zones))} priority).")
        return all_zones

    except Exception as e:
        print(f"  [OSM] Overpass query failed for '{location_name}': {e}")
        return []


def _fetch_major_cities_in_bbox(bbox: list, location_name: str) -> list[str]:
    """
    Queries Overpass API for major cities/towns within the bounding box.
    This is better than grid because cities don't overlap geographically.
    
    bbox format: [south, north, west, east]
    Returns a list of city name strings.
    """
    south, north, west, east = bbox

    # Query for cities and towns
    query = f"""
    [out:json][timeout:90];
    (
      node["place"~"city|town"]
        ({south},{west},{north},{east});
      way["place"~"city|town"]
        ({south},{west},{north},{east});
      relation["place"~"city|town"]
        ({south},{west},{north},{east});
    );
    out center tags;
    """

    try:
        resp = requests.post(_OVERPASS_URL, data={"data": query},
                             headers=_HEADERS, timeout=90)
        resp.raise_for_status()
        data = resp.json()

        cities = set()
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            name = tags.get("name:en") or tags.get("name", "")
            if name and len(name) > 1 and len(name) < 60:
                # Filter out generic names
                if not re.match(r'^\d+$', name):
                    cities.add(name)

        city_list = sorted(cities)
        print(f"  [OSM] Found {len(city_list)} cities/towns for '{location_name}' via Overpass.")
        return city_list

    except Exception as e:
        print(f"  [OSM] City query failed for '{location_name}': {e}")
        return []


# ---------------------------------------------------------------------------
# Step 3: Grid-based fallback — split bounding box into cells
# ---------------------------------------------------------------------------

def _generate_grid_zones(bbox: list, location_name: str, num_cells: int = 9) -> list[str]:
    """
    Splits the bounding box into a grid and generates search queries
    using coordinate-based areas.
    
    OPTIMIZED: Uses fewer, larger cells to minimize overlap.
    num_cells default: 9 (3x3 grid)

    Returns zone name strings like "area near 12.97,77.59"
    """
    south, north, west, east = bbox

    # Calculate grid dimensions - use square grid for better coverage
    cols = int(math.sqrt(num_cells))
    rows = int(math.ceil(num_cells / cols))

    lat_step = (north - south) / rows
    lon_step = (east - west) / cols

    zones = []
    for r in range(rows):
        for c in range(cols):
            center_lat = south + (r + 0.5) * lat_step
            center_lon = west + (c + 0.5) * lon_step
            zone_name = f"area near {center_lat:.4f},{center_lon:.4f}"
            zones.append(zone_name)

    print(f"  [Grid] Generated {len(zones)} grid zones for '{location_name}' ({rows}x{cols} grid).")
    return zones


# ---------------------------------------------------------------------------
# Step 4: Smart query formatting based on zone type
# ---------------------------------------------------------------------------

def format_query(term: str, zone: str, parent_city: str, strategy: str) -> str:
    """
    Build the optimal Google Maps search query for a zone.

    Named zone:  "plumbers in Koramangala, Bengaluru"
    Grid zone:   "plumbers near Bengaluru @12.97,77.59,14z"
    """
    if strategy == "grid":
        # Extract coordinates from zone name
        coord_match = re.search(r'([\d.-]+),([\d.-]+)', zone)
        if coord_match:
            lat, lon = coord_match.group(1), coord_match.group(2)
            return f"{term} near {parent_city} @{lat},{lon},14z"
        return f"{term} in {parent_city}"
    else:
        # Named zone (OSM or cities)
        return f"{term} in {zone}, {parent_city}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_zones(location: str, max_results: int) -> dict:
    """
    Dynamically discovers sub-areas of the given location.
    Returns:
      - zones: list of zone strings to search
      - parent_city: the parent city name
      - strategy: 'osm' | 'cities' | 'grid' | 'single'
      - results_per_zone: target results per zone
    """
    RESULTS_PER_ZONE = 60  # With adaptive scrolling, we can get 60+ per zone

    # Check cache first
    cache_key = location.strip().lower()
    if cache_key in _zone_cache:
        cached = _zone_cache[cache_key]
        zones_needed = max(
            min(math.ceil((max_results * 2.0) / RESULTS_PER_ZONE), len(cached["all_zones"])),
            min(25, len(cached["all_zones"]))  # 25 zones minimum for better coverage
        )
        selected = _select_zones(cached["all_zones"], zones_needed)
        return {
            "zones": selected,
            "parent_city": cached["parent_city"],
            "strategy": cached["strategy"],
            "results_per_zone": RESULTS_PER_ZONE,
        }

    # Step 1: Geocode
    print(f"\n [Zone Manager] Discovering zones for '{location}'...")
    geo = _geocode_location(location)
    if not geo:
        print(f"  [Zone Manager] Could not geocode '{location}'. Using single zone.")
        return {
            "zones": [location],
            "parent_city": location,
            "strategy": "single",
            "results_per_zone": RESULTS_PER_ZONE,
        }

    # Respect Nominatim rate limit (1 req/sec)
    time.sleep(1.1)

    # Extract clean city name from display_name
    parent_city = _extract_city_name(geo["display_name"], location)

    all_zones = []
    strategy = "osm"

    # Step 2: Try Overpass for neighborhoods
    if geo["bbox"]:
        all_zones = _fetch_osm_neighborhoods(geo["bbox"], location)
        time.sleep(1)  # Rate limit
        
        # If TOO MANY neighborhoods (>1000), use cities instead for better quality
        # Large areas like states have thousands of tiny neighborhoods - cities work better
        if len(all_zones) > 1000:
            cities = _fetch_major_cities_in_bbox(geo["bbox"], location)
            time.sleep(1)
            if cities and len(cities) >= 10:
                all_zones = cities
                strategy = "cities"
                print(f"  [Cities] Using {len(cities)} cities instead (better quality for large areas)")
        
        # If very few neighborhoods found, try cities instead
        elif len(all_zones) < 5:
            cities = _fetch_major_cities_in_bbox(geo["bbox"], location)
            time.sleep(1)
            if cities and len(cities) >= 3:
                all_zones = cities
                strategy = "cities"
                print(f"  [Cities] Using {len(cities)} cities for '{location}' (better than grid)")

    # Step 3: If OSM returned too few, use grid as fallback
    if len(all_zones) < 3:
        if geo["bbox"]:
            grid_zones = _generate_grid_zones(geo["bbox"], location, num_cells=9)
            if not all_zones:
                all_zones = grid_zones
                strategy = "grid"
            else:
                all_zones = all_zones + grid_zones
                strategy = "osm"

    # Cache the results
    _zone_cache[cache_key] = {
        "all_zones": all_zones,
        "parent_city": parent_city,
        "strategy": strategy,
    }

    zones_needed = max(
        min(math.ceil((max_results * 2.0) / RESULTS_PER_ZONE), len(all_zones)),
        min(25, len(all_zones))  # 25 zones minimum for better coverage
    )
    selected = _select_zones(all_zones, zones_needed)

    print(f"  [Zone Manager] Selected {len(selected)} of {len(all_zones)} zones. Strategy: {strategy}.")

    return {
        "zones": selected,
        "parent_city": parent_city,
        "strategy": strategy,
        "results_per_zone": RESULTS_PER_ZONE,
    }


def _select_zones(all_zones: list, zones_needed: int) -> list:
    """Evenly distribute zone selection across the full list."""
    if zones_needed >= len(all_zones):
        return all_zones[:]
    step = len(all_zones) / zones_needed
    return [all_zones[int(i * step)] for i in range(zones_needed)]


def _extract_city_name(display_name: str, original_input: str) -> str:
    """Extract a clean city name from Nominatim's display_name."""
    # display_name looks like "Bengaluru, Bangalore Urban, Karnataka, India"
    # We want the first part
    parts = [p.strip() for p in display_name.split(",")]
    if parts:
        city = parts[0]
        # If the first part is too generic, use original
        if city.lower() in ("india", "united states", "united kingdom"):
            return original_input.strip()
        return city
    return original_input.strip()


# ---------------------------------------------------------------------------
# Utility: Check if a location is a "large area" vs a specific locality
# ---------------------------------------------------------------------------

def is_large_area(location: str) -> bool:
    """
    Heuristic: determines if a location is a broad area (city/region)
    that benefits from zone splitting, vs a specific locality.

    Rules:
    - If location has NO comma → likely a city name → large area
    - If location has a comma → likely "locality, City" → specific area
    - Exception: "City, Country" patterns are still large

    Examples:
      "Bengaluru"              → True  (city)
      "Jaipur, India"          → True  (city + country)
      "Koramangala, Bengaluru" → False (locality)
      "MG Road, Pune"          → False (locality)
      "New York"               → True  (city)
      "Manhattan, New York"    → False (locality)
    """
    loc = location.strip()

    # No comma → likely just a city name
    if ',' not in loc:
        return True

    # Has comma — check if second part is a country (still a large area)
    parts = [p.strip().lower() for p in loc.split(',')]
    country_words = {
        "india", "in", "usa", "us", "united states", "uk", "united kingdom",
        "canada", "australia", "germany", "france", "uae", "singapore",
        "japan", "china", "brazil", "south africa", "indonesia", "malaysia",
        "thailand", "philippines", "vietnam", "nepal", "sri lanka", "bangladesh",
    }

    if len(parts) == 2 and parts[1] in country_words:
        return True

    # Two or more parts where first part looks like a locality → NOT large
    return False
