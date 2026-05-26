import json
import boto3
import os
import re
import traceback
from datetime import datetime, timezone
import urllib.request

# Clients
s3 = boto3.client('s3')

# Config
RAW_DOCS_BUCKET = os.environ.get('RAW_DOCS_BUCKET', 'hcc-platform-dev-raw-docs')
API_BASE_URL = os.environ.get('API_BASE_URL', 'http://54.84.217.140:8080').rstrip('/')
STATUS_API_URL = f"{API_BASE_URL}/api/files/status"
EXTRACT_DATA_API_URL = f"{API_BASE_URL}/extract-data"
PROJECT_NAME_ENV = os.environ.get('PROJECT_NAME', 'hcc-platform')
INTERNAL_API_KEY_ARN = os.environ.get('INTERNAL_API_KEY_ARN') or os.environ.get('INTERNAL_API_KEY_SECRET_ARN')
INTERNAL_API_KEY = os.environ.get('INTERNAL_API_KEY') or os.environ.get('API_KEY')

# Partial merge settings: if we have >= this fraction of chunks and oldest
# chunk is older than PARTIAL_MERGE_TIMEOUT_SECONDS, merge what we have.
PARTIAL_MERGE_MIN_FRACTION = 0.80   # 80% of chunks required
PARTIAL_MERGE_TIMEOUT_SECONDS = 600  # 10 minutes

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

