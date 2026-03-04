"""
Background Queue Manager for Google Keep API Operations

Handles rate-limited deletion of notes in the background while allowing
the UI to remain responsive. Respects GCP quota limits with 20% safety margin.

GCP Quotas (from API Console):
- 90 read requests/minute
- 90 write requests/minute (includes delete)
- 30 create requests/minute

With 20% safety margin:
- Reads: 72/minute = 1.2/second
- Writes: 72/minute = 1.2/second
- Creates: 24/minute = 0.4/second

For deletes (write operations): Max 1.2 per second = ~833ms between requests
"""

import queue
import time
from datetime import datetime
from typing import Optional, Dict, List
from enum import Enum
from googleapiclient.errors import HttpError
from threading import Thread, Lock

from keep_client import get_keep_service
from db import get_db


class OperationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class RateLimiter:
    """
    Token bucket rate limiter for Google Keep API requests.
    Ensures we stay within quota limits with 20% safety margin.
    """

    def __init__(self, requests_per_minute: int):
        """
        Args:
            requests_per_minute: Maximum requests allowed per minute
        """
        self.requests_per_minute = requests_per_minute
        self.min_interval = 60.0 / requests_per_minute  # seconds between requests
        self.last_request_time = 0.0
        self.lock = Lock()

    def acquire(self):
        """
        Block until it's safe to make another request.
        Enforces minimum interval between requests.
        """
        with self.lock:
            now = time.time()
            elapsed = now - self.last_request_time

            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                time.sleep(sleep_time)

            self.last_request_time = time.time()


