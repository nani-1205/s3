# s3_migration_app/app.py

import os
import threading
import time
from flask import Flask, redirect, url_for
from flask_login import current_user
from dotenv import load_dotenv
from urllib.parse import quote_plus
from bson.objectid import ObjectId

# Import the extension instances from extensions.py
from extensions import mongo_client, login_manager

# --- Load Environment Variables ---
load_dotenv()

# --- Global Variables for Collection Names ---
# These can be defined here as they don't depend on the app object
USERS_COLLECTION_NAME = os.getenv("USERS_COLLECTION", "users")
MIGRATION_HISTORY_COLLECTION_NAME = os.getenv("MIGRATION_HISTORY_COLLECTION", "migration_history")
MIGRATION_STATE_COLLECTION_NAME = os.getenv("MIGRATION_STATE_COLLECTION", "migration_state")

# --- Application Factory Function ---
def create_app():
    """
    Creates and configures an instance of the Flask application.
    """
    app = Flask(__name__)
    
    # --- Configuration ---
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key-please-change")
    
    # --- MongoDB Connection Setup ---
    mongo_host = os.getenv("MONGO_HOST")
    mongo_port = int(os.getenv("MONGO_PORT", 27017))
    mongo_user = os.getenv("MONGO_USER")
    mongo_pass = os.getenv("MONGO_PASS")
    mongo_auth_db = os.getenv("MONGO_AUTH_DB", "admin")
    mongo_app_db = os.getenv("MONGO_APP_DB")

    if not all([mongo_host, mongo_user, mongo_pass, mongo_app_db]):
        raise ValueError("Missing MongoDB configuration in .env file (MONGO_HOST, MONGO_USER, MONGO_PASS, MONGO_APP_DB are required)")

    encoded_user = quote_plus(mongo_user)
    encoded_password = quote_plus(mongo_pass)
    mongo_uri = f"mongodb://{encoded_user}:{encoded_password}@{mongo_host}:{mongo_port}/?authSource={mongo_auth_db}"
    app.config["MONGO_URI"] = mongo_uri

    app.logger.info(f"Connecting to MongoDB at {mongo_host} to use database '{mongo_app_db}'")

    # --- Initialize Extensions with the App ---
    mongo_client.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    
    # --- Import and Register Blueprints ---
    from auth import auth_bp
    from migration import migration_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(migration_bp)

    # --- Import Models (now safe to do so within the factory) ---
    from models import User
    
    @login_manager.user_loader
    def load_user(user_id):
        # This function needs to be defined within the app context
        return User.find_by_id(user_id)
        
    # --- Define a database object for the app context ---
    # This makes it easy to access the correct database via current_app.db
    with app.app_context():
        app.db = mongo_client.cx[mongo_app_db]
    
    # --- Base Route ---
    @app.route('/')
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        return redirect(url_for('migration.dashboard'))

    return app

# --- Create the Flask App Instance using the factory ---
# This instance will be imported by your WSGI server (like Gunicorn) or used by `if __name__ == '__main__':`
app = create_app()

# --- Main execution block for direct run (`python app.py`) ---
if __name__ == '__main__':
    with app.app_context():
        # Initialize the migration state document on startup
        app.db[MIGRATION_STATE_COLLECTION_NAME].update_one(
            {'_id': 'live_migration_status'},
            {'$setOnInsert': {'is_running': False, 'last_updated': time.time()}},
            upsert=True
        )
        app.logger.info("Application server started and ready.")
        
    app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", host='0.0.0.0', port=5000, threaded=True)