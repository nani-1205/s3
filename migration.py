# s3_migration_app/migration.py

import threading
import time
import json
from flask import Blueprint, render_template, jsonify, request, Response, stream_with_context, current_app
from flask_login import login_required, current_user
from bson.objectid import ObjectId
import boto3
import botocore

migration_bp = Blueprint('migration', __name__)

def get_live_migration_state():
    MIGRATION_STATE_COLLECTION_NAME = current_app.config['MIGRATION_STATE_COLLECTION_NAME']
    state = current_app.db[MIGRATION_STATE_COLLECTION_NAME].find_one({'_id': 'live_migration_status'})
    return state if state else {'is_running': False}

def do_migration_with_checkpointing(app_context, migration_id_str, config):
    with app_context:
        MIGRATION_HISTORY_COLLECTION_NAME = current_app.config['MIGRATION_HISTORY_COLLECTION_NAME']
        MIGRATION_STATE_COLLECTION_NAME = current_app.config['MIGRATION_STATE_COLLECTION_NAME']
        migration_id = ObjectId(migration_id_str)
        
        def log_to_db(level, message):
            timestamp = time.time()
            log_entry = {'level': level, 'message': message, 'timestamp': timestamp}
            current_app.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one(
                {'_id': migration_id},
                {'$push': {'logs': {'$each': [log_entry], '$slice': -100}}}
            )

        log_to_db('info', 'Migration task started.')
        current_app.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one(
            {'_id': migration_id}, {'$set': {'status': 'running', 'start_time': time.time()}}
        )
        
        try:
            source_s3 = boto3.client(
                's3', region_name=config['source_region'],
                aws_access_key_id=config.get('source_access_key') or None,
                aws_secret_access_key=config.get('source_secret_key') or None,
                config=botocore.client.Config(signature_version='s3v4')
            )
            dest_s3 = boto3.client(
                's3', region_name=config['dest_region'],
                aws_access_key_id=config.get('dest_access_key') or None,
                aws_secret_access_key=config.get('dest_secret_key') or None,
                config=botocore.client.Config(signature_version='s3v4')
            )
            log_to_db('info', 'S3 clients initialized.')
        except Exception as e:
            log_to_db('error', f"Failed to initialize S3 clients: {e}")
            current_app.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one({'_id': migration_id}, {'$set': {'status': 'failed'}})
            current_app.db[MIGRATION_STATE_COLLECTION_NAME].update_one({'_id': 'live_migration_status'}, {'$set': {'is_running': False}})
            return
            
        try:
            log_to_db('info', f"Scanning source: s3://{config['source_bucket']}/{config['source_prefix']}")
            all_source_objects = [obj for page in source_s3.get_paginator('list_objects_v2').paginate(Bucket=config['source_bucket'], Prefix=config['source_prefix']) for obj in page.get('Contents', [])]
            
            log_to_db('info', f"Scanning destination: s3://{config['dest_bucket']}/{config['dest_prefix']}")
            dest_files_map = {item['Key']: item['Size'] for page in dest_s3.get_paginator('list_objects_v2').paginate(Bucket=config['dest_bucket'], Prefix=config['dest_prefix']) for item in page.get('Contents', [])}
            
            source_prefix_len = len(config['source_prefix'])
            dest_prefix = config['dest_prefix']
            files_to_copy = [obj for obj in all_source_objects if (dest_prefix + obj['Key'][source_prefix_len:]) not in dest_files_map or dest_files_map[dest_prefix + obj['Key'][source_prefix_len:]] != obj['Size']]
            
            total_files_to_copy = len(files_to_copy)
            log_to_db('info', f"Found {len(all_source_objects)} source files. Checkpointing determined {total_files_to_copy} files need to be copied.")
            current_app.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one({'_id': migration_id}, {'$set': {'total_files': total_files_to_copy, 'files_scanned': len(all_source_objects)}})
            
            copied_count, failed_count = 0, 0
            for i, obj_to_copy in enumerate(files_to_copy):
                source_key = obj_to_copy['Key']; relative_key = source_key[source_prefix_len:]
                dest_key = dest_prefix + relative_key
                try:
                    dest_s3.copy_object(CopySource={'Bucket': config['source_bucket'], 'Key': source_key}, Bucket=config['dest_bucket'], Key=dest_key)
                    copied_count += 1
                except Exception as e:
                    failed_count += 1; log_to_db('error', f"Failed to copy {source_key}: {e}")
                
                if (i + 1) % 10 == 0 or (i + 1) == total_files_to_copy:
                    progress = ((i + 1) / total_files_to_copy) * 100 if total_files_to_copy > 0 else 100
                    current_app.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one(
                        {'_id': migration_id},
                        {'$set': {'progress': progress, 'files_completed': copied_count, 'files_failed': failed_count, 'last_log': f"Processed {i+1}/{total_files_to_copy}: {source_key.split('/')[-1]}"}}
                    )

            final_status = 'completed_partial' if failed_count > 0 else 'completed_fully'
            log_to_db('info', f"Migration finished. Status: {final_status}. Copied: {copied_count}, Failed: {failed_count}.")
            current_app.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one({'_id': migration_id}, {'$set': {'status': final_status, 'end_time': time.time(), 'progress': 100}})
        except Exception as e:
            current_app.logger.error(f"A critical error occurred in migration {migration_id_str}: {e}", exc_info=True)
            log_to_db('error', f"CRITICAL ERROR: {e}")
            current_app.db[MIGRATION_HISTORY_COLLECTION_NAME].update_one({'_id': migration_id}, {'$set': {'status': 'failed'}})
        finally:
            current_app.db[MIGRATION_STATE_COLLECTION_NAME].update_one({'_id': 'live_migration_status'}, {'$set': {'is_running': False}})

