# s3_migration_app/extensions.py

from flask_pymongo import PyMongo
from flask_login import LoginManager

# Create extension instances without initializing them with an app
mongo_client = PyMongo()
login_manager = LoginManager()