# Architecture — Keep Manager

## System Overview

Keep Manager is a local-first web application that caches Google Keep notes into a SQLite database for fast searching, filtering, and bulk management operations.

```mermaid
graph TB
    subgraph "User's Browser"
        UI["Frontend<br/>HTML + CSS + JS"]
    end

    subgraph "Local Server"
        API["FastAPI Backend<br/>main.py"]
        QUEUE["Queue Manager<br/>queue_manager.py<br/>(Background Thread)"]
        AUTH["Auth Module<br/>keep_client.py"]
        SYNC["Sync Engine<br/>sync.py"]
        DB["SQLite Cache<br/>keep_cache.db"]
    end

    subgraph "Google Cloud"
        GAPI["Google Keep API v1<br/>(Rate Limited)"]
        GSA["Service Account<br/>+ Domain-Wide Delegation"]
    end

    UI -->|"HTTP REST"| API
    UI -->|"Poll Status"| API
    API -->|"Read/Write"| DB
    API -->|"Enqueue Deletes"| QUEUE
    QUEUE -->|"Rate-Limited Deletes"| AUTH
    QUEUE -->|"Update Status"| DB
    AUTH -->|"OAuth2 JWT"| GSA
    GSA -->|"Impersonate User"| GAPI
    SYNC -->|"Pull Notes"| GAPI
    SYNC -->|"Upsert"| DB
    AUTH -->|"Credentials"| SYNC
```

## Component Responsibilities

### `main.py` — FastAPI Application
- Serves the single-page frontend (`templates/index.html`)
- Mounts static files (`/static/`)
- Exposes REST API endpoints for notes and filters
- Handles mass-delete operations with background sync

### `keep_client.py` — Authentication Module
- Loads Google Service Account credentials from `credentials.json`
- Applies domain-wide delegation via user impersonation (`with_subject`)
- Returns an authenticated `googleapiclient` service object

### `sync.py` — Sync Engine
- Paginates through all notes via `service.notes().list()`
- Parses both text notes and checklist notes
- Upserts notes into the local SQLite database
- Handles attachment detection

### `db.py` — Database Layer
- Manages SQLite connection with `Row` factory for dict-like access
- Defines the schema: `notes`, `labels`, `note_labels`, `filters`
- Provides `init_db()` for first-time setup

### Frontend (`templates/` + `static/`)
- Single-page app with split-pane layout
- Left pane: searchable/filterable notes table with checkboxes
- Right pane: read-only note preview with inline delete
- Dark theme with Inter font and violet accent colors
- Polls `/api/queue/status` every 2 seconds during active deletions
- Shows queue status indicator in header

### `queue_manager.py` — Background Queue System ⭐ NEW

**Purpose**: Asynchronous deletion processing with rate limiting to respect GCP quotas.

**Components**:
1. **`RateLimiter`** class:
   - Token bucket implementation
   - Enforces 72 writes/minute (90/min with 20% safety margin)
   - Thread-safe with mutex lock
   - Calculates minimum interval between requests (~833ms)

2. **`QueueManager`** singleton class:
   - Manages an asyncio.Queue for pending deletions
   - Runs background worker thread (daemon)
   - Processes queue with rate limiting
   - Tracks statistics (queued, processed, succeeded, failed)
   - Updates `pending_deletes` table in database

**Worker Thread Flow**:
```
1. Get next note from queue (blocking, 1s timeout)
2. Update DB: status='processing'
3. Enforce rate limit (wait if needed)
4. Attempt deletion with retry logic (3 attempts, exponential backoff)
5. Update DB: status='completed' or 'failed'
6. Update statistics
7. Repeat until queue is empty
```

**Error Handling**:
- **404**: Treat as success (already deleted)
- **429/403 quota**: Exponential backoff (1s, 2s, 4s), then fail
- **403 permission**: Immediate failure with clear message
- **Other errors**: Immediate failure with error code/message

## Data Flow — Note Sync

```mermaid
sequenceDiagram
    participant Script as sync.py
    participant Auth as keep_client.py
    participant API as Google Keep API
    participant DB as SQLite (keep_cache.db)

    Script->>Auth: get_keep_service(email)
    Auth->>Auth: Load credentials.json
    Auth->>Auth: Apply user impersonation
    Auth-->>Script: Authenticated service

    loop For each page of notes
        Script->>API: notes().list(pageSize=100)
        API-->>Script: Notes page + nextPageToken
        loop For each note
            Script->>Script: Parse body (text or checklist)
            Script->>Script: Generate snippet (first 150 chars)
            Script->>DB: UPSERT into notes table
        end
    end

    Script-->>Script: Print sync summary
```

## Data Flow — User Search

