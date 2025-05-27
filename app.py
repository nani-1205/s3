import os
import boto3
import botocore
import json
import time
from flask import Flask, render_template, Response, stream_with_context
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

SOURCE_AWS_ACCESS_KEY_ID = os.getenv("SOURCE_AWS_ACCESS_KEY_ID")
SOURCE_AWS_SECRET_ACCESS_KEY = os.getenv("SOURCE_AWS_SECRET_ACCESS_KEY")
SOURCE_S3_REGION = os.getenv("SOURCE_S3_REGION")
SOURCE_S3_BUCKET = os.getenv("SOURCE_S3_BUCKET")
SOURCE_S3_PREFIX = os.getenv("SOURCE_S3_PREFIX", "")

DEST_AWS_ACCESS_KEY_ID = os.getenv("DEST_AWS_ACCESS_KEY_ID")
DEST_AWS_SECRET_ACCESS_KEY = os.getenv("DEST_AWS_SECRET_ACCESS_KEY")
DEST_S3_REGION = os.getenv("DEST_S3_REGION")
DEST_S3_BUCKET = os.getenv("DEST_S3_BUCKET")
DEST_S3_PREFIX = os.getenv("DEST_S3_PREFIX", "")

def get_aws_account_id(access_key, secret_key, region):
    try:
        sts_client = boto3.client(
            'sts',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region if region else 'us-east-1'
        )
        return sts_client.get_caller_identity().get('Account')
    except botocore.exceptions.ClientError as e:
        app.logger.error(f"STS ClientError getting Account ID (Region: {region}): {e.response['Error']['Message']}")
        if "InvalidClientTokenId" in str(e) or "SignatureDoesNotMatch" in str(e):
            app.logger.error("This often means AWS Keys are incorrect or malformed for STS.")
        elif "explicitly denied" in str(e):
            app.logger.error("The IAM user/role lacks sts:GetCallerIdentity permission.")
    except Exception as e:
        app.logger.error(f"Could not get Account ID (Region: {region}): {e}")
    return "N/A (Error)" # Return a string indicating error for display

def get_s3_client(access_key, secret_key, region, client_type="Source"):
    if not all([access_key, secret_key, region]):
        raise ValueError(f"Missing AWS credentials or region for {client_type} S3 client.")
    return boto3.client(
        's3',
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=botocore.client.Config(signature_version='s3v4')
    )

def sse_message(data):
    return f"data: {json.dumps(data)}\n\n"

