"""Supplier simulator catalog — illustrative data.

Airports and landmarks are real places (so routes and ETAs are plausible). Airlines, hotels,
transfer and activity operators are fictional. Nothing here is a live price or inventory.
"""
from __future__ import annotations

from dataclasses import dataclass

ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)


@dataclass(frozen=True, slots=True)
class Airport:
    code: str
    city: str
    name: str
    lat: float
    lng: float
    country: str = "India"
    utc_offset_min: int = 330


@dataclass(frozen=True, slots=True)
class Hotel:
    id: str
    name: str
    area: str
    lat: float
    lng: float
    rating: float
    tier: str
    base_rate: int
    check_in: str = "14:00"
    check_out: str = "11:00"


@dataclass(frozen=True, slots=True)
class Activity:
    id: str
    name: str
    area: str
    lat: float
    lng: float
    tags: tuple[str, ...]
    duration_min: int
    slots: tuple[str, ...]
    price_pp: int
    full_day: bool = False
    strenuous: bool = False
    weekdays: tuple[int, ...] = ALL_DAYS


@dataclass(frozen=True, slots=True)
class TransferProfile:
    provider: str
    base: int
    per_km: int
    wait_min: int = 20
    reschedule_fee: int = 300
    free_reschedule_hours: int = 24


@dataclass(frozen=True, slots=True)
class Destination:
    code: str
    city: str
    region: str
    country: str
    tagline: str
    tags: tuple[str, ...]
    climate: tuple[tuple[int, int], ...]  # (avg high °C, rain mm) Jan..Dec
    demand: tuple[float, ...]  # seasonal demand multiplier Jan..Dec
    funding_index: float  # 0..1 business-travel pressure from regional funding inflow
    extras_pp_day: int
    transfer: TransferProfile
    hotels: tuple[Hotel, ...]
    activities: tuple[Activity, ...]
    advisory: str | None = None
    rest_on_arrival: bool = False
    international: bool = False
    local_fare_scale: float = 1.0


AIRPORTS: dict[str, Airport] = {a.code: a for a in (
    Airport("DEL", "Delhi", "Indira Gandhi International", 28.5562, 77.1000),
    Airport("BOM", "Mumbai", "Chhatrapati Shivaji Maharaj International", 19.0896, 72.8656),
    Airport("BLR", "Bengaluru", "Kempegowda International", 13.1986, 77.7066),
    Airport("MAA", "Chennai", "Chennai International", 12.9941, 80.1709),
    Airport("HYD", "Hyderabad", "Rajiv Gandhi International", 17.2403, 78.4294),
    Airport("CCU", "Kolkata", "Netaji Subhas Chandra Bose International", 22.6547, 88.4467),
    Airport("AMD", "Ahmedabad", "Sardar Vallabhbhai Patel International", 23.0772, 72.6347),
    Airport("PNQ", "Pune", "Pune International", 18.5821, 73.9197),
    Airport("COK", "Kochi", "Cochin International", 10.1520, 76.4019),
    Airport("GOI", "Goa", "Goa International (Dabolim)", 15.3808, 73.8314),
    Airport("JAI", "Jaipur", "Jaipur International", 26.8242, 75.8122),
    Airport("UDR", "Udaipur", "Maharana Pratap Airport", 24.6177, 73.8961),
    Airport("VNS", "Varanasi", "Lal Bahadur Shastri International", 25.4524, 82.8593),
    Airport("IXL", "Leh", "Kushok Bakula Rimpochee Airport", 34.1359, 77.5465),
    Airport("IXZ", "Port Blair", "Veer Savarkar International", 11.6412, 92.7297),
    Airport("DXB", "Dubai", "Dubai International", 25.2532, 55.3657, "UAE", 240),
    Airport("SIN", "Singapore", "Changi", 1.3644, 103.9915, "Singapore", 480),
    Airport("BKK", "Bangkok", "Suvarnabhumi", 13.6900, 100.7501, "Thailand", 420),
)}