class QueueManager:
    """
    Singleton queue manager for background delete operations.
    Processes deletes with rate limiting and retry logic.
    """

    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # Rate limiter: 72 writes/minute with 20% margin
        self.rate_limiter = RateLimiter(requests_per_minute=72)

        # Queue state
        self.queue = queue.Queue()
        self.is_processing = False
        self.worker_thread: Optional[Thread] = None

        # Statistics
        self.stats = {
            "total_queued": 0,
            "total_processed": 0,
            "total_succeeded": 0,
            "total_failed": 0,
            "currently_processing": 0,
            "queue_size": 0
        }
        self.stats_lock = Lock()

        self._initialized = True

    def start_worker(self):
        """Start the background worker thread if not already running."""
        with self._lock:
            if self.worker_thread is None or not self.worker_thread.is_alive():
                self.is_processing = True
                
                # Load orphans from DB before starting
                self._load_pending_from_db()
                
                self.worker_thread = Thread(target=self._process_queue, daemon=True)
                self.worker_thread.start()
                print("Queue worker started")

    def _load_pending_from_db(self):
        """Load pending and interrupted operations from the database into the queue."""
        conn = get_db()
        try:
            cursor = conn.cursor()
            # Fetch both pending and processing (which were interrupted by a restart)
            cursor.execute("SELECT note_id FROM pending_deletes WHERE status IN (?, ?)", 
                          (OperationStatus.PENDING, OperationStatus.PROCESSING))
            rows = cursor.fetchall()
            
            count = 0
            for row in rows:
                note_id = row['note_id']
                # Check if already in queue (unlikely on startup)
                # Note: items are dicts in the queue
                self.queue.put({
                    "note_id": note_id,
                    "user_email": None # Service defaults to env var
                })
                count += 1
            
            if count > 0:
                print(f"Resumed {count} pending deletions from database")
                with self.stats_lock:
                    self.stats["total_queued"] += count
                    self.stats["queue_size"] = self.queue.qsize()
        except Exception as e:
            print(f"Error loading pending tasks from DB: {e}")
        finally:
            conn.close()

    def enqueue_delete(self, note_id: str, user_email: Optional[str] = None):
        """
        Add a note deletion to the queue.

        Args:
            note_id: The note ID to delete (format: notes/abc123)
            user_email: Optional user email for service account impersonation
        """
        # Mark as pending in database immediately
        conn = get_db()
        try:
            with conn:
                conn.execute('''
                    INSERT INTO pending_deletes (note_id, status, queued_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(note_id) DO UPDATE SET
                        status = 'pending',
                        queued_at = excluded.queued_at,
                        attempts = 0,
                        last_error = NULL
                ''', (note_id, OperationStatus.PENDING, datetime.utcnow().isoformat()))

                # Also mark the note as trashed in the notes table for immediate UI feedback
                conn.execute("UPDATE notes SET trashed = 1 WHERE id = ?", (note_id,))
        finally:
            conn.close()

        # Add to in-memory queue
        self.queue.put_nowait({
            "note_id": note_id,
            "user_email": user_email
        })

        with self.stats_lock:
            self.stats["total_queued"] += 1
            self.stats["queue_size"] = self.queue.qsize()

        # Ensure worker is running
        self.start_worker()

    def enqueue_batch(self, note_ids: List[str], user_email: Optional[str] = None):
        """
        Enqueue multiple notes for deletion.

        Args:
            note_ids: List of note IDs to delete
            user_email: Optional user email for service account impersonation
        """
        for note_id in note_ids:
            self.enqueue_delete(note_id, user_email)

    def _process_queue(self):
        """
        Background worker that processes the queue with rate limiting.
        Runs in a separate thread.
        """
        print("Queue processor started")
        service = get_keep_service()

        if not service:
            print("ERROR: Failed to initialize Keep service in queue worker")
            self.is_processing = False
            return

        while self.is_processing:
            try:
                # Get next item from queue (with timeout to allow graceful shutdown)
                try:
                    item = self.queue.get(timeout=1.0)
                except:
                    # Queue empty or timeout - check if we should continue
                    if self.queue.qsize() == 0:
                        with self.stats_lock:
                            self.stats["queue_size"] = 0
                    continue

                note_id = item["note_id"]

                with self.stats_lock:
                    self.stats["currently_processing"] = 1
                    self.stats["queue_size"] = self.queue.qsize()

                print(f"Processing note: {note_id}")
                # Update status to processing
                self._update_db_status(note_id, OperationStatus.PROCESSING)

                # Rate limit enforcement
                self.rate_limiter.acquire()

                # Attempt deletion with retry logic
                success, error_msg = self._delete_with_retry(service, note_id)

                # Update database based on result
                if success:
                    print(f"Successfully deleted: {note_id}")
                    self._update_db_status(
                        note_id,
                        OperationStatus.COMPLETED,
                        completed_at=datetime.utcnow().isoformat()
                    )
                    with self.stats_lock:
                        self.stats["total_succeeded"] += 1
                        self.stats["total_processed"] += 1
                else:
                    self._update_db_status(
                        note_id,
                        OperationStatus.FAILED,
                        error=error_msg,
                        completed_at=datetime.utcnow().isoformat()
                    )
                    with self.stats_lock:
                        self.stats["total_failed"] += 1
                        self.stats["total_processed"] += 1

                self.queue.task_done()

                with self.stats_lock:
                    self.stats["currently_processing"] = 0
                    self.stats["queue_size"] = self.queue.qsize()

            except Exception as e:
                print(f"Queue processor error: {e}")
                with self.stats_lock:
                    self.stats["currently_processing"] = 0

        print("Queue processor stopped")

    def _delete_with_retry(self, service, note_id: str, max_retries: int = 3) -> tuple[bool, Optional[str]]:
        """
        Delete a note with exponential backoff retry.

        Returns:
            (success: bool, error_message: Optional[str])
        """
        conn = get_db()

        for attempt in range(max_retries):
            try:
                # Increment attempt counter in DB
                with conn:
                    conn.execute(
                        "UPDATE pending_deletes SET attempts = attempts + 1 WHERE note_id = ?",
                        (note_id,)
                    )

                # Attempt deletion
                service.notes().delete(name=note_id).execute()
                conn.close()
                return (True, None)

            except HttpError as e:
                error_code = e.resp.status

                # Handle different error types
                if error_code == 404:
                    # Note already deleted - treat as success
                    print(f"Note {note_id} not found (already deleted)")
                    conn.close()
                    return (True, None)

                elif error_code == 429 or (error_code == 403 and 'quota' in str(e).lower()):
                    # Rate limit hit - exponential backoff
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # 1s, 2s, 4s
                        print(f"Rate limit hit for {note_id}, waiting {wait_time}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        conn.close()
                        return (False, f"Quota exceeded after {max_retries} retries")

                elif error_code == 403:
                    conn.close()
                    return (False, "Permission denied")

                else:
                    conn.close()
                    return (False, f"HTTP {error_code}: {str(e)[:100]}")

            except Exception as e:
                conn.close()
                return (False, f"Unexpected error: {str(e)[:100]}")

        conn.close()
        return (False, "Max retries exceeded")

    def _update_db_status(self, note_id: str, status: OperationStatus,
                          error: Optional[str] = None,
                          completed_at: Optional[str] = None):
        """Update the status of a pending delete operation in the database."""
        conn = get_db()
        try:
            with conn:
                if completed_at:
                    conn.execute('''
                        UPDATE pending_deletes
                        SET status = ?, last_error = ?, completed_at = ?
                        WHERE note_id = ?
                    ''', (status, error, completed_at, note_id))
                else:
                    conn.execute('''
                        UPDATE pending_deletes
                        SET status = ?, last_error = ?
                        WHERE note_id = ?
                    ''', (status, error, note_id))
        finally:
            conn.close()

    def get_status(self) -> Dict:
        """
        Get current queue status and statistics.

        Returns:
            Dictionary with queue statistics and recent operations
        """
        with self.stats_lock:
            stats = self.stats.copy()

        # Get pending operations from database
        conn = get_db()
        try:
            cursor = conn.cursor()

            # Count by status
            cursor.execute('''
                SELECT status, COUNT(*) as count
                FROM pending_deletes
                GROUP BY status
            ''')
            status_counts = {row['status']: row['count'] for row in cursor.fetchall()}

            # Get recent failures
            cursor.execute('''
                SELECT note_id, last_error, attempts, queued_at
                FROM pending_deletes
                WHERE status = 'failed'
                ORDER BY completed_at DESC
                LIMIT 10
            ''')
            recent_failures = [dict(row) for row in cursor.fetchall()]

            # Get currently processing
            cursor.execute('''
                SELECT note_id, attempts, queued_at
                FROM pending_deletes
                WHERE status = 'processing'
            ''')
            processing = [dict(row) for row in cursor.fetchall()]

        finally:
            conn.close()

        return {
            "queue_size": stats["queue_size"],
            "currently_processing": len(processing),
            "total_queued": stats["total_queued"],
            "total_processed": stats["total_processed"],
            "total_succeeded": stats["total_succeeded"],
            "total_failed": stats["total_failed"],
            "status_counts": status_counts,
            "recent_failures": recent_failures,
            "processing": processing,
            "worker_alive": self.worker_thread is not None and self.worker_thread.is_alive()
        }

    def cleanup_old_records(self, days: int = 7):
        """
        Clean up completed/failed records older than specified days.

        Args:
            days: Number of days to retain records
        """
        conn = get_db()
        try:
            with conn:
                conn.execute('''
                    DELETE FROM pending_deletes
                    WHERE status IN ('completed', 'failed')
                    AND datetime(completed_at) < datetime('now', '-' || ? || ' days')
                ''', (days,))
                deleted = conn.total_changes
                print(f"Cleaned up {deleted} old pending_deletes records")
        finally:
            conn.close()


# Global singleton instance
queue_manager = QueueManager()
