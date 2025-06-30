# s3_migration_app/admin.py

from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app, abort
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
from functools import wraps

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# --- Decorator to check for admin privileges ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not hasattr(current_user, 'is_admin') or not current_user.is_admin:
            abort(403) # Forbidden
        return f(*args, **kwargs)
    return decorated_function


# --- Admin Routes for Managing All Users ---
@admin_bp.route('/users')
@login_required
@admin_required
def user_list():
    """Displays a list of all users for the admin."""
    USERS_COLLECTION_NAME = current_app.config['USERS_COLLECTION_NAME']
    all_users = list(current_app.db[USERS_COLLECTION_NAME].find({}))
    return render_template('admin/user_list.html', users=all_users)

@admin_bp.route('/user/<user_id>/change_password', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_change_password(user_id):
    """Allows an admin to change another user's password."""
    USERS_COLLECTION_NAME = current_app.config['USERS_COLLECTION_NAME']
    user_to_edit = current_app.db[USERS_COLLECTION_NAME].find_one({'_id': ObjectId(user_id)})
    
    if not user_to_edit:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.user_list'))

    if request.method == 'POST':
        new_password = request.form.get('new_password')
        if not new_password or len(new_password) < 8:
            flash('Password must be at least 8 characters long.', 'danger')
        else:
            hashed_password = generate_password_hash(new_password)
            current_app.db[USERS_COLLECTION_NAME].update_one(
                {'_id': ObjectId(user_id)},
                {'$set': {'password': hashed_password}}
            )
            flash(f"Password for user '{user_to_edit['username']}' has been updated successfully.", 'success')
            return redirect(url_for('admin.user_list'))

    return render_template('admin/change_password.html', user_to_edit=user_to_edit)


# --- Regular User Route (for changing their own password) ---
# Note: We place this in the admin blueprint for organization, but it's not admin-only.
@admin_bp.route('/change_my_password', methods=['GET', 'POST'])
@login_required
def user_change_password():
    """Allows a logged-in user to change their own password."""
    USERS_COLLECTION_NAME = current_app.config['USERS_COLLECTION_NAME']
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')

        # Verify current password
        if not check_password_hash(current_user.password_hash, current_password):
            flash('Your current password was incorrect. Please try again.', 'danger')
        elif not new_password or len(new_password) < 8:
            flash('New password must be at least 8 characters long.', 'danger')
        else:
            hashed_password = generate_password_hash(new_password)
            current_app.db[USERS_COLLECTION_NAME].update_one(
                {'_id': ObjectId(current_user.id)},
                {'$set': {'password': hashed_password}}
            )
            flash('Your password has been updated successfully.', 'success')
            return redirect(url_for('migration.dashboard')) # Redirect to a safe page after success
    
    return render_template('admin/change_my_password.html')