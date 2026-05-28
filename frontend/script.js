document.addEventListener('DOMContentLoaded', () => {
    const form              = document.getElementById('scrape-form');
    const submitCsvBtn      = document.getElementById('submit-csv-btn');
    const submitSheetBtn     = document.getElementById('submit-sheet-btn');
    const viewResultsSheetBtn = document.getElementById('view-results-sheet-btn');
    const statusContainer   = document.getElementById('status-container');
    const statusMessage     = document.getElementById('status-message');
    const statusSubtext     = document.getElementById('status-subtext');
    const downloadCsvBtn    = document.getElementById('download-csv-btn');
    const successIcon       = document.querySelector('.success-icon');
    const errorIcon         = document.querySelector('.error-icon');
    const progressContainer = document.getElementById('progress-container');
    const progressBarFill   = document.getElementById('progress-bar-fill');
    const progressLabel     = document.getElementById('progress-label');
    const progressCount     = document.getElementById('progress-count');
    const zoneIndicator     = document.getElementById('zone-indicator');
    const zoneCounter       = document.getElementById('zone-counter');
    const cancelBtn         = document.getElementById('cancel-btn');
    const maxResultsInput   = document.getElementById('max-results');
    const resultsHint       = document.getElementById('results-hint');
    const zoneSplitToggle   = document.getElementById('zone-split-toggle');
    const activityLog       = document.getElementById('activity-log');

    // BACKEND_URL is loaded from config.js

    let currentJobId  = null;
    let pollInterval  = null;

    // ── Restore job state on page load (handles refresh during scrape) ────
    function restoreJobState() {
        const savedJobId = sessionStorage.getItem('currentJobId');
        const savedStatus = sessionStorage.getItem('jobStatus');
        
        if (savedJobId && savedStatus && savedStatus !== 'completed' && savedStatus !== 'failed' && savedStatus !== 'cancelled') {
            currentJobId = savedJobId;
            setUIState('progress');
            progressLabel.textContent = 'Resuming scrape...';
            progressCount.textContent = 'Reconnecting...';
            beginPolling();
        }
    }

    // Call on page load
    restoreJobState();

    // Fetch dynamic sheet URL from backend
    function fetchSheetUrl() {
        if (!viewResultsSheetBtn) return;
        fetch(`${BACKEND_URL}/sheet-url`)
            .then(res => res.json())
            .then(data => {
                if (data && data.url) {
                    viewResultsSheetBtn.href = data.url;
                }
            })
            .catch(err => console.warn('Could not fetch sheet URL:', err));
    }
    fetchSheetUrl();

    // ── Results hint: show zone estimate as user types ─────────────────────
    function updateResultsHint() {
        const val = parseInt(maxResultsInput.value, 10);
        const zoneSplit = zoneSplitToggle.checked;
        if (!val || val <= 0 || !zoneSplit) { resultsHint.classList.remove('visible'); return; }

        const zones = Math.ceil(val / 20);
        if (zones > 1) {
            resultsHint.textContent = `~${zones} zones`;
            resultsHint.classList.add('visible');
        } else {
            resultsHint.classList.remove('visible');
        }
    }

    maxResultsInput.addEventListener('input', updateResultsHint);
    zoneSplitToggle.addEventListener('change', updateResultsHint);

    // Trigger hint on load
    updateResultsHint();

    // ── Cancel button ──────────────────────────────────────────────────────
    cancelBtn.addEventListener('click', async () => {
        if (!currentJobId) return;
        cancelBtn.disabled = true;
        cancelBtn.style.opacity = '0.5';
        try {
            const response = await fetch(`${BACKEND_URL}/cancel/${currentJobId}`, { 
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            if (response.ok) {
                console.log('Cancel request sent successfully');
            } else {
                console.error('Cancel request failed:', response.status);
                cancelBtn.disabled = false;
                cancelBtn.style.opacity = '1';
            }
        } catch (e) { 
            console.error('Cancel error:', e);
            cancelBtn.disabled = false;
            cancelBtn.style.opacity = '1';
        }
    });

    let selectedMode = 'csv';

    function setSubmitButtonsLoading(loading, activeBtnId = null) {
        const buttons = [submitCsvBtn, submitSheetBtn];
        buttons.forEach(btn => {
            if (!btn) return;
            btn.disabled = loading;
            const btnText = btn.querySelector('.btn-text');
            const btnLoader = btn.querySelector('.loader-dots');
            
            if (loading) {
                if (btn.id === activeBtnId) {
                    if (btnText) btnText.style.display = 'none';
                    if (btnLoader) btnLoader.style.display = 'flex';
                }
            } else {
                if (btnText) btnText.style.display = 'block';
                if (btnLoader) btnLoader.style.display = 'none';
            }
        });
    }

    // ── Form submit ────────────────────────────────────────────────────────
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        stopPolling();

        if (e.submitter && e.submitter.id === 'submit-sheet-btn') {
            selectedMode = 'sheet';
        } else {
            selectedMode = 'csv';
        }

        const term       = document.getElementById('term').value.trim();
        const location   = document.getElementById('location').value.trim();
        const maxResults = parseInt(maxResultsInput.value, 10) || 50;
        const zoneSplit  = zoneSplitToggle.checked;
        const sheetTab   = document.getElementById('sheet-tab-input').value.trim();

        if (!term || !location) return;

        // Decide: async job (> 50 results) vs legacy sync call
        const isLargeRequest = maxResults > 50;

        if (isLargeRequest) {
            await startAsyncJob(term, location, maxResults, zoneSplit, sheetTab);
        } else {
            await runLegacySync(term, location, maxResults, zoneSplit, sheetTab);
        }
    });

    // ── Legacy sync flow ───────────────────────────────────────────────────
    async function runLegacySync(term, location, maxResults, zoneSplit, sheetTab) {
        setUIState('loading-simple');
        try {
            const response = await fetch(`${BACKEND_URL}/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ term, location, max_results: maxResults, zone_split: zoneSplit, sheet_tab: sheetTab, export_mode: selectedMode }),
            });
            const data = await response.json();

            // Backend may return job_id if it redirected to async
            if (data.job_id) {
                currentJobId = data.job_id;
                setUIState('progress');
                beginPolling();
                return;
            }

            if (response.ok) {
                if (selectedMode === 'sheet') {
                    setUIState('success', `Done! ${data.leads_found} leads saved to your Sheet.`);
                } else {
                    setUIState('success', `Done! ${data.leads_found} leads saved locally.`, `/download/leads_${data.job_id || 'current'}.csv`);
                }
            } else {
                setUIState('error', `Error: ${data.error || 'Something went wrong'}`);
            }
        } catch (err) {
            console.error(err);
            setUIState('error', 'Could not connect to the scraper backend.');
        }
    }

    // ── Async job flow ─────────────────────────────────────────────────────
    async function startAsyncJob(term, location, maxResults, zoneSplit, sheetTab) {
        setUIState('progress');
        try {
            const response = await fetch(`${BACKEND_URL}/scrape`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ term, location, max_results: maxResults, zone_split: zoneSplit, sheet_tab: sheetTab, export_mode: selectedMode }),
            });
            const data = await response.json();

            if (!response.ok || !data.job_id) {
                setUIState('error', `Failed to start job: ${data.error || 'Unknown error'}`);
                return;
            }

            currentJobId = data.job_id;
            sessionStorage.setItem('currentJobId', currentJobId);
            sessionStorage.setItem('jobStatus', 'running');
            progressLabel.textContent = zoneSplit ? 'Preparing zones…' : 'Scanning Google Maps…';
            progressCount.textContent = '0 leads found';
            beginPolling();
        } catch (err) {
            console.error(err);
            setUIState('error', 'Could not connect to the scraper backend.');
        }
    }

    // ── Polling loop ───────────────────────────────────────────────────────
    function beginPolling() {
        pollInterval = setInterval(pollStatus, 3000);
        pollStatus(); // immediate first poll
    }

    async function pollStatus() {
        if (!currentJobId) return;
        try {
            const res  = await fetch(`${BACKEND_URL}/status/${currentJobId}`);
            const data = await res.json();

            if (!res.ok) {
                stopPolling();
                sessionStorage.removeItem('currentJobId');
                if (res.status === 404) {
                    // Worker was restarted (OOM or Cloud Run scaling) — job state lost
                    setUIState('error', 'The server restarted mid-scrape and the job was lost. Check your Google Sheet for any leads saved so far, then start a new scrape.');
                } else {
                    setUIState('error', data.error || 'Job status unavailable.');
                }
                return;
            }

            console.log('Poll response:', data);
            updateProgressUI(data);

            if (data.export_mode) {
                selectedMode = data.export_mode;
            }

            if (data.status === 'completed') {
                stopPolling();
                sessionStorage.setItem('jobStatus', 'completed');
                sessionStorage.removeItem('currentJobId');
                if (selectedMode === 'sheet') {
                    setUIState('success', `Done! ${data.leads_found} leads saved to your Google Sheet.`);
                } else {
                    setUIState('success', `Done! ${data.leads_found} leads saved locally.`, data.download_url);
                }
            } else if (data.status === 'failed') {
                stopPolling();
                sessionStorage.setItem('jobStatus', 'failed');
                sessionStorage.removeItem('currentJobId');
                setUIState('error', `Scrape failed. ${data.errors?.[0] || 'Check server logs.'}`, selectedMode === 'csv' && data.leads_found > 0 ? data.download_url : '');
            } else if (data.status === 'cancelled') {
                stopPolling();
                sessionStorage.setItem('jobStatus', 'cancelled');
                sessionStorage.removeItem('currentJobId');
                setUIState('error', `Scrape cancelled. ${data.leads_found} leads were saved before stopping.`, selectedMode === 'csv' && data.leads_found > 0 ? data.download_url : '');
            } else {
                sessionStorage.setItem('jobStatus', data.status);
            }
        } catch (err) {
            console.warn('Poll error:', err);
            // Don't stop on transient network errors — keep polling
        }
    }

    function updateProgressUI(data) {
        const pct      = data.progress_pct || 0;
        const total    = data.total_zones  || 1;
        const done     = data.zones_completed || 0;
        const found    = data.leads_found  || 0;
        const zone     = data.current_zone || '';
        const isSplit  = data.zone_split !== false; // true by default

        progressBarFill.style.width = `${pct}%`;
        progressCount.textContent   = `${found} lead${found !== 1 ? 's' : ''} found`;

        if (!isSplit) {
            // Single-zone mode — simple message, no zone name
            progressLabel.textContent = 'Scanning Google Maps…';
            zoneIndicator.textContent = '';
            zoneCounter.textContent   = '';
        } else if (zone) {
            progressLabel.textContent = `Scanning ${zone}…`;
            zoneIndicator.textContent = zone;
            zoneCounter.textContent   = total > 1 ? `${done} / ${total} zones` : '';
        } else if (data.status === 'starting') {
            progressLabel.textContent = 'Preparing zones…';
            zoneIndicator.textContent = '';
            zoneCounter.textContent   = '';
        }
        
        // Update activity log
        if (data.activity_log && data.activity_log.length > 0) {
            updateActivityLog(data.activity_log);
        }
    }
    
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.appendChild(document.createTextNode(String(text)));
        return div.innerHTML;
    }

    function updateActivityLog(activities) {
        if (!activities || activities.length === 0) return;

        // Clear placeholder
        const placeholder = activityLog.querySelector('.activity-item .activity-message');
        if (placeholder && placeholder.textContent === 'Waiting for updates...') {
            activityLog.innerHTML = '';
        }

        // Add new activities (reverse to show newest first), escape all content to prevent XSS
        const reversed = [...activities].reverse();
        activityLog.innerHTML = reversed.map(activity => {
            const levelClass = activity.level === 'error' ? 'activity-error' :
                               activity.level === 'warning' ? 'activity-warning' : '';
            return `<div class="activity-item ${levelClass}">` +
                   `<span class="activity-time">${escapeHtml(activity.time)}</span>` +
                   `<span class="activity-message">${escapeHtml(activity.message)}</span>` +
                   `</div>`;
        }).join('');
    }

    function stopPolling() {
        if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
    }

    // ── UI State machine ───────────────────────────────────────────────────
    function setUIState(state, message = '', downloadUrl = '') {
        // Reset all panels first
        progressContainer.classList.add('progress-hidden');
        statusContainer.classList.add('status-hidden');
        successIcon.style.display = 'none';
        errorIcon.style.display   = 'none';
        downloadCsvBtn.style.display = 'none';
        viewResultsSheetBtn.style.display = 'none';
        cancelBtn.disabled  = false;
        cancelBtn.style.opacity = '1';

        switch (state) {
            case 'loading-simple':
                setSubmitButtonsLoading(true, selectedMode === 'csv' ? 'submit-csv-btn' : 'submit-sheet-btn');
                statusContainer.classList.remove('status-hidden');
                statusMessage.textContent = 'Scanning Google Maps…';
                statusSubtext.textContent = 'This usually takes 1–3 minutes.';
                break;

            case 'progress':
                setSubmitButtonsLoading(true, selectedMode === 'csv' ? 'submit-csv-btn' : 'submit-sheet-btn');
                progressContainer.classList.remove('progress-hidden');
                progressBarFill.style.width = '0%';
                break;

            case 'success':
                setSubmitButtonsLoading(false);
                statusContainer.classList.remove('status-hidden');
                statusMessage.textContent = message;
                
                if (selectedMode === 'sheet') {
                    viewResultsSheetBtn.style.display = 'inline-flex';
                    statusSubtext.textContent = 'Check your Google Sheet for the results.';
                } else {
                    if (downloadUrl) {
                        downloadCsvBtn.href = `${BACKEND_URL}${downloadUrl}`;
                        downloadCsvBtn.style.display = 'inline-flex';
                        statusSubtext.textContent = 'Results are saved locally.';
                        
                        // Auto-trigger download
                        const link = document.createElement('a');
                        link.href = `${BACKEND_URL}${downloadUrl}`;
                        link.download = downloadUrl.split('/').pop();
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);
                    } else {
                        statusSubtext.textContent = 'No download link available.';
                    }
                }
                successIcon.style.display = 'block';
                currentJobId = null;
                break;

            case 'error':
                setSubmitButtonsLoading(false);
                statusContainer.classList.remove('status-hidden');
                statusMessage.textContent = message;
                
                if (selectedMode === 'sheet') {
                    statusSubtext.textContent = 'Please check the credentials/terminal and try again.';
                } else {
                    if (downloadUrl) {
                        downloadCsvBtn.href = `${BACKEND_URL}${downloadUrl}`;
                        downloadCsvBtn.style.display = 'inline-flex';
                        statusSubtext.textContent = 'You can download the leads collected so far.';
                    } else {
                        statusSubtext.textContent = 'Please check the terminal and try again.';
                    }
                }
                errorIcon.style.display   = 'block';
                currentJobId = null;
                break;
        }
    }
});
