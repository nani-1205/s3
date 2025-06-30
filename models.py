# s3_migration_app/models.py

from flask import current_app
from flask_login import UserMixin
from bson.objectid import ObjectId

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data.get('_id'))
        self.username = user_data.get('username')
        self.password_hash = user_data.get('password')
        self.otp_secret = user_data.get('otp_secret')

    @staticmethod
    def find_by_username(username):
        """Finds a user in the database by their username."""
        # Get the collection name from the app's config
        collection_name = current_app.config['USERS_COLLECTION_NAME']
        # Access the db object from the current application context
        user_data = current_app.db[collection_name].find_one({'username': username})
        if user_data:
            return User(user_data)
        return None

    @staticmethod
    def find_by_id(user_id):
        """Finds a user by their ObjectId. Required by Flask-Login."""
        collection_name = current_app.config['USERS_COLLECTION_NAME']
        try:
            # Use current_app.db to access the database
            user_data = current_app.db[collection_name].find_one({'_id': ObjectId(user_id)})
            if user_data:
                return User(user_data)
        except Exception:
            return None
        return None