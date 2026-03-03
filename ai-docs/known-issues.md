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
