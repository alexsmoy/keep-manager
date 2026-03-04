# Known Issues & Lessons Learned

> **Purpose**: This document captures mistakes, bugs, and misunderstandings discovered during development.
> It serves as a reference to prevent repeating errors and to help any AI or developer working on this project.
> 
> **Progressive Disclosure**: Load this doc when debugging, reviewing code for correctness, or before modifying core logic.

---

## Issue Log

### ISSUE-001: Delete API is Permanent, Not Trash ⚠️ CRITICAL

**Discovered**: 2026-03-03
**Severity**: Critical
**Status**: Fixed
**Files Affected**: `main.py`, `static/app.js`

#### Problem
The Google Keep API `notes.delete()` method **permanently deletes** a note. It does NOT move it to trash. However, our code and UI treated it as if it were a "move to trash" operation:

- `app.js` line 49: confirm dialog said *"moved to the Trash in Google Keep"* — **incorrect**
- `main.py` line 96: After calling `delete()`, we set `trashed = 1` locally — while this is functionally fine for hiding the note from the UI, the mental model was wrong. The note is gone from Google Keep permanently.

#### Root Cause
Assumed the API `delete` worked like the Keep mobile app's "move to trash" feature. The official API docs clearly state: *"Deletes a note"* with response being *"an empty JSON object"* — no mention of trashing.

#### Fix Applied
- Updated `app.js` confirm dialogs to say **"permanently delete"** instead of "moved to Trash"
- Added a comment in `main.py` clarifying the behavior
- The local DB `trashed = 1` update is kept as a practical mechanism to hide deleted notes from the UI pending the next sync

#### Lesson
> **Always verify API behavior against official documentation before implementing destructive operations.** Do not assume API behavior mirrors the consumer-facing app behavior. See `ai-docs/google-keep-api.md` for the authoritative API reference.

---

### ISSUE-002: Labels Not Available in REST API

**Discovered**: 2026-03-03
**Severity**: Medium
**Status**: Documented (DB tables kept for future use)
**Files Affected**: `db.py`

#### Problem
The database schema includes `labels` and `note_labels` tables, but the Google Keep REST API **does not expose labels**. Labels are a mobile app concept not available through the REST API.

#### Root Cause
Assumed the REST API would mirror all features of the Google Keep app. The API is actually limited to notes, attachments, and permissions only.

#### Resolution
- Tables are kept in the schema (harmless, and may be useful if the API adds label support in the future)
- Documented in `ai-docs/google-keep-api.md` under "What the API Does NOT Support"
- Roadmap updated to remove any label-dependent features that rely on API data

#### Lesson
> **Check API capability boundaries before designing database schemas.** The REST API for a product may only expose a subset of the product's features.

---

### ISSUE-003: `notes.list()` Default Filter Excludes Trashed Notes

**Discovered**: 2026-03-03
**Severity**: Low
**Status**: Documented (current behavior is acceptable)
**Files Affected**: `sync.py`

#### Problem
Per the official docs: *"If no filter is supplied, the trashed filter is applied by default."* This means our `sync.py` call to `notes().list(pageSize=100)` only returns non-trashed notes.

#### Impact
- Notes that users trash via the Keep app or other clients won't show as trashed in our local DB
- Notes we delete via our API (which permanently deletes them) are marked `trashed = 1` locally, and they'll never appear in a subsequent sync, so they'll remain as stale `trashed = 1` rows in the DB indefinitely

#### Resolution
- This is acceptable behavior for now since trashed notes are already hidden from the UI (`WHERE trashed = 0`)
- Added a `filter` parameter comment in `sync.py` for clarity
- Future enhancement: periodic cleanup of stale trashed notes from local DB

#### Lesson
> **Read API documentation for default behaviors, especially on filtering.** Implicit defaults can cause subtle data inconsistencies.

---

### ISSUE-004: Child List Items Not Parsed

**Discovered**: 2026-03-03
**Severity**: Low
**Status**: Fixed
**Files Affected**: `sync.py`

#### Problem
The Google Keep API supports `childListItems[]` on each `ListItem` (one level of nesting). Our sync code only parsed top-level list items, causing nested sub-items to be silently dropped.

#### Fix Applied
- Updated `sync.py` to iterate through `childListItems` within each list item
- Nested items are rendered with indentation: `  [x] Sub-item`

#### Lesson
> **When parsing nested data structures, check the API schema for child/nested fields.** Always parse recursively or to the maximum supported depth.

---

### ISSUE-005: No Note Update/Edit API Method

**Discovered**: 2026-03-03
**Severity**: Medium (design constraint)
**Status**: Documented
**Files Affected**: Roadmap

#### Problem
The Google Keep API does NOT have a PATCH or PUT method for notes. Once a note is created, its content **cannot be modified** through the API. Only permissions can be changed.

#### Impact
- The "Note editing" feature on the roadmap is **impossible** with the current API
- Our app is correctly designed as read-only + delete, which aligns with API capabilities

#### Resolution
- Updated roadmap to mark "Note editing" as blocked by API limitation
- Documented in `ai-docs/google-keep-api.md`

#### Lesson
> **Verify all CRUD operations are available before putting features on the roadmap.** The Keep API supports Create, Read, Delete but NOT Update.

---

### ISSUE-006: No Batch Delete API - Individual Calls Required

**Discovered**: 2026-03-03
**Updated**: 2026-03-04 (Implemented background queue system)
**Severity**: Medium
**Status**: Fixed (background queue with rate limiting)
**Files Affected**: `main.py`, `static/app.js`, `queue_manager.py`, `db.py`

