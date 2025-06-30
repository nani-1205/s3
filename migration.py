# s3_migration_app/migration.py

import threading
import time
import json
from flask import Blueprint, render_template, jsonify, request, Response, stream_with_context
from flask_login import login_required, current_user
from bson.objectid import ObjectId
from app import mongo, MIGRATION_HISTORY_COLLECTION_NAME, MIGRATION_STATE_COLLECTION_NAME, app # Import mongo, collection names, and the app object for logging
import boto3
import botocore

migration_bp = Blueprint('migration', __name__)

# --- Helper to get the current state doc from MongoDB ---
def get_live_migration_state():
    state = mongo.db[MIGRATION_STATE_COLLECTION_NAME].find_one({'_id': 'live_migration_status'})
    if not state:
        state = {
            '_id': 'live_migration_status', 'is_running': False, 'progress': 0, 
            'current_migration_id': None, 'last_updated': time.time()
        }
        mongo.db[MIGRATION_STATE_COLLECTION_NAME].insert_one(state)
    return state

# --- Core Migration Task (with Checkpointing) ---
def do_migration_with_checkpointing(migration_id_str, config):
    migration_id = ObjectId(migration_id_str)
    
    def log_to_db(level, message):
        """Helper to log messages to the migration history document."""
        timestamp = time.time()
        log_entry = {'level': level, 'message': message, 'timestamp': timestamp}
        mongo.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one(
            {'_id': migration_id},
            {'$push': {'logs': {'$each': [log_entry], '$slice': -100}}} # Keep last 100 logs
        )

    log_to_db('info', 'Migration task started.')
    
    # 1. Update status in DB to "Running"
    mongo.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one(
        {'_id': migration_id},
        {'$set': {'status': 'running', 'start_time': time.time()}}
    )
    
    # 2. Initialize S3 clients
    try:
        source_s3 = boto3.client(
            's3', region_name=config['source_region'],
            aws_access_key_id=config.get('source_access_key'),
            aws_secret_access_key=config.get('source_secret_key'),
            config=botocore.client.Config(signature_version='s3v4')
        )
        dest_s3 = boto3.client(
            's3', region_name=config['dest_region'],
            aws_access_key_id=config.get('dest_access_key'),
            aws_secret_access_key=config.get('dest_secret_key'),
            config=botocore.client.Config(signature_version='s3v4')
        )
        log_to_db('info', 'S3 clients initialized successfully.')
    except Exception as e:
        log_to_db('error', f"Failed to initialize S3 clients: {e}")
        mongo.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one({'_id': migration_id}, {'$set': {'status': 'failed'}})
        mongo.db[MIGRATION_STATE_COLLECTION_NAME].update_one({'_id': 'live_migration_status'}, {'$set': {'is_running': False}})
        return
        
    # 3. List all source objects
    log_to_db('info', f"Scanning source bucket: {config['source_bucket']}/{config['source_prefix']}")
    all_source_objects = []
    try:
        paginator = source_s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=config['source_bucket'], Prefix=config['source_prefix']):
            if 'Contents' in page:
                all_source_objects.extend(page['Contents'])
    except Exception as e:
        log_to_db('error', f"Failed to list source bucket objects: {e}")
        mongo.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one({'_id': migration_id}, {'$set': {'status': 'failed'}})
        mongo.db[MIGRATION_STATE_COLLECTION_NAME].update_one({'_id': 'live_migration_status'}, {'$set': {'is_running': False}})
        return

    # 4. List all destination objects (for checkpointing)
    log_to_db('info', f"Scanning destination bucket for existing files: {config['dest_bucket']}/{config['dest_prefix']}")
    all_dest_keys = set()
    try:
        paginator = dest_s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=config['dest_bucket'], Prefix=config['dest_prefix']):
            if 'Contents' in page:
                for item in page['Contents']:
                    all_dest_keys.add(item['Key'])
    except Exception as e:
        log_to_db('warning', f"Could not list destination bucket (will attempt to copy all files): {e}")


    # 5. Determine files to copy
    files_to_copy = []
    source_prefix_len = len(config['source_prefix'])
    for obj in all_source_objects:
        source_key = obj['Key']
        # Calculate destination key
        relative_key = source_key[source_prefix_len:]
        dest_key = config['dest_prefix'] + relative_key
        # Check if destination key exists AND has same size (basic checkpointing)
        if dest_key not in all_dest_keys: # A more robust check would compare ETag and Size
            files_to_copy.append(obj)
    
    total_files_to_copy = len(files_to_copy)
    log_to_db('info', f"Found {len(all_source_objects)} total source files. Need to copy {total_files_to_copy} new/updated files.")
    mongo.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one(
        {'_id': migration_id},
        {'$set': {'total_files': total_files_to_copy, 'files_scanned': len(all_source_objects)}}
    )

    # 6. Loop and copy files
    copied_count = 0
    failed_count = 0
    for obj_to_copy in files_to_copy:
        source_key = obj_to_copy['Key']
        relative_key = source_key[source_prefix_len:]
        dest_key = config['dest_prefix'] + relative_key
        
        try:
            copy_source = {'Bucket': config['source_bucket'], 'Key': source_key}
            dest_s3.copy_object(CopySource=copy_source, Bucket=config['dest_bucket'], Key=dest_key)
            copied_count += 1
        except Exception as e:
            failed_count += 1
            log_to_db('error', f"Failed to copy {source_key} to {dest_key}: {e}")
        
        # Update progress in MongoDB periodically to avoid too many writes
        if (copied_count + failed_count) % 10 == 0 or (copied_count + failed_count) == total_files_to_copy:
            progress = ((copied_count + failed_count) / total_files_to_copy) * 100 if total_files_to_copy > 0 else 100
            mongo.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one(
                {'_id': migration_id},
                {'$set': {
                    'progress': progress, 
                    'files_completed': copied_count,
                    'files_failed': failed_count,
                    'last_log': f"Processed {copied_count + failed_count}/{total_files_to_copy}"
                }}
            )

    # 7. Update status to "Completed"
    final_status = 'completed_partial' if failed_count > 0 else 'completed_fully'
    log_to_db('info', f"Migration finished. Status: {final_status}. Copied: {copied_count}, Failed: {failed_count}.")
    mongo.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one(
        {'_id': migration_id},
        {'$set': {'status': final_status, 'end_time': time.time(), 'progress': 100}}
    )
    # Reset the live state tracker
    mongo.db[MIGRATION_STATE_COLLECTION_NAME].update_one({'_id': 'live_migration_status'}, {'$set': {'is_running': False}})


