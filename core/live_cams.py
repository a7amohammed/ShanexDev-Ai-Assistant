"""
Catalogue of public live webcams for the "CAMERAS" layer of the globe.

Each camera is a dict with:
    id       unique, stable identifier (slug)
    name     short name shown on the card and in the window header
    place    city / location
    country  country
    lat/lon  marker position on the globe and on the map
    page     URL of the ORIGINAL broadcast page (the one opened by the
             "ORIGINAL SITE ↗" button; it is also what the window loads if there is no `youtube`)
    source   who broadcasts (EarthCam, SkylineWebcams, explore.org, …)
    desc     one or two sentences about what is shown
    stream   (optional) direct stream address (.m3u8). If present, the window opens it as is
             instead of looking for it in `page`. Useful for cameras on other sites whose link you know.
    youtube  (optional) 11-character video id of a YouTube live stream, or
             "channel:UCxxxxxxxxxxxxxxxxxxxxxx" for "the current live stream of this channel".
             If present, the window extracts its HLS stream (yt-dlp, or the YouTube page
             itself) and plays it directly in the window, without a browser. If not, the
             stream is located in `page` (see core/cam_stream.py: .m3u8, EarthCam, embedded
             YouTube, iframes, or the city listing if the path no longer exists).

Webcams change URL over time (websites rebuild their paths, YouTube live
streams restart with another id). That is why this file does NOT need to be edited to
fix it: create `config/live_cams.json` with a list of cameras (same fields).
An entry with the same `id` as one here replaces it; with `"disabled": true`
it removes it; with a new `id` it adds it. To check at a glance which links
are still alive: `python tools/check_cams.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

_USER_FILE = Path(__file__).resolve().parent.parent / "config" / "live_cams.json"

# NOTE: lat/lon are those of the place the camera points at. The `page` URLs are the
# usual public pages of each broadcaster; verify with tools/check_cams.py.
BUILTIN_CAMS: list[dict] = [
    {
        "id": "nyc-times-square", "name": "Times Square", "place": "New York",
        "country": "United States", "lat": 40.7580, "lon": -73.9855,
        "page": "https://www.skylinewebcams.com/en/webcam/united-states/new-york/new-york/times-square.html",
        "source": "EarthCam",
        "desc": "Manhattan's most famous intersection, with its screens and the constant flow of pedestrians and taxis.",
    },
    {
        "id": "london-abbey-road", "name": "Abbey Road", "place": "London",
        "country": "United Kingdom", "lat": 51.5320, "lon": -0.1773,
        "page": "https://www.earthcam.com/world/england/london/abbeyroad/?cam=abbeyroad_uk",
        "source": "EarthCam",
        "desc": "The Beatles' zebra crossing; there is always someone trying to recreate the album cover photo.",
    },
    {
        "id": "niagara-falls", "name": "Niagara Falls", "place": "Niagara Falls",
        "country": "Canada / USA", "lat": 43.0828, "lon": -79.0742,
        "page": "https://www.earthcam.com/canada/niagarafalls/",
        "source": "EarthCam",
        "desc": "View of the Horseshoe and American Falls from the Canadian side.",
    },
    {
        "id": "venice-grand-canal", "name": "Grand Canal", "place": "Venice",
        "country": "Italy", "lat": 45.4408, "lon": 12.3155,
        "page": "https://www.skylinewebcams.com/en/webcam/italia/veneto/venezia/canal-grande.html",
        "source": "SkylineWebcams",
        "desc": "Gondolas, vaporettos and palaces along Venice's main canal.",
    },
    {
        "id": "rome-trevi", "name": "Fontana di Trevi", "place": "Rome",
        "country": "Italy", "lat": 41.9009, "lon": 12.4833,
        "page": "https://www.skylinewebcams.com/en/webcam/italia/lazio/roma/fontana-di-trevi.html",
        "source": "SkylineWebcams",
        "desc": "The world's most famous baroque fountain, at any hour of the day.",
    },
    {
        "id": "paris-eiffel", "name": "Eiffel Tower", "place": "Paris",
        "country": "France", "lat": 48.8584, "lon": 2.2945,
        "page": "https://www.skylinewebcams.com/en/webcam/france/ile-de-france/paris/tour-eiffel.html",
        "source": "SkylineWebcams",
        "desc": "The Eiffel Tower and the Champ de Mars; at night, with the lights sparkling every hour.",
    },
    {
        "id": "santorini-oia", "name": "Oia", "place": "Santorini",
        "country": "Greece", "lat": 36.4618, "lon": 25.3753,
        "page": "https://www.skylinewebcams.com/en/webcam/ellada/south-aegean/santorini.html",
        "source": "SkylineWebcams",
        "desc": "The white houses above the caldera and the most photographed sunsets of the Aegean.",
    },
    {
        "id": "tokyo-shibuya", "name": "Shibuya Crossing", "place": "Tokyo",
        "country": "Japan", "lat": 35.6595, "lon": 139.7005,
        "page": "https://www.skylinewebcams.com/en/webcam/nihon/kanto/tokyo.html",
        "source": "SkylineWebcams",
        "desc": "The busiest pedestrian crossing in the world, seen from above.",
    },
    {
        "id": "dubai-marina", "name": "Dubai Marina", "place": "Dubai",
        "country": "United Arab Emirates", "lat": 25.0805, "lon": 55.1403,
        "page": "https://www.skylinewebcams.com/en/webcam/uae/dubai.html",
        "source": "SkylineWebcams",
        "desc": "Skyscrapers and artificial canal of one of the tallest districts on the planet.",
    },
    {
        "id": "rio-copacabana", "name": "Copacabana", "place": "Rio de Janeiro",
        "country": "Brazil", "lat": -22.9711, "lon": -43.1822,
        "page": "https://www.skylinewebcams.com/en/webcam/brasil/rio-de-janeiro/rio-de-janeiro.html",
        "source": "SkylineWebcams",
        "desc": "Brazil's most famous beach with Sugarloaf Mountain in the background.",
    },
    {
        "id": "sydney-harbour", "name": "Sydney Harbour", "place": "Sydney",
        "country": "Australia", "lat": -33.8568, "lon": 151.2153,
        "page": "https://www.skylinewebcams.com/en/webcam/australia/new-south-wales/sydney.html",
        "source": "SkylineWebcams",
        "desc": "The Opera House and the bridge over Sydney Harbour.",
    },
    {
        "id": "katmai-brooks-falls", "name": "Brooks Falls Bears", "place": "Katmai National Park, Alaska",
        "country": "United States", "lat": 58.5547, "lon": -155.7826,
        "page": "https://explore.org/livecams/brown-bears/brown-bear-salmon-cam-brooks-falls",
        "source": "explore.org",
        "desc": "Brown bears fishing for salmon at the falls (peak season is July–September).",
    },
    {
        "id": "iss-earth", "name": "Earth from the ISS", "place": "Low Earth orbit",
        "country": "Space", "lat": 0.0, "lon": 0.0,
        "page": "https://www.nasa.gov/live/",
        "youtube": "channel:UCLA_DiR1FfKNvjuUpBHmylQ",   # current live stream of the NASA channel
        "source": "NASA",
        "desc": "Broadcast from the International Space Station above the Earth (there are pauses when it loses signal).",
    },
    {
        "id": 'new-orleans-bourbon', "name": 'Bourbon Street', "place": 'New Orleans',
        "country": 'United States', "lat": 29.9584, "lon": -90.0652,
        "page": 'https://www.earthcam.com/usa/louisiana/neworleans/bourbonstreet/?cam=bourbonstreet',
        "source": 'EarthCam',
        "desc": 'The liveliest street of the French Quarter, with music and crowds at all hours.',
    },
    {
        "id": 'chicago-skyline', "name": 'Chicago Skyline', "place": 'Chicago',
        "country": 'United States', "lat": 41.8827, "lon": -87.6233,
        "page": 'https://www.earthcam.com/usa/illinois/chicago/',
        "source": 'EarthCam',
        "desc": 'Skyscrapers and Millennium Park on the shore of Lake Michigan.',
    },
    {
        "id": 'miami-beach', "name": 'Miami Beach', "place": 'Miami',
        "country": 'United States', "lat": 25.7907, "lon": -80.13,
        "page": 'https://www.earthcam.com/usa/florida/miamibeach/',
        "source": 'EarthCam',
        "desc": 'Beach and boardwalk of South Beach.',
    },
    {
        "id": 'las-vegas-strip', "name": 'The Strip', "place": 'Las Vegas',
        "country": 'United States', "lat": 36.1147, "lon": -115.1728,
        "page": 'https://www.earthcam.com/usa/nevada/lasvegas/',
        "source": 'EarthCam',
        "desc": 'The casino boulevard, much better at night.',
    },
    {
        "id": 'san-francisco', "name": 'San Francisco Bay', "place": 'San Francisco',
        "country": 'United States', "lat": 37.8199, "lon": -122.4783,
        "page": 'https://www.skylinewebcams.com/en/webcam/united-states.html',
        "source": 'SkylineWebcams',
        "desc": 'The Golden Gate and the bay, with the fog rolling in during the afternoon.',
    },
    {
        "id": 'honolulu-waikiki', "name": 'Waikiki', "place": 'Honolulu, Hawaii',
        "country": 'United States', "lat": 21.2766, "lon": -157.827,
        "page": 'https://www.skylinewebcams.com/en/webcam/united-states.html',
        "source": 'SkylineWebcams',
        "desc": 'Waikiki beach with Diamond Head in the background.',
    },
    {
        "id": 'mexico-cancun', "name": 'Cancun', "place": 'Cancun',
        "country": 'Mexico', "lat": 21.1619, "lon": -86.8515,
        "page": 'https://www.skylinewebcams.com/en/webcam/mexico.html',
        "source": 'SkylineWebcams',
        "desc": 'The hotel zone and the Caribbean Sea.',
    },
    {
        "id": 'rio-sugarloaf', "name": 'Sugarloaf Mountain', "place": 'Rio de Janeiro',
        "country": 'Brazil', "lat": -22.9486, "lon": -43.1546,
        "page": 'https://www.skylinewebcams.com/en/webcam/brasil.html',
        "source": 'SkylineWebcams',
        "desc": 'Guanabara Bay and Sugarloaf Mountain.',
    },
    {
        "id": 'buenos-aires', "name": 'Buenos Aires', "place": 'Buenos Aires',
        "country": 'Argentina', "lat": -34.6037, "lon": -58.3816,
        "page": 'https://www.skylinewebcams.com/en/webcam/argentina.html',
        "source": 'SkylineWebcams',
        "desc": 'View of the Argentine capital.',
    },
    {
        "id": 'lisbon', "name": 'Lisbon', "place": 'Lisbon',
        "country": 'Portugal', "lat": 38.7223, "lon": -9.1393,
        "page": 'https://www.skylinewebcams.com/en/webcam/portugal.html',
        "source": 'SkylineWebcams',
        "desc": 'Rooftops, the Tagus and the trams of the city of seven hills.',
    },
    {
        "id": 'amsterdam', "name": 'Amsterdam', "place": 'Amsterdam',
        "country": 'Netherlands', "lat": 52.3676, "lon": 4.9041,
        "page": 'https://www.skylinewebcams.com/en/webcam/nederland.html',
        "source": 'SkylineWebcams',
        "desc": 'Canals and narrow houses of the centre.',
    },
    {
        "id": 'berlin', "name": 'Berlin', "place": 'Berlin',
        "country": 'Germany', "lat": 52.5163, "lon": 13.3777,
        "page": 'https://www.skylinewebcams.com/en/webcam/deutschland.html',
        "source": 'SkylineWebcams',
        "desc": 'The Brandenburg Gate and the centre of the capital.',
    },
    {
        "id": 'prague', "name": 'Prague', "place": 'Prague',
        "country": 'Czechia', "lat": 50.0875, "lon": 14.4214,
        "page": 'https://www.skylinewebcams.com/en/webcam/cesko.html',
        "source": 'SkylineWebcams',
        "desc": 'The Old Town Square and its astronomical clock.',
    },
    {
        "id": 'vienna', "name": 'Vienna', "place": 'Vienna',
        "country": 'Austria', "lat": 48.2082, "lon": 16.3738,
        "page": 'https://www.skylinewebcams.com/en/webcam/osterreich.html',
        "source": 'SkylineWebcams',
        "desc": "The imperial centre of Vienna and St. Stephen's Cathedral.",
    },
    {
        "id": 'athens-acropolis', "name": 'Acropolis', "place": 'Athens',
        "country": 'Greece', "lat": 37.9715, "lon": 23.7257,
        "page": 'https://www.skylinewebcams.com/en/webcam/ellada.html',
        "source": 'SkylineWebcams',
        "desc": 'The Parthenon above the city.',
    },
    {
        "id": 'istanbul-bosphorus', "name": 'Bosphorus', "place": 'Istanbul',
        "country": 'Turkey', "lat": 41.0082, "lon": 28.9784,
        "page": 'https://www.skylinewebcams.com/en/webcam/turkiye.html',
        "source": 'SkylineWebcams',
        "desc": 'Ships between Europe and Asia.',
    },
    {
        "id": 'dubrovnik', "name": 'Dubrovnik', "place": 'Dubrovnik',
        "country": 'Croatia', "lat": 42.6507, "lon": 18.0944,
        "page": 'https://www.skylinewebcams.com/en/webcam/hrvatska.html',
        "source": 'SkylineWebcams',
        "desc": 'City walls and red rooftops above the Adriatic.',
    },
    {
        "id": 'zermatt-matterhorn', "name": 'Matterhorn', "place": 'Zermatt',
        "country": 'Switzerland', "lat": 46.0207, "lon": 7.7491,
        "page": 'https://www.skylinewebcams.com/en/webcam/schweiz.html',
        "source": 'SkylineWebcams',
        "desc": 'The most iconic mountain of the Alps.',
    },
    {
        "id": 'reykjavik', "name": 'Reykjavik', "place": 'Reykjavik',
        "country": 'Iceland', "lat": 64.1466, "lon": -21.9426,
        "page": 'https://www.skylinewebcams.com/en/webcam/island.html',
        "source": 'SkylineWebcams',
        "desc": 'The northernmost capital; with luck and clear skies, auroras in winter.',
    },
    {
        "id": 'moscow-red-square', "name": 'Red Square', "place": 'Moscow',
        "country": 'Russia', "lat": 55.7539, "lon": 37.6208,
        "page": 'https://www.skylinewebcams.com/en/webcam/russia.html',
        "source": 'SkylineWebcams',
        "desc": "The Kremlin and St. Basil's Cathedral.",
    },
    {
        "id": 'cape-town', "name": 'Cape Town', "place": 'Cape Town',
        "country": 'South Africa', "lat": -33.9249, "lon": 18.4241,
        "page": 'https://www.skylinewebcams.com/en/webcam/south-africa.html',
        "source": 'SkylineWebcams',
        "desc": 'Table Mountain above the city and the bay.',
    },
    {
        "id": 'africa-wildlife', "name": 'African Wildlife', "place": 'African reserves',
        "country": 'South Africa / Kenya', "lat": -24.0, "lon": 31.5,
        "page": 'https://explore.org/livecams/african-wildlife',
        "source": 'explore.org',
        "desc": 'Waterhole cameras with elephants, lions and giraffes.',
    },
    {
        "id": 'bangkok', "name": 'Bangkok', "place": 'Bangkok',
        "country": 'Thailand', "lat": 13.7563, "lon": 100.5018,
        "page": 'https://www.skylinewebcams.com/en/webcam/thailand.html',
        "source": 'SkylineWebcams',
        "desc": 'The Chao Phraya River and the skyline.',
    },
    {
        "id": 'singapore-marina-bay', "name": 'Marina Bay', "place": 'Singapore',
        "country": 'Singapore', "lat": 1.2838, "lon": 103.8591,
        "page": 'https://www.skylinewebcams.com/en/webcam/singapore.html',
        "source": 'SkylineWebcams',
        "desc": 'Marina Bay Sands and the financial skyline.',
    },
    {
        "id": 'hong-kong', "name": 'Victoria Harbour', "place": 'Hong Kong',
        "country": 'China', "lat": 22.293, "lon": 114.1694,
        "page": 'https://www.skylinewebcams.com/en/webcam/hong-kong.html',
        "source": 'SkylineWebcams',
        "desc": 'The illuminated skyline above the harbour.',
    },
    {
        "id": 'seoul', "name": 'Seoul', "place": 'Seoul',
        "country": 'South Korea', "lat": 37.5665, "lon": 126.978,
        "page": 'https://www.skylinewebcams.com/en/webcam/south-korea.html',
        "source": 'SkylineWebcams',
        "desc": 'Centre of the South Korean capital.',
    },
    {
        "id": 'osaka', "name": 'Osaka', "place": 'Osaka',
        "country": 'Japan', "lat": 34.6687, "lon": 135.5013,
        "page": 'https://www.skylinewebcams.com/en/webcam/nihon.html',
        "source": 'SkylineWebcams',
        "desc": 'Dotonbori and downtown Osaka.',
    },
    {
        "id": 'kyoto', "name": 'Kyoto', "place": 'Kyoto',
        "country": 'Japan', "lat": 35.0116, "lon": 135.7681,
        "page": 'https://www.skylinewebcams.com/en/webcam/nihon.html',
        "source": 'SkylineWebcams',
        "desc": 'Temples and rooftops of the former capital.',
    },
    {
        "id": 'bali', "name": 'Bali', "place": 'Bali',
        "country": 'Indonesia', "lat": -8.4095, "lon": 115.1889,
        "page": 'https://www.skylinewebcams.com/en/webcam/indonesia.html',
        "source": 'SkylineWebcams',
        "desc": 'Beaches and temples of the island of the gods.',
    },
    {
        "id": 'mumbai', "name": 'Mumbai', "place": 'Mumbai',
        "country": 'India', "lat": 18.922, "lon": 72.8347,
        "page": 'https://www.skylinewebcams.com/en/webcam/india.html',
        "source": 'SkylineWebcams',
        "desc": 'The Gateway of India and the bay.',
    },
    {
        "id": 'maldives', "name": 'Maldives', "place": 'Maldives',
        "country": 'Maldives', "lat": 3.2028, "lon": 73.2207,
        "page": 'https://www.skylinewebcams.com/en/webcam/maldives.html',
        "source": 'SkylineWebcams',
        "desc": 'Atolls and turquoise waters of the Indian Ocean.',
    },
    {
        "id": 'auckland', "name": 'Auckland', "place": 'Auckland',
        "country": 'New Zealand', "lat": -36.8485, "lon": 174.7633,
        "page": 'https://www.skylinewebcams.com/en/webcam/new-zealand.html',
        "source": 'SkylineWebcams',
        "desc": 'Sky Tower and Waitematā Harbour.',
    },
    {
        "id": 'tahiti', "name": 'Tahiti', "place": 'Papeete',
        "country": 'French Polynesia', "lat": -17.5516, "lon": -149.5585,
        "page": 'https://www.skylinewebcams.com/en/webcam/french-polynesia.html',
        "source": 'SkylineWebcams',
        "desc": 'Lagoon and mountains of Tahiti.',
    },
]


# Cameras of Spain (list supplied by the user) — see core/live_cams_es.py
try:
    from core.live_cams_es import SPAIN_CAMS
    BUILTIN_CAMS += SPAIN_CAMS
except Exception as _e:  # noqa: BLE001 — optional, must never bring down the HUD
    print(f"[Cams] Spain catalogue unavailable: {_e}")


# Cameras of the United States (most popular cities and places) — see core/live_cams_us.py
try:
    from core.live_cams_us import US_CAMS
    _have = {c["id"] for c in BUILTIN_CAMS}
    BUILTIN_CAMS += [c for c in US_CAMS if c["id"] not in _have]
except Exception as _e:  # noqa: BLE001 — optional, must never bring down the HUD
    print(f"[Cams] US catalogue unavailable: {_e}")


def load_cams() -> list[dict]:
    """Final catalogue: the built-in one + whatever is in config/live_cams.json."""
    by_id: dict[str, dict] = {c["id"]: dict(c) for c in BUILTIN_CAMS}
    try:
        if _USER_FILE.exists():
            extra = json.loads(_USER_FILE.read_text(encoding="utf-8"))
            if isinstance(extra, dict):
                extra = extra.get("cams", [])
            for c in extra or []:
                cid = str((c or {}).get("id") or "").strip()
                if not cid:
                    continue
                if c.get("disabled"):
                    by_id.pop(cid, None)
                    continue
                merged = {**by_id.get(cid, {}), **c}
                by_id[cid] = merged
    except Exception as e:  # noqa: BLE001 — a broken JSON must never bring down the HUD
        print(f"[Cams] No pude leer {_USER_FILE.name}: {e}")

    out: list[dict] = []
    for c in by_id.values():
        try:
            c["lat"], c["lon"] = float(c["lat"]), float(c["lon"])
            if not str(c.get("page", "")).startswith(("http://", "https://")):
                continue
            c.setdefault("name", c["id"])
            for k in ("place", "country", "source", "desc"):
                c.setdefault(k, "")
            out.append(c)
        except Exception:  # noqa: BLE001
            continue
    return out


def find_cam(cam_id: str) -> dict | None:
    for c in load_cams():
        if c["id"] == cam_id:
            return c
    return None
