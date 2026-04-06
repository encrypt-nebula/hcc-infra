import json
import boto3
import os
import re
import traceback
import time
import random
import urllib.request

# Clients
s3 = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')
sqs = boto3.client('sqs')

# Env
RESULTS_QUEUE_URL = os.environ.get('RESULTS_QUEUE_URL')
RAW_DOCS_BUCKET = os.environ.get('RAW_DOCS_BUCKET', 'hcc-platform-dev-raw-docs')



import io
import tempfile

try:
    import pypdf
    PDF_TOOLS_AVAILABLE = True
except ImportError as e:
    pypdf = None
    PDF_TOOLS_AVAILABLE = False
    print(f"[WARNING] PDF extraction tools missing: {e}.")

class DeterministicPipeline:
    def __init__(self, signature_conf=0.5):
        self.signature_conf = signature_conf
        self.sig_patterns = [
            re.compile(r'\b' + kw + r'\b', re.IGNORECASE) 
            for kw in ["electronically signed", "digitally signed", "signed by"]
        ]

    def _extract_structured_data(self, text, output):
        output['patient_info'] = {'first_name': '', 'last_name': '', 'dob': '', 'hcin_number': '', 'member_id': ''}
        output['provider_info'] = {'first_name': '', 'last_name': '', 'physician_name': '', 'credentials': ''}
        
        # HCIN and Member ID
        hcin_match = re.search(r'(?:HCIN|HCIN\s*NUMBER|HCIN#)[:\s]*([A-Z0-9]+)', text, re.IGNORECASE)
        if hcin_match: output['patient_info']['hcin_number'] = hcin_match.group(1)
        
        member_match = re.search(r'(?:Member ID|Patient ID|Member#)[:\s]*([A-Z0-9]+)', text, re.IGNORECASE)
        if member_match: output['patient_info']['member_id'] = member_match.group(1)

        pt_match = re.search(r'(?:Patient Name|Patient)[:\s]*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text, re.IGNORECASE)
        if pt_match:
            parts = pt_match.group(1).split()
            if len(parts) >= 2:
                output['patient_info']['first_name'] = parts[0]
                output['patient_info']['last_name'] = parts[-1]
                
        dob_match = re.search(r'(?:DOB|Date of Birth)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', text, re.IGNORECASE)
        if dob_match:
            output['patient_info']['dob'] = dob_match.group(1)
            
        prov_match = re.search(r'(?:Provider|Attending|Physician)[:\s]*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text, re.IGNORECASE)
        if prov_match:
            output['provider_info']['physician_name'] = prov_match.group(1)
            parts = prov_match.group(1).split()
            if len(parts) >= 2:
                output['provider_info']['first_name'] = parts[0]
                output['provider_info']['last_name'] = parts[-1]
                
        cred_match = re.search(r'\b(MD|DO|NP|PA|FNP)\b', text, re.IGNORECASE)
        if cred_match:
            output['provider_info']['credentials'] = cred_match.group(1).upper()
            
        dos_list = set()
        for dos_val in re.findall(r'(?:DOS|Date of Service|Encounter Date|Service Date)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', text, re.IGNORECASE):
            dos_list.add(dos_val)
        output['dos_list'] = list(dos_list)
        
        icd_codes = set()
        in_section = False
        lines = text.split('\n')
        for line in lines:
            lower_line = line.lower()
            if any(kw in lower_line for kw in ["diagnosis", "assessment", "problem list"]):
                in_section = True
            elif lower_line.strip() == "" or "plan" in lower_line: 
                pass # Check within reasonable block limit
            
            if in_section:
                codes = re.findall(r'\b([A-TV-Z][0-9][0-9](?:\.[0-9A-Z]{1,4})?)\b', line)
                icd_codes.update(codes)
                
        output['extracted_icd_codes'] = list(icd_codes)



    def process_file(self, file_path):
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        ext = file_path.lower().split('.')[-1]
        if ext == 'pdf': return self.process_pdf(file_path)
        elif ext in ['jpg', 'jpeg', 'png', 'tiff']: return self.process_image(file_path)
        elif ext in ['xls', 'xlsx']: return self.process_excel(file_path)
        else: raise ValueError(f"Unsupported type: {ext}")

    def process_pdf(self, file_path):
        output = {"pages": [], "signatures_detected": False, "metadata": {}}
        
        if not PDF_TOOLS_AVAILABLE:
            print("[WARNING] Skipping PDF processing because PDF tools are missing.")
            return output
            
        full_text = ""
        try:
            with open(file_path, 'rb') as f:
                pdf_reader = pypdf.PdfReader(f)
                output["metadata"]["total_pages"] = len(pdf_reader.pages)
                if pdf_reader.metadata:
                    output["metadata"]["doc_info"] = dict(pdf_reader.metadata)
                
                for page in pdf_reader.pages:
                    page_text = page.extract_text() or ""
                    full_text += page_text + "\n"
                    
                    has_sig = False
                    if page_text and any(p.search(page_text) for p in self.sig_patterns):
                        has_sig = True

                    output["pages"].append({"text": page_text, "signature_present": has_sig})
                    if has_sig: 
                        output["signatures_detected"] = True
                        
        except Exception as e:
            print(f"[ERROR] PDF processing failed: {e}")
            
        self._extract_structured_data(full_text, output)
        return output

    def process_image(self, file_path):
        # Image processing disabled to remove heavy cv2 and yolo dependencies
        return {"pages": [], "signatures_detected": False}

    def process_excel(self, file_path):
        # Excel processing disabled to remove heavy pandas dependency
        return {"sheets": [], "patient_info": {}, "provider_info": {}, "dos_list": [], "extracted_icd_codes": []}

pipeline_instance = DeterministicPipeline()

def extract_raw_text(file_content, file_ext):
    if file_ext in ['txt', 'csv']:
        try: 
            return file_content.decode('utf-8', errors='ignore'), False, {}
        except: 
            return "", False, {}
        
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name
        
    raw_text = ""
    deterministic_sig = False
    structured_data = {}
    try:
        res = pipeline_instance.process_file(tmp_path)
        for page in res.get("pages", []):
            if page.get("text"): raw_text += page["text"] + "\n"
        deterministic_sig = res.get("signatures_detected", False)
        
        structured_data = {
            "metadata": res.get("metadata", {}),
            "patient_info": res.get("patient_info", {}),
            "provider_info": res.get("provider_info", {}),
            "dos_list": res.get("dos_list", []),
            "extracted_icd_codes": res.get("extracted_icd_codes", [])
        }
    except Exception as e:
        print(f"[EXTRACTOR] pipeline failed: {e}")
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)
            
    return raw_text, deterministic_sig, structured_data



# --- MODEL SELECTION TOGGLE ---
# Set to 'NOVA' for amazon.nova-lite-v1:0 (Fastest, cheapest)
# Set to 'NOVA_PRO' for amazon.nova-pro-v1:0 (Highest accuracy among Nova models)
# Set to 'CLAUDE' for anthropic.claude-3-haiku-20240307-v1:0 (Strong reasoning, requires Marketplace billing)
ACTIVE_MODEL = "NOVA_PRO" 

USE_PYTHON_PIPELINE = str(os.environ.get('USE_PYTHON_PIPELINE', 'false')).lower() == 'false'

MODELS = {
    "NOVA": "amazon.nova-lite-v1:0",
    "NOVA_PRO": "amazon.nova-pro-v1:0",
    "CLAUDE": "anthropic.claude-3-haiku-20240307-v1:0"
}

def validate_icd_codes(queries):
    """
    Validates a list of potential ICD codes or condition names via the Spring Boot API
    which performs a fuzzy match against the icd_codes table.
    """
    if not queries:
        return []
        
    url = "http://54.84.217.140:8080/icd-codes/validate"
    payload = {"queries": queries}
    
    env_key = os.environ.get('INTERNAL_API_KEY') or os.environ.get('API_KEY')
    api_key = env_key.strip() if env_key else "hcc-internal-secure-key-2026"
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'X-Internal-Service-Key': api_key, 'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            resp = json.loads(res.read().decode('utf-8'))
            return resp.get("validCodes", [])
    except Exception as e:
        print(f"[ICD_VALIDATION] API Error: {str(e)}")
        # If the API fails, return the original queries to prevent data loss or blocking
        return queries

def call_bedrock_llm(file_content, file_ext, page_range, file_key, raw_text):
    model_id = MODELS.get(ACTIVE_MODEL, MODELS["NOVA"])
    print(f"[LLM_CHUNKER] Calling Bedrock ({ACTIVE_MODEL}) for {file_key}, Range: {page_range}")
    
    prompt = f"""Analyze the provided text and document. Extract Patient and Provider info.
Specifically, look for and extract:
- hcin_number (HCIN NUMBER)
- member_id (MEMBER ID)
- physician_name (PHYSICIAN or PROVIDER NAME)

For each visit/encounter found in the document:
1. Identify the Date of Service (DOS). **CRITICAL: The DOS MUST NOT be empty. If you cannot find a clear DOS for a section, associate it with the closest preceding date.**
2. Map **EVERY** found ICD-10 code (or clinical condition) to its specific DOS. DO NOT return codes without a DOS.
3. List both the Alphanumeric codes (e.g., I10, E11.9) AND clear descriptions of any clinical conditions mentioned that don't have a code written next to them (e.g. "Diabetes", "Hypertension").

Return strict JSON only. No text/markdown.

OUTPUT FORMAT:
{{
  "signature_found": false,
  "hcin_number": "",
  "member_id": "",
  "physician_name": "",
  "patient_info": {{"first_name": "", "last_name": "", "dob": ""}},
  "provider_info": {{"first_name": "", "last_name": "", "credentials": ""}},
  "details": [{{
    "dos": "MM/DD/YYYY",
    "extracted_icd_codes": ["I10", "E11.9"],
    "ai_suggested_icd_code": ["Diabetes", "Hypertension"]
  }}]
}}"""

    content_blocks = []
    # Pass the raw text directly as a block
    # Note: We truncate to 20k to stay safe on context vs cost, but can be increased
    content_blocks.append({'text': f"RAW DOCUMENT TEXT:\n{raw_text[:30000]}\n\nPROMPT:\n{prompt}"})
    
    # Also pass the actual document/image for vision-side reasoning if supported
    if file_ext in ['pdf', 'csv', 'txt']:
        # IMPORTANT: Bedrock 'document' block is currently only supported for certain models (like Nova)
        if "nova" in model_id.lower():
            content_blocks.append({'document': {'name': 'Doc', 'format': file_ext, 'source': {'bytes': file_content}}})
        else:
            # For Claude, we rely on the raw_text already provided above
            pass
    else:
        # Images (png, jpg) are supported by both Nova and Claude in the converse API
        content_blocks.append({'image': {'format': 'jpeg' if file_ext in ['jpg', 'jpeg'] else file_ext, 'source': {'bytes': file_content}}})

    try:
        response = bedrock_runtime.converse(
            modelId=model_id,
            messages=[{'role': 'user', 'content': content_blocks}],
            inferenceConfig={
                'temperature': 0,
                'maxTokens': 5120
            }
        )
        text = response['output']['message']['content'][0]['text']
        print(f"[LLM_CHUNKER] Bedrock response length: {len(text)} chars")
        
        # Clean JSON
        json_match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        json_str = json_match.group(1).strip() if json_match else text.strip()
        json_str = re.sub(r'```json\s*|\s*```', '', json_str).strip()
        
        return json.loads(json_str)
    except Exception as e:
        print(f"[LLM_CHUNKER] Bedrock ERROR: {str(e)}")
        raise e

def lambda_handler(event, context):
    print(f"[LLM_CHUNKER] STARTING Batch.")
    results = []
    
    for record in event['Records']:
        file_key = "unknown"
        try:
            body = json.loads(record['body'])
            file_key = body.get('file_key', 'unknown')
            chunk_index = body.get('chunk_index', 0)
            page_range = body.get('page_range', '1')
            file_ext = body.get('file_ext', 'pdf').lower()
            s3_path = body.get('s3_path') or f"s3://{RAW_DOCS_BUCKET}/{file_key}"

            # 1. Fetch from S3
            target_key = body.get('chunk_s3_key') or file_key
            s3_obj = s3.get_object(Bucket=RAW_DOCS_BUCKET, Key=target_key)
            file_content = s3_obj['Body'].read()

            # 2. DETERMINISTIC SIGNATURE DETECTION
            raw_text, det_sig_found, structured_data = extract_raw_text(file_content, file_ext)

            # 3. Use LLM to assign keywords to the correct DOS
            extraction = None
            for attempt in range(3):
                try:
                    extraction = call_bedrock_llm(file_content, file_ext, page_range, file_key, raw_text)
                    
                    # 4. Integrate Model Reasoner with Python Accurate Matcher
                    if extraction and 'details' in extraction:
                        validated_details = []
                        for entry in extraction['details']:
                            dos = entry.get('dos')
                            if not dos or str(dos).strip() in ["", "None", "null", "Unknown"]:
                                print(f"[CHUNK_PROCESS] Skipping detail entry with empty DOS.")
                                continue
                                
                            # --- ICD Validation via Spring Boot API ---
                            raw_ext = entry.get('extracted_icd_codes', [])
                            raw_ai = entry.get('ai_suggested_icd_code', [])
                            
                            # Filter and validate codes
                            # Alphanumeric vs Descriptive
                            code_queries = []
                            desc_queries = []
                            
                            seen = set()
                            for q in ((raw_ext if isinstance(raw_ext, list) else []) + (raw_ai if isinstance(raw_ai, list) else [])):
                                q_str = str(q).strip()
                                if not q_str or q_str.isdigit() or q_str in seen: continue
                                seen.add(q_str)
                                
                                # Check if it contains digits (alphanumeric codes like I10) or is purely alpha (description like "Diabetes")
                                if any(char.isdigit() for char in q_str):
                                    code_queries.append(q_str)
                                else:
                                    desc_queries.append(q_str)
                            
                            # Validate Alphanumeric Codes
                            if code_queries:
                                print(f"[CHUNK_PROCESS] Validating {len(code_queries)} Alphanumeric codes for DOS {dos}")
                                entry['extracted_icd_codes'] = validate_icd_codes(code_queries)
                            else:
                                entry['extracted_icd_codes'] = []
                                
                            # Validate Descriptive Strings -> map to AI Suggestions
                            if desc_queries:
                                print(f"[CHUNK_PROCESS] Validating {len(desc_queries)} Descriptions for DOS {dos}")
                                entry['ai_suggested_icd_code'] = validate_icd_codes(desc_queries)
                            else:
                                entry['ai_suggested_icd_code'] = []
                                
                            # Only keep entry if it has at least one valid code or suggestion
                            if entry['extracted_icd_codes'] or entry['ai_suggested_icd_code']:
                                validated_details.append(entry)
                            else:
                                print(f"[CHUNK_PROCESS] Skipping DOS {dos} because no valid codes or suggestions found.")
                            # ------------------------------------------

                        extraction['details'] = validated_details
                        

                    break 
                except Exception as e:
                    print(f"[CHUNK_PROCESS] Attempt {attempt+1} failed: {e}")
                    time.sleep(2)
            
            if not extraction: raise Exception("All LLM attempts failed.")

            if det_sig_found and extraction:
                extraction["signature_found"] = True

            if extraction and structured_data:
                # Integration with Bedrock Output (Step 11 & 12)
                # Output deterministic results
                extraction["metadata"] = structured_data.get("metadata", {})

                if USE_PYTHON_PIPELINE:
                    if not extraction.get("hcin_number") and structured_data.get("patient_info", {}).get("hcin_number"):
                        extraction["hcin_number"] = structured_data["patient_info"]["hcin_number"]
                    if not extraction.get("member_id") and structured_data.get("patient_info", {}).get("member_id"):
                        extraction["member_id"] = structured_data["patient_info"]["member_id"]
                    if not extraction.get("physician_name") and structured_data.get("provider_info", {}).get("physician_name"):
                        extraction["physician_name"] = structured_data["provider_info"]["physician_name"]

                    if not extraction.get("patient_info", {}).get("first_name") and structured_data.get("patient_info"):
                        extraction["patient_info"] = structured_data["patient_info"]
                    if not extraction.get("provider_info", {}).get("credentials") and structured_data.get("provider_info"):
                        extraction["provider_info"] = structured_data["provider_info"]
                    
                    extraction["patient_name"] = f"{structured_data.get('patient_info', {}).get('first_name', '')} {structured_data.get('patient_info', {}).get('last_name', '')}".strip()
                    extraction["patient_dob"] = structured_data.get("patient_info", {}).get("dob", "")
                    extraction["provider_name"] = f"{structured_data.get('provider_info', {}).get('first_name', '')} {structured_data.get('provider_info', {}).get('last_name', '')}".strip()
                    extraction["provider_credentials"] = structured_data.get("provider_info", {}).get("credentials", "")
                    extraction["dos_list"] = structured_data.get("dos_list", [])
                    extraction["extracted_icd_codes"] = structured_data.get("extracted_icd_codes", [])
                    
                    # Also map the deterministic ones for raw_text_extractor.py to override Bedrock Arrays in Step 12
                    extraction["deterministic_dos_list"] = structured_data.get("dos_list", [])
                    extraction["deterministic_icd_codes"] = structured_data.get("extracted_icd_codes", [])

            # Prepare final payload
            result_payload = {
                "file_key": file_key,
                "project_id": body.get('project_id'),
                "project_type": body.get('project_type'),
                "chunk_index": chunk_index,
                "total_chunks": body.get('total_chunks', 1),
                "extraction": extraction,
                "pages": page_range,
                "s3_path": s3_path,
                "total_pages": body.get('total_pages', 0)
            }

            # Save full result to S3 to bypass SQS 256KB limit
            state_key = f"_processing/{file_key}/{chunk_index}.json"
            print(f"[CHUNK_PROCESS] Saving full result to S3: {state_key}")
            s3.put_object(Bucket=RAW_DOCS_BUCKET, Key=state_key, Body=json.dumps(result_payload))

            # Send minimal result to aggregator to trigger it
            # Remove the potentially huge extraction data from the SQS message
            minimal_payload = result_payload.copy()
            del minimal_payload['extraction']
            minimal_payload['data_in_s3'] = True
            
            print(f"[CHUNK_PROCESS] Sending trigger for chunk {chunk_index} to Aggregator via SQS")
            sqs.send_message(
                QueueUrl=RESULTS_QUEUE_URL,
                MessageBody=json.dumps(minimal_payload)
            )
            print(f"[CHUNK_PROCESS] Successfully processed chunk {chunk_index} for {file_key}")
            results.append({"status": "success", "chunk": chunk_index})

        except Exception as e:
            print(f"[LLM_CHUNKER] CRITICAL ERROR processing record for {file_key}: {str(e)}")
            print(traceback.format_exc())
            # Re-raise to trigger SQS retry policy
            raise e

    return {'statusCode': 200, 'body': "Batch processed successfully"}