# --- Routes for Migration ---

@migration_bp.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@migration_bp.route('/trigger-migration', methods=['POST'])
@login_required
def trigger_migration():
    live_state = get_live_migration_state()
    if live_state.get('is_running'):
        return jsonify({'status': 'error', 'message': 'A migration is already in progress.'}), 409
    
    # Get configuration from the form post and validate
    config = {
        'source_access_key': request.form.get('source_access_key'),
        'source_secret_key': request.form.get('source_secret_key'),
        'source_region': request.form.get('source_region'),
        'source_bucket': request.form.get('source_bucket'),
        'source_prefix': request.form.get('source_prefix'),
        'dest_access_key': request.form.get('dest_access_key'),
        'dest_secret_key': request.form.get('dest_secret_key'),
        'dest_region': request.form.get('dest_region'),
        'dest_bucket': request.form.get('dest_bucket'),
        'dest_prefix': request.form.get('dest_prefix'),
    }
    
    # Basic validation
    if not all([config['source_region'], config['source_bucket'], config['dest_region'], config['dest_bucket']]):
        return jsonify({'status': 'error', 'message': 'Missing required fields: regions and bucket names.'}), 400

    migration_record = {
        'user_id': current_user.id,
        'username': current_user.username,
        'status': 'starting',
        'config': {k: v for k, v in config.items() if 'secret' not in k}, # Don't store secrets in DB
        'start_time': time.time(),
        'progress': 0,
        'logs': [{'level': 'info', 'message': 'Migration initiated by user.', 'timestamp': time.time()}]
    }
    result = mongo.db[MIGRATION_HISTORY_COLLECTION_NAME].insert_one(migration_record)
    migration_id = str(result.inserted_id)
    
    mongo.db[MIGRATION_STATE_COLLECTION_NAME].update_one(
        {'_id': 'live_migration_status'},
        {'$set': {'is_running': True, 'current_migration_id': migration_id}},
        upsert=True
    )
    
    # Pass the full config with secrets to the background thread
    thread = threading.Thread(target=do_migration_with_checkpointing, args=(migration_id, config))
    thread.daemon = True
    thread.start()
    
    return jsonify({'status': 'success', 'message': 'Migration initiated.', 'migration_id': migration_id})

@migration_bp.route('/migration-status-stream')
@login_required
def migration_status_stream():
    def generate():
        last_sent_json = ""
        while True:
            live_state = get_live_migration_state()
            data_to_send = {'is_running': live_state.get('is_running', False)}
            
            if live_state.get('is_running'):
                migration_id = live_state.get('current_migration_id')
                if migration_id:
                    progress_data = mongo.db[MIGRATION_HISTORY_COLLECTION_NAME].find_one(
                        {'_id': ObjectId(migration_id)},
                        {
                            'progress': 1, 'files_completed': 1, 'files_failed': 1,
                            'total_files': 1, 'status': 1, 'config': 1,
                            'logs': {'$slice': -20} # Get last 20 logs
                        }
                    )
                    if progress_data:
                        data_to_send.update(progress_data)

            current_state_json = json.dumps(data_to_send, default=str)
            if current_state_json != last_sent_json:
                yield f"data: {current_state_json}\n\n"
                last_sent_json = current_state_json
            else:
                yield ": KEEPALIVE\n\n"
            
            time.sleep(1) # Update every second
            
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@migration_bp.route('/history')
@login_required
def history():
    migrations = list(mongo.db[MIGRATION_HISTORY_COLLECTION_NAME].find({'user_id': current_user.id}).sort('start_time', -1).limit(50))
    return render_template('history.html', migrations=migrations)