def migrate_s3_data():
    migration_start_time = time.time()
    try:
        source_s3 = get_s3_client(SOURCE_AWS_ACCESS_KEY_ID, SOURCE_AWS_SECRET_ACCESS_KEY, SOURCE_S3_REGION, "Source")
        dest_s3 = get_s3_client(DEST_AWS_ACCESS_KEY_ID, DEST_AWS_SECRET_ACCESS_KEY, DEST_S3_REGION, "Destination")
        yield sse_message({'event_type': 'log', 'message_text': 'Successfully initialized S3 clients.', 'message_type': 'info'})
    except ValueError as e:
        yield sse_message({'event_type': 'error', 'error_message': str(e), 'critical': True, 'status': 'failed'})
        return
    except botocore.exceptions.ClientError as e:
        err_msg = e.response.get("Error", {}).get("Message", str(e))
        yield sse_message({'event_type': 'error', 'error_message': f'Error initializing S3 clients: {err_msg}', 'critical': True, 'status': 'failed'})
        return
    except Exception as e:
        yield sse_message({'event_type': 'error', 'error_message': f'Unexpected error during S3 client init: {str(e)}', 'critical': True, 'status': 'failed'})
        return

    source_prefix_effective = SOURCE_S3_PREFIX.strip()
    dest_prefix_effective = DEST_S3_PREFIX.strip()

    try:
        yield sse_message({'event_type': 'log', 'message_text': f'Listing objects from s3://{SOURCE_S3_BUCKET}/{source_prefix_effective} ...', 'message_type': 'info'})
        
        objects_to_copy_meta = []
        total_size_bytes = 0
        paginator = source_s3.get_paginator('list_objects_v2')
        
        # Handle case where prefix might be empty (list whole bucket)
        list_args = {'Bucket': SOURCE_S3_BUCKET}
        if source_prefix_effective:
            list_args['Prefix'] = source_prefix_effective

        page_iterator = paginator.paginate(**list_args)
        
        for page in page_iterator:
            if "Contents" in page:
                for obj in page["Contents"]:
                    # Skip if it's the "folder" object itself and has size 0
                    if source_prefix_effective and obj['Key'] == source_prefix_effective and obj.get('Size', 0) == 0: 
                        continue
                    if obj.get('Size', 0) > 0 :
                        objects_to_copy_meta.append({'Key': obj['Key'], 'Size': obj['Size']})
                        total_size_bytes += obj['Size']
        
        total_files = len(objects_to_copy_meta)

        yield sse_message({
            'event_type': 'initial_stats',
            'total_files': total_files,
            'total_size_bytes': total_size_bytes,
            'message': f'Found {total_files} object(s) totaling {total_size_bytes / (1024*1024):.2f} MB to migrate.'
        })

        if total_files == 0:
            yield sse_message({
                'event_type': 'migration_complete', 
                'status': 'completed_fully',
                'final_message': 'No objects found in source path to migrate.',
                'total_time_seconds': time.time() - migration_start_time
            })
            return

        copied_count = 0
        failed_count = 0
        data_transferred_bytes_total = 0

        for i, obj_meta in enumerate(objects_to_copy_meta):
            source_key = obj_meta['Key']
            file_size_bytes = obj_meta['Size']
            
            yield sse_message({
                'event_type': 'file_start',
                'file_name': source_key.split('/')[-1], # Show only filename
                'file_size_bytes': file_size_bytes
            })

            relative_key = source_key[len(source_prefix_effective):] if source_prefix_effective and source_key.startswith(source_prefix_effective) else source_key
            relative_key = relative_key.lstrip('/') # Ensure no leading slash if source_prefix was empty or root
            
            destination_key = f"{dest_prefix_effective}{relative_key}".replace('//', '/')
            if dest_prefix_effective and not dest_prefix_effective.endswith('/') and relative_key :
                 destination_key = f"{dest_prefix_effective}/{relative_key}".replace('//','/') # Ensure separator if dest_prefix is not just root and relative key exists
            elif not dest_prefix_effective and relative_key:
                 destination_key = relative_key # Copy to root if dest_prefix is empty
            elif dest_prefix_effective.endswith('/') and relative_key:
                 destination_key = f"{dest_prefix_effective}{relative_key}".replace('//','/')
            elif dest_prefix_effective and not relative_key: # copying a single file named as prefix
                 destination_key = dest_prefix_effective


            copy_source = {'Bucket': SOURCE_S3_BUCKET, 'Key': source_key}
            file_status = "success"
            error_msg_for_file = None
            
            try:
                dest_s3.copy_object(CopySource=copy_source, Bucket=DEST_S3_BUCKET, Key=destination_key)
                copied_count += 1
                data_transferred_bytes_total += file_size_bytes
            except botocore.exceptions.ClientError as e:
                file_status = "failure"
                failed_count += 1
                error_msg_for_file = e.response.get('Error', {}).get('Message', str(e))
                app.logger.error(f"Error copying {source_key} to {destination_key}: {error_msg_for_file}")
            except Exception as e:
                file_status = "failure"
                failed_count += 1
                error_msg_for_file = str(e)
                app.logger.error(f"Unexpected error copying {source_key} to {destination_key}: {error_msg_for_file}")

            progress_percentage_files = ((copied_count + failed_count) / total_files * 100) if total_files > 0 else 0
            progress_percentage_bytes = (data_transferred_bytes_total / total_size_bytes * 100) if total_size_bytes > 0 else 0
            elapsed_time_seconds = time.time() - migration_start_time
            transfer_speed_bps = (data_transferred_bytes_total / elapsed_time_seconds) if elapsed_time_seconds > 0 else 0
            
            eta_seconds = 0
            if transfer_speed_bps > 0 and total_size_bytes > data_transferred_bytes_total:
                remaining_bytes = total_size_bytes - data_transferred_bytes_total
                eta_seconds = remaining_bytes / transfer_speed_bps
            elif total_size_bytes == data_transferred_bytes_total :
                 eta_seconds = 0

            yield sse_message({
                'event_type': 'file_result',
                'file_name': source_key.split('/')[-1],
                'status': file_status,
                'error_message': error_msg_for_file,
                'files_processed_count': copied_count + failed_count,
                'data_transferred_bytes_total': data_transferred_bytes_total,
                'progress_percentage_files': progress_percentage_files,
                'progress_percentage_bytes': progress_percentage_bytes,
                'transfer_speed_bps': transfer_speed_bps,
                'eta_seconds': eta_seconds
            })

        total_time_seconds = time.time() - migration_start_time
        final_status_type = "completed_fully"
        final_message = f"Migration completed. Copied {copied_count} of {total_files} objects."
        if failed_count > 0:
            final_status_type = "completed_partial"
            final_message += f" {failed_count} object(s) failed. Check logs."
        
        yield sse_message({
            'event_type': 'migration_complete', 
            'status': final_status_type, 
            'final_message': final_message,
            'total_time_seconds': total_time_seconds
        })

    except botocore.exceptions.ClientError as e:
        error_message = e.response.get('Error', {}).get('Message', str(e))
        app.logger.error(f"S3 Client Error during migration listing/setup: {error_message}", exc_info=True)
        yield sse_message({'event_type': 'error', 'error_message': f'S3 Client Error: {error_message}', 'critical': True, 'status':'failed'})
    except Exception as e:
        app.logger.error(f"Unhandled exception in migrate_s3_data: {str(e)}", exc_info=True)
        yield sse_message({'event_type': 'error', 'error_message': f'An unexpected error occurred: {str(e)}', 'critical': True, 'status':'failed'})

