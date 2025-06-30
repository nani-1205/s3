# models.py
from flask_login import UserMixin
from bson.objectid import ObjectId
from app import mongo, USERS_COLLECTION_NAME # Import the collection name

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']
        self.password_hash = user_data['password']
        self.otp_secret = user_data.get('otp_secret')

    @staticmethod
    def find_by_username(username):
        # Use the configured collection name
        user_data = mongo.db[USERS_COLLECTION_NAME].find_one({'username': username})
        if user_data:
            return User(user_data)
        return None

    @staticmethod
    def find_by_id(user_id):
        try:
            # Use the configured collection name
            user_data = mongo.db[USERS_COLLECTION_NAME].find_one({'_id': ObjectId(user_id)})
            if user_data:
                return User(user_data)
        except:
            return None # Handle invalid ObjectId format
        return None