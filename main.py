import os
import re
import time
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Body, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
from googleapiclient.errors import HttpError

from db import get_db
from keep_client import get_keep_service
from sync import sync_notes

# Load environment variables from .env file
load_dotenv()

app = FastAPI(title="Google Keep Manager")

# Ensure static and templates directories exist
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    index_path = os.path.join("templates", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Welcome to Google Keep Manager! (Frontend not yet created)"}

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

# --- API Endpoints ---

class NoteModel(BaseModel):
    id: str
    title: str
    snippet: str
    body: str
    has_attachments: bool

@app.get("/api/notes")
async def get_notes(search: str = "", regex: str = ""):
    conn = get_db()
    with conn:
        cursor = conn.cursor()
        if search:
            query = "SELECT id, title, snippet, body, has_attachments FROM notes WHERE trashed = 0 AND (title LIKE ? OR body LIKE ?)"
            val = f"%{search}%"
            cursor.execute(query, (val, val))
        else:
            query = "SELECT id, title, snippet, body, has_attachments FROM notes WHERE trashed = 0"
            cursor.execute(query)
        
        rows = cursor.fetchall()
        
    notes = [dict(row) for row in rows]
    
    # In-memory regex filtering
    if regex:
        try:
            pattern = re.compile(regex, re.IGNORECASE)
            filtered_notes = []
            for note in notes:
                if pattern.search(note['title']) or pattern.search(note['body']):
                    filtered_notes.append(note)
            notes = filtered_notes
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"Invalid regular expression: {str(e)}")
            
    return {"notes": notes}

class DeleteRequest(BaseModel):
    note_ids: List[str]

class DeleteResponse(BaseModel):
    success: bool
    deleted: int
    failed: int
    success_ids: List[str]
    errors: List[Dict[str, str]]
    quota_exceeded: bool
    warning: Optional[str] = None

def delete_note_with_retry(service, note_id: str, max_retries: int = 3) -> tuple[bool, Optional[str]]:
    """
    Delete a single note with exponential backoff retry logic.

    Returns:
        (success: bool, error_message: Optional[str])
    """
    for attempt in range(max_retries):
        try:
            service.notes().delete(name=note_id).execute()
            return (True, None)
        except HttpError as e:
            error_code = e.resp.status
            error_reason = e.error_details if hasattr(e, 'error_details') else str(e)

            # Quota exceeded errors (429 or specific 403 quota errors)
            if error_code == 429 or (error_code == 403 and 'quota' in str(e).lower()):
                if attempt < max_retries - 1:
                    # Exponential backoff: 1s, 2s, 4s
                    wait_time = 2 ** attempt
                    print(f"Quota/rate limit hit for {note_id}, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    return (False, f"Quota exceeded after {max_retries} retries")

            # Resource not found (note already deleted)
            elif error_code == 404:
                print(f"Note {note_id} not found (already deleted?)")
                return (True, None)  # Consider this a success

            # Permission denied
            elif error_code == 403:
                return (False, "Permission denied - check service account permissions")

            # Other errors
            else:
                return (False, f"HTTP {error_code}: {str(e)[:100]}")

        except Exception as e:
            # Non-HTTP errors
            return (False, f"Unexpected error: {str(e)[:100]}")

    return (False, "Max retries exceeded")

@app.post("/api/action/delete", response_model=DeleteResponse)
async def mass_delete(req: DeleteRequest, background_tasks: BackgroundTasks):
    """
    Delete multiple notes with graceful error handling.

    NOTE: Google Keep API has NO batch delete method - we must delete notes individually.
    This endpoint handles rate limiting, retries, and returns detailed error information.
    """
    if not req.note_ids:
        return DeleteResponse(
            success=True,
            deleted=0,
            failed=0,
            success_ids=[],
            errors=[],
            quota_exceeded=False
        )

    service = get_keep_service()
    if not service:
        raise HTTPException(
            status_code=500,
            detail="Failed to initialize Keep Service. Check credentials and .env configuration."
        )

    conn = get_db()
    success_ids = []
    errors = []
    quota_exceeded = False

    # NOTE: Google Keep API notes.delete() is a PERMANENT deletion, not a "move to trash".
    # There is no undo. See ai-docs/google-keep-api.md and ai-docs/known-issues.md (ISSUE-001).

    # Add small delay between requests to avoid hitting rate limits (50ms per request)
    for i, note_id in enumerate(req.note_ids):
        if i > 0:
            time.sleep(0.05)  # 50ms delay = max 20 requests/second

        success, error_msg = delete_note_with_retry(service, note_id)

        if success:
            # Mark as trashed locally to hide from UI pending next sync
            try:
                with conn:
                    conn.execute("UPDATE notes SET trashed = 1 WHERE id = ?", (note_id,))
                success_ids.append(note_id)
            except Exception as db_error:
                print(f"Database error for {note_id}: {db_error}")
                # Still count as success since API deletion worked
                success_ids.append(note_id)
        else:
            errors.append({
                "note_id": note_id,
                "error": error_msg
            })

            # Check if this is a quota error
            if error_msg and "quota" in error_msg.lower():
                quota_exceeded = True
                print(f"Quota exceeded - stopping deletion batch at {i + 1}/{len(req.note_ids)} notes")
                # Add remaining notes as failed
                for remaining_id in req.note_ids[i + 1:]:
                    errors.append({
                        "note_id": remaining_id,
                        "error": "Skipped due to quota limit"
                    })
                break

    # Trigger a background sync to keep local DB clean with remote state
    background_tasks.add_task(sync_notes)

    warning = None
    if quota_exceeded:
        warning = "API quota limit reached. Some notes were not deleted. Please wait a few minutes and try again."
    elif errors and not quota_exceeded:
        warning = f"{len(errors)} note(s) failed to delete. Check error details."

    return DeleteResponse(
        success=len(success_ids) > 0 or len(errors) == 0,
        deleted=len(success_ids),
        failed=len(errors),
        success_ids=success_ids,
        errors=errors,
        quota_exceeded=quota_exceeded,
        warning=warning
    )

class FilterModel(BaseModel):
    name: str
    regex: str

@app.get("/api/filters")
async def get_filters():
    conn = get_db()
    with conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, regex FROM filters")
        rows = cursor.fetchall()
    return {"filters": [dict(row) for row in rows]}

@app.post("/api/filters")
async def save_filter(f: FilterModel):
    conn = get_db()
    with conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO filters (name, regex) VALUES (?, ?)", (f.name, f.regex))
        inserted_id = cursor.lastrowid
    return {"success": True, "id": inserted_id, "name": f.name, "regex": f.regex}
