# s3_migration_app/auth.py

import pyotp
import qrcode
import io
import base64
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from app import mongo, USERS_COLLECTION_NAME # Import mongo and the collection name
from models import User

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('migration.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user_exists = mongo.db[USERS_COLLECTION_NAME].find_one({'username': username})
        if user_exists:
            flash('Username already exists. Please choose another.', 'danger')
            return redirect(url_for('auth.register'))

        hashed_password = generate_password_hash(password)
        
        # We'll generate the OTP secret and store it, then guide the user to set it up.
        otp_secret = pyotp.random_base32()
        
        new_user_id = mongo.db[USERS_COLLECTION_NAME].insert_one({
            'username': username,
            'password': hashed_password,
            'otp_secret': otp_secret
        }).inserted_id
        
        flash('Registration successful! Please scan the QR code to set up 2-Factor Authentication.', 'success')
        
        # Log the new user in to proceed to 2FA setup page
        user_data = mongo.db[USERS_COLLECTION_NAME].find_one({'_id': new_user_id})
        user_object = User(user_data)
        login_user(user_object)
        
        return redirect(url_for('auth.two_factor_setup'))
    
    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('migration.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        otp_token = request.form.get('otp')
        
        user_data = mongo.db[USERS_COLLECTION_NAME].find_one({'username': username})
        
        if not user_data or not check_password_hash(user_data['password'], password):
            flash('Invalid username or password. Please try again.', 'danger')
            return redirect(url_for('auth.login'))
            
        # Verify 2FA token
        user = User(user_data)
        totp = pyotp.TOTP(user.otp_secret)
        if not totp.verify(otp_token):
            flash('Invalid 2FA token. Please try again.', 'danger')
            return redirect(url_for('auth.login'))
            
        login_user(user)
        # Redirect to the dashboard after a successful login
        return redirect(url_for('migration.dashboard'))
        
    return render_template('login.html')

@auth_bp.route('/two_factor_setup')
@login_required
def two_factor_setup():
    # If user has no OTP secret (should not happen with current register flow, but good practice)
    if not current_user.otp_secret:
        flash('2FA secret not found for your account.', 'danger')
        return redirect(url_for('index'))

    # Generate QR code for the user's OTP secret
    otp_secret = current_user.otp_secret
    # Use the username in the provisioning URI for clarity in the authenticator app
    provisioning_uri = pyotp.totp.TOTP(otp_secret).provisioning_uri(
        name=current_user.username, 
        issuer_name='S3 Migration App'
    )
    
    # Create QR code image in memory to avoid saving to a file
    img = qrcode.make(provisioning_uri)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

    return render_template('two_factor_setup.html', qr_code=img_b64)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))