# JSON Parser

## Overview

The **JSON Parser** is a simple web application designed to parse JSON data. Users can either enter JSON data directly or upload a JSON file for parsing. The application provides a user-friendly interface to visualize and validate JSON structures.

## Features

- **Enter JSON Data**: Input JSON directly into a text area for immediate parsing.
- **Upload JSON File**: Upload a JSON file to parse its contents.
- **Error Handling**: The application provides feedback on parsing errors, making it easy to identify issues with the JSON data.

## Technologies Used

- **Flask**: A lightweight WSGI web application framework in Python.
- **HTML/CSS**: For the front end of the application.
- **JavaScript**: For interactivity and handling file uploads.
- **Heroku**: Deployed on Heroku for easy access and sharing.

## Installation

To run this project locally, follow these steps:

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/Markkimotho/json-parser.git
   cd json-parser
   ```

2. **Set Up a Virtual Environment**:
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```

3. **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4. **Run the Application**:
    ```bash
    python app.py
    ```

5. **Access the App**: Open your web browser and go to http://127.0.0.1:5000/.

## Usage
1. **Enter JSON Data**: Paste your JSON data into the text area and click "Parse" to validate it.
2. **Upload JSON File**: Click on the upload button to select a JSON file from your device. The app will parse the contents and display any errors or the parsed data.

## Deployment
This application is deployed on Heroku. You can access it at:
[JSON Parser](https://json-parser-py-e1ec55614d20.herokuapp.com/)

## Contributing
Contributions are welcome! If you have suggestions for improvements or want to report issues, please open an issue or submit a pull request.

## License
This project is licensed under the MIT License. See the LICENSE file for details.

## Acknowledgments
Thanks to the [John Crickett](https://www.linkedin.com/in/johncrickett/) for the [JSON Parser Challenge](https://codingchallenges.fyi/challenges/challenge-json-parser)
