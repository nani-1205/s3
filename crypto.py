# s3_migration_app/crypto.py

import os
import base64
from flask import current_app
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

def get_fernet_key():
    """
    Derives a stable, 32-byte encryption key from the Flask SECRET_KEY.
    This method is deterministic, so the same SECRET_KEY will always produce the same encryption key.
    For high-security production environments, using a dedicated key management service (like AWS KMS or Vault) is recommended.
    """
    secret_key = current_app.config['SECRET_KEY'].encode()
    # A salt is used to prevent rainbow table attacks. It can be a fixed value for this use case
    # as its main purpose is to make this derived key unique to this application.
    salt = b'_s3-migration-app-salt_' 
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000, # Recommended value by OWASP as of 2021
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret_key))
    return key

def encrypt_data(data_str: str) -> str:
    """Encrypts a string and returns it as a URL-safe base64 encoded string."""
    if not data_str:
        return ""
    key = get_fernet_key()
    f = Fernet(key)
    encrypted_data = f.encrypt(data_str.encode('utf-8'))
    return encrypted_data.decode('utf-8')

def decrypt_data(encrypted_str: str) -> str:
    """Decrypts a URL-safe base64 encoded string and returns it."""
    if not encrypted_str:
        return ""
    try:
        key = get_fernet_key()
        f = Fernet(key)
        decrypted_data = f.decrypt(encrypted_str.encode('utf-8'))
        return decrypted_data.decode('utf-8')
    except Exception as e:
        current_app.logger.error(f"Failed to decrypt data. This can happen if the SECRET_KEY has changed. Error: {e}")
        return "DECRYPTION_ERROR"