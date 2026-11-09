"""Live cameras of the United States for the CAMERAS layer of the globe.

Same scheme as core/live_cams_es.py: each row is
    (id, name, place, state, lat, lon, broadcaster, page, description)
and is converted into a dict compatible with core/live_cams.py. It is loaded from there.

Pages (`page`): the public pages of each broadcaster are used (EarthCam by city,
SkylineWebcams by country/state). core/cam_stream.py locates the stream inside the page and,
if the path changed or it is a listing, picks the camera most similar to the name. To correct one
without touching this file: config/live_cams.json with the same `id` (see live_cams.py).
Check links:  python tools/check_cams.py

Coordinates are those of the place the camera points at (approx.). They are separated by a few metres between
cameras of the same city so that the markers do not completely cover each other on the enlarged map.
"""
from __future__ import annotations

_EC = "https://www.earthcam.com/usa/"
_SK = "https://www.skylinewebcams.com/en/webcam/united-states.html"
_EX = "https://explore.org/livecams/"

# (id, name, place, state, lat, lon, broadcaster, page, description)
_ROWS = [
    # ── New York ────────────────────────────────────────────────────────────
    ("nyc-times-square-2", "Times Square (intersection)", "New York", "New York", 40.7590, -73.9845, "EarthCam", _EC + "newyork/timessquare/", "The Broadway and 7th Avenue crossing with its screens, day and night."),
    ("nyc-brooklyn-bridge", "Brooklyn Bridge", "New York", "New York", 40.7061, -73.9969, "EarthCam", _EC + "newyork/brooklynbridge/", "The historic suspension bridge with the Lower Manhattan skyline."),
    ("nyc-statue-liberty", "Statue of Liberty", "New York", "New York", 40.6892, -74.0445, "EarthCam", _EC + "newyork/statueofliberty/", "Lady Liberty on her island in New York Harbor."),
    ("nyc-empire-state", "Empire State Building", "New York", "New York", 40.7484, -73.9857, "EarthCam", _EC + "newyork/", "Manhattan's most iconic skyscraper."),
    ("nyc-central-park", "Central Park", "New York", "New York", 40.7829, -73.9654, "EarthCam", _EC + "newyork/", "Manhattan's great park with the Midtown buildings in the background."),
    ("nyc-one-wtc", "One World Trade Center", "New York", "New York", 40.7127, -74.0134, "EarthCam", _EC + "newyork/", "The World Trade Center tower and ground zero."),
    ("nyc-hudson-skyline", "Hudson Skyline", "New York", "New York", 40.7370, -74.0100, "EarthCam", _EC + "newyork/", "Manhattan seen from the Hudson River."),
    ("nyc-coney-island", "Coney Island", "New York", "New York", 40.5749, -73.9857, "EarthCam", _EC + "newyork/", "Boardwalk, amusement park and beach in Brooklyn."),
    ("nyc-grand-central", "Grand Central", "New York", "New York", 40.7527, -73.9772, "EarthCam", _EC + "newyork/", "The legendary Midtown Manhattan station."),
    # ── Los Angeles and Southern California ──────────────────────────────────────
    ("la-hollywood-sign", "Hollywood Sign", "Los Angeles", "California", 34.1341, -118.3215, "EarthCam", _EC + "california/hollywood/", "The sign on Mount Lee, icon of Hollywood."),
    ("la-hollywood-blvd", "Hollywood Boulevard", "Los Angeles", "California", 34.1016, -118.3387, "EarthCam", _EC + "california/hollywood/", "The Walk of Fame and the TCL Chinese Theatre."),
    ("la-santa-monica-pier", "Santa Monica Pier", "Santa Monica", "California", 34.0092, -118.4973, "EarthCam", _EC + "california/santamonica/", "The pier with the Pacific Park Ferris wheel and the beach."),
    ("la-venice-beach", "Venice Beach", "Los Angeles", "California", 33.9850, -118.4695, "EarthCam", _EC + "california/venicebeach/", "The most eccentric boardwalk in Los Angeles."),
    ("la-downtown", "Downtown LA Skyline", "Los Angeles", "California", 34.0522, -118.2437, "EarthCam", _EC + "california/losangeles/", "The skyscrapers of downtown Los Angeles."),
    ("la-griffith", "Griffith Observatory", "Los Angeles", "California", 34.1184, -118.3004, "SkylineWebcams", _SK, "View of the city from the hills of Griffith Park."),
    ("la-huntington-beach", "Huntington Beach Pier", "Huntington Beach", "California", 33.6559, -118.0035, "SkylineWebcams", _SK, "Surf and sunset in 'Surf City USA'."),
    ("la-malibu", "Malibu", "Malibu", "California", 34.0259, -118.7798, "SkylineWebcams", _SK, "The Malibu coast on the Pacific."),
    ("la-long-beach", "Port of Long Beach", "Long Beach", "California", 33.7544, -118.1893, "EarthCam", _EC + "california/", "Harbour, cranes and the Queen Mary."),
    ("la-disneyland", "Anaheim", "Anaheim", "California", 33.8121, -117.9190, "EarthCam", _EC + "california/", "The Anaheim area, next to the theme parks."),
    ("sd-skyline", "San Diego Skyline", "San Diego", "California", 32.7157, -117.1611, "EarthCam", _EC + "california/sandiego/", "San Diego bay and skyscrapers."),
    ("sd-la-jolla", "La Jolla Cove", "San Diego", "California", 32.8509, -117.2713, "SkylineWebcams", _SK, "La Jolla cove with sea lions and divers."),
    ("sd-pacific-beach", "Pacific Beach", "San Diego", "California", 32.7946, -117.2551, "SkylineWebcams", _SK, "The pier and beach of Pacific Beach."),
    ("sb-santa-barbara", "Stearns Wharf", "Santa Barbara", "California", 34.4093, -119.6851, "SkylineWebcams", _SK, "The historic pier of the 'American Riviera'."),
    # ── San Francisco and Northern California ──────────────────────────────────
    ("sf-golden-gate-2", "Golden Gate (Marina)", "San Francisco", "California", 37.8080, -122.4750, "EarthCam", _EC + "california/sanfrancisco/", "The red bridge from the Marina shoreline."),
    ("sf-fishermans-wharf", "Fisherman's Wharf", "San Francisco", "California", 37.8080, -122.4177, "EarthCam", _EC + "california/sanfrancisco/", "The Pier 39 sea lions and the harbour promenade."),
    ("sf-alcatraz", "Alcatraz", "San Francisco", "California", 37.8267, -122.4230, "EarthCam", _EC + "california/sanfrancisco/", "The former prison on its island in the bay."),
    ("sf-bay-bridge", "Bay Bridge", "San Francisco", "California", 37.7983, -122.3778, "EarthCam", _EC + "california/sanfrancisco/", "The Bay Bridge and the financial skyline."),
    ("sf-union-square", "Union Square", "San Francisco", "California", 37.7879, -122.4075, "EarthCam", _EC + "california/sanfrancisco/", "The commercial heart of the city."),
    ("sf-skyline", "San Francisco Skyline", "San Francisco", "California", 37.7749, -122.4194, "SkylineWebcams", _SK, "Panorama of the city and the fog rolling in over the bay."),
    ("sj-san-jose", "Downtown San Jose", "San Jose", "California", 37.3352, -121.8811, "EarthCam", _EC + "california/", "Silicon Valley, from downtown."),
    ("sac-sacramento", "Sacramento", "Sacramento", "California", 38.5816, -121.4944, "EarthCam", _EC + "california/", "California's capital by the river."),
    ("tahoe-lake", "Lake Tahoe", "Lake Tahoe", "California", 39.0968, -120.0324, "SkylineWebcams", _SK, "The high-mountain lake of the Sierra Nevada."),
    ("yosemite-valley", "Yosemite Valley", "Yosemite", "California", 37.7459, -119.5936, "SkylineWebcams", _SK, "El Capitan and Half Dome in the national park."),
    ("monterey-bay", "Monterey Bay", "Monterey", "California", 36.6002, -121.8947, "SkylineWebcams", _SK, "The Monterey coast and Cannery Row."),
    # ── Las Vegas and the Southwest ─────────────────────────────────────────────────
    ("lv-strip-2", "Las Vegas Boulevard", "Las Vegas", "Nevada", 36.1147, -115.1740, "EarthCam", _EC + "nevada/lasvegas/", "The Strip with its street-level casinos."),
    ("lv-fremont", "Fremont Street", "Las Vegas", "Nevada", 36.1708, -115.1425, "EarthCam", _EC + "nevada/lasvegas/", "The covered street of old Las Vegas, with its giant screen."),
    ("lv-bellagio", "Bellagio Fountains", "Las Vegas", "Nevada", 36.1126, -115.1767, "EarthCam", _EC + "nevada/lasvegas/", "The water and music show in front of the Bellagio lake."),
    ("lv-stratosphere", "The STRAT", "Las Vegas", "Nevada", 36.1475, -115.1556, "EarthCam", _EC + "nevada/lasvegas/", "The tallest tower in Las Vegas and the north end of the Strip."),
    ("lv-skyline", "Las Vegas Skyline", "Las Vegas", "Nevada", 36.1699, -115.1398, "SkylineWebcams", _SK, "Casinos and desert from above."),
    ("az-phoenix", "Phoenix Skyline", "Phoenix", "Arizona", 33.4484, -112.0740, "EarthCam", _EC + "arizona/", "Downtown Phoenix and the Valley of the Sun."),
    ("az-grand-canyon", "Grand Canyon", "Grand Canyon", "Arizona", 36.0544, -112.1401, "SkylineWebcams", _SK, "The canyon's south rim at sunrise and sunset."),
    ("az-sedona", "Sedona", "Sedona", "Arizona", 34.8697, -111.7610, "SkylineWebcams", _SK, "The red rocks of Sedona."),
    ("nm-santa-fe", "Santa Fe", "Santa Fe", "New Mexico", 35.6870, -105.9378, "SkylineWebcams", _SK, "The historic plaza of Santa Fe."),
    ("ut-salt-lake", "Salt Lake City", "Salt Lake City", "Utah", 40.7608, -111.8910, "EarthCam", _EC + "utah/", "Downtown Salt Lake City with the Wasatch Mountains."),
    ("co-denver", "Denver Skyline", "Denver", "Colorado", 39.7392, -104.9903, "EarthCam", _EC + "colorado/", "Denver with the Rockies in the background."),
    # ── Florida ──────────────────────────────────────────────────────────────
    ("mia-ocean-drive", "Ocean Drive", "Miami", "Florida", 25.7814, -80.1300, "EarthCam", _EC + "florida/miamibeach/", "The art deco street of South Beach, with cars and terraces."),
    ("mia-south-beach", "South Beach", "Miami", "Florida", 25.7826, -80.1341, "EarthCam", _EC + "florida/miamibeach/", "The beach and the colourful lifeguard towers."),
    ("mia-brickell", "Brickell Skyline", "Miami", "Florida", 25.7617, -80.1918, "EarthCam", _EC + "florida/miami/", "Miami's financial district beside Biscayne Bay."),
    ("mia-port", "Port of Miami", "Miami", "Florida", 25.7781, -80.1651, "SkylineWebcams", _SK, "Cruise ships and container ships in the port."),
    ("fl-fort-lauderdale", "Fort Lauderdale Beach", "Fort Lauderdale", "Florida", 26.1224, -80.1060, "EarthCam", _EC + "florida/fortlauderdale/", "The beach and the promenade with its undulating wall."),
    ("fl-key-west-duval", "Duval Street", "Key West", "Florida", 24.5551, -81.8000, "EarthCam", _EC + "florida/keywest/", "Key West's main street, with bars and music."),
    ("fl-key-west-sunset", "Mallory Square", "Key West", "Florida", 24.5601, -81.8063, "EarthCam", _EC + "florida/keywest/", "The famous sunset over the Gulf."),
    ("fl-orlando", "Orlando (downtown)", "Orlando", "Florida", 28.5383, -81.3792, "EarthCam", _EC + "florida/orlando/", "Downtown of the theme park capital."),
    ("fl-daytona", "Daytona Beach", "Daytona Beach", "Florida", 29.2108, -81.0228, "SkylineWebcams", _SK, "The beach where you can drive, and its pier."),
    ("fl-clearwater", "Clearwater Beach", "Clearwater", "Florida", 27.9772, -82.8270, "SkylineWebcams", _SK, "White sand and sunsets on the Gulf of Mexico."),
    ("fl-tampa", "Tampa Skyline", "Tampa", "Florida", 27.9506, -82.4572, "EarthCam", _EC + "florida/", "Downtown Tampa and its bay."),
    # ── East Coast ───────────────────────────────────────────────────────────
    ("dc-capitol", "US Capitol", "Washington, D.C.", "District of Columbia", 38.8899, -77.0091, "EarthCam", _EC + "dc/washington/", "The Capitol dome and the National Mall."),
    ("dc-washington-monument", "Washington Monument", "Washington, D.C.", "District of Columbia", 38.8895, -77.0353, "EarthCam", _EC + "dc/washington/", "The obelisk and the esplanade to the Lincoln Memorial."),
    ("dc-white-house", "White House", "Washington, D.C.", "District of Columbia", 38.8977, -77.0365, "EarthCam", _EC + "dc/washington/", "The presidential residence and Lafayette Park."),
    ("bos-skyline", "Boston Skyline", "Boston", "Massachusetts", 42.3601, -71.0589, "EarthCam", _EC + "massachusetts/boston/", "Downtown Boston beside the Charles River."),
    ("bos-fenway", "Fenway Park", "Boston", "Massachusetts", 42.3467, -71.0972, "EarthCam", _EC + "massachusetts/boston/", "The oldest stadium in Major League Baseball."),
    ("phi-skyline", "Philadelphia Skyline", "Philadelphia", "Pennsylvania", 39.9526, -75.1652, "EarthCam", _EC + "pennsylvania/philadelphia/", "Downtown of the city of the Liberty Bell."),
    ("nj-atlantic-city", "Atlantic City", "Atlantic City", "New Jersey", 39.3643, -74.4229, "EarthCam", _EC + "newjersey/", "The boardwalk and casinos of the Jersey shore."),
    ("nj-seaside", "Seaside Heights", "Seaside Heights", "New Jersey", 39.9440, -74.0732, "EarthCam", _EC + "newjersey/", "Boardwalk and amusement park facing the Atlantic."),
    ("atl-skyline", "Atlanta Skyline", "Atlanta", "Georgia", 33.7490, -84.3880, "EarthCam", _EC + "georgia/atlanta/", "Midtown and Downtown Atlanta."),
    ("sav-savannah", "Savannah", "Savannah", "Georgia", 32.0809, -81.0912, "SkylineWebcams", _SK, "The Savannah River and the historic centre."),
    ("sc-charleston", "Charleston", "Charleston", "South Carolina", 32.7765, -79.9311, "SkylineWebcams", _SK, "The harbour and colonial quarter of Charleston."),
    ("sc-myrtle-beach", "Myrtle Beach", "Myrtle Beach", "South Carolina", 33.6891, -78.8867, "EarthCam", _EC + "southcarolina/", "The boardwalk and the pier."),
    ("nc-outer-banks", "Outer Banks", "Outer Banks", "North Carolina", 35.9582, -75.6201, "SkylineWebcams", _SK, "The barrier islands of the Carolina coast."),
    ("va-virginia-beach", "Virginia Beach", "Virginia Beach", "Virginia", 36.8529, -75.9780, "EarthCam", _EC + "virginia/", "The boardwalk and beach of Virginia Beach."),
    ("me-portland", "Portland (Maine)", "Portland", "Maine", 43.6591, -70.2568, "SkylineWebcams", _SK, "Harbour and lighthouses of Maine."),
    # ── South, Texas and Midwest ─────────────────────────────────────────────
    ("no-bourbon-2", "Bourbon Street (French Quarter)", "New Orleans", "Louisiana", 29.9584, -90.0660, "EarthCam", _EC + "louisiana/neworleans/", "The liveliest street of the French Quarter."),
    ("no-jackson-square", "Jackson Square", "New Orleans", "Louisiana", 29.9574, -90.0629, "EarthCam", _EC + "louisiana/neworleans/", "The square in front of St. Louis Cathedral."),
    ("nash-broadway", "Broadway (Nashville)", "Nashville", "Tennessee", 36.1599, -86.7785, "EarthCam", _EC + "tennessee/nashville/", "The honky-tonks and live music of Lower Broadway."),
    ("mem-beale", "Beale Street", "Memphis", "Tennessee", 35.1390, -90.0535, "EarthCam", _EC + "tennessee/", "Memphis's blues street."),
    ("tx-austin", "Austin Skyline", "Austin", "Texas", 30.2672, -97.7431, "EarthCam", _EC + "texas/austin/", "Austin and Lady Bird Lake."),
    ("tx-houston", "Houston Skyline", "Houston", "Texas", 29.7604, -95.3698, "EarthCam", _EC + "texas/houston/", "Downtown Houston."),
    ("tx-dallas", "Dallas Skyline", "Dallas", "Texas", 32.7767, -96.7970, "EarthCam", _EC + "texas/dallas/", "Reunion Tower and downtown Dallas."),
    ("tx-san-antonio", "River Walk", "San Antonio", "Texas", 29.4241, -98.4936, "EarthCam", _EC + "texas/", "The riverside promenade beside the Alamo."),
    ("tx-galveston", "Galveston", "Galveston", "Texas", 29.3013, -94.7977, "SkylineWebcams", _SK, "The seawall and beach on the Gulf of Mexico."),
    ("il-chicago-skyline-2", "Chicago (Navy Pier)", "Chicago", "Illinois", 41.8917, -87.6086, "EarthCam", _EC + "illinois/chicago/", "The pier, the Ferris wheel and the skyline from Lake Michigan."),
    ("il-chicago-bean", "Cloud Gate (The Bean)", "Chicago", "Illinois", 41.8827, -87.6233, "EarthCam", _EC + "illinois/chicago/", "The reflective sculpture in Millennium Park."),
    ("mn-minneapolis", "Minneapolis", "Minneapolis", "Minnesota", 44.9778, -93.2650, "EarthCam", _EC + "minnesota/", "Downtown of the Twin Cities."),
    ("mo-st-louis-arch", "Gateway Arch", "St. Louis", "Missouri", 38.6247, -90.1848, "EarthCam", _EC + "missouri/", "The steel arch beside the Mississippi."),
    ("mi-detroit", "Detroit Skyline", "Detroit", "Michigan", 42.3314, -83.0458, "EarthCam", _EC + "michigan/", "Downtown Detroit and the border with Canada."),
    ("oh-cleveland", "Cleveland", "Cleveland", "Ohio", 41.4993, -81.6944, "EarthCam", _EC + "ohio/", "Downtown and Lake Erie."),
    ("ny-niagara-us", "Niagara Falls (US side)", "Niagara Falls", "New York", 43.0828, -79.0650, "EarthCam", _EC + "newyork/niagarafalls/", "The falls seen from the American side."),
    # ── Northwest, Alaska, Hawaii and nature ─────────────────────────────────
    ("wa-seattle-needle", "Space Needle", "Seattle", "Washington", 47.6205, -122.3493, "EarthCam", _EC + "washington/seattle/", "Seattle's icon with Mount Rainier in the background."),
    ("wa-seattle-waterfront", "Seattle Waterfront", "Seattle", "Washington", 47.6062, -122.3410, "EarthCam", _EC + "washington/seattle/", "The harbour and the Elliott Bay ferries."),
    ("or-portland", "Portland (Oregon)", "Portland", "Oregon", 45.5152, -122.6784, "EarthCam", _EC + "oregon/", "Downtown Portland and Mount Hood."),
    ("hi-waikiki-2", "Waikiki Beach", "Honolulu", "Hawaii", 21.2793, -157.8294, "EarthCam", _EC + "hawaii/waikiki/", "Waikiki beach and Diamond Head."),
    ("hi-maui", "Lahaina / Maui", "Maui", "Hawaii", 20.8783, -156.6825, "SkylineWebcams", _SK, "Maui's west coast at sunset."),
    ("hi-kona", "Kailua-Kona", "Hawaii (Big Island)", "Hawaii", 19.6400, -155.9969, "SkylineWebcams", _SK, "Kona's seafront promenade."),
    ("ak-anchorage", "Anchorage", "Anchorage", "Alaska", 61.2181, -149.9003, "SkylineWebcams", _SK, "Alaska's largest city and the Chugach Mountains."),
    ("ak-katmai-lower", "Katmai Bears (river)", "Katmai National Park", "Alaska", 58.5560, -155.7790, "explore.org", _EX + "brown-bears/", "Bears fishing for salmon on the Brooks River."),
    ("wy-yellowstone-oldfaithful", "Old Faithful", "Yellowstone", "Wyoming", 44.4605, -110.8281, "SkylineWebcams", _SK, "The world's most famous geyser, erupting about every 90 minutes."),
    ("sd-mount-rushmore", "Mount Rushmore", "Keystone", "South Dakota", 43.8791, -103.4591, "SkylineWebcams", _SK, "The four presidents carved in granite."),
    ("mt-glacier", "Glacier National Park", "Montana", "Montana", 48.7596, -113.7870, "SkylineWebcams", _SK, "Lake McDonald and the mountains of Glacier NP."),
]

US_CAMS: list[dict] = [
    {
        "id": _id, "name": _n, "place": _pl, "country": "Estados Unidos",
        "lat": _la, "lon": _lo, "page": _page, "source": _src,
        "desc": f"{_d} ({_st})",
    }
    for _id, _n, _pl, _st, _la, _lo, _src, _page, _d in _ROWS
]
