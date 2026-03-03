from keep_client import get_keep_service
from db import get_db

def sync_notes(user_email=None):
    print("Starting sync process...")
    service = get_keep_service(user_email)
    if not service:
        print("Failed to get Google Keep service. Check credentials.")
        return False

    conn = get_db()
    
    try:
        next_page_token = None
        total_synced = 0
        
        while True:
            # Note: filter constraints can be applied here. We pull all for local caching.
            request = service.notes().list(pageSize=100, pageToken=next_page_token)
            response = request.execute()
            
            notes = response.get('notes', [])
            for note in notes:
                note_id = note.get('name')
                title = note.get('title', '')
                create_time = note.get('createTime', '')
                update_time = note.get('updateTime', '')
                trashed = note.get('trashed', False)
                trashed_db = 1 if trashed else 0
                has_attachments = 1 if 'attachments' in note and len(note['attachments']) > 0 else 0
                
                # Parse the note body
                body_content = ""
                snippet = ""
                if 'body' in note:
                    body = note['body']
                    if 'text' in body and 'text' in body['text']:
                        body_content = body['text']['text']
                    elif 'list' in body and 'listItems' in body['list']:
                        items = []
                        for item in body['list']['listItems']:
                            text = item.get('text', {}).get('text', '')
                            checked = item.get('checked', False)
                            mark = "[x]" if checked else "[ ]"
                            items.append(f"{mark} {text}")
                        body_content = "\n".join(items)
                
                snippet = body_content[:150] + "..." if len(body_content) > 150 else body_content

                # Upsert note into database
                with conn:
                    conn.execute('''
                        INSERT INTO notes (id, title, snippet, body, create_time, update_time, trashed, archived, has_attachments)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            title=excluded.title,
                            snippet=excluded.snippet,
                            body=excluded.body,
                            update_time=excluded.update_time,
                            trashed=excluded.trashed,
                            archived=excluded.archived,
                            has_attachments=excluded.has_attachments
                    ''', (note_id, title, snippet, body_content, create_time, update_time, trashed_db, 0, has_attachments))
                
                total_synced += 1

            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
                
        print(f"Sync complete. Synced {total_synced} notes.")
        return True
    except Exception as e:
        print(f"Error syncing notes: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    import os
    # Default to env var if running script directly
    email = os.environ.get('KEEP_USER_EMAIL', '')
    import sys
    if len(sys.argv) > 1:
        email = sys.argv[1]
        
    print(f"Using email: {email}")
    os.environ['KEEP_USER_EMAIL'] = email # override for keep_client
    sync_notes(email)