#### Problem
There is **no batch delete endpoint** in the Google Keep API. The API only provides individual `notes.delete()` calls. When users select many notes for mass deletion, the backend must make individual API requests in a loop, which:
- Takes longer (network latency per request)
- Can hit rate limits/quotas with large batches
- Has no transactional guarantee (partial failures are possible)
- Blocks the HTTP response until all deletes complete

#### Root Cause
Initially assumed there would be a batch operation similar to `permissions.batchCreate/batchDelete`. After reviewing the official API documentation, confirmed that only these batch operations exist:
- `notes.permissions.batchCreate`
- `notes.permissions.batchDelete`

There is no `notes.batchDelete` method.

#### Fix Applied (v2 - Background Queue System)

**New Architecture (`queue_manager.py`):**
- Implemented singleton `QueueManager` class with background worker thread
- Token bucket `RateLimiter` enforces GCP quota limits:
  - 72 writes/minute (90/min with 20% safety margin)
  - ~833ms between requests (1.2 requests/second)
- Background worker processes queue continuously
- Retry logic with exponential backoff (3 attempts: 1s, 2s, 4s)
- Handles all Google API error codes gracefully
- Tracks operation status: pending → processing → completed/failed

**Database (`db.py`):**
- Added `pending_deletes` table:
  - `note_id` (PRIMARY KEY)
  - `status` (pending/processing/completed/failed)
  - `queued_at`, `completed_at` timestamps
  - `attempts` counter
  - `last_error` message
- Index on `status` for fast queries

**Backend (`main.py`):**
- Simplified `/api/action/delete` endpoint:
  - Immediately marks notes as trashed in UI
  - Enqueues notes to background queue
  - Returns immediately (non-blocking)
  - Response includes queued count and note IDs
- New `/api/queue/status` endpoint:
  - Returns real-time queue statistics
  - Shows pending, processing, completed, failed counts
  - Lists recent failures
  - Indicates worker thread health

**Frontend (`static/app.js`, `templates/index.html`, `static/style.css`):**
- **Immediate UI feedback**: Notes disappear instantly when deleted (optimistic update)
- **Queue status indicator**: Shows "Processing N deletion(s)" in header
- **Status polling**: Fetches queue status every 2 seconds
- **Non-blocking UI**: User can continue searching, filtering, deleting while queue processes
- **Error notifications**: Modal shows failures from background queue
- **Informative modal**: For large batches (>5 notes), explains background processing
- Pulsing animation on queue status indicator

#### Impact & Limitations (After Queue System)
- ✅ **Scales to any batch size** — queue processes deletions at safe rate (72/min)
- ✅ **Non-blocking UI** — user can continue working while deletions process
- ✅ **Respects quotas** — rate limiting ensures we stay within GCP limits with 20% margin
- ✅ **Resilient to failures** — retries, error tracking, and status reporting
- ⚠️ **No transactional guarantee** — deletions are still individual API calls
- ⚠️ **Background processing** — large batches may take several minutes
- ⚠️ **Permanent deletion** — no undo once queue processes the note
- ℹ️ **Database overhead** — tracking table adds minimal storage cost

**Performance**:
- Small batches (< 10 notes): ~10 seconds
- Medium batches (50 notes): ~42 seconds
- Large batches (500 notes): ~7 minutes
- Very large batches (1000 notes): ~14 minutes

#### Lesson
> **When APIs lack batch operations and have quota limits**:
> 1. ✅ Implement background queue with rate limiting
> 2. ✅ Decouple UI from backend processing (optimistic updates)
> 3. ✅ Track operation status in database
> 4. ✅ Provide real-time status updates to users
> 5. ✅ Handle all error cases gracefully with retry logic
> 6. ✅ Calculate safe request rates based on published quotas (with margin)
> 7. ✅ Use thread-safe queue for concurrent operations
> 8. ✅ Implement worker health monitoring
>
> **Key architectural principle**: For rate-limited APIs, always process operations asynchronously with proper queue management. Never block the user's experience waiting for slow API operations.

---

### ISSUE-007: Google Keep API Quotas Not Publicly Documented

**Discovered**: 2026-03-03
**Severity**: Low
**Status**: Documented (mitigation in place)
**Files Affected**: `main.py`

#### Problem
Google does not publish specific rate limits or quota information for the Google Keep API. This makes it difficult to:
- Know how many requests per second/minute/day are allowed
- Predict when quota errors will occur
- Optimize batch sizes for mass operations

#### Impact
Without knowing exact limits, we must be conservative:
- Added arbitrary 50ms delay between delete requests (20 req/sec max)
- Cannot provide accurate estimates to users for large deletion batches
- Users may encounter quota errors unpredictably

#### Resolution
- Implemented retry logic with exponential backoff for all API calls
- Handle 429 and 403 quota errors explicitly
- Show clear user-facing messages when quota is hit
- Recommend users delete in smaller batches (50-100 notes at a time)
- Monitor server logs for quota patterns to adjust delays if needed

#### Lesson
> **When working with APIs that don't publish quotas**:
> 1. Implement conservative rate limiting from the start
> 2. Add comprehensive error handling for quota errors (429, 403)
> 3. Provide user guidance on recommended batch sizes
> 4. Log quota errors to identify patterns over time
> 5. Consider adding configurable rate limits for power users

---

## Issue Template

When adding new issues, use this format:

```markdown
### ISSUE-NNN: Brief Title

**Discovered**: YYYY-MM-DD  
**Severity**: Critical | High | Medium | Low  
**Status**: Open | Fixed | Documented | Won't Fix  
**Files Affected**: `file1.py`, `file2.js`

#### Problem
What went wrong and how it manifested.

#### Root Cause
Why it happened.

#### Fix Applied / Resolution
What was done to fix or mitigate it.

#### Lesson
> Key takeaway to prevent future occurrences.
```
