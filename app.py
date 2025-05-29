import os
import boto3
import botocore
import json
import time
import threading
from flask import Flask, render_template, Response, stream_with_context, jsonify, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- Configuration ---
SOURCE_S3_REGION = os.getenv("SOURCE_S3_REGION")
SOURCE_S3_BUCKET = os.getenv("SOURCE_S3_BUCKET")
SOURCE_S3_PREFIX = os.getenv("SOURCE_S3_PREFIX", "")

DEST_S3_REGION = os.getenv("DEST_S3_REGION")
DEST_S3_BUCKET = os.getenv("DEST_S3_BUCKET")
DEST_S3_PREFIX = os.getenv("DEST_S3_PREFIX", "")

# --- Shared Migration State ---
MIGRATION_STATE = {} 
STATE_LOCK = threading.Lock()
MAX_LOG_MESSAGES = 100

def reset_migration_state():
    with STATE_LOCK:
        MIGRATION_STATE.clear() 
        MIGRATION_STATE.update({
            "is_running": False,
            "total_files": 0,
            "total_size_bytes": 0,
            "files_processed_count": 0,
            "data_transferred_bytes_total": 0,
            "progress_percentage_files": 0,
            "transfer_speed_bps": 0,
            "eta_seconds": 0,
            "current_file_name": None,
            "current_file_size_bytes": 0,
            "log_messages": [],
            "completion_status": None,
            "final_message": None,
            "start_time": None,
            "last_updated": time.time()
        })
        # app.logger.info("Migration state has been reset.")

def add_log_to_state(text, type='info'):
    with STATE_LOCK:
        current_time_str = time.strftime('%H:%M:%S')
        log_entry = {"text": f"[{current_time_str}] {text}", "type": type, "timestamp": time.time()}
        
        MIGRATION_STATE["log_messages"].append(log_entry)
        MIGRATION_STATE["log_messages"] = sorted(MIGRATION_STATE["log_messages"], key=lambda x: x["timestamp"])[-MAX_LOG_MESSAGES:]
        MIGRATION_STATE["last_updated"] = time.time()

def update_migration_state(updates_dict):
    with STATE_LOCK:
        MIGRATION_STATE.update(updates_dict)
        MIGRATION_STATE["last_updated"] = time.time()

# --- AWS Helper Functions ---
def get_aws_account_id(access_key, secret_key, region_name):
    try:
        if not access_key or not secret_key:
            return "N/A (Keys Not Provided)"
        sts_client = boto3.client(
            'sts', aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            region_name=region_name if region_name else 'us-east-1'
        )
        return sts_client.get_caller_identity().get('Account')
    except botocore.exceptions.ClientError as e:
        app.logger.error(f"STS ClientError getting Account ID (Region: {region_name}): {e.response.get('Error', {}).get('Message', str(e))}")
        return "N/A (STS Error)"
    except Exception as e:
        app.logger.error(f"Could not get Account ID (Region: {region_name}): {str(e)}")
        return "N/A (Error)"

def get_s3_client_with_env_fallback(access_key_env_name, secret_key_env_name, region_env_name, client_type="Generic"):
    access_key = os.getenv(access_key_env_name)
    secret_key = os.getenv(secret_key_env_name)
    region = os.getenv(region_env_name)

    if access_key and secret_key and region:
        return boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=botocore.client.Config(signature_version='s3v4', retries={'max_attempts': 5, 'mode': 'standard'})
        )
    elif region:
        return boto3.client(
            's3',
            region_name=region,
            config=botocore.client.Config(signature_version='s3v4', retries={'max_attempts': 5, 'mode': 'standard'})
        )
    else:
        err_msg = f"Missing region for {client_type} S3 client (env var: {region_env_name}), and no credentials provided in .env."
        app.logger.error(err_msg)
        raise ValueError(err_msg)

