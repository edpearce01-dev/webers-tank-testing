import json
import sqlite3
from pathlib import Path
from database import DB_PATH


def get_bh_db_path() -> Path:
    if "_local_preview" in DB_PATH.name:
        return DB_PATH.parent / "_local_preview_bbq.db"
    return DB_PATH.parent / "bbq_homestead.db"


def get_bh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_bh_db_path(), timeout=25.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_bbq_db() -> None:
    conn = get_bh_conn()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS bh_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bbq_menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            spice_level INTEGER DEFAULT 0,
            opt1_label TEXT DEFAULT '1/2 Pan',
            opt1_price REAL DEFAULT 45.0,
            opt2_label TEXT DEFAULT 'Full Pan',
            opt2_price REAL DEFAULT 0.0,
            image_url TEXT DEFAULT '',
            available INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 100
        );

        CREATE TABLE IF NOT EXISTS bbq_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            subtitle TEXT DEFAULT '',
            event_date TEXT DEFAULT '',
            cutoff_note TEXT DEFAULT '',
            location TEXT DEFAULT '',
            event_mode TEXT DEFAULT 'BOTH',
            host_pin TEXT DEFAULT '1234',
            pit_providing TEXT DEFAULT '',
            allowed_items_json TEXT DEFAULT '[]',
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS bbq_club_checklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            category TEXT DEFAULT 'Supplies',
            item_name TEXT NOT NULL,
            qty_needed INTEGER DEFAULT 1,
            claims_json TEXT DEFAULT '[]',
            is_open_potluck INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS bbq_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            source TEXT DEFAULT 'BBQ',
            event_id INTEGER,
            order_type TEXT DEFAULT 'PREORDER',
            customer_name TEXT NOT NULL,
            customer_phone TEXT DEFAULT '',
            customer_email TEXT DEFAULT '',
            pickup_or_event_date TEXT DEFAULT '',
            headcount INTEGER DEFAULT 0,
            service_style TEXT DEFAULT 'PICKUP_PANS',
            items_json TEXT DEFAULT '[]',
            total_amount REAL DEFAULT 0.0,
            payment_method TEXT DEFAULT 'Cash',
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'NEW'
        );

        CREATE TABLE IF NOT EXISTS homestead_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            unit_label TEXT DEFAULT 'Bottle',
            price REAL DEFAULT 10.0,
            stock_qty INTEGER DEFAULT 10,
            description TEXT DEFAULT '',
            badge TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 100
        );
        """
    )

    defaults = [
        ("brand_top", "BACKROAD"),
        ("brand_bottom", "PIT & SMOKE"),
        ("tagline", "Hickory & Fruitwood Smoked Meats • Trailer Catering & Party Pans"),
        ("hero_eyebrow", "SMOKERS • FRYERS • WARMERS • ON-SITE OR PARTY PANS"),
        ("hero_headline_white", "LOW & SLOW"),
        ("hero_headline_orange", "WOOD-FIRED BBQ"),
        ("lead_time_note", "Please allow at least 72 hours notice for smoked meat & pan orders. Custom club events, stags & private parties available."),
        ("contact_phone", "724-266-8008"),
        ("pickup_location", "Baden / Beaver County, PA (Exact pickup or trailer arrival time confirmed on order)"),
        ("hero_video_url", "https://assets.mixkit.co/videos/preview/mixkit-barbecue-meat-cooking-on-the-grill-with-fire-43235-large.mp4"),
        ("pay_cash", "1"),
        ("pay_venmo", "1"),
        ("venmo_handle", ""),
        ("pay_cashapp", "1"),
        ("cashapp_handle", ""),
        ("pay_paypal", "0"),
        ("paypal_handle", ""),
        ("pay_zelle", "0"),
        ("zelle_handle", ""),
        ("homestead_name", "Backroad Homestead & Sauce Stand"),
        ("homestead_tagline", "Small-Batch House BBQ Sauces • Raw Honey • Wood-Fired PA Maple Syrup • Fresh Duck & Chicken Eggs"),
        ("homestead_pickup_note", "Porch / shop pickup in Baden — pay Cash, Venmo, or Cash App at pickup."),
    ]
    cur.executemany("INSERT OR IGNORE INTO bh_settings (key, value) VALUES (?, ?);", defaults)

    if cur.execute("SELECT COUNT(*) FROM bbq_menu_items;").fetchone()[0] == 0:
        seed_menu = [
            ("SMOKED_MEATS", "Whole Texas-Style Brisket", "Approx. 10–12 lbs pre-trimmed — coarse salt, cracked black pepper & garlic bark, 14-hour hickory smoke. Sliced in pan or whole.", 0, "Half Brisket (~5 lb)", 105.0, "Whole Brisket (~11 lb)", 205.0, 10),
            ("SMOKED_MEATS", "Hand-Pulled Pork Shoulder", "12-hour hickory & applewood smoked pork butt, hand-pulled tender with house mop & BBQ sauce on the side.", 0, "1/2 Pan (feeds 12–15)", 50.0, "Full Pan (feeds 25–30)", 95.0, 20),
            ("SMOKED_MEATS", "Smoked & Flash-Fried Jumbo Wings", "Pit-smoked over cherrywood, then flash-dropped in our trailer fryers for crisp skin. Dry rub, Sweet Heat, or Maple-Bourbon.", 1, "2 Dozen (24 Wings)", 34.0, "4 Dozen (48 Wings)", 64.0, 30),
            ("SMOKED_MEATS", "St. Louis Cut Ribs", "4.5-hour fruitwood smoke with brown-sugar chili rub and caramelized house glaze.", 0, "Single Full Slab", 32.0, "3-Slab Party Pack", 90.0, 40),
            ("SMOKED_MEATS", "24-Hr Brined Smoked Turkey", "Herb-citrus brined and smoked golden & juicy over applewood — killer for holidays & club dinners.", 0, "Boneless Breast (~5 lb)", 65.0, "Whole Smoked Turkey", 110.0, 50),
            ("SMOKED_MEATS", "Maple-Bourbon Double-Smoked Ham", "Hickory-smoked spiral or pit ham basted in our own wood-fired PA maple-bourbon glaze.", 0, "Half Ham Pan", 55.0, "Whole Glazed Ham", 95.0, 60),
            ("SPECIALTIES", "Smoked Bacon-Wrapped Eggs", "Soft-centered farm eggs wrapped in seasoned sausage & thick-cut bacon, smoked over fruitwood & glazed.", 1, "1 Dozen", 30.0, "2 Dozen Tray", 56.0, 10),
            ("SPECIALTIES", "Pork Belly Burnt Ends", "Cubed pork belly smoked tender and caramelized in sweet-heat honey BBQ glaze.", 2, "1/2 Pan", 55.0, "Full Pan", 105.0, 20),
            ("SPECIALTIES", "Bacon-Wrapped Armadillo Eggs", "Fresh jalapeños stuffed with cream cheese & sharp cheddar, wrapped in sausage & bacon, smoked & glazed.", 2, "1 Dozen", 32.0, "2 Dozen Tray", 60.0, 30),
            ("SPECIALTIES", "Smoked Kielbasa, Peppers & Onions", "Local smoked kielbasa rounds simmered with sweet bell peppers, onions & tangy mustard-brown-sugar glaze.", 0, "1/2 Pan", 45.0, "Full Pan", 85.0, 40),
            ("SIDES", "Signature Smoked Mac & Cheese", "Velvety three-cheese blend kissed with hickory smoke and finished with a buttery toasted cheddar crumb.", 0, "1/2 Pan (10–15 ppl)", 45.0, "Full Pan (20–25 ppl)", 85.0, 10),
            ("SIDES", "Pitmaster Cowboy Baked Beans", "Slow-simmered beans loaded with smoked brisket & pulled pork burnt ends, bacon, sweet peppers & brown sugar.", 0, "1/2 Pan", 42.0, "Full Pan", 80.0, 20),
            ("SIDES", "Cheesy Smokehouse Potatoes", "Warm crockpot/pan favorite — shredded potatoes, sour cream, sharp cheddar, smoked bacon & scallions.", 0, "1/2 Pan", 40.0, "Full Pan", 75.0, 30),
            ("SIDES", "Sweet Tangy Vinegar Pit Slaw", "Crisp shredded cabbage & carrots in house apple-cider vinegar sweet slaw dressing.", 0, "1/2 Pan", 25.0, "Full Pan", 48.0, 40),
            ("SIDES", "Honey-Butter Skillet Cornbread", "Fresh-baked cheddar cornbread served with whipped local raw honey butter.", 0, "1/2 Pan (12 pcs)", 25.0, "Full Pan (24 pcs)", 45.0, 50),
            ("EXTRAS", "Disposable Chafing Dish Set (Set of 3)", "3 wire racks, deep water pans, serving spoons & 6 Sterno fuel cans.", 0, "Set of 3 Racks", 40.0, "", 0.0, 10),
            ("EXTRAS", "Plates, Cutlery & Napkins Pack", "Heavy-duty BBQ plates, forks, knives & paper towels.", 0, "25-Guest Pack", 22.0, "50-Guest Pack", 40.0, 20),
            ("EXTRAS", "Fresh Brioche Sandwich Buns", "Soft bakery buns for pulled pork or chopped brisket.", 0, "1 Dozen", 6.0, "3 Dozen", 16.0, 30),
        ]
        cur.executemany(
            """INSERT INTO bbq_menu_items
               (category, name, description, spice_level, opt1_label, opt1_price, opt2_label, opt2_price, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            seed_menu,
        )

    if cur.execute("SELECT COUNT(*) FROM homestead_items;").fetchone()[0] == 0:
        seed_hs = [
            ("House BBQ Sauces", "Backroad Sweet Heat BBQ Sauce", "16 oz Glass Bottle", 10.0, 12, "Rich brown-sugar & tomato base with a warm cayenne-honey kick at the finish.", "Fan Favorite", 10),
            ("House BBQ Sauces", "PA Maple-Bourbon Glaze", "16 oz Glass Bottle", 12.0, 8, "Made with our own wood-fired PA maple syrup, bourbon, black pepper & apple cider.", "Small Batch", 20),
            ("House BBQ Sauces", "Carolina Tangy Gold Mustard", "16 oz Glass Bottle", 10.0, 8, "Zesty golden mustard vinegar sauce — unbelievable on pulled pork, wings & kielbasa.", "Pork Favorite", 30),
            ("House BBQ Sauces", "Backyard Hickory Classic", "16 oz Glass Bottle", 10.0, 10, "Smooth, smoky, kid-friendly sweet & tangy all-purpose pit sauce.", "Mild", 40),
            ("Honey & Maple Syrup", "Raw Unfiltered Hive Honey", "1 lb Glass Jar", 14.0, 10, "Pure raw wildflower & clover honey straight from our hives — never heated or ultra-filtered.", "Raw Local", 50),
            ("Honey & Maple Syrup", "Wood-Fired PA Pure Maple Syrup", "Pint Glass Bottle (16 oz)", 16.0, 6, "Tapped & boiled locally over hardwood fire — deep amber color with rich vanilla-maple flavor.", "Limited Run", 60),
            ("Honey & Maple Syrup", "Smokehouse Hot Honey", "12 oz Bottle", 13.0, 6, "Our raw hive honey infused with smoked chilies — drizzle on wings, cornbread, or pizza.", "Sweet & Spicy", 70),
            ("Coop Eggs", "Farm-Fresh Duck Eggs", "Half-Dozen (6) / Dozen", 8.0, 6, "Huge, rich golden yolks — bakers' secret weapon and amazing fried or smoked.", "Fresh Daily", 80),
            ("Coop Eggs", "Backyard Free-Range Chicken Eggs", "1 Dozen Carton", 5.0, 10, "Fresh multi-colored brown, green & cream eggs from our backyard flock.", "Coop Fresh", 90),
        ]
        cur.executemany(
            """INSERT INTO homestead_items
               (category, name, unit_label, price, stock_qty, description, badge, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
            seed_hs,
        )

    if cur.execute("SELECT COUNT(*) FROM bbq_events;").fetchone()[0] == 0:
        cur.execute(
            """INSERT INTO bbq_events
               (slug, title, subtitle, event_date, cutoff_note, location, event_mode, host_pin, pit_providing, active, created_at)
               VALUES ('christmas-party-smoke', 'Christmas Party & Holiday Pan Drop', 'Pre-order hot party pans or sign up to bring a dish/supply for the club party!', '2026-12-20', 'Orders close 72 hrs prior at 6:00 PM', 'Club Pavilion / Baden Pickup', 'BOTH', '2026', 'Smoked Brisket, Pulled Pork, Smoked Mac & Cheese & House Sauces', 1, '2026-11-16');"""
        )
        ev_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO bbq_club_checklist (event_id, category, item_name, qty_needed, claims_json, is_open_potluck) VALUES (?, ?, ?, ?, '[]', 0);",
            [
                (ev_id, "Supplies", "20 lb Bags of Ice", 4),
                (ev_id, "Supplies", "Heavy-Duty Paper Plates & Forks", 2),
                (ev_id, "Drinks / Ice", "2-Liters / Mixers / Pop", 4),
                (ev_id, "Dessert", "Holiday Cookies / Pie / Dessert Tray", 3),
                (ev_id, "Side Dish", "Chips, Pretzels or Appetizer Dip", 3),
            ],
        )

    conn.commit()
    conn.close()
