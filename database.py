import sqlite3
import math
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL

def get_connection():
    # PostgreSQL vs SQLite connection handler
    if DATABASE_URL.startswith("sqlite"):
        db_file = DATABASE_URL.replace("sqlite:///", "")
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"
    else:
        # For Render PostgreSQL
        conn = psycopg2.connect(DATABASE_URL, sslmode='prefer')
        return conn, "postgres"

def init_db():
    conn, db_type = get_connection()
    cursor = conn.cursor()
    
    # Auto-increment dynamic query
    auto_inc = "AUTOINCREMENT" if db_type == "sqlite" else "SERIAL"
    
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS vendors (
            telegram_id BIGINT PRIMARY KEY,
            name TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            status TEXT DEFAULT 'active'
        )
    ''')
    
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS riders (
            telegram_id BIGINT PRIMARY KEY,
            name TEXT NOT NULL,
            lat REAL DEFAULT 0.0,
            lon REAL DEFAULT 0.0,
            is_online INTEGER DEFAULT 0,
            is_busy INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active'
        )
    ''')

    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS orders (
            order_id {auto_inc} PRIMARY KEY,
            vendor_id BIGINT,
            rider_id BIGINT,
            content TEXT,
            status TEXT DEFAULT 'pending',
            rejected_riders TEXT DEFAULT ''
        )
    ''')
    
    conn.commit()
    conn.close()

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0 # Earth Radius in KM
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_nearest_available_rider(vendor_lat, vendor_lon, radius_km, excluded_ids):
    conn, db_type = get_connection()
    if db_type == "sqlite":
        cursor = conn.cursor()
    else:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
    cursor.execute("SELECT telegram_id, lat, lon FROM riders WHERE is_online = 1 AND is_busy = 0 AND status = 'active'")
    riders = cursor.fetchall()
    conn.close()

    nearest_rider = None
    min_dist = float('inf')

    for r in riders:
        r_id = r['telegram_id']
        if r_id in excluded_ids:
            continue
        if r['lat'] == 0.0 and r['lon'] == 0.0:
            continue
            
        dist = calculate_distance(vendor_lat, vendor_lon, r['lat'], r['lon'])
        if dist <= radius_km and dist < min_dist:
            min_dist = dist
            nearest_rider = r_id

    return nearest_rider
