# s3_migration_app/app.py

import os
import threading
import time
from flask import Flask, redirect, url_for
from flask_login import current_user
from dotenv import load_dotenv
from urllib.parse import quote_plus
from bson.objectid import ObjectId
import pyotp
from werkzeug.security import generate_password_hash

# Import the extension instances from extensions.py
from extensions import mongo_client, login_manager

def create_app():
    """
    Application Factory: Creates and configures the Flask application.
    """
    load_dotenv()
    
    app = Flask(__name__)
    
    # --- Load Configuration from .env into Flask's app.config ---
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "a-super-secret-key-that-you-should-change")
    
    app.config['USERS_COLLECTION_NAME'] = os.getenv("USERS_COLLECTION", "users")
    app.config['MIGRATION_HISTORY_COLLECTION_NAME'] = os.getenv("MIGRATION_HISTORY_COLLECTION", "migration_history")
    app.config['MIGRATION_STATE_COLLECTION_NAME'] = os.getenv("MIGRATION_STATE_COLLECTION", "migration_state")

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
    
    # --- Define a database object for the app context ---
    # This makes it easy to access the correct database via current_app.db
    with app.app_context():
        app.db = mongo_client.cx[mongo_app_db]
    
    # --- Import and Register Blueprints ---
    from auth import auth_bp
    from migration import migration_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(migration_bp)

    # --- Import Models and Define User Loader within the factory scope ---
    from models import User
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.find_by_id(user_id)
        
    # --- Base Route ---
    @app.route('/')
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        return redirect(url_for('migration.dashboard'))

    return app

# --- Create the Flask App Instance ---
app = create_app()

# --- Main execution block for direct run (`python app.py`) ---
if __name__ == '__main__':
    with app.app_context():
        # Initialize startup documents
        app.db[app.config['MIGRATION_STATE_COLLECTION_NAME']].update_one(
            {'_id': 'live_migration_status'},
            {'$setOnInsert': {'is_running': False, 'last_updated': time.time()}},
            upsert=True
        )
        if app.db[app.config['USERS_COLLECTION_NAME']].count_documents({'username': 'admin'}) == 0:
            app.logger.info("No 'admin' user found. Creating default admin user...")
            hashed_password = generate_password_hash('changethispassword')
            otp_secret = pyotp.random_base32()
            app.db[app.config['USERS_COLLECTION_NAME']].insert_one({
                'username': 'admin', 'password': hashed_password, 'otp_secret': otp_secret
            })
            app.logger.warning("Default user 'admin' created with password 'changethispassword'. PLEASE CHANGE THIS and set up 2FA upon first login.")

    app.logger.info("Application server started and ready.")
    app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", host='0.0.0.0', port=5000, threaded=True)