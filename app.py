# s3_migration_app/app.py

import os
import threading
import time
from flask import Flask, session, render_template
from flask_pymongo import PyMongo
from flask_login import LoginManager, current_user
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv
from bson.objectid import ObjectId
from urllib.parse import quote_plus # For encoding password special characters

# Load environment variables from .env file
load_dotenv()

# --- App Initialization ---
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

# --- MongoDB Connection Setup ---
# Read individual MongoDB details from .env
mongo_host = os.getenv("MONGO_HOST")
mongo_port = int(os.getenv("MONGO_PORT", 27017))
mongo_user = os.getenv("MONGO_USER")
mongo_pass = os.getenv("MONGO_PASS")
mongo_dbname = os.getenv("MONGO_DBNAME")

# Check if essential MongoDB config is present
if not all([mongo_host, mongo_user, mongo_pass, mongo_dbname]):
    raise ValueError("Missing MongoDB configuration in .env file (MONGO_HOST, MONGO_USER, MONGO_PASS, MONGO_DBNAME are required)")

# URL-encode the password to handle special characters
encoded_password = quote_plus(mongo_pass)

# Construct the MongoDB URI and set it in the Flask app config
# Format: mongodb://user:password@host:port/dbname
mongo_uri = f"mongodb://{mongo_user}:{encoded_password}@{mongo_host}:{mongo_port}/{mongo_dbname}"
app.config["MONGO_URI"] = mongo_uri

app.logger.info(f"Connecting to MongoDB at {mongo_host} in database '{mongo_dbname}'")

# --- Extensions Initialization ---
mongo = PyMongo(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'auth.login' # Redirect to login page if user is not authenticated

# --- Collection Names from .env (with defaults) ---
USERS_COLLECTION_NAME = os.getenv("USERS_COLLECTION", "users")
MIGRATION_HISTORY_COLLECTION_NAME = os.getenv("MIGRATION_HISTORY_COLLECTION", "migration_history")
MIGRATION_STATE_COLLECTION_NAME = os.getenv("MIGRATION_STATE_COLLECTION", "migration_state")

# --- User Loader for Flask-Login ---
# This part now uses the collection name from the .env file
from models import User
@login_manager.user_loader
def load_user(user_id):
    return User.find_by_id(user_id)

# --- Register Blueprints ---
# These blueprints will use the globally configured 'mongo' object
from auth import auth_bp
from migration import migration_bp
app.register_blueprint(auth_bp, url_prefix='/auth') # Added url_prefix for organization
app.register_blueprint(migration_bp)

# --- Base Route ---
@app.route('/')
def index():
    if not current_user.is_authenticated:
        # You can create a simple public landing page if you want
        return redirect(url_for('auth.login'))
    
    # User is logged in, show them the main migration dashboard
    return redirect(url_for('migration.dashboard'))


if __name__ == '__main__':
    # Initialize the migration state document on startup if it doesn't exist
    mongo.db[MIGRATION_STATE_COLLECTION_NAME].update_one(
        {'_id': 'live_migration_status'},
        {'$setOnInsert': {'is_running': False, 'last_updated': time.time()}},
        upsert=True
    )
    app.logger.info("Application server started and ready.")
    app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", host='0.0.0.0', port=5000, threaded=True)