```mermaid
sequenceDiagram
    participant User
    participant Browser as Frontend (app.js)
    participant Server as FastAPI (main.py)
    participant DB as SQLite

    User->>Browser: Enter search + optional regex
    Browser->>Server: GET /api/notes?search=...&regex=...
    Server->>DB: SELECT with LIKE filter
    DB-->>Server: Matching rows
    alt Regex provided
        Server->>Server: In-memory regex filter
    end
    Server-->>Browser: JSON {notes: [...]}
    Browser->>Browser: Render table rows
    Browser-->>User: Display filtered notes
```

## Data Flow — Delete Notes (Queue-Based)

```mermaid
sequenceDiagram
    participant User
    participant Browser as Frontend
    participant Server as FastAPI (main.py)
    participant Queue as QueueManager (Background Thread)
    participant DB as SQLite
    participant API as Google Keep API

    Note over User,API: Phase 1: Immediate UI Response
    User->>Browser: Select notes → Click Delete
    Browser->>Server: POST /api/action/delete {note_ids: [A, B, C]}
    Server->>DB: UPDATE notes SET trashed=1 WHERE id IN (A,B,C)
    Server->>DB: INSERT INTO pending_deletes (A,B,C) status='pending'
    Server->>Queue: Enqueue [A, B, C]
    Server-->>Browser: {queued: 3, note_ids: [A,B,C]}
    Browser->>DB: GET /api/notes (notes A,B,C excluded - trashed=1)
    Browser-->>User: Notes immediately disappear from UI

    Note over User,API: Phase 2: Background Processing (72/minute)
    loop Every 2 seconds
        Browser->>Server: GET /api/queue/status
        Server->>DB: SELECT COUNT(*) FROM pending_deletes WHERE status='pending'
        Server-->>Browser: {queue_size: N, ...}
        Browser-->>User: "Processing N deletions" indicator
    end

    Note over Queue,API: Background Worker Thread
    Queue->>Queue: Get next note (A) from queue
    Queue->>DB: UPDATE pending_deletes SET status='processing' WHERE note_id=A
    Queue->>Queue: Rate limit wait (~833ms)
    Queue->>API: notes().delete(name=A)
    alt Success
        API-->>Queue: 200 OK
        Queue->>DB: UPDATE pending_deletes SET status='completed'
    else Error 404
        API-->>Queue: 404 Not Found
        Queue->>DB: UPDATE pending_deletes SET status='completed' (already deleted)
    else Error 429/403 Quota
        API-->>Queue: 429 Rate Limit
        Queue->>Queue: Exponential backoff (1s, 2s, 4s)
        Queue->>API: Retry up to 3 times
        alt Retry succeeds
            API-->>Queue: 200 OK
            Queue->>DB: UPDATE pending_deletes SET status='completed'
        else Retry fails
            Queue->>DB: UPDATE pending_deletes SET status='failed', last_error='Quota exceeded'
        end
    end
    Queue->>Queue: Repeat for notes B, C, ...

    Note over Browser,User: User notified of failures via poll
    Browser->>Server: GET /api/queue/status
    Server->>DB: SELECT * FROM pending_deletes WHERE status='failed'
    Server-->>Browser: {recent_failures: [{note_id: X, error: "..."}]}
    Browser-->>User: Error modal with details
```

## Key Design Decisions

1. **Local-first caching** — Google Keep API is slow for searching; SQLite enables instant local queries
2. **Service Account auth** — avoids OAuth consent flow; requires Google Workspace domain
3. **Background queue for deletes** ⭐ — Respects GCP quotas (72 writes/min) while keeping UI responsive
4. **Optimistic UI updates** — Notes disappear immediately; actual deletion happens asynchronously
5. **Background sync after delete** — keeps local cache consistent without blocking the UI
6. **Vanilla frontend** — no build step, no dependencies, fast iteration
7. **Regex over SQL LIKE** — SQL LIKE handles basic search, Python regex handles advanced patterns in-memory
8. **Rate limiting with margin** — 20% safety margin (72 vs 90 req/min) prevents quota errors
9. **Thread-based queue** — Uses Python threading (not asyncio) for compatibility with sync API client
10. **Status polling** — Frontend polls queue status every 2 seconds for real-time progress

## GCP Quota Management

**Published Limits** (from GCP Console):
- 90 read requests/minute
- 90 write requests/minute (includes DELETE)
- 30 create requests/minute

**Our Implementation** (with 20% safety margin):
- Reads: 72/minute (unused - only used during sync)
- Writes: **72/minute = 1.2/second = ~833ms interval**
- Creates: 24/minute (unused - no create functionality)

**Rate Limiter Strategy**:
```python
min_interval = 60.0 / 72  # 0.833 seconds
# Enforced via token bucket in RateLimiter class
# Prevents bursting; guarantees safe spacing
```