# --- Core Migration Logic ---
def do_actual_migration_task():
    with STATE_LOCK:
        MIGRATION_STATE["is_running"] = True
        MIGRATION_STATE["start_time"] = time.time()
        MIGRATION_STATE["total_files"] = 0
        MIGRATION_STATE["total_size_bytes"] = 0
        MIGRATION_STATE["files_processed_count"] = 0
        MIGRATION_STATE["data_transferred_bytes_total"] = 0
        MIGRATION_STATE["progress_percentage_files"] = 0
        MIGRATION_STATE["transfer_speed_bps"] = 0
        MIGRATION_STATE["eta_seconds"] = 0
        MIGRATION_STATE["current_file_name"] = None
        MIGRATION_STATE["current_file_size_bytes"] = 0
        MIGRATION_STATE["completion_status"] = None
        MIGRATION_STATE["final_message"] = None
        MIGRATION_STATE["log_messages"] = [] 
        MIGRATION_STATE["last_updated"] = time.time()
    add_log_to_state('Migration process started in background thread.', 'info')

    try:
        source_s3 = get_s3_client_with_env_fallback("SOURCE_AWS_ACCESS_KEY_ID", "SOURCE_AWS_SECRET_ACCESS_KEY", SOURCE_S3_REGION, "Source")
        dest_s3 = get_s3_client_with_env_fallback("DEST_AWS_ACCESS_KEY_ID", "DEST_AWS_SECRET_ACCESS_KEY", DEST_S3_REGION, "Destination")
        add_log_to_state('Successfully initialized S3 clients.', 'info')
    except Exception as e:
        error_msg = f'Error initializing S3 clients: {str(e)}'
        app.logger.error(error_msg)
        add_log_to_state(error_msg, 'error')
        update_migration_state({
            "is_running": False, "completion_status": "failed", "final_message": error_msg
        })
        return

    source_prefix_effective = SOURCE_S3_PREFIX.strip()
    dest_prefix_effective = DEST_S3_PREFIX.strip()
    
    try:
        add_log_to_state(f'Listing objects from s3://{SOURCE_S3_BUCKET}/{source_prefix_effective} ...', 'info')
        objects_to_copy_meta = []
        current_total_size_bytes_scan = 0
        paginator = source_s3.get_paginator('list_objects_v2')
        list_args = {'Bucket': SOURCE_S3_BUCKET}
        if source_prefix_effective: list_args['Prefix'] = source_prefix_effective
        page_iterator = paginator.paginate(**list_args)

        for page in page_iterator:
            if "Contents" in page:
                for obj in page["Contents"]:
                    if source_prefix_effective and obj['Key'] == source_prefix_effective and obj.get('Size', 0) == 0: continue
                    if obj.get('Size', 0) > 0:
                        objects_to_copy_meta.append({'Key': obj['Key'], 'Size': obj['Size']})
                        current_total_size_bytes_scan += obj['Size']
        
        update_migration_state({
            "total_files": len(objects_to_copy_meta),
            "total_size_bytes": current_total_size_bytes_scan
        })
        add_log_to_state(f'Scan complete: Found {MIGRATION_STATE["total_files"]} object(s) totaling {MIGRATION_STATE["total_size_bytes"] / (1024*1024):.2f} MB.', 'info')

        if MIGRATION_STATE["total_files"] == 0:
            msg = 'No objects found in source path to migrate.'
            add_log_to_state(msg, 'info')
            update_migration_state({
                "is_running": False, "completion_status": "completed_fully", "final_message": msg
            })
            return

        copied_count = 0
        failed_count = 0
        data_transferred_this_run = 0

        for i, obj_meta in enumerate(objects_to_copy_meta):
            with STATE_LOCK:
                if not MIGRATION_STATE["is_running"]:
                    add_log_to_state("Migration was stopped or cancelled.", "warning")
                    break
            
            source_key = obj_meta['Key']
            file_size_bytes = obj_meta['Size']
            
            update_migration_state({
                "current_file_name": source_key.split('/')[-1],
                "current_file_size_bytes": file_size_bytes
            })

            relative_key = source_key[len(source_prefix_effective):] if source_prefix_effective and source_key.startswith(source_prefix_effective) else source_key
            relative_key = relative_key.lstrip('/')
            destination_key = dest_prefix_effective.rstrip('/') + '/' + relative_key if dest_prefix_effective else relative_key
            destination_key = destination_key.replace('//', '/')

            copy_source = {'Bucket': SOURCE_S3_BUCKET, 'Key': source_key}
            try:
                dest_s3.copy_object(CopySource=copy_source, Bucket=DEST_S3_BUCKET, Key=destination_key)
                copied_count += 1
                data_transferred_this_run += file_size_bytes
            except Exception as e:
                failed_count += 1
                err_msg_file = f'Error copying {source_key.split("/")[-1]}: {str(e)}'
                app.logger.error(err_msg_file)
                add_log_to_state(err_msg_file, 'error')

            processed_this_loop = copied_count + failed_count
            prog_files_now = (processed_this_loop / MIGRATION_STATE["total_files"] * 100) if MIGRATION_STATE["total_files"] > 0 else 0
            elapsed_time_now = time.time() - MIGRATION_STATE["start_time"]
            speed_bps_now = (data_transferred_this_run / elapsed_time_now) if elapsed_time_now > 0 else 0
            eta_now = 0
            if speed_bps_now > 0 and MIGRATION_STATE["total_size_bytes"] > data_transferred_this_run:
                remaining_bytes = MIGRATION_STATE["total_size_bytes"] - data_transferred_this_run
                eta_now = remaining_bytes / speed_bps_now
            
            update_migration_state({
                "files_processed_count": processed_this_loop,
                "data_transferred_bytes_total": data_transferred_this_run,
                "progress_percentage_files": prog_files_now,
                "transfer_speed_bps": speed_bps_now,
                "eta_seconds": eta_now
            })
            time.sleep(0.005)

        final_msg_text = f"Migration run finished. Copied {copied_count} of {MIGRATION_STATE['total_files']} objects."
        final_status_now = "completed_fully"
        if failed_count > 0:
            final_status_now = "completed_partial"
            final_msg_text += f" {failed_count} object(s) failed."
        
        add_log_to_state(final_msg_text, 'success' if failed_count == 0 else 'warning')
        update_migration_state({
            "is_running": False, "completion_status": final_status_now, "final_message": final_msg_text,
            "current_file_name": None, "current_file_size_bytes": 0
        })

    except Exception as e:
        error_msg_main = f'A critical error occurred during migration: {str(e)}'
        app.logger.error(error_msg_main, exc_info=True)
        add_log_to_state(error_msg_main, 'error')
        update_migration_state({
            "is_running": False, "completion_status": "failed", "final_message": error_msg_main
        })

