import json
import boto3
import os
import re
import traceback
from datetime import datetime
import urllib.request

# Clients
s3 = boto3.client('s3')

# Config
RAW_DOCS_BUCKET = os.environ.get('RAW_DOCS_BUCKET', 'hcc-platform-dev-raw-docs')
API_BASE_URL = os.environ.get('API_BASE_URL', 'http://13.235.138.74:8080').rstrip('/')
STATUS_API_URL = f"{API_BASE_URL}/api/files/status"
EXTRACT_DATA_API_URL = f"{API_BASE_URL}/extract-data"
PROJECT_NAME_ENV = os.environ.get('PROJECT_NAME', 'hcc-platform')
INTERNAL_API_KEY_ARN = os.environ.get('INTERNAL_API_KEY_ARN') or os.environ.get('INTERNAL_API_KEY_SECRET_ARN')
INTERNAL_API_KEY = os.environ.get('INTERNAL_API_KEY') or os.environ.get('API_KEY')

secretsmanager = boto3.client('secretsmanager')
_cached_internal_api_key = None

def get_internal_api_key():
    global _cached_internal_api_key
    if _cached_internal_api_key:
        return _cached_internal_api_key

    if INTERNAL_API_KEY:
        _cached_internal_api_key = INTERNAL_API_KEY.strip()
        return _cached_internal_api_key

    if INTERNAL_API_KEY_ARN:
        try:
            secret_value = secretsmanager.get_secret_value(SecretId=INTERNAL_API_KEY_ARN)
            secret_string = secret_value.get('SecretString', '') or ''
            try:
                parsed = json.loads(secret_string)
                for candidate_key in ('api_key', 'API_KEY', 'internal_api_key', 'password', 'secret'):
                    candidate = parsed.get(candidate_key)
                    if candidate:
                        _cached_internal_api_key = str(candidate).strip()
                        return _cached_internal_api_key
            except json.JSONDecodeError:
                pass
            _cached_internal_api_key = secret_string.strip()
            return _cached_internal_api_key
        except Exception as e:
            print(f"[AUTH] Failed to load internal API key from Secrets Manager: {e}")

    _cached_internal_api_key = "hcc-internal-secure-key-2026"
    return _cached_internal_api_key