@migration_bp.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@migration_bp.route('/trigger-migration', methods=['POST'])
@login_required
def trigger_migration():
    MIGRATION_HISTORY_COLLECTION_NAME = current_app.config['MIGRATION_HISTORY_COLLECTION_NAME']
    MIGRATION_STATE_COLLECTION_NAME = current_app.config['MIGRATION_STATE_COLLECTION_NAME']
    
    if get_live_migration_state().get('is_running'):
        return jsonify({'status': 'error', 'message': 'A migration is already in progress.'}), 409
    
    config = {k: v for k, v in request.form.items()}
    if not all([config.get('source_region'), config.get('source_bucket'), config.get('dest_region'), config.get('dest_bucket')]):
        return jsonify({'status': 'error', 'message': 'Missing required fields: regions and bucket names.'}), 400

    migration_record = {'user_id': current_user.id, 'username': current_user.username, 'status': 'starting', 'config': {k: v for k, v in config.items() if 'secret' not in k and 'key' not in k}, 'start_time': time.time(), 'progress': 0, 'logs': []}
    result = current_app.db[MIGRATION_HISTORY_COLLECTION_NAME].insert_one(migration_record)
    migration_id = str(result.inserted_id)
    
    current_app.db[MIGRATION_STATE_COLLECTION_NAME].update_one({'_id': 'live_migration_status'}, {'$set': {'is_running': True, 'current_migration_id': migration_id}}, upsert=True)
    
    thread = threading.Thread(target=do_migration_with_checkpointing, args=(current_app.app_context(), migration_id, config), daemon=True)
    thread.start()
    
    return jsonify({'status': 'success', 'message': 'Migration initiated successfully.', 'migration_id': migration_id})

@migration_bp.route('/migration-status-stream')
@login_required
def migration_status_stream():
    MIGRATION_HISTORY_COLLECTION_NAME = current_app.config['MIGRATION_HISTORY_COLLECTION_NAME']
    def generate():
        last_sent_json = ""
        while True:
            live_state = get_live_migration_state()
            data_to_send = {'is_running': live_state.get('is_running', False)}
            
            if live_state.get('is_running'):
                migration_id = live_state.get('current_migration_id')
                if migration_id:
                    progress_data = current_app.db[MIGRATION_HISTORY_COLLECTION_NAME].find_one({'_id': ObjectId(migration_id)}, {'logs': {'$slice': -20}})
                    if progress_data:
                        data_to_send.update(progress_data)

            current_state_json = json.dumps(data_to_send, default=str)
            if current_state_json != last_sent_json:
                yield f"data: {current_state_json}\n\n"
                last_sent_json = current_state_json
            else:
                yield ": KEEPALIVE\n\n"
            
            time.sleep(1.5)
            
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@migration_bp.route('/history')
@login_required
def history():
    MIGRATION_HISTORY_COLLECTION_NAME = current_app.config['MIGRATION_HISTORY_COLLECTION_NAME']
    migrations = list(current_app.db[MIGRATION_HISTORY_COLLECTION_NAME].find({'user_id': current_user.id}).sort('start_time', -1).limit(50))
    return render_template('history.html', migrations=migrations)