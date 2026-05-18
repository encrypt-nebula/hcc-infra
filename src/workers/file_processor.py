import json
import boto3
import os
import re
import math
import traceback
from datetime import datetime
import urllib.request
import urllib.parse
import io

try:
    from pypdf import PdfReader, PdfWriter
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

# Clients
s3 = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')
sqs = boto3.client('sqs')

# Config
RAW_DOCS_BUCKET = os.environ.get('RAW_DOCS_BUCKET')
API_BASE_URL = os.environ.get('API_BASE_URL', 'http://54.84.217.140:8080').rstrip('/')
PROJECT_NAME_ENV = os.environ.get('PROJECT_NAME', 'hcc-platform')
LLM_QUEUE_URL = os.environ.get('LLM_QUEUE_URL')
RESULTS_QUEUE_URL = os.environ.get('RESULTS_QUEUE_URL')
STATUS_API_URL = f"{API_BASE_URL}/api/files/status"
INTERNAL_API_KEY_ARN = os.environ.get('INTERNAL_API_KEY_ARN') or os.environ.get('INTERNAL_API_KEY_SECRET_ARN')
INTERNAL_API_KEY = os.environ.get('INTERNAL_API_KEY') or os.environ.get('API_KEY')

secretsmanager = boto3.client('secretsmanager')
_cached_internal_api_key = None

CHUNK_THRESHOLD = 10
LARGE_FILE_CHUNK_SIZE = 5

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

