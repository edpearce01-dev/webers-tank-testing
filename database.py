import os
import secrets
import sqlite3
from pathlib import Path


def _resolve_db_path() -> Path:
    env_p = os.getenv("WEBER_DB_PATH") or os.getenv("DATABASE_PATH")
    if env_p:
        return Path(env_p)
    for disk in (Path("/var/data"), Path("/data")):
        if disk.is_dir() and os.access(disk, os.W_OK):
            existing = sorted(disk.glob("*.db"))
            return existing[0] if existing else (disk / "webers_hydro.db")
    base = Path(__file__).parent
    existing_local = sorted(base.glob("*.db"))
    if existing_local:
        return existing_local[0]
    return base / "webers_hydro.db"


DB_PATH = _resolve_db_path()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, col_def: str) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table});").fetchall()}
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def};")


def bump_db_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key='db_version';").fetchone()
    nxt = (int(row["value"]) if row and str(row["value"]).isdigit() else 0) + 1
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', ?);", (str(nxt),))
    return nxt


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS crew_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT UNIQUE NOT NULL,
            initials TEXT NOT NULL,
            role TEXT DEFAULT 'Visual Inspection',
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS crew_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            initials TEXT NOT NULL UNIQUE,
            pin TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL DEFAULT 'CREW',
            can_sign_dot INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS crew_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_date TEXT NOT NULL,
            assigned_to TEXT NOT NULL DEFAULT 'ALL',
            assigned_name TEXT DEFAULT 'All Crew',
            category TEXT DEFAULT 'SHOP',
            title TEXT NOT NULL,
            priority TEXT DEFAULT 'NORMAL',
            status TEXT DEFAULT 'OPEN',
            completed_by TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            crew_note TEXT DEFAULT '',
            created_by TEXT DEFAULT 'RP',
            created_at TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS calibration_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            serial_number TEXT NOT NULL,
            pressure_psi INTEGER NOT NULL,
            expected_cc REAL NOT NULL,
            UNIQUE(serial_number, pressure_psi)
        );

        CREATE TABLE IF NOT EXISTS daily_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            test_date TEXT NOT NULL,
            test_time TEXT NOT NULL,
            jacket_type TEXT NOT NULL,
            calibrated_serial TEXT NOT NULL,
            max_target_psi INTEGER NOT NULL,
            target_pressure_psi INTEGER NOT NULL,
            expected_cc REAL NOT NULL,
            actual_total_cc REAL NOT NULL,
            permanent_cc REAL DEFAULT 0.0,
            elastic_cc REAL DEFAULT 0.0,
            percent_deviation REAL DEFAULT 0.0,
            passed INTEGER DEFAULT 1,
            logged_by TEXT DEFAULT 'RP',
            operator_name TEXT DEFAULT 'Ryan Pearce',
            operator_initials TEXT DEFAULT 'RP',
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS cga_specs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            label TEXT NOT NULL,
            dot_spec TEXT NOT NULL,
            service_psi INTEGER NOT NULL,
            test_psi INTEGER NOT NULL,
            outside_dia TEXT DEFAULT '',
            length_in TEXT DEFAULT '',
            ree_limit REAL
        );

        CREATE TABLE IF NOT EXISTS cylinder_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_date TEXT NOT NULL,
            intake_at TEXT DEFAULT '',
            sheet_no INTEGER DEFAULT 0,
            customer_name TEXT NOT NULL,
            customer_phone TEXT DEFAULT '',
            dropoff_id INTEGER,
            schedule_job_id INTEGER,
            priority TEXT DEFAULT 'STANDARD',
            rush_fee INTEGER DEFAULT 0,
            promised_date TEXT DEFAULT '',
            holding_zone TEXT DEFAULT 'ZONE_C_READY_RACK',
            serial_number TEXT NOT NULL,
            valve_tag TEXT DEFAULT '',
            valve_reinstalled INTEGER DEFAULT 0,
            rin_marked INTEGER DEFAULT 0,
            rin_mark_type TEXT DEFAULT '',
            star_10yr INTEGER DEFAULT 0,
            mfg_id TEXT DEFAULT '',
            mfg_date TEXT DEFAULT '',
            gas_service TEXT DEFAULT 'AIR',
            outside_dia TEXT DEFAULT '',
            length_in TEXT DEFAULT '',
            physical_size TEXT DEFAULT '',
            dot_spec TEXT NOT NULL,
            service_psi INTEGER NOT NULL,
            test_psi INTEGER NOT NULL,
            ree_limit REAL,
            service_mode TEXT DEFAULT 'HYDRO_AND_VISUAL',
            status TEXT DEFAULT 'QUEUED',
            shop_stage TEXT DEFAULT 'IN_SHOP',
            verification_id INTEGER,
            jacket_type TEXT DEFAULT '',
            total_cc REAL,
            permanent_cc REAL,
            elastic_cc REAL,
            percent_permanent REAL,
            coating_type TEXT DEFAULT 'Painted',
            coating_condition TEXT DEFAULT 'Good',
            chk_corrosion TEXT DEFAULT 'OK',
            chk_dents_dings TEXT DEFAULT 'OK',
            chk_cuts_gouges TEXT DEFAULT 'OK',
            chk_leaks TEXT DEFAULT 'OK',
            chk_fire_damage TEXT DEFAULT 'OK',
            chk_bulges TEXT DEFAULT 'OK',
            chk_neck_defects TEXT DEFAULT 'OK',
            relief_valve_changed TEXT DEFAULT 'No',
            addon_oring INTEGER DEFAULT 0,
            addon_burst_disc INTEGER DEFAULT 0,
            addon_clean INTEGER DEFAULT 0,
            condemn_action TEXT DEFAULT '',
            picked_up_by TEXT DEFAULT '',
            picked_up_at TEXT DEFAULT '',
            visual_pass INTEGER DEFAULT 1,
            hydro_disposition TEXT DEFAULT 'A',
            visual_disposition TEXT DEFAULT 'OK',
            intake_by TEXT DEFAULT 'RP',
            tested_by TEXT DEFAULT 'RP',
            completed_at TEXT DEFAULT '',
            operator_name TEXT DEFAULT 'Ryan Pearce',
            operator_initials TEXT DEFAULT 'RP',
            invoice_id INTEGER,
            notes TEXT DEFAULT '',
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS dropoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            work_date TEXT NOT NULL,
            entry_type TEXT DEFAULT 'DROPOFF',
            account_type TEXT DEFAULT 'WALK_IN',
            po_station TEXT DEFAULT '',
            contact_person TEXT DEFAULT '',
            customer_name TEXT NOT NULL,
            customer_phone TEXT DEFAULT '',
            customer_email TEXT DEFAULT '',
            pickup_address TEXT DEFAULT '',
            preferred_date TEXT DEFAULT '',
            bottle_count INTEGER DEFAULT 1,
            imported_count INTEGER DEFAULT 0,
            tank_summary TEXT DEFAULT '',
            service_requested TEXT DEFAULT 'Hydro + Visual',
            valves_attached INTEGER DEFAULT 1,
            needs_air_fill INTEGER DEFAULT 0,
            rush_requested INTEGER DEFAULT 0,
            promised_date TEXT DEFAULT '',
            holding_zone TEXT DEFAULT 'ZONE_A_BACKROOM',
            picked_up_by TEXT DEFAULT '',
            pickup_ticket_ref TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'IN_BACKROOM',
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS schedule_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            scheduled_date TEXT NOT NULL,
            due_date TEXT DEFAULT '',
            job_type TEXT DEFAULT 'SHOP_BATCH',
            account_type TEXT DEFAULT 'WALK_IN',
            po_station TEXT DEFAULT '',
            contact_person TEXT DEFAULT '',
            customer_name TEXT NOT NULL,
            customer_phone TEXT DEFAULT '',
            location_notes TEXT DEFAULT '',
            bottle_count INTEGER DEFAULT 1,
            tank_summary TEXT DEFAULT '',
            valves_attached INTEGER DEFAULT 1,
            needs_air_fill INTEGER DEFAULT 0,
            priority TEXT DEFAULT 'STANDARD',
            rush_fee INTEGER DEFAULT 0,
            holding_zone TEXT DEFAULT 'ZONE_A_BACKROOM',
            prep_status TEXT DEFAULT 'WAITING',
            locked_for_day INTEGER DEFAULT 0,
            auto_paced INTEGER DEFAULT 0,
            status TEXT DEFAULT 'OPEN',
            notes TEXT DEFAULT '',
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS shop_day_plans (
            work_date TEXT PRIMARY KEY,
            shift_mode TEXT DEFAULT 'FULL_HYDRO',
            bottle_capacity INTEGER DEFAULT 24,
            day_notes TEXT DEFAULT '',
            lineup_locked INTEGER DEFAULT 0,
            allow_extra_autopace INTEGER DEFAULT 1,
            started_by TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            closed_out_at TEXT DEFAULT '',
            shift_notes TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS pricing_rates (
            code TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            unit_price REAL DEFAULT 0.0,
            notes TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 100
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT UNIQUE NOT NULL,
            invoice_date TEXT NOT NULL,
            work_date TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            customer_phone TEXT DEFAULT '',
            subtotal REAL DEFAULT 0.0,
            tax_rate REAL DEFAULT 0.0,
            tax_amount REAL DEFAULT 0.0,
            total_amount REAL DEFAULT 0.0,
            status TEXT DEFAULT 'DRAFT',
            payment_method TEXT DEFAULT '',
            paid_at TEXT DEFAULT '',
            created_by TEXT DEFAULT 'RP',
            notes TEXT DEFAULT '',
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            record_id INTEGER,
            description TEXT NOT NULL,
            qty REAL DEFAULT 1.0,
            unit_price REAL DEFAULT 0.0,
            line_total REAL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS crew_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            submitted_by TEXT DEFAULT 'RP',
            category TEXT DEFAULT 'Improvement',
            page_tab TEXT DEFAULT '',
            priority TEXT DEFAULT 'Normal',
            message TEXT NOT NULL,
            status TEXT DEFAULT 'OPEN',
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT UNIQUE NOT NULL,
            account_type TEXT DEFAULT 'WALK_IN',
            contact_person TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            street_address TEXT DEFAULT '',
            city TEXT DEFAULT '',
            state TEXT DEFAULT 'PA',
            zip_code TEXT DEFAULT '',
            payment_terms TEXT DEFAULT 'DUE_AT_PICKUP',
            tax_exempt INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        );


        CREATE TABLE IF NOT EXISTS time_punches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            crew_initials TEXT NOT NULL,
            crew_name TEXT NOT NULL,
            work_date TEXT NOT NULL,
            clock_in_at TEXT NOT NULL,
            clock_in_iso TEXT NOT NULL,
            clock_out_at TEXT DEFAULT '',
            clock_out_iso TEXT DEFAULT '',
            duration_minutes INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'OPEN',
            edited_by TEXT DEFAULT '',
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS operating_hours (
            day_of_week INTEGER PRIMARY KEY,
            day_name TEXT NOT NULL,
            is_open INTEGER DEFAULT 1,
            open_time TEXT DEFAULT '09:00',
            close_time TEXT DEFAULT '16:00',
            dock_24_7 INTEGER DEFAULT 1,
            note TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS holiday_closures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            title TEXT NOT NULL,
            staffed_open INTEGER DEFAULT 0,
            special_hours TEXT DEFAULT '',
            dock_open INTEGER DEFAULT 1,
            public_note TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS sms_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            message TEXT NOT NULL,
            kind TEXT DEFAULT 'READY',
            ref_id TEXT DEFAULT '',
            status TEXT DEFAULT 'PENDING',
            sent_at TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS shop_supplies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            item_name TEXT NOT NULL,
            spec_note TEXT DEFAULT '',
            vendor_name TEXT DEFAULT 'Amazon / Industrial',
            reorder_url TEXT DEFAULT '',
            low_stock INTEGER DEFAULT 0,
            last_ordered_date TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 100
        );
        """
    )

    # 1. Run ALL column migrations on existing tables BEFORE any seed INSERTs execute!
    for col, cdef in [
        ("notes", "TEXT DEFAULT ''"),
        ("sort_order", "INTEGER DEFAULT 100"),
    ]:
        _ensure_column(conn, "pricing_rates", col, cdef)

    for col, cdef in [
        ("day_notes", "TEXT DEFAULT ''"),
        ("lineup_locked", "INTEGER DEFAULT 0"),
        ("allow_extra_autopace", "INTEGER DEFAULT 1"),
        ("started_by", "TEXT DEFAULT ''"),
        ("started_at", "TEXT DEFAULT ''"),
        ("closed_out_at", "TEXT DEFAULT ''"),
        ("shift_notes", "TEXT DEFAULT ''"),
        ("updated_at", "TEXT DEFAULT ''"),
    ]:
        _ensure_column(conn, "shop_day_plans", col, cdef)

    for col, cdef in [
        ("intake_at", "TEXT DEFAULT ''"),
        ("customer_phone", "TEXT DEFAULT ''"),
        ("dropoff_id", "INTEGER"),
        ("schedule_job_id", "INTEGER"),
        ("priority", "TEXT DEFAULT 'STANDARD'"),
        ("rush_fee", "INTEGER DEFAULT 0"),
        ("promised_date", "TEXT DEFAULT ''"),
        ("holding_zone", "TEXT DEFAULT 'ZONE_C_READY_RACK'"),
        ("valve_tag", "TEXT DEFAULT ''"),
        ("valve_reinstalled", "INTEGER DEFAULT 0"),
        ("rin_marked", "INTEGER DEFAULT 0"),
        ("rin_mark_type", "TEXT DEFAULT ''"),
        ("star_10yr", "INTEGER DEFAULT 0"),
        ("shop_stage", "TEXT DEFAULT 'IN_SHOP'"),
        ("addon_oring", "INTEGER DEFAULT 0"),
        ("addon_burst_disc", "INTEGER DEFAULT 0"),
        ("addon_clean", "INTEGER DEFAULT 0"),
        ("condemn_action", "TEXT DEFAULT ''"),
        ("picked_up_by", "TEXT DEFAULT ''"),
        ("picked_up_at", "TEXT DEFAULT ''"),
        ("intake_by", "TEXT DEFAULT 'RP'"),
        ("tested_by", "TEXT DEFAULT 'RP'"),
        ("completed_at", "TEXT DEFAULT ''"),
        ("invoice_id", "INTEGER"),
    ]:
        _ensure_column(conn, "cylinder_records", col, cdef)

    for col, cdef in [
        ("entry_type", "TEXT DEFAULT 'DROPOFF'"),
        ("account_type", "TEXT DEFAULT 'WALK_IN'"),
        ("po_station", "TEXT DEFAULT ''"),
        ("contact_person", "TEXT DEFAULT ''"),
        ("customer_email", "TEXT DEFAULT ''"),
        ("pickup_address", "TEXT DEFAULT ''"),
        ("preferred_date", "TEXT DEFAULT ''"),
        ("imported_count", "INTEGER DEFAULT 0"),
        ("tank_summary", "TEXT DEFAULT ''"),
        ("valves_attached", "INTEGER DEFAULT 1"),
        ("needs_air_fill", "INTEGER DEFAULT 0"),
        ("rush_requested", "INTEGER DEFAULT 0"),
        ("promised_date", "TEXT DEFAULT ''"),
        ("holding_zone", "TEXT DEFAULT 'ZONE_A_BACKROOM'"),
        ("picked_up_by", "TEXT DEFAULT ''"),
        ("pickup_ticket_ref", "TEXT DEFAULT ''"),
    ]:
        _ensure_column(conn, "dropoffs", col, cdef)

    for col, cdef in [
        ("account_type", "TEXT DEFAULT 'WALK_IN'"),
        ("po_station", "TEXT DEFAULT ''"),
        ("contact_person", "TEXT DEFAULT ''"),
        ("tank_summary", "TEXT DEFAULT ''"),
        ("valves_attached", "INTEGER DEFAULT 1"),
        ("needs_air_fill", "INTEGER DEFAULT 0"),
        ("priority", "TEXT DEFAULT 'STANDARD'"),
        ("rush_fee", "INTEGER DEFAULT 0"),
        ("holding_zone", "TEXT DEFAULT 'ZONE_A_BACKROOM'"),
        ("prep_status", "TEXT DEFAULT 'WAITING'"),
        ("locked_for_day", "INTEGER DEFAULT 0"),
        ("auto_paced", "INTEGER DEFAULT 0"),
    ]:
        _ensure_column(conn, "schedule_jobs", col, cdef)

    for col, cdef in [
        ("customer_phone", "TEXT DEFAULT ''"),
        ("payment_method", "TEXT DEFAULT ''"),
        ("paid_at", "TEXT DEFAULT ''"),
        ("created_by", "TEXT DEFAULT 'RP'"),
    ]:
        _ensure_column(conn, "invoices", col, cdef)


    for col, cdef in [
        ("pickup_time", "TEXT DEFAULT ''"),
        ("dropoff_time", "TEXT DEFAULT ''"),
        ("return_address", "TEXT DEFAULT ''"),
        ("customer_email", "TEXT DEFAULT ''"),
        ("drive_miles", "REAL DEFAULT 0.0"),
        ("drive_eta_mins", "INTEGER DEFAULT 0"),
        ("pickup_leg_done", "INTEGER DEFAULT 0"),
    ]:
        _ensure_column(conn, "schedule_jobs", col, cdef)

    for col, cdef in [
        ("preferred_time", "TEXT DEFAULT ''"),
        ("drive_miles", "REAL DEFAULT 0.0"),
        ("drive_eta_mins", "INTEGER DEFAULT 0"),
    ]:
        _ensure_column(conn, "dropoffs", col, cdef)

    for col, cdef in [
        ("on_hand_qty", "INTEGER DEFAULT 5"),
        ("reorder_threshold", "INTEGER DEFAULT 2"),
        ("unit_label", "TEXT DEFAULT 'units'"),
        ("alert_sent", "INTEGER DEFAULT 0"),
    ]:
        _ensure_column(conn, "shop_supplies", col, cdef)

    # 2. Seed Core Settings
    default_settings = [
        ("dot_rin", "B386"),
        ("tc_reg", ""),
        ("phone", "724-266-8008"),
        ("calibrated_serial", "1SF11904"),
        ("starting_sheet_no", "8095"),
        ("current_sheet_no", "8095"),
        ("daily_hydro_capacity", "24"),
        ("default_tax_rate", "6.0"),
        ("gemini_api_key", ""),
        ("sheets_webhook_url", ""),
        ("feedback_webhook_url", ""),
        ("alert_email", ""),
        ("smtp_sender_email", ""),
        ("smtp_app_password", ""),
        ("shop_pin", "0386"),
        ("crew_pin", "0386"),
        ("session_secret", secrets.token_hex(24)),
        ("testing_mode", "1"),
        ("rin_expires_date", ""),
        ("gauge_cert_expires_date", ""),
        ("resend_api_key", ""),
        ("resend_from_email", "alerts@weberstanktesting.com"),
        ("master_sheet_url", ""),
        ("gbp_edit_url", "https://business.google.com/"),
        ("sms_shortcut_key", secrets.token_hex(8)),
        ("db_version", "1"),
    ]
    cur.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?);", default_settings)

    cur.executemany(
        "INSERT OR IGNORE INTO crew_members (full_name, initials, role, active) VALUES (?, ?, ?, 1);",
        [
            ("Ryan Pearce", "RP", "Facility Manager"),
            ("Kenny", "KN", "Prep & Visual"),
        ],
    )

    if cur.execute("SELECT COUNT(*) FROM calibration_targets;").fetchone()[0] == 0:
        cur.executemany(
            "INSERT OR IGNORE INTO calibration_targets (serial_number, pressure_psi, expected_cc) VALUES (?, ?, ?);",
            [
                ("1SF11904", 2000, 34.2),
                ("1SF11904", 2500, 42.8),
                ("1SF11904", 3000, 51.4),
                ("1SF11904", 4000, 68.6),
                ("1SF11904", 5000, 86.2),
                ("1SF11904", 6000, 103.6),
                ("1SF11904", 7000, 121.8),
            ],
        )

    if cur.execute("SELECT COUNT(*) FROM cga_specs;").fetchone()[0] == 0:
        cur.executemany(
            """INSERT INTO cga_specs (category, label, dot_spec, service_psi, test_psi, outside_dia, length_in, ree_limit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
            [
                ("Fire SCBA", "45-Min Carbon SCBA (4500#)", "SP10915", 4500, 7500, "6.8", "21.5", None),
                ("Fire SCBA", "30-Min Carbon SCBA (4500#)", "SP10945", 4500, 7500, "5.4", "18.5", None),
                ("Fire SCBA", "30-Min Low-Psi SCBA (2216#)", "SP10915", 2216, 3693, "6.8", "19.0", None),
                ("Fire Cascade", "6000 PSI Station Cascade", "3AA", 6000, 10000, "9.0", "51", None),
                ("Paintball HPA", "68ci / 4500 Carbon HPA", "SP14387", 4500, 7500, "4.5", "9.3", None),
                ("Paintball HPA", "77ci / 4500 Carbon HPA", "SP14387", 4500, 7500, "4.5", "10.5", None),
                ("Paintball HPA", "48ci / 3000 Alum HPA", "3AL", 3000, 5000, "3.5", "8.5", None),
                ("EMS Oxygen", "Medical E Cylinder O2", "3AL", 2015, 3360, "4.25", "25.5", None),
                ("EMS Oxygen", "Medical D Cylinder O2", "3AL", 2015, 3360, "4.25", "16.5", None),
                ("Scuba", "AL80 Scuba Dive Tank", "3AL", 3000, 5000, "7.25", "26.1", None),
                ("Beverage CO2", "20 lb Aluminum CO2", "3AL", 1800, 3000, "8.0", "23.3", None),
                ("Beverage CO2", "5 lb Aluminum CO2", "3AL", 1800, 3000, "5.25", "14.2", None),
                ("Industrial", "250 cf Welding Steel (2015#)", "3AA", 2015, 3360, "9.0", "51", None),
                ("Industrial", "125 cf Welding Steel (2015#)", "3AA", 2015, 3360, "7.0", "43", None),
            ],
        )


    if cur.execute("SELECT COUNT(*) FROM cga_specs WHERE category='Propane';").fetchone()[0] == 0:
        cur.executemany(
            """INSERT INTO cga_specs (category, label, dot_spec, service_psi, test_psi, outside_dia, length_in, ree_limit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
            [
                ("Propane", "20 lb–40 lb Grill / RV Propane (Visual)", "4BA", 240, 480, "12.0", "18.0", None),
                ("Propane", "33 lb Forklift / 100 lb Propane (Visual)", "4BW", 240, 480, "14.5", "48.0", None),
            ],
        )

    cur.executemany(
        "INSERT OR IGNORE INTO pricing_rates (code, description, unit_price, notes, sort_order) VALUES (?, ?, ?, ?, ?);",
        [
            ("HYDRO_STD", "Standard Steel / Aluminum Cylinder Hydro + Visual", 0.0, "", 10),
            ("HYDRO_SCBA", "Carbon / Composite SCBA Cylinder Hydro + Visual", 0.0, "", 20),
            ("HYDRO_PAINTBALL", "Paintball / Airsoft HPA Tank Hydro + Visual", 0.0, "", 30),
            ("VISUAL_ONLY", "Annual Visual Inspection Only (No Hydro)", 0.0, "", 40),
            ("PROPANE_VISUAL", "Propane Cylinder Visual Inspection / Requalification", 0.0, "", 42),
            ("RUSH_FEE", "Priority / Rush Turnaround Service Fee", 0.0, "", 50),
            ("NECK_ORING", "Cylinder Neck / Valve O-Ring Replacement", 0.0, "", 60),
            ("VALVE_RELIEF", "Relief / Burst Disc Assembly Replacement", 0.0, "", 70),
            ("INTERNAL_CLEAN", "Internal Cylinder Tumble / Cleaning", 0.0, "", 80),
            ("REG_SERVICE", "Valve Removal / Reinstall Service", 0.0, "", 85),
            ("CONDEMNED_FEE", "Condemned / Rejected Cylinder Handling", 0.0, "", 90),
            ("TRUCK_RUN", "Regional Truck Pickup / Delivery Stop", 0.0, "", 95),
        ],
    )
    cur.execute("DELETE FROM pricing_rates WHERE code='AIR_FILL';")

    if cur.execute("SELECT COUNT(*) FROM shop_supplies;").fetchone()[0] == 0:
        cur.executemany(
            """INSERT INTO shop_supplies (category, item_name, spec_note, vendor_name, reorder_url, low_stock, sort_order)
               VALUES (?, ?, ?, ?, ?, 0, ?);""",
            [
                ("Marking & Labels", "Avery 5160 Laser Labels (1\" x 2-5/8\")", "30-up sheet for Standard & 2-up Mini B386 RIN stickers", "Amazon", "https://www.amazon.com/s?k=Avery+5160+Laser+Labels+3000", 10),
                ("Marking & Labels", "2-Part Clear Epoxy / Overlaminate Shields", "49 CFR §180.213 protective coat over composite SCBA/HPA RIN labels", "Amazon", "https://www.amazon.com/s?k=clear+2+part+epoxy+resin+brush+on", 20),
                ("Marking & Labels", "1/4\" & 3/16\" Heavy-Duty Steel Number Stamp Set", "Low-stress neck stamping punches (0-9 & RIN)", "Amazon / McMaster", "https://www.amazon.com/s?k=heavy+duty+steel+number+stamp+set+1%2F4", 30),
                ("Marking & Labels", "Industrial Oil-Based Paint Markers (White/Yellow)", "Highlighting stamped dates on dark steel necks", "Amazon", "https://www.amazon.com/s?k=industrial+paint+marker+white+yellow", 40),
                ("Valves, O-Rings & Lube", "Kano Kroil Penetrating Oil (AeroKroil)", "Loosening seized steel industrial & CO2 cylinder valves", "Amazon", "https://www.amazon.com/s?k=Kano+AeroKroil+Penetrating+Oil", 50),
                ("Valves, O-Rings & Lube", "Christo-Lube MCG 111 Oxygen-Safe Lubricant", "O2/SCBA/Scuba safe thread & O-ring grease (never petroleum)", "Amazon", "https://www.amazon.com/s?k=Christo-Lube+MCG+111+oxygen+grease", 60),
                ("Valves, O-Rings & Lube", "Oxygen-Safe Green PTFE Thread Sealant Tape", "High-density taper thread tape for 3A/3AA steel valves", "Amazon", "https://www.amazon.com/s?k=oxygen+safe+green+ptfe+thread+tape", 70),
                ("Valves, O-Rings & Lube", "Viton & Buna-N Cylinder Neck O-Ring Assortment", "SCBA, AL80 Scuba (-112/-210/-214) & Paintball HPA tank O-rings", "Amazon", "https://www.amazon.com/s?k=scuba+paintball+viton+neck+oring+kit", 80),
                ("Valves, O-Rings & Lube", "HPA & CO2 Burst Disc Assortment (1.8K / 3K / 5K / 7.5K)", "High-pressure & CO2 safety relief burst discs", "Amazon", "https://www.amazon.com/s?k=paintball+burst+disc+1.8k+3k+5k+7.5k", 90),
                ("Stand & Inspection", "Numbered Brass / Plastic Valve Tags (1-100) + Rings", "Reusable numbered brass/plastic valve tags", "Amazon", "https://www.amazon.com/s?k=numbered+brass+valve+tags+with+hooks", 100),
                ("Stand & Inspection", "Flexible LED Cylinder Bore Inspection Light Wand", "Slim internal corrosion/pitting inspection wand for 3AL/3AA/HPA", "Amazon", "https://www.amazon.com/s?k=cylinder+bore+inspection+light+wand+scuba", 110),
                ("Office & Printer", "Laser Printer Toner & Sheet Protectors (3-Ring DOT Binder)", "Form 393-001 5-year DOT compliance binder archival supplies", "Amazon", "https://www.amazon.com/s?k=heavy+duty+sheet+protectors+laser+toner", 120),
            ],
        )

    existing_custs = cur.execute(
        """SELECT customer_name, MAX(customer_phone) AS ph FROM (
               SELECT customer_name, customer_phone FROM cylinder_records
               UNION ALL
               SELECT customer_name, customer_phone FROM dropoffs
               UNION ALL
               SELECT customer_name, customer_phone FROM schedule_jobs
           ) WHERE TRIM(customer_name) != '' GROUP BY customer_name;"""
    ).fetchall()
    dept_keywords = ("VFD", " FD", "FIRE", "EMS", "AMBULANCE", "RESCUE", "BORO", "TOWNSHIP", "TWP", "MUNICIPAL")
    for ec in existing_custs:
        cname = ec["customer_name"].strip()
        up = cname.upper()
        is_dept = any(k in up for k in dept_keywords)
        acct = "FIRE_EMS" if is_dept else "WALK_IN"
        terms = "NET_30" if is_dept else "DUE_AT_PICKUP"
        cur.execute(
            """INSERT OR IGNORE INTO customers
               (customer_name, account_type, phone, payment_terms, tax_exempt)
               VALUES (?, ?, ?, ?, ?);""",
            (cname, acct, ec["ph"] or "", terms, 1 if is_dept else 0),
        )

    # 3. Seed Individual Role-Based PIN Accounts if empty
    if cur.execute("SELECT COUNT(*) FROM crew_users;").fetchone()[0] == 0:
        legacy_row = cur.execute(
            "SELECT value FROM settings WHERE key IN ('crew_pin', 'shop_pin') AND TRIM(value) != '' ORDER BY CASE key WHEN 'shop_pin' THEN 0 ELSE 1 END LIMIT 1;"
        ).fetchone()
        ryan_pin = str(legacy_row[0]).strip() if legacy_row and legacy_row[0] else "0386"
        admin_pin = "1997" if ryan_pin != "1997" else "9997"
        seed_users = [
            ("Eric Pearce (Admin)", "EP", admin_pin, "SYSTEM_ADMIN", 0),
            ("Ryan Pearce", "RP", ryan_pin, "OWNER", 1),
            ("Kenny", "KN", "1111" if ryan_pin != "1111" else "1112", "CREW", 0),
            ("Twin 1 (Edit Name)", "T1", "2222" if ryan_pin != "2222" else "2223", "CREW", 0),
            ("Twin 2 (Edit Name)", "T2", "3333" if ryan_pin != "3333" else "3334", "CREW", 0),
        ]
        for uname, uinit, upin, urole, usign in seed_users:
            cur.execute(
                """INSERT OR IGNORE INTO crew_users (name, initials, pin, role, can_sign_dot, active)
                   VALUES (?, ?, ?, ?, ?, 1);""",
                (uname, uinit, upin, urole, usign),
            )


    if cur.execute("SELECT COUNT(*) FROM operating_hours;").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO operating_hours (day_of_week, day_name, is_open, open_time, close_time, dock_24_7, note) VALUES (?, ?, ?, ?, ?, 1, ?);",
            [
                (0, "Monday", 1, "09:00", "16:00", ""),
                (1, "Tuesday", 1, "09:00", "14:00", ""),
                (2, "Wednesday", 1, "09:00", "14:00", ""),
                (3, "Thursday", 1, "09:00", "14:00", ""),
                (4, "Friday", 1, "09:00", "16:00", ""),
                (5, "Saturday", 0, "09:00", "14:00", "24/7 Loading Dock Open"),
                (6, "Sunday", 0, "09:00", "14:00", "24/7 Loading Dock Open"),
            ],
        )

    conn.commit()
    conn.close()


def wipe_all_operational_data(conn: sqlite3.Connection, wipe_customers: bool = False) -> None:
    tables = [
        "daily_verifications",
        "cylinder_records",
        "dropoffs",
        "schedule_jobs",
        "shop_day_plans",
        "crew_tasks",
        "time_punches",
        "sms_outbox",
        "invoice_items",
        "invoices",
        "crew_feedback",
    ]
    if wipe_customers:
        tables.append("customers")
    for tbl in tables:
        conn.execute(f"DELETE FROM {tbl};")
    q_marks = ",".join(f"'{t}'" for t in tables)
    conn.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({q_marks});")
    base = conn.execute("SELECT value FROM settings WHERE key='starting_sheet_no';").fetchone()
    if base:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('current_sheet_no', ?);", (base["value"],))
    bump_db_version(conn)
