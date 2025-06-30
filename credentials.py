# s3_migration_app/credentials.py

from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from bson.objectid import ObjectId
from crypto import encrypt_data
from admin import admin_required # Import the admin decorator

credentials_bp = Blueprint('credentials', __name__, url_prefix='/credentials')

@credentials_bp.route('/', methods=['GET', 'POST'])
@login_required
@admin_required # <-- PROTECTS THIS ENTIRE ROUTE
def manage():
    """
    Allows admins to add new global credentials and view all existing ones.
    """
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
            
            # Insert the new global credential without any user ID
            current_app.db[CREDENTIALS_COLLECTION_NAME].insert_one({
                'profile_name': profile_name,
                'access_key': access_key,
                'secret_key_encrypted': encrypted_secret,
                'added_by': current_user.username # Optional: track who added it
            })
            flash(f'Global credential profile "{profile_name}" was saved successfully.', 'success')
        return redirect(url_for('credentials.manage'))

    # GET request: Fetch ALL credentials to display on the management page
    all_credentials = list(current_app.db[CREDENTIALS_COLLECTION_NAME].find({}))
    return render_template('credentials.html', credentials=all_credentials)

@credentials_bp.route('/delete/<credential_id>', methods=['POST'])
@login_required
@admin_required # <-- PROTECTS THIS ROUTE
def delete(credential_id):
    """
    Allows admins to delete any global credential.
    """
    CREDENTIALS_COLLECTION_NAME = current_app.config['CREDENTIALS_COLLECTION_NAME']
    
    # Admin can delete any credential by its ID
    result = current_app.db[CREDENTIALS_COLLECTION_NAME].delete_one({
        '_id': ObjectId(credential_id)
    })
    
    if result.deleted_count > 0:
        flash('Credential deleted successfully.', 'info')
    else:
        flash('Credential not found.', 'danger')
        
    return redirect(url_for('credentials.manage'))