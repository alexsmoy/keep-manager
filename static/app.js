document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const searchInput = document.getElementById('search-input');
    const regexInput = document.getElementById('regex-input');
    const searchBtn = document.getElementById('search-btn');
    const tableBody = document.getElementById('notes-table-body');
    const selectAllCheckbox = document.getElementById('select-all');
    const actionBar = document.getElementById('action-bar');
    const selectedCountText = document.getElementById('selected-count');
    const massDeleteBtn = document.getElementById('mass-delete-btn');
    const clearSelectionBtn = document.getElementById('clear-selection-btn');
    const statusEl = document.getElementById('status');
    const previewArea = document.getElementById('preview-area');

    // State
    let notes = [];
    let selectedNoteIds = new Set();
    let activeNoteId = null;

    // Initialization
    loadNotes();

    // Event Listeners
    searchBtn.addEventListener('click', () => loadNotes());
    searchInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') loadNotes(); });
    regexInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') loadNotes(); });

    selectAllCheckbox.addEventListener('change', (e) => {
        const isChecked = e.target.checked;
        if (isChecked) {
            notes.forEach(n => selectedNoteIds.add(n.id));
        } else {
            selectedNoteIds.clear();
        }
        updateTableSelectionVisuals();
        updateActionBar();
    });

    clearSelectionBtn.addEventListener('click', () => {
        selectedNoteIds.clear();
        selectAllCheckbox.checked = false;
        updateTableSelectionVisuals();
        updateActionBar();
    });

    massDeleteBtn.addEventListener('click', async () => {
        if (selectedNoteIds.size === 0) return;

        const confirmed = confirm(`Are you sure you want to delete ${selectedNoteIds.size} notes? They will be moved to the Trash in Google Keep.`);
        if (!confirmed) return;

        await performDelete(selectedNoteIds, null);
    });

    async function performDelete(idSet, nextActiveId = null) {
        if (idSet.size === 0) return;

        massDeleteBtn.disabled = true;
        statusEl.textContent = `Deleting ${idSet.size} notes...`;
        statusEl.className = 'status-indicator status-loading';

        try {
            const resp = await fetch('/api/action/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note_ids: Array.from(idSet) })
            });

            if (resp.ok) {
                const result = await resp.json();
                statusEl.textContent = `Deleted ${result.deleted} notes.`;
                statusEl.className = 'status-indicator status-ok';

                // Clear active preview if the active note was deleted
                if (idSet.has(activeNoteId)) {
                    clearPreviewPane();
                }

                idSet.forEach(id => selectedNoteIds.delete(id));
                selectAllCheckbox.checked = false;

                // Keep track of next active component
                activeNoteId = nextActiveId;

                await loadNotes(); // Reload the table

                // If we have a next note to cycle to, open it
                if (activeNoteId) {
                    const next = notes.find(n => n.id === activeNoteId);
                    if (next) showPreview(next);
                }
            } else {
                throw new Error("Failed to delete notes");
            }
        } catch (err) {
            console.error(err);
            statusEl.textContent = "Error deleting notes.";
            statusEl.className = 'status-indicator';
        } finally {
            massDeleteBtn.disabled = false;
        }
    }

    // Functions
    async function loadNotes() {
        statusEl.textContent = 'Loading notes...';
        statusEl.className = 'status-indicator status-loading';
        tableBody.innerHTML = '<tr><td colspan="3" style="text-align:center;">Loading...</td></tr>';

        const search = encodeURIComponent(searchInput.value.trim());
        const regex = encodeURIComponent(regexInput.value.trim());

        try {
            const response = await fetch(`/api/notes?search=${search}&regex=${regex}`);
            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.detail || "Failed to load notes");
            }
            const data = await response.json();
            notes = data.notes;

            // Clean up selections and active note if they were filtered out
            const noteIds = new Set(notes.map(n => n.id));
            if (activeNoteId && !noteIds.has(activeNoteId)) {
                clearPreviewPane();
            }
            // Keep selected ones even if filtered out, or optionally clear them.
            // Let's keep them selected so mass delete can still apply.

            statusEl.textContent = `Found ${notes.length} notes`;
            statusEl.className = 'status-indicator status-ok';

            renderTable();
        } catch (err) {
            console.error(err);
            tableBody.innerHTML = `<tr><td colspan="3" style="text-align:center; color: var(--danger);">${err.message}</td></tr>`;
            statusEl.textContent = 'Error';
            statusEl.className = 'status-indicator';
        }
    }

    function renderTable() {
        tableBody.innerHTML = '';

        if (notes.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="3" style="text-align:center; color: var(--text-secondary);">No notes matched your query.</td></tr>';
            return;
        }

        notes.forEach(note => {
            const tr = document.createElement('tr');
            tr.dataset.id = note.id;

            const isSelected = selectedNoteIds.has(note.id);
            if (isSelected) tr.classList.add('selected');
            if (note.id === activeNoteId) tr.classList.add('active-note');

            // Optional attachment indicator (we don't have it in the DB yet, but this is how it would look)
            const attachmentIndicator = note.has_attachments ? '<span class="attachment-icon" title="Has Attachments">📎</span>' : '';

            tr.innerHTML = `
                <td class="checkbox-col">
                    <input type="checkbox" class="row-checkbox" data-id="${note.id}" ${isSelected ? 'checked' : ''}>
                </td>
                <td class="title-col">
                    ${escapeHTML(note.title) || '<em>Untitled Note</em>'}
                    ${attachmentIndicator}
                </td>
                <td class="snippet-col">${escapeHTML(note.snippet) || '<em>Empty body</em>'}</td>
            `;

            // Row click handling
            tr.addEventListener('click', (e) => {
                if (e.target.tagName.toLowerCase() === 'input') {
                    return; // Handled by checkbox listener
                }

                // Show preview on the right
                showPreview(note);
            });

            const checkbox = tr.querySelector('.row-checkbox');
            checkbox.addEventListener('change', (e) => {
                toggleNoteSelection(note.id, e.target.checked);
            });

            tableBody.appendChild(tr);
        });

        updateActionBar();
    }

    function showPreview(note) {
        activeNoteId = note.id;

        // Update row highlight
        const rows = tableBody.querySelectorAll('tr');
        rows.forEach(row => {
            if (row.dataset.id === note.id) {
                row.classList.add('active-note');
            } else {
                row.classList.remove('active-note');
            }
        });

        // Populate the right pane
        previewArea.innerHTML = `
            <div class="preview-meta" style="display: flex; justify-content: space-between; align-items: center;">
                <strong>Read-Only Note Preview</strong>
                <button id="single-delete-btn" class="danger" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;">🗑️ Delete Note</button>
            </div>
            <h2 class="preview-title">${escapeHTML(note.title) || '<em>Untitled Note</em>'}</h2>
            <div class="preview-body">${escapeHTML(note.body) || '<em>Empty note body</em>'}</div>
        `;

        // Handle single delete click
        document.getElementById('single-delete-btn').addEventListener('click', () => {
            let nextNoteId = null;
            const idx = notes.findIndex(n => n.id === note.id);
            if (idx !== -1) {
                // Try next note, or previous if at end
                if (idx + 1 < notes.length) {
                    nextNoteId = notes[idx + 1].id;
                } else if (idx - 1 >= 0) {
                    nextNoteId = notes[idx - 1].id;
                }
            }
            performDelete(new Set([note.id]), nextNoteId);
        });
    }

    function clearPreviewPane() {
        activeNoteId = null;
        previewArea.innerHTML = '<div class="preview-empty">Select a note to view its contents (Read-Only)</div>';
    }

    function toggleNoteSelection(id, isSelected) {
        if (isSelected) {
            selectedNoteIds.add(id);
        } else {
            selectedNoteIds.delete(id);
            selectAllCheckbox.checked = false;
        }
        updateTableSelectionVisuals();
        updateActionBar();
    }

    function updateTableSelectionVisuals() {
        const rows = tableBody.querySelectorAll('tr');
        rows.forEach(row => {
            const cb = row.querySelector('.row-checkbox');
            if (cb) {
                cb.checked = selectedNoteIds.has(cb.dataset.id);
                if (cb.checked) {
                    row.classList.add('selected');
                } else {
                    row.classList.remove('selected');
                }
            }
        });
    }

    function updateActionBar() {
        const count = selectedNoteIds.size;
        selectedCountText.textContent = count;
        if (count > 0) {
            actionBar.style.display = 'flex';
        } else {
            actionBar.style.display = 'none';
        }
    }

    // Utils
    function escapeHTML(str) {
        if (!str) return str;
        return str.replace(/[&<>'"]/g,
            tag => ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                "'": '&#39;',
                '"': '&quot;'
            }[tag])
        );
    }
});
