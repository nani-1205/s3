# s3_migration_app/credentials.py

from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from bson.objectid import ObjectId
from crypto import encrypt_data # Import our crypto helpers

credentials_bp = Blueprint('credentials', __name__, url_prefix='/credentials')

@credentials_bp.route('/', methods=['GET', 'POST'])
@login_required
def manage():
    CREDENTIALS_COLLECTION_NAME = current_app.config['CREDENTIALS_COLLECTION_NAME']
    
    if request.method == 'POST':
        profile_name = request.form.get('profile_name')
        access_key = request.form.get('access_key')
        secret_key = request.form.get('secret_key')

        if not all([profile_name, access_key, secret_key]):
            flash('All fields are required.', 'danger')
        else:
            # Encrypt the secret key before storing
            encrypted_secret = encrypt_data(secret_key)
            
            current_app.db[CREDENTIALS_COLLECTION_NAME].insert_one({
                'user_id': current_user.id,
                'username': current_user.username,
                'profile_name': profile_name,
                'access_key': access_key,
                'secret_key_encrypted': encrypted_secret
            })
            flash(f'Credential profile "{profile_name}" saved successfully.', 'success')
        return redirect(url_for('credentials.manage'))

    # GET request: Show existing credentials for the logged-in user
    credentials = list(current_app.db[CREDENTIALS_COLLECTION_NAME].find({'user_id': current_user.id}))
    return render_template('credentials.html', credentials=credentials)

@credentials_bp.route('/delete/<credential_id>', methods=['POST'])
@login_required
def delete(credential_id):
    CREDENTIALS_COLLECTION_NAME = current_app.config['CREDENTIALS_COLLECTION_NAME']
    
    # Ensure the user can only delete their own credentials
    result = current_app.db[CREDENTIALS_COLLECTION_NAME].delete_one({
        '_id': ObjectId(credential_id),
        'user_id': current_user.id
    })
    
    if result.deleted_count > 0:
        flash('Credential deleted successfully.', 'info')
    else:
        flash('Credential not found or you do not have permission to delete it.', 'danger')
        
    return redirect(url_for('credentials.manage'))