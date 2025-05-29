# AWS S3 Data Migration 

A Flask-based web application to migrate specific data from an AWS S3 bucket in one AWS account to another S3 bucket, potentially in a different AWS account and region. The application provides a real-time dashboard UI to monitor the migration progress.

![Screenshot_29-5-2025_173421_18 61 78 72](https://github.com/user-attachments/assets/b139ab55-d799-4f57-8482-bdded309c477)


## Features

*   **Cross-Account & Cross-Region Migration:** Copies S3 objects from a source bucket/prefix to a destination bucket/prefix.
*   **Configuration via `.env`:** All AWS credentials, bucket names, regions, and paths are managed securely via a `.env` file.
*   **Real-time Progress UI:**
    *   Displays source and destination configuration.
    *   Shows total files, files transferred, total size, and estimated transfer speed.
    *   Live progress bar.
    *   Displays the currently processing file.
    *   Live log stream of migration events.
*   **Multi-Client View Synchronization:** If multiple users open the dashboard, they see the status of the currently active (or last run) migration.
*   **Background Migration:** The migration process runs in a background thread on the server, allowing the UI to remain responsive.


## Prerequisites

*   Python 3.7+
*   `pip` (Python package installer)
*   Access to two AWS accounts (source and destination) with permissions to:
    *   Create/manage IAM users and policies.
    *   Create/manage S3 buckets and objects.
    *   (Optionally) Manage KMS keys if buckets are encrypted with CMKs.
*   An environment where the Flask application can run (e.g., local machine, EC2 instance).

## Setup Instructions

1.  **Clone the Repository (if applicable) or Create Project Directory:**
    ```bash
    # If cloned:
    # git clone -b V-2 https://github.com/nani-1205/s3.git
    # cd s3_migration_app

    # If creating manually:
    mkdir s3_migration_app
    cd s3_migration_app
    mkdir templates static
    # Then copy app.py, templates/index.html, static/style.css, etc., into these folders.
    ```

2.  **Create a Python Virtual Environment:**
    It's highly recommended to use a virtual environment to manage project dependencies.
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure Environment Variables:**
    
    ```bash
    vi .env
    ```
    *   Edit the `.env` file with your specific AWS credentials, S3 bucket details, and prefixes:

        ```dotenv
        # Source AWS Account & S3 Details
        # If these are commented out or not present, the app will attempt to use
        # the EC2 instance role or default AWS CLI profile credentials.
        # SOURCE_AWS_ACCESS_KEY_ID="YOUR_SOURCE_ACCOUNT_ACCESS_KEY"
        # SOURCE_AWS_SECRET_ACCESS_KEY="YOUR_SOURCE_ACCOUNT_SECRET_KEY"
        SOURCE_S3_REGION="ap-south-1" # e.g., us-east-1
        SOURCE_S3_BUCKET="your-source-bucket-name"
        SOURCE_S3_PREFIX="your/source/prefix/" # Include trailing '/' for folder-like prefixes

        # Destination AWS Account & S3 Details
        # If these are commented out or not present, the app will attempt to use
        # the EC2 instance role or default AWS CLI profile credentials.
        # DEST_AWS_ACCESS_KEY_ID="YOUR_DEST_ACCOUNT_ACCESS_KEY"
        # DEST_AWS_SECRET_ACCESS_KEY="YOUR_DEST_ACCOUNT_SECRET_KEY"
        DEST_S3_REGION="ap-south-2" # e.g., us-west-2
        DEST_S3_BUCKET="your-destination-bucket-name"
        DEST_S3_PREFIX="your/destination/prefix/" # Include trailing '/'

        # Flask specific
        FLASK_APP=app.py
        FLASK_DEBUG=True # Set to False for production
        ```
    *   **Important:**
        *   If running on an EC2 instance with an IAM Role attached, you can often omit the `*_AWS_ACCESS_KEY_ID` and `*_AWS_SECRET_ACCESS_KEY` lines, and Boto3 will automatically use the instance role credentials. The IAM role will then need the necessary permissions.
        *   Ensure prefixes end with a `/` if you intend to copy the contents of a "folder".

5.  **Configure AWS IAM Permissions:**

    This application requires specific IAM permissions for both the source and destination AWS accounts. The principal performing the migration (either an IAM user whose keys are in `.env` or the EC2 instance role) needs:

    *   **Permissions in the Destination Account (for the principal running the script):**
        *   Read access to the source S3 bucket/prefix (e.g., `s3:GetObject`, `s3:ListBucket`).
        *   Write access to the destination S3 bucket/prefix (e.g., `s3:PutObject`, `s3:PutObjectAcl`).
        *   (Optional) KMS permissions if Customer Managed Keys (CMKs) are used for S3 bucket encryption.

        *See example IAM policy in previous discussions or `example_iam_policy_destination.json` (if you create one).*

    *   **Bucket Policy on the Source S3 Bucket (in the Source Account):**
        *   This policy must grant the principal from the destination account (e.g., `arn:aws:iam::DESTINATION_ACCOUNT_ID:user/YOUR_USER` or `arn:aws:iam::DESTINATION_ACCOUNT_ID:role/YOUR_EC2_ROLE`) permissions to `s3:GetObject` on the source objects and `s3:ListBucket` on the source bucket (restricted to the source prefix).

        *See example S3 bucket policy in previous discussions or `example_s3_bucket_policy_source.json` (if you create one).*

## Running the Application

1.  Ensure your virtual environment is activated:
    ```bash
    source venv/bin/activate # Or venv\Scripts\activate on Windows
    ```

2.  Start the Flask development server:
    ```bash
    flask run
    ```
    Or, if `FLASK_APP` is not set in `.env`:
    ```bash
    FLASK_APP=app.py flask run
    ```
    Or simply:
    ```bash
    python app.py
    ```
    The application will typically be available at `http://127.0.0.1:5000` or `http://0.0.0.0:5000`. The console output will show the exact URL.

3.  Open your web browser and navigate to the URL provided by Flask.

4.  Click the "🚀 Start Migration" button to initiate the S3 data transfer. Progress will be displayed on the dashboard.

## How it Works

*   **Backend (Flask/Python):**
    *   Serves the HTML dashboard.
    *   Uses `python-dotenv` to load configurations from `.env`.
    *   Uses `boto3` (AWS SDK for Python) to interact with S3.
    *   Manages a shared migration state in a global dictionary (Note: This is suitable for single-worker development. For production with multiple workers, an external state manager like Redis would be needed).
    *   A `/trigger-migration` endpoint (POST) initiates the migration in a background thread.
    *   A `/migration-status-stream` endpoint (GET) provides Server-Sent Events (SSE) to clients, pushing updates from the shared migration state.
*   **Frontend (HTML/CSS/JavaScript):**
    *   Displays configuration details and migration statistics.
    *   Connects to the `/migration-status-stream` SSE endpoint to receive real-time updates.
    *   Dynamically updates the progress bar, log messages, and other UI elements.
    *   Sends a request to `/trigger-migration` when the start button is clicked.

## Development Notes

*   **Debugging:** Set `FLASK_DEBUG=True` in `.env` for development. This enables the Flask debugger and automatic reloading on code changes.
*   **Multi-Worker Environments (e.g., Gunicorn):** The current shared state mechanism using a Python global dictionary will **not** work correctly if you deploy the application with multiple worker processes (e.g., `gunicorn -w 4 app:app`). Each worker would have its own independent state. For such deployments, you must implement shared state management using an external service like Redis or Memcached.
*   **Error Handling:** The application includes basic error handling, but for production, more robust error logging and user feedback mechanisms would be beneficial.
*   **Security of Credentials:** Never commit your `.env` file (or any file containing sensitive credentials) to version control. Use the provided `.gitignore` to prevent this. For EC2 deployments, prefer using IAM Roles for EC2 instances instead of hardcoding credentials.

## Troubleshooting "Access Denied" Errors

"Access Denied" errors during migration usually indicate IAM permission issues. Check the following:
1.  **IAM Policy on the Principal Performing the Migration (Destination Account):** Ensure it has `s3:GetObject` and `s3:ListBucket` for the source bucket/prefix, AND `s3:PutObject` for the destination bucket/prefix.
2.  **Bucket Policy on the Source S3 Bucket:** Ensure it grants the principal from the destination account the necessary `s3:GetObject` and `s3:ListBucket` permissions.
3.  **Bucket Policy on the Destination S3 Bucket:** Ensure it doesn't have explicit `Deny` statements that override the IAM user's permissions.
4.  **KMS Key Policies:** If either bucket uses SSE-KMS with Customer Managed Keys, ensure the relevant KMS key policies and IAM permissions for KMS actions (`kms:Decrypt`, `kms:GenerateDataKey*`, `kms:Encrypt`) are correctly configured for cross-account access if needed.
5.  **VPC Endpoint Policies:** If running in a VPC with S3 Gateway Endpoints, check the endpoint policies.
6.  **Service Control Policies (SCPs):** If using AWS Organizations, check for restrictive SCPs.



