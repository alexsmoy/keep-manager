import sqlite3

DB_PATH = "keep_cache.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    with conn:
        # Notes table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                title TEXT,
                snippet TEXT,
                body TEXT,
                create_time TEXT,
                update_time TEXT,
                trashed BOOLEAN,
                archived BOOLEAN,
                has_attachments BOOLEAN DEFAULT 0,
                saved BOOLEAN DEFAULT 0
            )
        ''')
        
        # Check for migration: add 'saved' column if it doesn't exist
        cursor = conn.execute("PRAGMA table_info(notes)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'saved' not in columns:
            print("Migrating database: adding 'saved' column to 'notes' table")
            conn.execute("ALTER TABLE notes ADD COLUMN saved BOOLEAN DEFAULT 0")

        # Labels table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS labels (
                id TEXT PRIMARY KEY,
                name TEXT
            )
        ''')
        # ... (rest of tables)
        # Note_Labels mapping
        conn.execute('''
            CREATE TABLE IF NOT EXISTS note_labels (
                note_id TEXT,
                label_name TEXT,
                PRIMARY KEY (note_id, label_name),
                FOREIGN KEY (note_id) REFERENCES notes (id) ON DELETE CASCADE
            )
        ''')
        # Saved filters
        conn.execute('''
            CREATE TABLE IF NOT EXISTS filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                regex TEXT
            )
        ''')
        # Pending delete operations queue
        conn.execute('''
            CREATE TABLE IF NOT EXISTS pending_deletes (
                note_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                queued_at TEXT NOT NULL,
                completed_at TEXT,
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                FOREIGN KEY (note_id) REFERENCES notes (id) ON DELETE CASCADE
            )
        ''')
        # Index for faster status queries
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_pending_deletes_status
            ON pending_deletes(status)
        ''')
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
