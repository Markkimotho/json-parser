// ===================================
// JSON Parser - Retro Terminal UI
// ===================================

// Form submission handler
document.getElementById('jsonForm').addEventListener('submit', function(event) {
    event.preventDefault();
    parseJSON();
});

// File input change handler
document.getElementById('jsonFile').addEventListener('change', function(e) {
    const fileName = e.target.files[0]?.name || '';
    const fileNameDisplay = document.getElementById('fileName');
    fileNameDisplay.textContent = fileName ? `[${fileName}]` : '';
});

function parseJSON() {
    const textarea = document.getElementById('jsonData');
    const fileInput = document.getElementById('jsonFile');
    const resultElement = document.getElementById('result');

    // Show loading state
resultElement.innerHTML = '<div class="result-empty"><span class="blink">_</span> Parsing...</div>';

    // Create FormData manually to avoid sending empty textarea
    const formData = new FormData();
    
    const textValue = textarea.value.trim();
    const hasFile = fileInput.files && fileInput.files.length > 0;
    
    console.log('Parse request - Text input:', !!textValue, 'File input:', !!hasFile);
    
    // Only add jsonData if it has content
    if (textValue) {
        formData.append('jsonData', textValue);
        console.log('Added jsonData to form');
    }
    
    // Add file if selected
    if (hasFile) {
        const file = fileInput.files[0];
        console.log('Adding file to form:', file.name, file.size, 'bytes');
        formData.append('jsonFile', file);
    }
    
    // Validate that we have something to send
    if (!textValue && !hasFile) {
        resultElement.innerHTML = `<div class="result-error">
<span class="error-label">[ERROR]</span> Please enter JSON or select a file
</div>`;
        return;
    }

    fetch('/parse-json', {
        method: 'POST',
        body: formData
    })
    .then(response => {
        console.log('Response status:', response.status);
        return response.json();
    })
    .then(data => {
        console.log('Response data:', data);
        resultElement.innerHTML = '';
        
        if (data.error) {
            // Error response
            displayError(resultElement, data);
        } else {
            // Success response
            displaySuccess(resultElement, data);
        }
    })
    .catch(error => {
        console.error('Fetch error:', error);
        resultElement.innerHTML = `<div class="result-error">
<span class="error-label">[ERROR]</span> Failed to parse JSON
<div class="error-detail">${escapeHtml(error.message)}</div>
</div>`;
    });
}

function displaySuccess(resultElement, data) {
    const timestamp = new Date().toLocaleTimeString();
    const jsonString = JSON.stringify(data.result, null, 2);
    
    let html = `<div class="result-success">`;
    html += `<div style="margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center;">`;
    html += `<span>[${timestamp}] Parse successful OK</span>`;
    html += `<button class="copy-btn" onclick="copyToClipboard('${btoa(jsonString)}');">Copy</button>`;
    html += `</div>`;
    
    if (data.source) {
        html += `<div style="margin-bottom: 15px; opacity: 0.8;">Source: ${data.source === 'file' ? '[FILE UPLOAD]' : '[TEXT INPUT]'}</div>`;
    }
    
    html += `<div class="json-output">`;
    html += `<pre>${escapeHtml(jsonString)}</pre>`;
    html += `</div>`;
    html += `</div>`;
    
    resultElement.innerHTML = html;
}

function copyToClipboard(encodedJson) {
    try {
        const jsonString = atob(encodedJson);
        navigator.clipboard.writeText(jsonString).then(() => {
            // Show success feedback
            const btn = event.target;
            const originalText = btn.textContent;
            btn.textContent = '[Copied]';
            setTimeout(() => {
                btn.textContent = originalText;
            }, 2000);
        }).catch(err => {
            console.error('Failed to copy:', err);
            alert('Failed to copy to clipboard');
        });
    } catch (err) {
        console.error('Error copying:', err);
        alert('Error copying to clipboard');
    }
}

function displayError(resultElement, data) {
    const timestamp = new Date().toLocaleTimeString();
    
    let html = `<div class="result-error">`;
    html += `<div style="margin-bottom: 10px;"><span class="error-label">[ERROR]</span> Parse failed at ${timestamp}</div>`;
    
    if (data.message) {
        html += `<div class="error-detail">${escapeHtml(data.message)}</div>`;
    }
    
    if (data.line && data.column) {
        html += `<div class="error-detail">Location: Line ${data.line}, Column ${data.column}</div>`;
    }
    
    html += `<div class="error-detail" style="margin-top: 10px; border-top: 1px solid #ff6b6b; padding-top: 10px;">`;
    html += `${escapeHtml(data.error)}`;
    html += `</div>`;
    html += `</div>`;
    
    resultElement.innerHTML = html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Reset handler
document.getElementById('jsonForm').addEventListener('reset', function() {
    document.getElementById('result').innerHTML = 
        '<div class="result-empty"><span class="blink">___</span> Waiting for input...</div>';
    document.getElementById('fileName').textContent = '';
});

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    // Ctrl+Enter to parse
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        parseJSON();
    }
    // Ctrl+K to clear (like terminal)
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        document.getElementById('jsonForm').reset();
    }
});

// Initialize
window.addEventListener('load', function() {
    document.getElementById('result').innerHTML = 
        '<div class="result-empty"><span class="blink">___</span> Waiting for input...</div>';
});