def update_file_status(s3_path, status, error=None):
    print(f"[STATUS_FINAL] Finalizing Status: {s3_path} -> {status}")
    payload = {"s3Path": s3_path, "status": status, "errorMessage": error}
    api_key = get_internal_api_key()
    req = urllib.request.Request(
        STATUS_API_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={'X-Internal-Service-Key': api_key, 'Content-Type': 'application/json'},
        method='PUT'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.read().decode('utf-8')
    except Exception as e:
        print(f"[STATUS_FINAL] Error updating status: {str(e)}")
        return None

def send_to_extract_api(payload):
    api_key = get_internal_api_key()
    print(f"[EXTRACT_API] Sending {len(payload.get('details', []))} DOS entries to {EXTRACT_DATA_API_URL}")
    print(f"[EXTRACT_API] Full Payload: {json.dumps(payload)}")
    
    req = urllib.request.Request(
        EXTRACT_DATA_API_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={'X-Internal-Service-Key': api_key, 'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            resp = res.read().decode('utf-8')
            print(f"[EXTRACT_API] Success Response: {resp}")
            return resp
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"[EXTRACT_API] HTTP ERROR {e.code}: {error_body}")
        raise Exception(f"Spring Boot Error {e.code}: {error_body}")
    except Exception as e:
        print(f"[EXTRACT_API] Connection Error: {str(e)}")
        print(traceback.format_exc())
        return str(e)

def merge_chunks(chunks, file_key):
    print(f"[MERGER] Starting safe recompilation for {file_key}. Merging {len(chunks)} chunks.")
    merged_details = {}
    top_level = {"firstName": "Unknown", "lastName": "Unknown", "dob": "Unknown", "credentials": "None", "signature": "no"}

    for idx, chunk in enumerate(chunks):
        extraction = chunk.get('extraction', {})
        print(f"[MERGER] Processing chunk {idx+1}/{len(chunks)} of {file_key}")
        
        # 1. Extract Patient Info (handle both new nested and old flat formats)
        p_info = extraction.get('patient_info', {})
        if p_info:
            if p_info.get('first_name') and top_level["firstName"] == "Unknown":
                top_level["firstName"] = p_info['first_name']
            if p_info.get('last_name') and top_level["lastName"] == "Unknown":
                top_level["lastName"] = p_info['last_name']
            if p_info.get('dob') and top_level["dob"] == "Unknown":
                top_level["dob"] = p_info['dob']
        
        # Fallback for older/flat keys
        for key, target in [("first_name", "firstName"), ("patient_first_name", "firstName"),
                           ("last_name", "lastName"), ("patient_last_name", "lastName"),
                           ("dob", "dob"), ("patient_dob", "dob")]:
            val = extraction.get(key)
            if val and top_level.get(target) == "Unknown":
                top_level[target] = val

        # 2. Extract Provider Info
        prov_info = extraction.get('provider_info', {})
        if prov_info:
            if prov_info.get('credentials') and top_level["credentials"] == "None":
                top_level["credentials"] = prov_info['credentials']
            # If doc name is missing but found in provider_info, we could store it, 
            # though the current backend expects doctor info merged/flattened later or handled via DB.
            # Using credentials as the primary 'provider' check here.
        
        # Fallback for direct/flat fields
        for key in ["doctor_credentials", "credentials"]:
            creds = extraction.get(key)
            if creds and top_level["credentials"] == "None":
                top_level["credentials"] = str(creds)

        if extraction.get('signature_found'): 
            print(f"[MERGER] Signature found in chunk {idx+1}")
            top_level["signature"] = "yes"

        # 3. Merge Details by DOS
        details = extraction.get('details', [])
        print(f"[MERGER] Found {len(details)} DOS entries in chunk {idx+1}")
        for entry in details:
            dos = entry.get('dos')
            if not dos: continue
            if dos not in merged_details:
                merged_details[dos] = {
                    "dos": dos, 
                    "extractedIcdCodes": set(), 
                    "aiSuggestedIcdCode": set()
                }
            cur = merged_details[dos]
            ext_codes = entry.get('extractedIcdCodes') or entry.get('extracted_icd_codes') or []
            ai_codes = entry.get('aiSuggestedIcdCode') or entry.get('ai_suggested_icd_code') or []
            cur["extractedIcdCodes"].update(ext_codes)
            cur["aiSuggestedIcdCode"].update(ai_codes)

    final = []
    for d, v in merged_details.items():
        v["extractedIcdCodes"] = sorted(list(v["extractedIcdCodes"]))
        v["aiSuggestedIcdCode"] = sorted(list(v["aiSuggestedIcdCode"]))
        final.append(v)
    
    print(f"[MERGER] Final Consolidation complete for {file_key}. Total Unique DOS: {len(final)}")
    return top_level, final

def lambda_handler(event, context):
    print(f"[AGGREGATOR] STARTING. Received {len(event.get('Records', []))} records.")
    
    for record in event['Records']:
        file_key = "unknown"
        try:
            msg = json.loads(record['body'])
            file_key = msg.get('file_key', 'unknown')
            chunk_idx = msg.get('chunk_index', 0)
            total_chunks = msg.get('total_chunks', 1)
            s3_path = msg.get('s3_path')
            
            print(f"[AGGREGATOR] {file_key}: Received internal chunk {chunk_idx + 1}/{total_chunks}")
            
            # S3 State Path
            state_key = f"_processing/{file_key}/{chunk_idx}.json"
            
            # If the worker already put the data in S3 (to bypass SQS limits), don't overwrite it with a minimal SQS msg
            if 'extraction' in msg:
                print(f"[AGGREGATOR] Saving chunk {chunk_idx} state to S3: {state_key}")
                s3.put_object(Bucket=RAW_DOCS_BUCKET, Key=state_key, Body=json.dumps(msg))
            else:
                print(f"[AGGREGATOR] Chunk {chunk_idx} already in S3 (confirmed by worker)")
            
            # Check progress
            prefix = f"_processing/{file_key}/"
            print(f"[AGGREGATOR] Checking cluster status for prefix: {prefix}")
            objs = s3.list_objects_v2(Bucket=RAW_DOCS_BUCKET, Prefix=prefix)
            contents = objs.get('Contents', [])
            received = len(contents)
            
            print(f"[AGGREGATOR] Progress for {file_key}: {received}/{total_chunks} parts received.")
            
            if received >= total_chunks and total_chunks > 0:
                print(f"[AGGREGATOR] ALL PARTS RECEIVED for {file_key}. Initializing global merge.")
                all_chunks = []
                # Sort contents by filename (index) to ensure we process them in order, though merge_chunks handles out-of-order
                for obj in sorted(contents, key=lambda x: x['Key']):
                    print(f"[AGGREGATOR] Fetching chunk from S3: {obj['Key']}")
                    content_body = s3.get_object(Bucket=RAW_DOCS_BUCKET, Key=obj['Key'])['Body'].read()
                    all_chunks.append(json.loads(content_body))
                
                print(f"[AGGREGATOR] Merging {len(all_chunks)} chunks for {file_key}")
                top_meta, merged_details = merge_chunks(all_chunks, file_key)
                
                # Ensure we have at least one DOS for the top-level 'dos' field
                representative_dos = "Unknown"
                if merged_details:
                    representative_dos = merged_details[0].get('dos', "Unknown")

                final_payload = {
                    "fileName": file_key.split('/')[-1], 
                    "s3Path": s3_path or f"s3://{RAW_DOCS_BUCKET}/{file_key}",
                    "totalPages": msg.get('total_pages', total_chunks * 25),
                    "signature": top_meta["signature"], 
                    "credentials": top_meta["credentials"],
                    "projectName": PROJECT_NAME_ENV, 
                    "projectType": msg.get('project_type', 'PROSPECTIVE'),
                    "dos": representative_dos,
                    "dob": top_meta["dob"], 
                    "firstName": top_meta["firstName"], 
                    "lastName": top_meta["lastName"],
                    "projectId": int(msg['project_id']) if msg.get('project_id') and str(msg['project_id']).isdigit() else 0,
                    "workUnitType": "PATIENT", 
                    "details": merged_details, 
                    "metadata": msg.get('extraction', {}).get('metadata', {}),
                    "dbStatus": "EXTRACTED"
                }

                # 3. Hit Spring Boot API
                print(f"[AGGREGATOR] Triggering final data sync to Spring Boot for {file_key}")
                try:
                    api_resp = send_to_extract_api(final_payload)
                    # 4. Notify status: EXTRACTED
                    update_file_status(s3_path, "EXTRACTED")
                except Exception as api_err:
                    print(f"[AGGREGATOR] API Sync FAILED: {str(api_err)}")
                    update_file_status(s3_path, "FAILED", error=str(api_err))
                    raise api_err
                
                # 5. Cleanup
                print(f"[AGGREGATOR] Cleaning up temp chunks and results in S3 for {file_key}")
                # 1. Cleanup result JSONs
                for obj in objs.get('Contents', []):
                    s3.delete_object(Bucket=RAW_DOCS_BUCKET, Key=obj['Key'])
                
                # 2. Cleanup physical PDF chunks if they exists
                try:
                    chunk_prefix = f"_chunks/{file_key}/"
                    chunk_objs = s3.list_objects_v2(Bucket=RAW_DOCS_BUCKET, Prefix=chunk_prefix)
                    for c_obj in chunk_objs.get('Contents', []):
                        s3.delete_object(Bucket=RAW_DOCS_BUCKET, Key=c_obj['Key'])
                except:
                    pass

                print(f"[AGGREGATOR] CLEANUP COMPLETE for {file_key}")
            else:
                print(f"[AGGREGATOR] {file_key}: Still waiting for {total_chunks - received} chunks.")
                    
        except Exception as e:
            print(f"[AGGREGATOR] ERROR in loop for {file_key}: {str(e)}")
            print(traceback.format_exc())
            continue

    return {'statusCode': 200}
