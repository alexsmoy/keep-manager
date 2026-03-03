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
        AUTH["Auth Module<br/>keep_client.py"]
        SYNC["Sync Engine<br/>sync.py"]
        DB["SQLite Cache<br/>keep_cache.db"]
    end

    subgraph "Google Cloud"
        GAPI["Google Keep API v1"]
        GSA["Service Account<br/>+ Domain-Wide Delegation"]
    end

    UI -->|"HTTP REST"| API
    API -->|"Read/Write"| DB
    API -->|"Delete Notes"| AUTH
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

## Data Flow — Delete Notes

```mermaid
sequenceDiagram
    participant User
    participant Browser as Frontend
    participant Server as FastAPI
    participant DB as SQLite
    participant API as Google Keep API

    User->>Browser: Select notes → Click Delete
    Browser->>Server: POST /api/action/delete {note_ids}
    loop For each note_id
        Server->>API: notes().delete(name=id)
        API-->>Server: 200 OK
        Server->>DB: UPDATE SET trashed=1
    end
    Server->>Server: Queue background sync
    Server-->>Browser: {deleted: N, ids: [...]}
    Browser->>Browser: Reload notes table
```

## Key Design Decisions

1. **Local-first caching** — Google Keep API is slow for searching; SQLite enables instant local queries
2. **Service Account auth** — avoids OAuth consent flow; requires Google Workspace domain
3. **Background sync after delete** — keeps local cache consistent without blocking the UI
4. **Vanilla frontend** — no build step, no dependencies, fast iteration
5. **Regex over SQL LIKE** — SQL LIKE handles basic search, Python regex handles advanced patterns in-memory
