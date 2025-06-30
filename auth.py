# s3_migration_app/auth.py

import pyotp
import qrcode
import io
import base64
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import User

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    # ... (Register logic is mostly the same) ...
    if request.method == 'POST':
        collection_name = current_app.config['USERS_COLLECTION_NAME']
        username = request.form.get('username')
        password = request.form.get('password')
        
        user_exists = current_app.db[collection_name].find_one({'username': username})
        if user_exists:
            flash('Username already exists.', 'danger')
            return redirect(url_for('auth.register'))

        hashed_password = generate_password_hash(password)
        otp_secret = pyotp.random_base32()
        
        new_user_id = current_app.db[collection_name].insert_one({
            'username': username,
            'password': hashed_password,
            'otp_secret': otp_secret,
            'otp_confirmed': False # New users must confirm 2FA
        }).inserted_id
        
        flash('Registration successful! Please set up & confirm 2FA.', 'success')
        
        user_data = current_app.db[collection_name].find_one({'_id': new_user_id})
        user = User(user_data)
        login_user(user) # Temporarily log in to access setup page
        return redirect(url_for('auth.two_factor_setup'))
    
    return render_template('register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('migration.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.find_by_username(username)
        
        if not user or not check_password_hash(user.password_hash, password):
            flash('Invalid username or password.', 'danger')
            return redirect(url_for('auth.login'))
            
        # --- NEW 2FA Logic ---
        # If 2FA is not yet confirmed, redirect to setup page
        if not user.otp_confirmed:
            login_user(user) # Temporarily log in
            flash('Your 2FA is not confirmed. Please complete the setup.', 'info')
            return redirect(url_for('auth.two_factor_setup'))

        # If 2FA is confirmed, now we require the token
        otp_token = request.form.get('otp')
        if not otp_token:
            flash('2FA Token is required.', 'danger')
            return redirect(url_for('auth.login'))

        totp = pyotp.TOTP(user.otp_secret)
        if not totp.verify(otp_token):
            flash('Invalid 2FA token.', 'danger')
            return redirect(url_for('auth.login'))
            
        login_user(user)
        return redirect(url_for('migration.dashboard'))
        
    return render_template('login.html')

@auth_bp.route('/two_factor_setup', methods=['GET', 'POST'])
@login_required
def two_factor_setup():
    if request.method == 'POST':
        collection_name = current_app.config['USERS_COLLECTION_NAME']
        otp_token = request.form.get('otp')
        totp = pyotp.TOTP(current_user.otp_secret)

        if totp.verify(otp_token):
            # Mark 2FA as confirmed in the database
            current_app.db[collection_name].update_one(
                {'_id': ObjectId(current_user.id)},
                {'$set': {'otp_confirmed': True}}
            )
            flash('2-Factor Authentication has been successfully set up!', 'success')
            return redirect(url_for('migration.dashboard'))
        else:
            flash('Invalid token. Please try again.', 'danger')

    # --- GET Request Logic ---
    provisioning_uri = pyotp.totp.TOTP(current_user.otp_secret).provisioning_uri(
        name=current_user.username, issuer_name='S3 Migration App'
    )
    img = qrcode.make(provisioning_uri)
    buf = io.BytesIO()
    img.save(buf); buf.seek(0)
    img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

    return render_template('two_factor_setup.html', qr_code=img_b64, user_is_confirmed=current_user.otp_confirmed)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))