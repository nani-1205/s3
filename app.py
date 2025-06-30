# s3_migration_app/app.py

import os
import threading
import time
from flask import Flask, session, render_template, redirect, url_for
from flask_pymongo import PyMongo
from flask_login import LoginManager, current_user
from werkzeug.security import generate_password_hash # Changed from Bcrypt for consistency with auth.py
from dotenv import load_dotenv
from bson.objectid import ObjectId
from urllib.parse import quote_plus

# Load environment variables from .env file
load_dotenv()

# --- App Initialization ---
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

# --- MongoDB Connection Setup ---
mongo_host = os.getenv("MONGO_HOST")
mongo_port = int(os.getenv("MONGO_PORT", 27017))
mongo_user = os.getenv("MONGO_USER")
mongo_pass = os.getenv("MONGO_PASS")
mongo_auth_db = os.getenv("MONGO_AUTH_DB", "admin") # Default to 'admin' if not specified
mongo_app_db = os.getenv("MONGO_APP_DB") # The DB the app will use

# Check for essential MongoDB configuration
if not all([mongo_host, mongo_user, mongo_pass, mongo_app_db]):
    raise ValueError("Missing MongoDB configuration in .env file (MONGO_HOST, MONGO_USER, MONGO_PASS, MONGO_APP_DB are required)")

# URL-encode the username and password to handle special characters
encoded_user = quote_plus(mongo_user)
encoded_password = quote_plus(mongo_pass)

# Construct the MongoDB URI with the authSource parameter
# This tells MongoDB to authenticate against the 'admin' db (or whatever is specified)
# but we will operate on the MONGO_APP_DB later.
mongo_uri = f"mongodb://{encoded_user}:{encoded_password}@{mongo_host}:{mongo_port}/?authSource={mongo_auth_db}"
app.config["MONGO_URI"] = mongo_uri

app.logger.info(f"Connecting to MongoDB at {mongo_host} (auth against '{mongo_auth_db}') to use database '{mongo_app_db}'")

# --- Extensions Initialization ---
# PyMongo is initialized here but we will select the database explicitly in our code
mongo_client = PyMongo(app)

# Create a database object that explicitly points to your application's database
# This ensures all operations like mongo.db.users actually mean mongo.db.s3_db.users
# We will use this 'db' object throughout the app instead of mongo_client.db
db = mongo_client.cx[mongo_app_db]


login_manager = LoginManager(app)
login_manager.login_view = 'auth.login'

# --- Collection Names from .env ---
USERS_COLLECTION_NAME = os.getenv("USERS_COLLECTION", "users")
MIGRATION_HISTORY_COLLECTION_NAME = os.getenv("MIGRATION_HISTORY_COLLECTION", "migration_history")
MIGRATION_STATE_COLLECTION_NAME = os.getenv("MIGRATION_STATE_COLLECTION", "migration_state")

# --- User Loader for Flask-Login ---
from models import User
@login_manager.user_loader
def load_user(user_id):
    return User.find_by_id(user_id)

# --- Register Blueprints ---
from auth import auth_bp
from migration import migration_bp
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(migration_bp)

# --- Base Route ---
@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    return redirect(url_for('migration.dashboard'))


if __name__ == '__main__':
    # Initialize the migration state document on startup if it doesn't exist
    # Use the explicit 'db' object here
    db[MIGRATION_STATE_COLLECTION_NAME].update_one(
        {'_id': 'live_migration_status'},
        {'$setOnInsert': {'is_running': False, 'last_updated': time.time()}},
        upsert=True
    )
    # Check if a default admin user exists, create if not
    if db[USERS_COLLECTION_NAME].count_documents({'username': 'admin'}) == 0:
        app.logger.info("No 'admin' user found. Creating default admin user...")
        default_password = 'changethispassword' # You should change this
        hashed_password = generate_password_hash(default_password)
        otp_secret = pyotp.random_base32()
        db[USERS_COLLECTION_NAME].insert_one({
            'username': 'admin',
            'password': hashed_password,
            'otp_secret': otp_secret
        })
        app.logger.info(f"Default user 'admin' created with password '{default_password}'. PLEASE CHANGE THIS PASSWORD and set up 2FA.")

    app.logger.info("Application server started and ready.")
    app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", host='0.0.0.0', port=5000, threaded=True)