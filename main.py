import os
import re
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Body, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict

from db import get_db
from keep_client import get_keep_service
from sync import sync_notes
from queue_manager import queue_manager

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
    queued: int
    note_ids: List[str]
    message: str

@app.post("/api/action/delete", response_model=DeleteResponse)
async def mass_delete(req: DeleteRequest, background_tasks: BackgroundTasks):
    """
    Queue notes for deletion in the background.

    This endpoint immediately marks notes as pending deletion in the UI and queues
    them for background processing with rate limiting. The UI remains responsive
    while deletions happen in the background.

    NOTE: Google Keep API notes.delete() is a PERMANENT deletion, not a "move to trash".
    There is no undo. See ai-docs/google-keep-api.md and ai-docs/known-issues.md (ISSUE-001).

    Rate Limiting: Backend queue processes max 72 deletes/minute (1.2/second) to stay
    within GCP quotas with 20% safety margin.
    """
    if not req.note_ids:
        return DeleteResponse(
            success=True,
            queued=0,
            note_ids=[],
            message="No notes to delete"
        )

    user_email = os.environ.get('KEEP_USER_EMAIL')

    # Enqueue all notes for background deletion
    queue_manager.enqueue_batch(req.note_ids, user_email)

    # Trigger a background sync after queue processes (delayed)
    background_tasks.add_task(sync_notes)

    return DeleteResponse(
        success=True,
        queued=len(req.note_ids),
        note_ids=req.note_ids,
        message=f"Queued {len(req.note_ids)} note(s) for deletion. Processing in background..."
    )

@app.get("/api/queue/status")
async def get_queue_status():
    """
    Get current status of the background deletion queue.

    Returns statistics about pending, processing, completed, and failed operations.
    Frontend should poll this endpoint every few seconds to update the UI.
    """
    status = queue_manager.get_status()
    return status

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