METROS = ("DEL", "BOM", "BLR", "MAA", "HYD", "CCU")
SECONDARY = ("AMD", "PNQ")
DEST_DIRECT: dict[str, set[str]] = {
    "COK": {"DEL", "BOM", "BLR", "MAA", "HYD", "CCU", "PNQ", "AMD", "DXB", "SIN", "BKK"},
    "GOI": {"DEL", "BOM", "BLR", "MAA", "HYD", "CCU", "PNQ", "AMD", "DXB"},
    "JAI": {"DEL", "BOM", "BLR", "HYD", "CCU", "AMD", "PNQ", "MAA", "DXB", "BKK"},
    "UDR": {"DEL", "BOM", "BLR", "HYD"},
    "VNS": {"DEL", "BOM", "BLR", "HYD", "CCU", "MAA"},
    "IXL": {"DEL", "BOM", "BLR"},
    "IXZ": {"MAA", "CCU", "DEL", "BLR", "HYD"},
    "DXB": {"DEL", "BOM", "BLR", "MAA", "HYD", "CCU", "AMD", "PNQ", "COK", "GOI", "JAI"},
    "SIN": {"DEL", "BOM", "BLR", "MAA", "HYD", "CCU", "COK"},
    "BKK": {"DEL", "BOM", "BLR", "MAA", "HYD", "CCU", "COK", "JAI"},
}

AIRLINES = (("SF", "Saffron Air", 1.00), ("MN", "Monsoon Air", 0.95), ("TW", "Tealwing", 1.06),
            ("KX", "Kestrel Express", 0.92), ("LC", "Lotus Connect", 1.03))


def is_direct(a: str, b: str) -> bool:
    if a == b:
        return False
    hubs = set(METROS) | set(SECONDARY)
    if a in hubs and b in hubs and (a in METROS or b in METROS):
        return True
    return a in DEST_DIRECT.get(b, set()) or b in DEST_DIRECT.get(a, set())


T = TransferProfile
H = Hotel
A = Activity

