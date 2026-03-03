import os
import re
from fastapi import FastAPI, HTTPException, Body, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional

from db import get_db
from keep_client import get_keep_service
from sync import sync_notes

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

@app.post("/api/action/delete")
async def mass_delete(req: DeleteRequest, background_tasks: BackgroundTasks):
    if not req.note_ids:
        return {"success": True, "deleted": 0}

    service = get_keep_service()
    if not service:
        raise HTTPException(status_code=500, detail="Failed to initialize Keep Service. Check credentials.")

    conn = get_db()
    success_ids = []
    
    # Ideally should use Batch APIs, but looping for simplicity since Google Keep API v1 Delete is actually relatively fast
    for note_id in req.note_ids:
        try:
            # Delete via Google Keep API
            service.notes().delete(name=note_id).execute()
            
            # Immediately mark as trashed locally so UI feels instantly updated
            with conn:
                conn.execute("UPDATE notes SET trashed = 1 WHERE id = ?", (note_id,))
            
            success_ids.append(note_id)
        except Exception as e:
            print(f"Error deleting note {note_id}: {e}")
            
    # Trigger a background sync to keep local DB clean with remote state
    background_tasks.add_task(sync_notes)
            
    return {"success": True, "deleted": len(success_ids), "ids": success_ids}

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
