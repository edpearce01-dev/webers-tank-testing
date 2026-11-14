import os
import secrets
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("WEBER_DB_PATH", str(Path(__file__).parent / "webers_hydro.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_column(cur: sqlite3.Cursor, table: str, column: str, col_def: str) -> None:
    existing = {row[1] for row in cur.execute(f"PRAGMA table_info({table});").fetchall()}
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def};")


def bump_db_version(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO settings (key, value) VALUES ('db_version', '1')
           ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT);"""
    )


def wipe_all_operational_data(conn: sqlite3.Connection) -> None:
    """Clears all test/operational records while preserving keys, crew, and settings."""
    cur = conn.cursor()
    cur.execute("DELETE FROM invoice_items;")
    cur.execute("DELETE FROM invoices;")
    cur.execute("DELETE FROM cylinder_records;")
    cur.execute("DELETE FROM daily_verifications;")
    cur.execute(
        "DELETE FROM sqlite_sequence WHERE name IN ('invoice_items', 'invoices', 'cylinder_records', 'daily_verifications');"
    )
    bump_db_version(conn)


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS crew_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL UNIQUE,
            initials TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'Inspector',
            active INTEGER DEFAULT 1
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS calibration_targets (
            serial_number TEXT NOT NULL,
            pressure_psi INTEGER NOT NULL,
            expected_cc REAL NOT NULL,
            recal_date TEXT DEFAULT '7500# Active',
            PRIMARY KEY (serial_number, pressure_psi)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT DEFAULT '',
            test_date TEXT NOT NULL,
            test_time TEXT NOT NULL,
            jacket_type TEXT NOT NULL CHECK(jacket_type IN ('Short Jacket', 'Tall Jacket')),
            calibrated_serial TEXT NOT NULL,
            max_target_psi INTEGER DEFAULT 0,
            target_pressure_psi INTEGER NOT NULL,
            expected_cc REAL NOT NULL,
            actual_total_cc REAL NOT NULL,
            permanent_cc REAL NOT NULL,
            elastic_cc REAL NOT NULL,
            percent_deviation REAL NOT NULL,
            passed INTEGER NOT NULL,
            operator_name TEXT NOT NULL,
            operator_initials TEXT DEFAULT 'RP',
            synced INTEGER DEFAULT 0
        );
    """)
    ensure_column(cur, "daily_verifications", "run_id", "TEXT DEFAULT ''")
    ensure_column(cur, "daily_verifications", "max_target_psi", "INTEGER DEFAULT 0")
    ensure_column(cur, "daily_verifications", "operator_initials", "TEXT DEFAULT 'RP'")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cga_specs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            label TEXT NOT NULL,
            dot_spec TEXT NOT NULL,
            service_psi INTEGER NOT NULL,
            test_psi INTEGER NOT NULL,
            outside_dia TEXT NOT NULL,
            length_in TEXT NOT NULL,
            ree_limit REAL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pricing_rates (
            code TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            unit_price REAL NOT NULL DEFAULT 0.0,
            notes TEXT DEFAULT ''
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT NOT NULL UNIQUE,
            invoice_date TEXT NOT NULL,
            work_date TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            subtotal REAL NOT NULL DEFAULT 0.0,
            tax_rate REAL NOT NULL DEFAULT 0.0,
            tax_amount REAL NOT NULL DEFAULT 0.0,
            total_amount REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'DRAFT',
            created_by TEXT NOT NULL DEFAULT 'RP',
            notes TEXT DEFAULT '',
            synced INTEGER DEFAULT 0
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            record_id INTEGER,
            description TEXT NOT NULL,
            qty REAL NOT NULL DEFAULT 1,
            unit_price REAL NOT NULL DEFAULT 0.0,
            line_total REAL NOT NULL DEFAULT 0.0,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cylinder_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_date TEXT NOT NULL,
            sheet_no INTEGER DEFAULT 8095,
            customer_name TEXT NOT NULL,
            serial_number TEXT NOT NULL,
            valve_tag TEXT DEFAULT '',
            valve_reinstalled INTEGER DEFAULT 0,
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
            service_mode TEXT NOT NULL CHECK(service_mode IN ('HYDRO_AND_VISUAL', 'VISUAL_ONLY')),
            status TEXT NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED', 'COMPLETED')),
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
            visual_pass INTEGER DEFAULT 1,
            hydro_disposition TEXT DEFAULT 'A',
            visual_disposition TEXT DEFAULT 'OK',
            intake_by TEXT DEFAULT 'RP',
            operator_name TEXT NOT NULL DEFAULT 'Ryan Pearce',
            operator_initials TEXT NOT NULL DEFAULT 'RP',
            invoice_id INTEGER,
            notes TEXT DEFAULT '',
            synced INTEGER DEFAULT 0,
            FOREIGN KEY (verification_id) REFERENCES daily_verifications(id)
        );
    """)
    ensure_column(cur, "cylinder_records", "sheet_no", "INTEGER DEFAULT 8095")
    ensure_column(cur, "cylinder_records", "valve_tag", "TEXT DEFAULT ''")
    ensure_column(cur, "cylinder_records", "valve_reinstalled", "INTEGER DEFAULT 0")
    ensure_column(cur, "cylinder_records", "intake_by", "TEXT DEFAULT 'RP'")
    ensure_column(cur, "cylinder_records", "operator_initials", "TEXT DEFAULT 'RP'")
    ensure_column(cur, "cylinder_records", "invoice_id", "INTEGER")

    cur.executemany(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?);",
        [
            ("company_name", "Weber's Tank Testing"),
            ("dot_rin", "B386"),
            ("tc_reg", ""),
            ("phone", "724-266-8008"),
            ("operator", "Ryan Pearce"),
            ("manager", "Ryan Pearce"),
            ("calibrated_serial", "1SF11904"),
            ("starting_sheet_no", "8095"),
            ("gemini_api_key", ""),
            ("sheets_webhook_url", ""),
            ("shop_pin", "0386"),
            ("testing_mode", "1"),
            ("session_secret", secrets.token_hex(32)),
            ("db_version", "1"),
        ],
    )

    if cur.execute("SELECT COUNT(*) FROM crew_members;").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO crew_members (full_name, initials, role) VALUES (?, ?, ?);",
            [
                ("Ryan Pearce", "RP", "Facility Manager / Primary Sign-Off"),
                ("Eric Pearce", "EP", "Intake / Visual Helper"),
                ("Adam Pearce", "AP", "Billing / Visual Helper"),
            ],
        )

    if cur.execute("SELECT COUNT(*) FROM pricing_rates;").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO pricing_rates (code, description, unit_price, notes) VALUES (?, ?, ?, ?);",
            [
                ("HYDRO_STD", "Standard Hydrostatic + Visual Test", 0.0, "Steel / Aluminum DOT cylinder"),
                ("HYDRO_SCBA", "Carbon Fiber / Special Permit Hydro Test", 0.0, "SCBA / SP composite cylinder"),
                ("VISUAL_ONLY", "Visual Inspection Only (Shop or Road)", 0.0, "7-point visual requalification"),
                ("VALVE_RELIEF", "Relief Valve Replacement", 0.0, "Safety relief device changed"),
                ("CONDEMNED_FEE", "Condemned / Rejected Cylinder Handling", 0.0, "Failed cylinder processing"),
            ],
        )

    cur.execute("DELETE FROM calibration_targets WHERE serial_number = 'X22176';")
    active_serial = cur.execute(
        "SELECT value FROM settings WHERE key = 'calibrated_serial';"
    ).fetchone()["value"]

    if cur.execute("SELECT COUNT(*) FROM calibration_targets;").fetchone()[0] == 0:
        cur.executemany(
            "INSERT OR IGNORE INTO calibration_targets (serial_number, pressure_psi, expected_cc) VALUES (?, ?, ?);",
            [
                (active_serial, 2000, 34.2),
                (active_serial, 2500, 42.8),
                (active_serial, 3000, 51.4),
                (active_serial, 4000, 68.6),
                (active_serial, 5000, 86.2),
                (active_serial, 6000, 103.6),
                (active_serial, 7000, 121.8),
                (active_serial, 7500, 130.4),
            ],
        )

    # Refresh expanded cylinder reference table (SCBA, CO2, Medical, Industrial) for OD x Length auto-fill
    if cur.execute("SELECT COUNT(*) FROM cga_specs;").fetchone()[0] < 32:
        cur.execute("DELETE FROM cga_specs;")
        cur.executemany(
            """INSERT INTO cga_specs
               (category, label, dot_spec, service_psi, test_psi, outside_dia, length_in, ree_limit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
            [
                ("SCBA / Fire", "Escape / Pony (SP14387)", "SP14387", 4500, 7500, "4.3", "8", 17.4),
                ("SCBA / Fire", "45-Min Composite (SP14157)", "SP14157", 3442, 5250, "7.3", "23", 75.3),
                ("SCBA / Fire", "30-Min Composite (4500#)", "SP10915", 4500, 7500, "5.4", "18.5", None),
                ("SCBA / Fire", "45-Min Composite (4500#)", "SP10945", 4500, 7500, "6.3", "21.5", None),
                ("SCBA / Fire", "SCBA Steel 30-Min", "3AA", 2250, 3750, "6.8", "25 1/2", None),
                ("SCBA / Fire", "SCBA Aluminum 30-Min", "3AL", 2216, 3700, "6.8", "20.5", None),
                ("SCBA / Fire", "SCUBA / Cascade 80cf", "3AL", 3000, 5000, "7.3", "26", None),
                ("CO2 / Beverage", "5 lb CO2 Aluminum", "3AL", 1800, 3000, "5.25", "14", None),
                ("CO2 / Beverage", "10 lb CO2 Aluminum", "3AL", 1800, 3000, "7", "16 3/4", None),
                ("CO2 / Beverage", "20 lb CO2 Aluminum", "3AL", 1800, 3000, "8", "23", None),
                ("CO2 / Extinguisher", "15 lb CO2 Steel", "3AA", 1800, 3000, "7", "21 1/2", None),
                ("CO2 / Extinguisher", "20 lb CO2 Steel", "3AA", 1800, 3000, "7.8", "24", None),
                ("Medical", "B", "3AA", 2015, 3360, "3 1/4", "13", 7.2),
                ("Medical", "D", "3AA", 2015, 3360, "4 1/4", "16 3/4", 14.5),
                ("Medical", "E", "3AA", 2015, 3360, "4 1/4", "25 3/4", 23.1),
                ("Medical", "M (3A)", "3A", 2015, 3360, "7", "43", 88.0),
                ("Medical", "M (3AA)", "3AA", 2015, 3360, "7", "43", 110.0),
                ("Medical", "G (3AA)", "3AA", 2015, 3360, "8 1/2", "51", 200.0),
                ("Medical", "H (2015)", "3AA", 2015, 3360, "9", "51", 216.0),
                ("Medical", "H (2265)", "3AA", 2265, 3775, "9", "51", 216.0),
                ("Industrial", "20 cu ft", "3AA", 2015, 3360, "5 1/4", "14 7/8", 19.8),
                ("Industrial", "40 cu ft", "3AA", 2015, 3360, "6 3/4", "18 1/2", 41.3),
                ("Industrial", "55 cu ft", "3AA", 2015, 3360, "5 3/4", "32", 57.0),
                ("Industrial", "80 cu ft (2015)", "3AA", 2015, 3360, "7", "32 1/2", 80.0),
                ("Industrial", "80 cu ft (2215)", "3AA", 2215, 3700, "7 1/8", "30", 73.0),
                ("Industrial", "110 cu ft", "3AA", 2015, 3360, "7", "43", 110.0),
                ("Industrial", "125 cu ft", "3AA", 2265, 3775, "7", "43", 110.0),
                ("Industrial", "150 cu ft", "3AA", 2015, 3360, "7 3/8", "46 1/8", 133.0),
                ("Industrial", "200 cu ft (3A-1800)", "3A", 1800, 3000, "9", "51", 179.0),
                ("Industrial", "220 cu ft", "3AA", 2015, 3360, "9", "51", 216.0),
                ("Industrial", "250 cu ft (2265)", "3AA", 2265, 3775, "9", "51", 216.0),
                ("Industrial", "250 cu ft (2400)", "3AA", 2400, 4000, "9", "51", 227.0),
                ("Industrial", "300 cu ft", "3AA", 2400, 4000, "9 1/4", "55", 251.0),
                ("Industrial", "400 cu ft", "3AA", 3600, 6000, "9 1/4", "51", 230.0),
                ("Industrial", "435 cu ft", "3AA", 6000, 10000, "9 1/2", "51", 209.0),
                ("Industrial", "500 cu ft", "3AA", 4500, 7500, "9 3/8", "51", 226.0),
            ],
        )

    # One-time automatic clean slate to strip all earlier test runs upon deploying Phase 3
    already_cleaned = cur.execute("SELECT value FROM settings WHERE key='clean_slate_v3';").fetchone()
    if not already_cleaned:
        wipe_all_operational_data(conn)
        cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('clean_slate_v3', '1');")

    conn.commit()
    conn.close()