@app.route('/')
def index():
    env_vars_display = {
        "SOURCE_S3_REGION": SOURCE_S3_REGION,
        "SOURCE_S3_BUCKET": SOURCE_S3_BUCKET,
        "SOURCE_S3_PREFIX": SOURCE_S3_PREFIX,
        "DEST_S3_REGION": DEST_S3_REGION,
        "DEST_S3_BUCKET": DEST_S3_BUCKET,
        "DEST_S3_PREFIX": DEST_S3_PREFIX,
    }
    # It's good practice to get account IDs here to ensure keys are somewhat working before migration starts
    source_account_id = get_aws_account_id(SOURCE_AWS_ACCESS_KEY_ID, SOURCE_AWS_SECRET_ACCESS_KEY, SOURCE_S3_REGION)
    dest_account_id = get_aws_account_id(DEST_AWS_ACCESS_KEY_ID, DEST_AWS_SECRET_ACCESS_KEY, DEST_S3_REGION)
    
    return render_template('index.html', env_vars=env_vars_display, source_account_id=source_account_id, dest_account_id=dest_account_id)

@app.route('/start-migration')
def start_migration():
    critical_vars_present = all([
        SOURCE_AWS_ACCESS_KEY_ID, SOURCE_AWS_SECRET_ACCESS_KEY, SOURCE_S3_REGION, SOURCE_S3_BUCKET,
        DEST_AWS_ACCESS_KEY_ID, DEST_AWS_SECRET_ACCESS_KEY, DEST_S3_REGION, DEST_S3_BUCKET
    ])
    if not critical_vars_present:
        def error_stream():
            yield sse_message({'event_type': 'error', 'error_message': 'One or more critical .env variables are missing on the server. Please check server logs and the .env file.', 'critical': True, 'status':'failed'})
        return Response(stream_with_context(error_stream()), mimetype='text/event-stream')

    return Response(stream_with_context(migrate_s3_data()), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", host='0.0.0.0', threaded=True)