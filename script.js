// script.js

document.getElementById('jsonForm').addEventListener('submit', function(event) {
    event.preventDefault();

    var formData = new FormData(this);

    fetch('/parse-json', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        var resultElement = document.getElementById('result');
        if (data.error) {
            resultElement.textContent = 'Error: ' + data.error;
        } else {
            resultElement.textContent = 'Parsed Result: ' + JSON.stringify(data.result);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        var resultElement = document.getElementById('result');
        resultElement.textContent = 'An error occurred. Please try again later.';
    });
});