def update_file_status(s3_path, status, error=None, is_valid=None):
    print(f"[STATUS_FINAL] Finalizing Status: {s3_path} -> {status} (Valid: {is_valid})")
    payload = {"s3Path": s3_path, "status": status, "errorMessage": error, "isValid": is_valid}
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
    
    # Track frequencies for voting
    names_fn = {}
    names_ln = {}
    dobs = {}
    creds = {}
    insurances = {}
    sigs = []

    for idx, chunk in enumerate(chunks):
        extraction = chunk.get('extraction', {})
        print(f"[MERGER] Processing chunk {idx+1}/{len(chunks)} of {file_key}")
        
        # 1. Collect Patient Info for voting
        p_info = extraction.get('patient_info', {})
        if p_info:
            fn = p_info.get('first_name')
            ln = p_info.get('last_name')
            dob = p_info.get('dob')
            if fn and fn != "Unknown": names_fn[fn] = names_fn.get(fn, 0) + 1
            if ln and ln != "Unknown": names_ln[ln] = names_ln.get(ln, 0) + 1
            if dob and dob != "Unknown": dobs[dob] = dobs.get(dob, 0) + 1
        
        # 2. Collect Provider Info for voting
        prov_info = extraction.get('provider_info', {})
        if prov_info:
            c = prov_info.get('credentials')
            if c and c != "None": creds[c] = creds.get(c, 0) + 1
        
        # 2.5 Collect Insurance for voting
        ins = extraction.get('insurance')
        if ins and ins != "Unknown" and ins != "": 
            insurances[ins] = insurances.get(ins, 0) + 1
        
        if extraction.get('signature_found'): 
            sigs.append(True)

        # 3. Merge Details by DOS
        details = extraction.get('details', [])
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

    # Resolve Voting
    def get_winner(counts, default):
        if not counts: return default
        return max(counts, key=counts.get)

    top_level = {
        "firstName": get_winner(names_fn, "Unknown"),
        "lastName": get_winner(names_ln, "Unknown"),
        "dob": get_winner(dobs, "Unknown"),
        "credentials": get_winner(creds, "None"),
        "insurance": get_winner(insurances, "Unknown"),
        "signature": "yes" if any(sigs) else "no"
    }

    final = []
    for d, v in merged_details.items():
        v["extractedIcdCodes"] = sorted(list(v["extractedIcdCodes"]))
        v["aiSuggestedIcdCode"] = sorted(list(v["aiSuggestedIcdCode"]))
        final.append(v)
    
    print(f"[MERGER] Final Consolidation complete. Winner: {top_level['firstName']} {top_level['lastName']}")
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
            
            # --- Merge Lock Check ---
            # If this file was already merged, skip immediately to avoid orphaned processing
            merge_marker_key = f"_processing/{file_key}/_merged"
            try:
                s3.head_object(Bucket=RAW_DOCS_BUCKET, Key=merge_marker_key)
                print(f"[AGGREGATOR] {file_key}: Already merged (marker found). Skipping chunk {chunk_idx}.")
                continue
            except s3.exceptions.ClientError:
                pass  # Marker doesn't exist yet — proceed normally
            except Exception:
                pass  # Any other error — proceed normally
            
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
            all_contents = objs.get('Contents', [])
            
            # Filter out the _merged marker and any non-JSON files from the count
            contents = [obj for obj in all_contents if obj['Key'].endswith('.json')]
            received = len(contents)
            
            print(f"[AGGREGATOR] Progress for {file_key}: {received}/{total_chunks} parts received.")
            
            should_merge = False
            is_partial = False

            if received >= total_chunks and total_chunks > 0:
                # All chunks received — full merge
                should_merge = True
                print(f"[AGGREGATOR] ALL PARTS RECEIVED for {file_key}. Initializing global merge.")

            elif total_chunks > 1 and received >= int(total_chunks * PARTIAL_MERGE_MIN_FRACTION):
                # Partial merge: check if oldest chunk is old enough
                oldest_time = min(obj['LastModified'] for obj in contents)
                now = datetime.now(timezone.utc)
                age_seconds = (now - oldest_time).total_seconds()
                print(f"[AGGREGATOR] Partial merge check for {file_key}: {received}/{total_chunks} received, oldest chunk age: {int(age_seconds)}s")
                
                if age_seconds >= PARTIAL_MERGE_TIMEOUT_SECONDS:
                    should_merge = True
                    is_partial = True
                    print(f"[AGGREGATOR] PARTIAL MERGE TRIGGERED for {file_key}. "
                          f"Got {received}/{total_chunks} chunks ({int(received/total_chunks*100)}%). "
                          f"Oldest chunk is {int(age_seconds)}s old (threshold: {PARTIAL_MERGE_TIMEOUT_SECONDS}s).")
                else:
                    remaining_wait = PARTIAL_MERGE_TIMEOUT_SECONDS - int(age_seconds)
                    print(f"[AGGREGATOR] {file_key}: Have {received}/{total_chunks} chunks but only {int(age_seconds)}s old. "
                          f"Will partial-merge in ~{remaining_wait}s if remaining chunks don't arrive.")

            if should_merge:
                # --- Acquire Merge Lock (atomic) ---
                # Write the marker BEFORE merging. If two Lambdas race here,
                # only the first one proceeds; the second will see the marker on its next check.
                try:
                    s3.put_object(
                        Bucket=RAW_DOCS_BUCKET,
                        Key=merge_marker_key,
                        Body=json.dumps({"merged_at": datetime.now(timezone.utc).isoformat(), "chunks_received": received, "total_chunks": total_chunks}),
                        IfNoneMatch='*'  # Only succeeds if the object does NOT already exist (S3 conditional write)
                    )
                    print(f"[AGGREGATOR] Merge lock ACQUIRED for {file_key}")
                except Exception as lock_err:
                    if 'PreconditionFailed' in str(lock_err) or '412' in str(lock_err):
                        print(f"[AGGREGATOR] {file_key}: Merge lock already held by another invocation. Skipping.")
                        continue
                    else:
                        # If conditional writes aren't supported, fall back to head_object check
                        try:
                            s3.head_object(Bucket=RAW_DOCS_BUCKET, Key=merge_marker_key)
                            print(f"[AGGREGATOR] {file_key}: Merge marker already exists (fallback check). Skipping.")
                            continue
                        except Exception:
                            # Marker doesn't exist, safe to proceed
                            s3.put_object(Bucket=RAW_DOCS_BUCKET, Key=merge_marker_key, Body=b'locked')
                            print(f"[AGGREGATOR] Merge lock ACQUIRED (fallback) for {file_key}")

                all_chunks = []
                # Sort contents by filename (index) to ensure we process them in order, though merge_chunks handles out-of-order
                for obj in sorted(contents, key=lambda x: x['Key']):
                    print(f"[AGGREGATOR] Fetching chunk from S3: {obj['Key']}")
                    content_body = s3.get_object(Bucket=RAW_DOCS_BUCKET, Key=obj['Key'])['Body'].read()
                    all_chunks.append(json.loads(content_body))
                
                merge_label = "PARTIAL" if is_partial else "FULL"
                print(f"[AGGREGATOR] [{merge_label}] Merging {len(all_chunks)}/{total_chunks} chunks for {file_key}")
                top_meta, merged_details = merge_chunks(all_chunks, file_key)
                
                # Ensure we have at least one DOS for the top-level 'dos' field
                representative_dos = "Unknown"
                if merged_details:
                    representative_dos = merged_details[0].get('dos', "Unknown")

                # 2.5 Calculate Document Validity
                # Process all files normally, missing signatures/credentials will just be reflected in the extracted fields
                is_doc_valid = True
                
                final_payload = {
                    "fileName": file_key.split('/')[-1], 
                    "s3Path": s3_path or f"s3://{RAW_DOCS_BUCKET}/{file_key}",
                    "totalPages": msg.get('total_pages') or (total_chunks * 5 if total_chunks > 1 else 1),
                    "signature": top_meta["signature"], 
                    "credentials": top_meta["credentials"],
                    "projectName": PROJECT_NAME_ENV, 
                    "projectType": msg.get('project_type', 'PROSPECTIVE'),
                    "dos": representative_dos,
                    "dob": top_meta["dob"], 
                    "firstName": top_meta["firstName"], 
                    "lastName": top_meta["lastName"],
                    "insurance": top_meta["insurance"],
                    "projectId": int(msg['project_id']) if msg.get('project_id') and str(msg['project_id']).isdigit() else 0,
                    "workUnitType": "PATIENT", 
                    "details": merged_details, 
                    "metadata": msg.get('extraction', {}).get('metadata', {}),
                    "dbStatus": "EXTRACTED" if is_doc_valid else "INVALID",
                    "isValid": is_doc_valid
                }

                if is_partial:
                    final_payload["partialMerge"] = True
                    final_payload["chunksReceived"] = received
                    final_payload["chunksExpected"] = total_chunks

                # 3. Hit Spring Boot API
                print(f"[AGGREGATOR] Triggering final data sync to Spring Boot for {file_key}")
                try:
                    api_resp = send_to_extract_api(final_payload)
                    # 4. Notify status: EXTRACTED or INVALID
                    update_file_status(s3_path, "EXTRACTED" if is_doc_valid else "INVALID", is_valid=is_doc_valid)
                except Exception as api_err:
                    print(f"[AGGREGATOR] API Sync FAILED: {str(api_err)}")
                    update_file_status(s3_path, "FAILED", error=str(api_err), is_valid=is_doc_valid)
                    # Remove merge lock so a retry can attempt again
                    try:
                        s3.delete_object(Bucket=RAW_DOCS_BUCKET, Key=merge_marker_key)
                    except:
                        pass
                    raise api_err
                
                # 5. Cleanup
                print(f"[AGGREGATOR] Cleaning up temp chunks and results in S3 for {file_key}")
                # 1. Cleanup result JSONs
                for obj in all_contents:
                    s3.delete_object(Bucket=RAW_DOCS_BUCKET, Key=obj['Key'])
                
                # 2. Cleanup physical PDF chunks if they exist
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

