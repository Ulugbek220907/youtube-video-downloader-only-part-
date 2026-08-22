document.addEventListener('DOMContentLoaded', function() {
  const startInput = document.getElementById('startTime');
  const endInput = document.getElementById('endTime');
  const resolutionSelect = document.getElementById('resolution');
  const downloadBtn = document.getElementById('downloadBtn');
  const statusDiv = document.getElementById('status');
  const progressDiv = document.getElementById('progress');
  const progressBar = document.getElementById('progress-bar');
  let pollInterval = null;

  // Load saved settings
  chrome.storage.local.get(['startTime', 'endTime', 'resolution'], (result) => {
    if (result.startTime) startInput.value = result.startTime;
    if (result.endTime) endInput.value = result.endTime;
    if (result.resolution) resolutionSelect.value = result.resolution;
  });

  downloadBtn.addEventListener('click', async () => {
    // Get current YouTube tab
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url || !tab.url.includes('youtube.com/watch')) {
      statusDiv.textContent = '❌ Please open a YouTube video page.';
      return;
    }

    const start = startInput.value.trim();
    const end = endInput.value.trim();
    const resolution = resolutionSelect.value;

    if (!start || !end) {
      statusDiv.textContent = '❌ Enter both start and end times.';
      return;
    }

    // Save for next time
    chrome.storage.local.set({ startTime: start, endTime: end, resolution: resolution });

    downloadBtn.disabled = true;
    statusDiv.textContent = `⏳ Starting download (${resolution}p)...`;
    progressDiv.style.display = 'block';
    progressBar.style.width = '0%';

    try {
      const url = tab.url;
      // 1. Ask server to start the job
      const response = await fetch(
        `http://localhost:5000/download?url=${encodeURIComponent(url)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&resolution=${resolution}`
      );
      const data = await response.json();

      if (data.status === 'error') {
        statusDiv.textContent = `❌ ${data.message}`;
        downloadBtn.disabled = false;
        return;
      }

      const jobId = data.job_id;
      statusDiv.textContent = `⏳ Processing clip (${resolution}p)... (this may take 20-60 seconds)`;

      // 2. Poll progress every 1.5 seconds
      let completed = false;
      pollInterval = setInterval(async () => {
        try {
          const progRes = await fetch(`http://localhost:5000/progress/${jobId}`);
          const progData = await progRes.json();

          if (progData.status === 'error') {
            clearInterval(pollInterval);
            statusDiv.textContent = `❌ ${progData.message}`;
            downloadBtn.disabled = false;
            progressDiv.style.display = 'none';
            return;
          }

          if (progData.status === 'complete') {
            // Done – download the file
            clearInterval(pollInterval);
            completed = true;
            statusDiv.textContent = '✅ Clip ready, downloading...';
            progressBar.style.width = '100%';

            const filename = progData.filename;
            const fileUrl = `http://localhost:5000/file/${filename}`;

            chrome.downloads.download({
              url: fileUrl,
              filename: filename,
              saveAs: true
            }, (downloadId) => {
              if (chrome.runtime.lastError) {
                statusDiv.textContent = `❌ Download error: ${chrome.runtime.lastError.message}`;
              } else {
                statusDiv.textContent = '✅ Download complete!';
              }
              downloadBtn.disabled = false;
              setTimeout(() => {
                progressDiv.style.display = 'none';
              }, 5000);
            });
            return;
          }

          // Update progress bar
          const percent = progData.percent || 0;
          progressBar.style.width = `${Math.min(percent, 100)}%`;
          if (percent > 0) {
            statusDiv.textContent = `⏳ Processing clip (${Math.round(percent)}%)...`;
          }
        } catch (e) {
          clearInterval(pollInterval);
          statusDiv.textContent = `⚠️ Polling error: ${e.message}`;
          downloadBtn.disabled = false;
          progressDiv.style.display = 'none';
        }
      }, 1500);

      // Safety: stop polling after 5 minutes
      setTimeout(() => {
        if (!completed) {
          clearInterval(pollInterval);
          statusDiv.textContent = '⚠️ Download timed out. Try again.';
          downloadBtn.disabled = false;
          progressDiv.style.display = 'none';
        }
      }, 300000);

    } catch (err) {
      statusDiv.textContent = `❌ Connection error: ${err.message}`;
      downloadBtn.disabled = false;
      progressDiv.style.display = 'none';
    }
  });
});