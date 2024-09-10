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
        resultElement.innerHTML = ''; // Clear previous results
        if (data.error) {
            resultElement.textContent = 'Error: ' + data.error;
        } else {
            resultElement.innerHTML = '<pre>' + JSON.stringify(data.result, null, 2) + '</pre>';
        }
    })
    .catch(error => {
        console.error('Error:', error);
        var resultElement = document.getElementById('result');
        resultElement.textContent = 'An error occurred. Please try again later.';
    });
});