# --- Flask Routes ---
@app.route('/')
def index_route(): # Changed function name to avoid conflict if you have other 'index'
    env_vars_display = {
        "SOURCE_S3_REGION": SOURCE_S3_REGION, "SOURCE_S3_BUCKET": SOURCE_S3_BUCKET, "SOURCE_S3_PREFIX": SOURCE_S3_PREFIX,
        "DEST_S3_REGION": DEST_S3_REGION, "DEST_S3_BUCKET": DEST_S3_BUCKET, "DEST_S3_PREFIX": DEST_S3_PREFIX,
    }
    src_acc_id = get_aws_account_id(os.getenv("SOURCE_AWS_ACCESS_KEY_ID"), os.getenv("SOURCE_AWS_SECRET_ACCESS_KEY"), SOURCE_S3_REGION)
    dest_acc_id = get_aws_account_id(os.getenv("DEST_AWS_ACCESS_KEY_ID"), os.getenv("DEST_AWS_SECRET_ACCESS_KEY"), DEST_S3_REGION)
    return render_template('index.html', env_vars=env_vars_display, source_account_id=src_acc_id, dest_account_id=dest_acc_id)

@app.route('/trigger-migration', methods=['POST'])
def trigger_migration_route(): # This is the correct endpoint name
    with STATE_LOCK:
        if MIGRATION_STATE.get("is_running", False):
            return jsonify({"status": "error", "message": "A migration is already in progress."}), 409
    
    migration_thread = threading.Thread(target=do_actual_migration_task, daemon=True)
    migration_thread.start()
    
    return jsonify({"status": "success", "message": "Migration process initiated."})

@app.route('/migration-status-stream')
def migration_status_stream():
    def generate_status_updates():
        last_state_sent_json = "" 
        while True:
            with STATE_LOCK:
                current_state_snapshot = dict(MIGRATION_STATE) 
                current_state_snapshot["log_messages"] = list(MIGRATION_STATE.get("log_messages", []))
            
            current_state_json = json.dumps(current_state_snapshot)

            if current_state_json != last_state_sent_json:
                yield f"data: {current_state_json}\n\n"
                last_state_sent_json = current_state_json
            else: 
                yield ": KEEPALIVE\n\n"
            time.sleep(0.75)
    return Response(stream_with_context(generate_status_updates()), mimetype='text/event-stream')

if __name__ == '__main__':
    reset_migration_state() 
    add_log_to_state("Application server started and ready.", "info")
    app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", host='0.0.0.0', port=5000, threaded=True)