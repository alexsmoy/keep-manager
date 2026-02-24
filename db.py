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
                archived BOOLEAN
            )
        ''')
        # Labels table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS labels (
                id TEXT PRIMARY KEY,
                name TEXT
            )
        ''')
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
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
