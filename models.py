# s3_migration_app/models.py

from flask_login import UserMixin
from bson.objectid import ObjectId
from app import db, USERS_COLLECTION_NAME  # Import the explicit 'db' object and collection name

class User(UserMixin):
    """
    User model for Flask-Login.
    """
    def __init__(self, user_data):
        """
        Initializes a User object from a MongoDB document.
        """
        self.id = str(user_data.get('_id'))
        self.username = user_data.get('username')
        self.password_hash = user_data.get('password')
        self.otp_secret = user_data.get('otp_secret')

    @staticmethod
    def find_by_username(username):
        """
        Finds a user in the database by their username.
        """
        user_data = db[USERS_COLLECTION_NAME].find_one({'username': username})
        if user_data:
            return User(user_data)
        return None

    @staticmethod
    def find_by_id(user_id):
        """
        Finds a user in the database by their ObjectId.
        Required by Flask-Login's user_loader.
        """
        try:
            # Ensure the user_id is a valid ObjectId before querying
            user_data = db[USERS_COLLECTION_NAME].find_one({'_id': ObjectId(user_id)})
            if user_data:
                return User(user_data)
        except Exception:
            # This handles cases where user_id is not a valid ObjectId string
            return None
        return None