def update_file_status(file_key, project_id, project_type, status, error=None, total_pages=None):
    print(f"[STATUS_UPDATE] Notifying API: {file_key} -> {status} (Total Pages: {total_pages})")
    payload = {
        "s3Path": f"s3://{RAW_DOCS_BUCKET}/{file_key}",
        "fileName": file_key.split('/')[-1],
        "projectId": int(project_id) if str(project_id).isdigit() else 0,
        "projectType": project_type,
        "status": status,
        "errorMessage": error,
        "total_pages": total_pages,
        "isValid": True
    }
    api_key = get_internal_api_key()
    req = urllib.request.Request(
        STATUS_API_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={'X-Internal-Service-Key': api_key, 'Content-Type': 'application/json'},
        method='PUT'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            resp_body = res.read().decode('utf-8')
            print(f"[STATUS_UPDATE] Success: {resp_body}")
            return resp_body
    except Exception as e:
        print(f"[STATUS_UPDATE] FAILED to notify API for {file_key}. Error: {str(e)}")
        return None

def call_bedrock_nova_direct(file_content, file_ext, file_key):
    print(f"[BEDROCK_DIRECT] Starting single-pass extraction for {file_key} ({len(file_content)} bytes)")
    
    prompt = """Analyze this clinical document and extract structured JSON. Follow these RULES strictly:
    1. **NO HALLUCINATION**: If text is not explicitly found, use "" or []. DO NOT invent or assume data.
    2. **IDENTIFICATION**:
       - PATIENT: Identify from fields like 'Name:', 'Patient:', 'DOB'.
       - PROVIDER: Identify the clinician/attending from signatures or 'Provider' headings.
       - INSURANCE: Identify the insurance carrier or payer name.
    3. **ICD-10 CATEGORIES**:
       - `extracted_icd_codes`: ONLY codes physically typed in the document (e.g. "I10", "E11.9").
       - `ai_suggested_icd_code`: Codes YOU determine from condition names (e.g. "Diabetes") that do NOT have a code written next to them.

    Return ONLY JSON in this structure:
    {
      "signature_found": boolean,
      "insurance": string,
      "patient_info": {
        "first_name": string,
        "last_name": string,
        "dob": string
      },
      "provider_info": {
        "first_name": string,
        "last_name": string,
        "credentials": string
      },
      "details": [
        {
          "dos": string,
          "extracted_icd_codes": [string],
          "ai_suggested_icd_code": [string]
        }
      ]
    }"""

    content_blocks = []
    if file_ext in ['pdf', 'csv', 'txt']:
        content_blocks.append({'document': {'name': 'Doc', 'format': file_ext, 'source': {'bytes': file_content}}})
    else:
        content_blocks.append({'image': {'format': 'jpeg' if file_ext in ['jpg', 'jpeg'] else file_ext, 'source': {'bytes': file_content}}})
    content_blocks.append({'text': prompt})

    try:
        response = bedrock_runtime.converse(
            modelId="us.amazon.nova-lite-v1:0",
            messages=[{'role': 'user', 'content': content_blocks}],
            inferenceConfig={
                'temperature': 0,
                'maxTokens': 4096
            }
        )
        text = response['output']['message']['content'][0]['text']
        print(f"[BEDROCK_DIRECT] Raw Response received for {file_key}. Length: {len(text)}")
        
        # Robust JSON extraction
        json_match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            json_str = text.strip()
            
        json_str = re.sub(r'```json\s*|\s*```', '', json_str).strip()
        parsed = json.loads(json_str)
        print(f"[BEDROCK_DIRECT] Successfully parsed JSON for {file_key}")
        return parsed
    except Exception as e:
        print(f"[BEDROCK_DIRECT] ERROR calling Bedrock for {file_key}: {str(e)}")
        print(traceback.format_exc())
        raise e

def lambda_handler(event, context):
    print(f"[ORCHESTRATOR] STARTING. Event size: {len(str(event))} chars")
    
    records = []
    if 'Records' in event:
        print(f"[ORCHESTRATOR] Found {len(event['Records'])} SQS Records")
        for r in event['Records']:
            try:
                body = json.loads(r['body'])
                if 'Records' in body:
                    print(f"[ORCHESTRATOR] Found {len(body['Records'])} S3 events in SQS message")
                    records.extend(body['Records'])
                else:
                    print(f"[ORCHESTRATOR] SQS message body does not contain expected S3 Records structure")
            except Exception as e:
                print(f"[ORCHESTRATOR] Error parsing SQS record: {str(e)}")

    elif 'body' in event:
        print("[ORCHESTRATOR] Triggered via Function URL / API")
        try:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
            records.append({'s3': {'object': {'key': body['file_key']}}})
        except Exception as e:
            print(f"[ORCHESTRATOR] Error parsing URL body: {str(e)}")

    if not records:
        print("[ORCHESTRATOR] No valid S3 records found to process. Exiting.")
        return {'statusCode': 200, 'body': 'No records found'}

    for record in records:
        file_key = "unknown"
        project_id = "0"
        project_type = "PROSPECTIVE"
        try:
            file_key = urllib.parse.unquote_plus(record['s3']['object']['key'])
            print(f"[FILE_PROCESS] Beginning processing for: {file_key}")
            
            parts = file_key.split('/')
            if len(parts) < 4:
                print(f"[FILE_PROCESS] SKIPPING. Invalid key structure: {file_key}. Expected uploads/{{id}}/{{type}}/{{file}}")
                continue
            
            project_id, project_type, file_name = parts[1], parts[2], parts[-1]
            file_ext = file_name.split('.')[-1].lower()

            # Precise Metadata Extraction 
            s3_obj = s3.head_object(Bucket=RAW_DOCS_BUCKET, Key=file_key)
            file_size = s3_obj['ContentLength']
            
            # Identify file type and determine page count
            is_image = file_ext in ['png', 'jpg', 'jpeg', 'webp', 'tiff', 'tif']
            
            if is_image:
                total_pages = 1
                print(f"[FILE_PROCESS] Image detected ({file_ext}), setting total_pages to 1")
            elif file_ext == 'pdf':
                try:
                    print(f"[FILE_PROCESS] Safely extracting PDF metadata for: {file_name}")
                    s3_body = s3.get_object(Bucket=RAW_DOCS_BUCKET, Key=file_key)['Body'].read()
                    
                    extracted = False
                    if HAS_PYPDF:
                        try:
                            pdf = PdfReader(io.BytesIO(s3_body))
                            total_pages = len(pdf.pages)
                            extracted = True
                            print(f"[FILE_PROCESS] SUCCESS: Extracted exact page count via pypdf: {total_pages}")
                        except Exception as pypdf_err:
                            print(f"[FILE_PROCESS] pypdf failed: {pypdf_err}. Trying regex fallback.")
                    
                    if not extracted:
                        # Regex fallback: Look for /Count followed by whitespace and a number
                        # We look for the last occurrence as it's usually the Catalog's count
                        matches = re.findall(rb'/Count\s+(\d+)', s3_body)
                        if matches:
                            total_pages = int(matches[-1])
                            extracted = True
                            print(f"[FILE_PROCESS] SUCCESS: Extracted page count via regex: {total_pages}")
                        else:
                            # Fallback if both fail
                            total_pages = max(1, math.ceil(file_size / 25000))
                            print(f"[FILE_PROCESS] WARNING: All metadata extractions failed for PDF. Using estimate: {total_pages}")
                except Exception as meta_err:
                    total_pages = max(1, math.ceil(file_size / 30000))
                    print(f"[FILE_PROCESS] WARNING: Precise extraction failed ({str(meta_err)}). Falling back to safe estimate: {total_pages}")
            else:
                # Default to 1 for small files, otherwise estimate for other documents (txt, csv, etc.)
                if file_size < 10000:
                    total_pages = 1
                else:
                    total_pages = max(1, math.ceil(file_size / 30000))
                print(f"[FILE_PROCESS] Using estimate for {file_ext}: {total_pages}")
            
            print(f"[FILE_PROCESS] Final Decision: {file_name}, Size: {file_size} bytes, Pages: {total_pages}")

            update_file_status(file_key, project_id, project_type, "PROCESSING", total_pages=total_pages)

            if total_pages <= CHUNK_THRESHOLD:
                print(f"[FILE_PROCESS] Small file mode (<={CHUNK_THRESHOLD} pgs) for {file_name}")
                s3_content = s3.get_object(Bucket=RAW_DOCS_BUCKET, Key=file_key)['Body'].read()
                extraction = call_bedrock_nova_direct(s3_content, file_ext, file_key)
                
                queue_payload = {
                    "file_key": file_key,
                    "project_id": project_id,
                    "project_type": project_type,
                    "total_chunks": 1,
                    "chunk_index": 0,
                    "extraction": extraction,
                    "pages": f"1-{total_pages}",
                    "s3_path": f"s3://{RAW_DOCS_BUCKET}/{file_key}",
                    "total_pages": total_pages
                }
                
                # Save to S3 for aggregator
                state_key = f"_processing/{file_key}/0.json"
                print(f"[FILE_PROCESS] Saving small file extraction to S3: {state_key}")
                s3.put_object(Bucket=RAW_DOCS_BUCKET, Key=state_key, Body=json.dumps(queue_payload))

                # Minimal SQS message
                minimal_payload = queue_payload.copy()
                del minimal_payload['extraction']
                minimal_payload['data_in_s3'] = True

                if not RESULTS_QUEUE_URL:
                    raise Exception("RESULTS_QUEUE_URL environment variable is missing!")
                
                print(f"[FILE_PROCESS] Sending trigger to results queue for aggregation: {RESULTS_QUEUE_URL}")
                sqs.send_message(QueueUrl=RESULTS_QUEUE_URL, MessageBody=json.dumps(minimal_payload))
                print(f"[FILE_PROCESS] Success: Extraction queued for {file_name}")
            else:
                num_chunks = math.ceil(total_pages / LARGE_FILE_CHUNK_SIZE)
                print(f"[FILE_PROCESS] Large file mode ({total_pages} pgs). Splitting into {num_chunks} chunks (each {LARGE_FILE_CHUNK_SIZE} pgs) for {file_name}")
                
                if not LLM_QUEUE_URL:
                    raise Exception("LLM_QUEUE_URL environment variable is missing!")

                s3_full_content = s3.get_object(Bucket=RAW_DOCS_BUCKET, Key=file_key)['Body'].read()

                for i in range(num_chunks):
                    start_page = (i * LARGE_FILE_CHUNK_SIZE) + 1
                    end_page = min((i + 1) * LARGE_FILE_CHUNK_SIZE, total_pages)
                    
                    chunk_s3_key = f"_chunks/{file_key}/chunk_{i}.{file_ext}"
                    
                    if file_ext == 'pdf' and HAS_PYPDF:
                        try:
                            reader = PdfReader(io.BytesIO(s3_full_content))
                            writer = PdfWriter()
                            for p_num in range(start_page - 1, end_page):
                                writer.add_page(reader.pages[p_num])
                            
                            chunk_buffer = io.BytesIO()
                            writer.write(chunk_buffer)
                            s3.put_object(Bucket=RAW_DOCS_BUCKET, Key=chunk_s3_key, Body=chunk_buffer.getvalue())
                            print(f"[FILE_PROCESS] Uploaded chunk PDF to: {chunk_s3_key} (Pages {start_page}-{end_page})")
                        except Exception as split_err:
                            print(f"[FILE_PROCESS] WARNING: Physical split failed for chunk {i}: {str(split_err)}. Falling back to full file.")
                            chunk_s3_key = file_key
                    else:
                        chunk_s3_key = file_key

                    chunk_msg = {
                        "file_key": file_key,
                        "chunk_s3_key": chunk_s3_key,
                        "project_id": project_id,
                        "project_type": project_type,
                        "chunk_index": i,
                        "total_chunks": num_chunks,
                        "page_range": f"{start_page}-{end_page}",
                        "file_ext": file_ext,
                        "total_pages": total_pages,
                        "s3_path": f"s3://{RAW_DOCS_BUCKET}/{file_key}"
                    }
                    sqs.send_message(QueueUrl=LLM_QUEUE_URL, MessageBody=json.dumps(chunk_msg))
                    print(f"[FILE_PROCESS] Queued chunk {i+1}/{num_chunks} (Pages {start_page}-{end_page})")
                
                print(f"[FILE_PROCESS] Success: All {num_chunks} chunks queued for {file_name}")

        except Exception as e:
            print(f"[ORCHESTRATOR] CRITICAL ERROR processing {file_key}: {str(e)}")
            print(traceback.format_exc())
            try:
                update_file_status(file_key, project_id, project_type, "FAILED", error=str(e))
            except:
                print("[ORCHESTRATOR] Follow-up error: Status update itself failed.")

    return {'statusCode': 200, 'body': 'Orchestration Complete'}