DESTINATIONS: dict[str, Destination] = {d.code: d for d in (
    Destination(
        "COK", "Kochi", "Kerala", "India", "Spice-route port town, backwaters a drive away",
        ("heritage", "culture", "food", "nature"),
        ((31, 10), (32, 20), (33, 40), (33, 120), (32, 290), (29, 700), (29, 600), (29, 380), (30, 300), (30, 320), (31, 170), (31, 40)),
        (1.10, 1.00, 0.95, 0.92, 0.88, 0.82, 0.85, 0.96, 0.93, 1.00, 1.06, 1.20), 0.55, 120,
        T("Backwater Cabs", 450, 22),
        (H("cok-harbour", "Harbour Lane Residency", "Fort Kochi", 9.9658, 76.2430, 4.3, "mid", 2700, "13:00"),
         H("cok-spice", "Spice Coast Heritage Villa", "Fort Kochi", 9.9632, 76.2456, 4.6, "premium", 6200),
         H("cok-bayview", "Marine Drive Bayview", "Ernakulam", 9.9785, 76.2770, 4.1, "mid", 2900, "13:00"),
         H("cok-courtyard", "Mattancherry Courtyard Stay", "Mattancherry", 9.9570, 76.2590, 4.0, "budget", 1900, "12:00")),
        (A("cok-heritage-walk", "Fort Kochi heritage walk", "Fort Kochi", 9.9663, 76.2423, ("heritage", "culture"), 120, ("07:30", "16:00"), 450),
         A("cok-kathakali", "Kathakali evening", "Fort Kochi", 9.9645, 76.2431, ("culture",), 120, ("17:00",), 500),
         A("cok-backwaters", "Alleppey backwater cruise", "Alappuzha", 9.4981, 76.3388, ("nature",), 420, ("09:00",), 950, full_day=True),
         A("cok-market", "Ernakulam spice market walk", "Broadway", 9.9816, 76.2780, ("food", "shopping"), 90, ("09:00", "10:30"), 300),
         A("cok-cooking", "Kerala home cooking class", "Fort Kochi", 9.9610, 76.2440, ("food",), 150, ("10:30", "15:30"), 1200),
         A("cok-mattancherry", "Mattancherry Palace & Jew Town", "Mattancherry", 9.9579, 76.2593, ("heritage", "shopping"), 120, ("10:00", "14:00"), 100),
         A("cok-kayak", "Sunrise kayaking, Kadamakudy", "Kadamakudy", 10.0620, 76.2830, ("nature", "adventure"), 180, ("06:30",), 1500, strenuous=True),
         A("cok-cherai", "Cherai beach & fishing nets cycle", "Cherai", 10.1416, 76.1783, ("beaches", "nature"), 180, ("14:30",), 650)),
    ),
    Destination(
        "GOI", "Goa", "Goa", "India", "Beaches, Latin quarters and Konkan food",
        ("beaches", "nightlife", "food", "heritage"),
        ((32, 1), (32, 0), (32, 1), (33, 10), (33, 90), (31, 870), (29, 1000), (29, 600), (30, 280), (32, 120), (33, 30), (33, 5)),
        (1.12, 1.02, 0.95, 0.90, 0.85, 0.75, 0.78, 0.82, 0.85, 1.00, 1.10, 1.30), 0.25, 150,
        T("Konkan Rides", 600, 24),
        (H("goi-palms", "Candolim Palms Retreat", "Candolim", 15.5150, 73.7660, 4.2, "mid", 3800),
         H("goi-casa", "Fontainhas Casa", "Panjim", 15.4980, 73.8330, 4.4, "budget", 2400, "13:00"),
         H("goi-cove", "Sinquerim Cove Resort", "Sinquerim", 15.4990, 73.7700, 4.6, "premium", 8500),
         H("goi-huts", "Palolem Shoreline Huts", "Palolem", 15.0090, 74.0230, 4.0, "budget", 2100, "12:00")),
        (A("goi-oldgoa", "Old Goa churches tour", "Old Goa", 15.5009, 73.9116, ("heritage", "spiritual"), 150, ("09:30", "14:30"), 600),
         A("goi-fontainhas", "Fontainhas Latin Quarter walk", "Panjim", 15.4986, 73.8326, ("heritage", "culture", "food"), 120, ("08:00", "16:30"), 500),
         A("goi-spice", "Spice plantation & Goan lunch", "Ponda", 15.4010, 74.0200, ("food", "nature"), 240, ("10:30",), 1100),
         A("goi-dudhsagar", "Dudhsagar falls jeep safari", "Mollem", 15.3144, 74.3143, ("nature", "adventure"), 480, ("08:00",), 2400, full_day=True, strenuous=True),
         A("goi-cruise", "Mandovi sunset cruise", "Panjim", 15.5017, 73.8331, ("nightlife", "nature"), 90, ("17:45",), 550),
         A("goi-kayak", "Mangrove kayaking, Chorao", "Chorao", 15.5280, 73.8600, ("nature", "adventure"), 150, ("07:00",), 1300, strenuous=True),
         A("goi-shacks", "Beach shack seafood trail", "Candolim", 15.5180, 73.7627, ("food", "nightlife", "beaches"), 150, ("19:00",), 900)),
    ),
    Destination(
        "JAI", "Jaipur", "Rajasthan", "India", "Forts, bazaars and block-printed everything",
        ("heritage", "culture", "shopping", "food"),
        ((22, 8), (26, 8), (32, 9), (37, 5), (41, 15), (40, 60), (35, 190), (33, 170), (34, 80), (33, 18), (29, 3), (24, 3)),
        (1.15, 1.08, 1.00, 0.85, 0.75, 0.75, 0.82, 0.85, 0.92, 1.08, 1.15, 1.25), 0.35, 120,
        T("Pink City Cabs", 400, 18),
        (H("jai-haveli", "Pink City Haveli", "Old City", 26.9230, 75.8240, 4.4, "mid", 3600, "13:00"),
         H("jai-amber", "Amber Courtyard Palace", "Amer Road", 26.9800, 75.8450, 4.7, "premium", 9200),
         H("jai-cscheme", "C-Scheme Comfort Inn", "C-Scheme", 26.9050, 75.8000, 4.0, "budget", 1800, "12:00")),
        (A("jai-amber", "Amber Fort guided visit", "Amer", 26.9855, 75.8513, ("heritage",), 180, ("08:30", "13:30"), 900),
         A("jai-citypalace", "City Palace & Jantar Mantar", "Old City", 26.9258, 75.8237, ("heritage", "culture"), 180, ("10:00", "14:00"), 1000),
         A("jai-hawamahal", "Hawa Mahal sunrise & chai", "Old City", 26.9239, 75.8267, ("heritage",), 90, ("06:45",), 400),
         A("jai-blockprint", "Block printing workshop", "Sanganer", 26.8170, 75.7880, ("culture", "shopping"), 150, ("10:30", "15:00"), 1100),
         A("jai-food", "Johari Bazaar food walk", "Johari Bazaar", 26.9196, 75.8267, ("food", "shopping"), 150, ("18:00",), 1200),
         A("jai-nahargarh", "Nahargarh fort sunset", "Nahargarh", 26.9373, 75.8155, ("heritage", "nature"), 120, ("16:30",), 500),
         A("jai-village", "Rajasthani village dinner & folk show", "Tonk Road", 26.7680, 75.8370, ("culture", "food"), 180, ("19:00",), 1300)),
    ),
    Destination(
        "UDR", "Udaipur", "Rajasthan", "India", "Lake palaces and slow sunsets",
        ("heritage", "culture", "nature"),
        ((25, 5), (28, 4), (33, 3), (37, 2), (39, 10), (36, 80), (31, 220), (29, 220), (31, 120), (33, 15), (30, 7), (26, 3)),
        (1.15, 1.05, 0.98, 0.85, 0.78, 0.75, 0.85, 0.90, 0.92, 1.08, 1.15, 1.28), 0.15, 120,
        T("Lakeside Cars", 400, 20),
        (H("udr-ghat", "Lake Pichola Ghat Haveli", "Lal Ghat", 24.5780, 73.6815, 4.5, "mid", 4200),
         H("udr-fateh", "Fateh Sagar Lakeview", "Fateh Sagar", 24.6000, 73.6770, 4.1, "budget", 2000, "12:00"),
         H("udr-palace", "Aravalli Lake Palace", "Pichola", 24.5700, 73.6790, 4.8, "premium", 12500)),
        (A("udr-citypalace", "City Palace museum tour", "Old City", 24.5764, 73.6835, ("heritage",), 150, ("09:30", "14:00"), 800),
         A("udr-boat", "Lake Pichola sunset boat", "Pichola", 24.5720, 73.6800, ("nature",), 60, ("17:00",), 700),
         A("udr-dharohar", "Dharohar folk dance, Bagore ki Haveli", "Gangaur Ghat", 24.5799, 73.6829, ("culture",), 75, ("19:00",), 350),
         A("udr-sajjangarh", "Monsoon Palace sunset drive", "Sajjangarh", 24.5935, 73.6409, ("heritage", "nature"), 150, ("16:00",), 650),
         A("udr-cooking", "Mewari cooking class", "Old City", 24.5800, 73.6850, ("food",), 150, ("10:30", "16:00"), 1200),
         A("udr-kumbhalgarh", "Kumbhalgarh fort day trip", "Kumbhalgarh", 25.1486, 73.5868, ("heritage", "adventure"), 540, ("08:00",), 2800, full_day=True),
         A("udr-saheliyon", "Saheliyon ki Bari & crafts village", "Fateh Sagar", 24.6033, 73.6866, ("heritage", "shopping"), 120, ("10:00", "15:00"), 450)),
    ),
    Destination(
        "VNS", "Varanasi", "Uttar Pradesh", "India", "Ghats at dawn, aarti at dusk",
        ("spiritual", "heritage", "culture", "food"),
        ((22, 18), (26, 15), (33, 8), (39, 5), (40, 10), (38, 100), (33, 300), (32, 280), (32, 230), (32, 40), (29, 5), (24, 3)),
        (1.10, 1.05, 1.00, 0.85, 0.78, 0.78, 0.82, 0.85, 0.90, 1.05, 1.18, 1.20), 0.10, 100,
        T("Ghat Line Taxis", 380, 17),
        (H("vns-assi", "Assi Ghat Guesthouse", "Assi Ghat", 25.2870, 83.0060, 4.2, "budget", 1700, "12:00"),
         H("vns-darshan", "Ganga Darshan Heritage", "Dashashwamedh", 25.3060, 83.0100, 4.4, "mid", 3900),
         H("vns-grand", "Sarnath Road Grand", "Cantonment", 25.3500, 82.9900, 4.5, "premium", 6800)),
        (A("vns-aarti", "Ganga aarti from a boat", "Dashashwamedh", 25.3069, 83.0104, ("spiritual", "culture"), 90, ("18:15",), 400),
         A("vns-sunrise", "Sunrise boat ride on the ghats", "Assi Ghat", 25.2900, 83.0100, ("spiritual", "nature"), 120, ("05:45",), 800),
         A("vns-oldcity", "Old city lanes & Kashi Vishwanath walk", "Old City", 25.3109, 83.0107, ("heritage", "spiritual"), 150, ("08:30", "15:00"), 600),
         A("vns-sarnath", "Sarnath half-day", "Sarnath", 25.3762, 83.0227, ("heritage", "spiritual"), 210, ("09:30", "13:30"), 1100),
         A("vns-silk", "Banarasi silk weavers visit", "Lallapura", 25.3250, 82.9980, ("culture", "shopping"), 120, ("11:00", "15:30"), 500),
         A("vns-food", "Godowlia street food walk", "Godowlia", 25.3100, 83.0070, ("food",), 150, ("11:30", "19:45"), 900)),
    ),
    Destination(
        "IXL", "Leh", "Ladakh", "India", "High-desert monasteries and blue lakes",
        ("adventure", "nature", "spiritual"),
        ((-2, 10), (1, 8), (7, 10), (13, 8), (17, 7), (22, 4), (25, 15), (24, 15), (21, 8), (14, 4), (7, 2), (1, 5)),
        (0.55, 0.55, 0.70, 0.90, 1.20, 1.40, 1.35, 1.30, 1.15, 0.85, 0.60, 0.55), 0.05, 150,
        T("Indus Cabs", 600, 30),
        (H("ixl-changspa", "Changspa Garden Guesthouse", "Changspa", 34.1700, 77.5700, 4.3, "budget", 2200, "12:00"),
         H("ixl-stupa", "Shanti Stupa View Hotel", "Changspa", 34.1690, 77.5780, 4.4, "mid", 4300),
         H("ixl-indus", "Indus Valley Retreat", "Skara", 34.1450, 77.5700, 4.6, "premium", 7900)),
        (A("ixl-shanti", "Shanti Stupa sunset", "Changspa", 34.1737, 77.5752, ("spiritual", "nature"), 90, ("17:30",), 300),
         A("ixl-palace", "Leh Palace & old town walk", "Old Town", 34.1652, 77.5853, ("heritage",), 150, ("10:00", "15:00"), 600),
         A("ixl-thiksey", "Thiksey monastery morning prayer", "Thiksey", 34.0560, 77.6670, ("spiritual", "culture"), 210, ("06:00",), 1400),
         A("ixl-khardungla", "Khardung La pass excursion", "Khardung La", 34.2785, 77.6046, ("adventure", "nature"), 300, ("09:00",), 3200, strenuous=True),
         A("ixl-pangong", "Pangong Tso day trip", "Pangong", 33.7595, 78.6674, ("nature", "adventure"), 720, ("06:00",), 4500, full_day=True, strenuous=True),
         A("ixl-sangam", "Indus–Zanskar sangam & Magnetic Hill", "Nimmu", 34.2593, 77.3237, ("nature",), 240, ("10:00",), 2200)),
        advisory="Leh is at 3,500 m. The first day is kept free to acclimatise.", rest_on_arrival=True,
    ),
    Destination(
        "IXZ", "Port Blair", "Andaman Islands", "India", "Coral water and colonial history",
        ("beaches", "nature", "heritage"),
        ((30, 40), (30, 20), (31, 10), (32, 60), (31, 360), (29, 480), (29, 400), (29, 400), (29, 450), (29, 300), (29, 230), (29, 150)),
        (1.15, 1.10, 1.05, 1.00, 0.85, 0.70, 0.70, 0.72, 0.75, 0.95, 1.10, 1.30), 0.05, 150,
        T("Island Wheels", 450, 25),
        (H("ixz-aberdeen", "Aberdeen Bazaar Residency", "Aberdeen Bazaar", 11.6670, 92.7360, 4.0, "budget", 2300, "12:00"),
         H("ixz-corbyn", "Corbyn's Cove Resort", "Corbyn's Cove", 11.6450, 92.7480, 4.3, "mid", 4800),
         H("ixz-rossview", "Ross View Premier", "Marine Hill", 11.6730, 92.7500, 4.5, "premium", 7400)),
        (A("ixz-cellular", "Cellular Jail light & sound show", "Atlanta Point", 11.6744, 92.7486, ("heritage", "culture"), 75, ("18:00",), 300),
         A("ixz-ross", "Ross Island & North Bay boat trip", "Ross Island", 11.6762, 92.7613, ("nature", "heritage", "beaches"), 300, ("09:00",), 1500),
         A("ixz-havelock", "Havelock day trip: Radhanagar beach", "Swaraj Dweep", 11.9840, 92.9500, ("beaches", "nature"), 660, ("06:30",), 4200, full_day=True),
         A("ixz-snorkel", "North Bay snorkelling", "North Bay", 11.7040, 92.7570, ("beaches", "adventure"), 180, ("09:30",), 2500, strenuous=True),
         A("ixz-chidiya", "Chidiya Tapu sunset", "Chidiya Tapu", 11.4900, 92.7070, ("nature",), 180, ("15:30",), 900),
         A("ixz-museum", "Samudrika marine museum", "Haddo", 11.6650, 92.7400, ("culture",), 120, ("10:00", "14:00"), 250)),
    ),
    Destination(
        "DXB", "Dubai", "Dubai", "UAE", "Desert, dhows and the tallest view in town",
        ("nightlife", "shopping", "adventure", "food"),
        ((24, 15), (25, 25), (28, 20), (33, 7), (38, 1), (40, 0), (41, 0), (41, 0), (39, 0), (35, 1), (30, 3), (26, 15)),
        (1.15, 1.12, 1.10, 0.95, 0.80, 0.70, 0.72, 0.75, 0.85, 1.00, 1.15, 1.30), 0.85, 600,
        T("Desert Rose Transfers", 1800, 55, reschedule_fee=600), international=True, local_fare_scale=3.2,
        hotels=(H("dxb-deira", "Deira Creek Hotel", "Deira", 25.2697, 55.3095, 4.0, "budget", 5200, "15:00", "12:00"),
                H("dxb-downtown", "Downtown Fountain Suites", "Downtown", 25.1950, 55.2780, 4.4, "mid", 11800, "15:00", "12:00"),
                H("dxb-marina", "Marina Pearl Tower", "Dubai Marina", 25.0805, 55.1403, 4.6, "premium", 17500, "15:00", "12:00")),
        activities=(A("dxb-burj", "Burj Khalifa At the Top", "Downtown", 25.1972, 55.2744, ("culture",), 90, ("10:00", "16:30"), 4300),
                    A("dxb-desert", "Desert safari & dinner", "Lahbab", 24.9850, 55.6000, ("adventure", "nightlife"), 360, ("15:00",), 5200),
                    A("dxb-creek", "Old Dubai creek, abra & souks", "Al Fahidi", 25.2637, 55.2972, ("heritage", "shopping", "food"), 150, ("09:30", "16:00"), 1900),
                    A("dxb-future", "Museum of the Future", "Sheikh Zayed Rd", 25.2192, 55.2820, ("culture",), 120, ("11:00", "15:00"), 3400),
                    A("dxb-dhow", "Marina dhow dinner cruise", "Dubai Marina", 25.0770, 55.1390, ("food", "nightlife"), 120, ("20:00",), 3600),
                    A("dxb-frame", "Dubai Frame", "Zabeel", 25.2356, 55.3003, ("culture",), 75, ("10:00", "17:00"), 1100)),
    ),
    Destination(
        "SIN", "Singapore", "Singapore", "Singapore", "Hawker queues and garden domes",
        ("food", "nature", "nightlife", "shopping"),
        ((30, 240), (31, 115), (32, 170), (32, 165), (32, 170), (31, 130), (31, 155), (31, 150), (31, 140), (31, 160), (31, 255), (30, 290)),
        (1.05, 1.08, 1.00, 0.95, 0.98, 1.08, 1.05, 1.00, 0.92, 0.95, 1.00, 1.20), 0.95, 700,
        T("Merlion Transfers", 2200, 70, reschedule_fee=600), international=True, local_fare_scale=3.6,
        hotels=(H("sin-lodge", "Little India Lodge", "Little India", 1.3066, 103.8518, 4.0, "budget", 7200, "15:00", "12:00"),
                H("sin-shophouse", "Chinatown Shophouse Hotel", "Chinatown", 1.2836, 103.8443, 4.4, "mid", 13500, "15:00", "12:00"),
                H("sin-bayfront", "Marina Bayfront", "Marina Bay", 1.2834, 103.8607, 4.7, "premium", 26000, "15:00", "12:00")),
        activities=(A("sin-gardens", "Gardens by the Bay domes", "Marina Bay", 1.2816, 103.8636, ("nature",), 150, ("10:00", "15:00"), 2900),
                    A("sin-nightsafari", "Night Safari", "Mandai", 1.4022, 103.7881, ("nature", "nightlife"), 180, ("19:15",), 3700),
                    A("sin-hawker", "Hawker centre food trail", "Chinatown", 1.2803, 103.8448, ("food",), 150, ("11:30", "18:30"), 2600),
                    A("sin-sentosa", "Sentosa beach & cable car", "Sentosa", 1.2494, 103.8303, ("beaches",), 300, ("10:30",), 3900),
                    A("sin-botanic", "Botanic Gardens & orchid garden", "Tanglin", 1.3138, 103.8159, ("nature",), 150, ("08:00", "15:30"), 1200),
                    A("sin-bumboat", "Riverside bumboat & light show", "Clarke Quay", 1.2839, 103.8591, ("nightlife",), 90, ("19:45",), 1500)),
    ),
    Destination(
        "BKK", "Bangkok", "Bangkok", "Thailand", "Temples, river boats and night markets",
        ("food", "nightlife", "heritage", "shopping"),
        ((32, 13), (33, 20), (34, 42), (35, 91), (34, 220), (33, 150), (33, 155), (33, 190), (32, 320), (32, 240), (31, 48), (31, 10)),
        (1.15, 1.10, 1.00, 1.05, 0.85, 0.85, 0.92, 0.90, 0.82, 0.90, 1.05, 1.25), 0.60, 350,
        T("Siam Shuttle", 900, 28, reschedule_fee=500), international=True, local_fare_scale=1.8,
        hotels=(H("bkk-banglamphu", "Banglamphu Boutique", "Banglamphu", 13.7590, 100.4970, 4.1, "budget", 2600),
                H("bkk-teak", "Riverside Teak House", "Riverside", 13.7236, 100.5141, 4.5, "mid", 5400),
                H("bkk-sky", "Sukhumvit Sky Residences", "Sukhumvit", 13.7380, 100.5600, 4.6, "premium", 9800)),
        activities=(A("bkk-palace", "Grand Palace & Wat Pho", "Rattanakosin", 13.7500, 100.4913, ("heritage", "spiritual"), 210, ("08:30", "13:00"), 2300),
                    A("bkk-floating", "Damnoen Saduak floating market", "Ratchaburi", 13.5195, 99.9590, ("culture", "food", "shopping"), 360, ("06:30",), 2700, full_day=True),
                    A("bkk-yaowarat", "Yaowarat street food tour", "Chinatown", 13.7400, 100.5100, ("food", "nightlife"), 180, ("18:30",), 2400),
                    A("bkk-river", "Chao Phraya dinner cruise", "Riverside", 13.7236, 100.5141, ("food", "nightlife"), 150, ("19:30",), 3100),
                    A("bkk-chatuchak", "Chatuchak market crawl", "Chatuchak", 13.7999, 100.5503, ("shopping", "food"), 180, ("10:00",), 900, weekdays=(5, 6)),
                    A("bkk-cooking", "Thai cooking class & market visit", "Silom", 13.7300, 100.5300, ("food",), 240, ("09:00", "14:00"), 2500)),
    ),
)}

TAGS = ("heritage", "culture", "food", "nature", "beaches", "adventure", "spiritual", "nightlife", "shopping", "wellness")

# Illustrative macro travel-price index (2025 = 1.00), used by Inflation Forge for history.
PRICE_INDEX = {2019: 0.795, 2020: 0.667, 2021: 0.731, 2022: 0.879, 2023: 0.936, 2024: 0.977, 2025: 1.000, 2026: 1.041}
