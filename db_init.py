# db_init.py
import sqlite3

conn = sqlite3.connect("resources.db")
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    filename TEXT,
    course_code TEXT,
    department TEXT,
    uploader TEXT,
    uploaded_at TEXT
)
""")
conn.commit()
conn.close()
print("resources.db initialized.")