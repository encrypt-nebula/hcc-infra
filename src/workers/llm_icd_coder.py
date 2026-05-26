import json
import boto3
import os
import re
import traceback
import time
import random
from json import JSONDecodeError
import urllib.request

# Clients
s3 = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')
sqs = boto3.client('sqs')
secretsmanager = boto3.client('secretsmanager')

# Env
RESULTS_QUEUE_URL = os.environ.get('RESULTS_QUEUE_URL')
RAW_DOCS_BUCKET = os.environ.get('RAW_DOCS_BUCKET', 'hcc-platform-dev-raw-docs')
INTERNAL_API_KEY_ARN = os.environ.get('INTERNAL_API_KEY_ARN') or os.environ.get('INTERNAL_API_KEY_SECRET_ARN')
INTERNAL_API_KEY = os.environ.get('INTERNAL_API_KEY') or os.environ.get('API_KEY')
CLAUDE_SECRET_ID = os.environ.get('CLAUDE_SECRET_ID') or os.environ.get('CLAUDE_API_KEY_ARN')
API_BASE_URL = os.environ.get('API_BASE_URL', 'http://54.84.217.140:8080').rstrip('/')



import io
import tempfile

try:
    import pypdf
    PDF_TOOLS_AVAILABLE = True
except ImportError as e:
    pypdf = None
    PDF_TOOLS_AVAILABLE = False
    print(f"[WARNING] PDF extraction tools missing: {e}.")

_cached_internal_api_key = None

def get_internal_api_key():
    global _cached_internal_api_key
    if _cached_internal_api_key:
        return _cached_internal_api_key

    if INTERNAL_API_KEY:
        print(f"[AUTH] Using internal API key from environment variable.")
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

            print(f"[AUTH] Successfully loaded internal API key from Secrets Manager.")
            _cached_internal_api_key = secret_string.strip()
            return _cached_internal_api_key
        except Exception as e:
            print(f"[AUTH] Failed to load internal API key from Secrets Manager: {e}")

    print(f"[AUTH] Using hardcoded fallback internal API key.")
    _cached_internal_api_key = "hcc-internal-secure-key-2026"
    return _cached_internal_api_key

class DeterministicPipeline:
    def __init__(self, signature_conf=0.5):
        self.signature_conf = signature_conf
        self.sig_patterns = [
            re.compile(r'\b' + kw + r'\b', re.IGNORECASE) 
            for kw in ["electronically signed", "digitally signed", "signed by", "signed electronically", "electronic signature", "signed at"]
        ]

    def _extract_structured_data(self, text, output):
        output['patient_info'] = {'first_name': '', 'last_name': '', 'dob': '', 'hcin_number': '', 'member_id': ''}
        output['provider_info'] = {'first_name': '', 'last_name': '', 'physician_name': '', 'credentials': ''}
        
        # 1. HCIN and Member ID (more robust patterns)
        hcin_match = re.search(r'(?:HCIN|HCIN\s*NUMBER|HCIN#|HICN)[:\s]*([A-Z0-9]{5,15})', text, re.IGNORECASE)
        if hcin_match: output['patient_info']['hcin_number'] = hcin_match.group(1).strip()
        
        member_match = re.search(r'(?:Member ID|Patient ID|Member#|ID#)[:\s]*([A-Z0-9]{5,20})', text, re.IGNORECASE)
        if member_match: output['patient_info']['member_id'] = member_match.group(1).strip()

        # 2. Patient Name (Handling "Name: Last, First" and "Patient: First Last")
        # Pattern for "First Last"
        pt_match_fl = re.search(r'(?:Patient Name|Patient|Name)[:\s]*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text)
        # Pattern for "Last, First"
        pt_match_lf = re.search(r'(?:Patient Name|Patient|Name)[:\s]*([A-Z][a-z]+),\s*([A-Z][a-z]+)', text)
        
        if pt_match_lf:
            output['patient_info']['first_name'] = pt_match_lf.group(2).strip()
            output['patient_info']['last_name'] = pt_match_lf.group(1).strip()
        elif pt_match_fl:
            parts = pt_match_fl.group(1).split()
            output['patient_info']['first_name'] = parts[0].strip()
            output['patient_info']['last_name'] = parts[-1].strip()
                
        # 3. Date of Birth
        dob_match = re.search(r'(?:DOB|Date of Birth|Birthdate|D\.O\.B)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', text, re.IGNORECASE)
        if dob_match:
            output['patient_info']['dob'] = dob_match.group(1).strip()
            
        # 4. Provider Information
        prov_match = re.search(r'(?:Provider|Attending|Physician|Clinician)[:\s]*([A-Z][A-Za-z\s\.\,]+)', text, re.IGNORECASE)
        if prov_match:
            name_text = prov_match.group(1).strip()
            # Clean up if credentials are included in name string
            name_text = re.split(r'[,]\s*(?:MD|DO|NP|PA|FNP|RN)', name_text, flags=re.IGNORECASE)[0]
            output['provider_info']['physician_name'] = name_text
            parts = name_text.split()
            if len(parts) >= 2:
                output['provider_info']['first_name'] = parts[0]
                output['provider_info']['last_name'] = parts[-1]
                
        cred_match = re.search(r'\b(MD|DO|NP|PA|FNP|RN|LPN|DPM)\b', text, re.IGNORECASE)
        if cred_match:
            output['provider_info']['credentials'] = cred_match.group(1).upper()
            
        dos_list = set()
        for dos_val in re.findall(r'(?:DOS|Date of Service|Encounter Date|Service Date|Visit Date)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', text, re.IGNORECASE):
            dos_list.add(dos_val)
        output['dos_list'] = list(dos_list)
        
        icd_codes = set()
        # Look for ICD-10 patterns globally
        codes = re.findall(r'\b([A-TV-Z][0-9][0-9](?:\.[0-9A-Z]{1,4})?)\b', text)
        icd_codes.update(codes)
                
        output['extracted_icd_codes'] = list(icd_codes)

        # 5. Insurance / Payer
        insurance_match = re.search(r'(?:Insurance|Payer|Carrier|Plan Name|Insurance Co)[:\s]*([A-Z\s]{3,40})', text, re.IGNORECASE)
        if insurance_match:
            output['insurance'] = insurance_match.group(1).strip()
        else:
            output['insurance'] = ''



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
ACTIVE_MODEL = "NOVA" 

USE_PYTHON_PIPELINE = str(os.environ.get('USE_PYTHON_PIPELINE', 'false')).lower() == 'false'

MODELS = {
    "NOVA": "us.amazon.nova-lite-v1:0",
    "NOVA_PRO": "us.amazon.nova-pro-v1:0"
}

def validate_icd_codes(queries):
    """
    Validates a list of potential ICD codes or condition names via the Spring Boot API
    which performs a fuzzy match against the icd_codes table.
    """
    if not queries:
        return []
        
    url = f"{API_BASE_URL}/icd-codes/validate"
    payload = {"queries": queries}
    
    api_key = get_internal_api_key()
    
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

def _build_converse_content(model_id, file_content, file_ext, prompt, raw_text, text_only=False):
    content_blocks = []
    content_blocks.append({'text': f"RAW DOCUMENT TEXT:\n{raw_text[:12000]}\n\nPROMPT:\n{prompt}"})

    if text_only:
        return content_blocks

    # Skip document attachment if content exceeds Bedrock's ~4.5MB document limit
    if len(file_content) > 4_500_000:
        print(f"[LLM_CHUNKER] Document too large ({len(file_content)} bytes), using text-only mode")
        return content_blocks

    if file_ext in ['pdf', 'csv', 'txt']:
        if "nova" in model_id.lower():
            content_blocks.append({'document': {'name': 'Doc', 'format': file_ext, 'source': {'bytes': file_content}}})
    else:
        content_blocks.append({'image': {'format': 'jpeg' if file_ext in ['jpg', 'jpeg'] else file_ext, 'source': {'bytes': file_content}}})

    return content_blocks

def _invoke_bedrock(model_id, content_blocks):
    return bedrock_runtime.converse(
        modelId=model_id,
        messages=[{'role': 'user', 'content': content_blocks}],
        inferenceConfig={
            'temperature': 0,
            'maxTokens': 5120
        }
    )

def _repair_json(json_str):
    """
    Attempt to fix common JSON formatting issues from LLM responses.
    Handles: trailing commas, control characters, missing commas between
    key-value pairs / array elements, and other common LLM artifacts.
    """
    s = json_str

    # 1. Remove trailing commas before } or ]
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*]', ']', s)

    # 2. Remove non-printable control characters (keep space, tab, newline, CR)
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)

    # 3. Fix missing commas between } { or ] [ in arrays/objects
    s = re.sub(r'}\s*\n\s*{', '},\n{', s)
    s = re.sub(r']\s*\n\s*\[', '],\n[', s)
    s = re.sub(r'}\s*{', '},{', s)
    s = re.sub(r']\s*\[', '],[', s)

    # 4. Fix missing commas between adjacent string values in arrays
    # e.g., "I10" "E11.9" or "I10"\n  "E11.9"
    s = re.sub(r'"\s*\n\s*"', '",\n"', s)

    # 5. FIX ROOT CAUSE: missing commas between a value and the next object key
    # Handles patterns like:  "value"\n  "nextKey":  or  true\n  "key":
    # or  123\n  "key":  or  null\n  "key":
    s = re.sub(r'("|true|false|null|\d)\s*\n(\s*"[^"]+"\s*:)', r'\1,\n\2', s)

    # 6. Fix missing commas after ] or } followed by a new key
    # e.g., ]\n  "nextKey":  or  }\n  "nextKey":
    s = re.sub(r'([\]\}])\s*\n(\s*"[^"]+"\s*:)', r'\1,\n\2', s)

    # 7. Remove BOM and zero-width characters
    s = s.replace('\ufeff', '').replace('\u200b', '')

    return s


def _balance_brackets(json_str):
    """
    Close any unclosed brackets/braces in a truncated JSON string.
    Handles truncated strings (unclosed quotes) as well.
    """
    in_string = False
    escape_next = False
    stack = []

    for ch in json_str:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ('{', '['):
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '{':
                stack.pop()
        elif ch == ']':
            if stack and stack[-1] == '[':
                stack.pop()

    result = json_str
    # If we ended inside an unclosed string, close it
    if in_string:
        result += '"'

    # Remove any trailing comma before we close brackets
    result = result.rstrip()
    if result.endswith(','):
        result = result[:-1]

    # Close remaining open brackets/braces in reverse order
    while stack:
        bracket = stack.pop()
        if bracket == '{':
            result += '}'
        elif bracket == '[':
            result += ']'

    return result


def _truncate_to_valid_json(json_str):
    """
    If the model response was cut off mid-JSON, try to find the last valid
    closing point and build a parseable JSON object from it.
    Uses progressive truncation and bracket balancing.
    """
    first_brace = json_str.find('{')
    if first_brace == -1:
        return None

    # Strategy 1: Find the last } that creates valid JSON (try more positions)
    last_pos = json_str.rfind('}')
    attempts = 0
    while last_pos > first_brace and attempts < 50:
        candidate = json_str[first_brace:last_pos + 1]
        candidate = re.sub(r',\s*}', '}', candidate)
        candidate = re.sub(r',\s*]', ']', candidate)
        candidate = re.sub(r'}\s*{', '},{', candidate)
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            pass
        last_pos = json_str.rfind('}', first_brace, last_pos)
        attempts += 1

    # Strategy 2: Truncate at the last complete array element or object entry,
    # then balance unclosed brackets
    candidate = json_str[first_brace:]
    # Find last comma outside of strings as a safe truncation point
    in_string = False
    escape_next = False
    last_comma = -1
    for i, ch in enumerate(candidate):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string and ch == ',':
            last_comma = i

    if last_comma > 0:
        truncated = candidate[:last_comma]
        balanced = _balance_brackets(truncated)
        try:
            json.loads(balanced)
            return balanced
        except Exception:
            pass

    # Strategy 3: Balance the full candidate as-is
    balanced = _balance_brackets(candidate)
    try:
        json.loads(balanced)
        return balanced
    except Exception:
        pass

    return None


def call_bedrock_llm(file_content, file_ext, page_range, file_key, raw_text):
    # Use environment variable if set, otherwise use the ACTIVE_MODEL constant
    model_name = os.environ.get('MODEL_NAME', ACTIVE_MODEL)
    model_id = MODELS.get(model_name, MODELS["NOVA"])

    print(f"[LLM_CHUNKER] Calling Bedrock ({model_name}) for {file_key}, Range: {page_range}")
    
    prompt = f"""Analyze the provided text and document. Extract Patient and Provider info.
Specifically, look for and extract:
- hcin_number (HCIN NUMBER)
- member_id (MEMBER ID)
- physician_name (PHYSICIAN or PROVIDER NAME)
- insurance (INSURANCE COMPANY or PAYER NAME). If the insurance payer is not explicitly stated on the document, you MUST return null. Do not infer or guess.

For each visit/encounter found in the document:
1. Identify the Date of Service (DOS). **CRITICAL: The DOS MUST NOT be empty. If you cannot find a clear DOS for a section, associate it with the closest preceding date.**
2. Do NOT extract alphanumeric ICD codes. Only extract clear textual descriptions of any clinical conditions, problems, or diagnoses mentioned (e.g. "Choledocholithiasis", "Hypertension"). Put these in clinical_conditions.

Return strict JSON only. No text/markdown.

OUTPUT FORMAT:
{{
  "signature_found": false,
  "hcin_number": "",
  "member_id": "",
  "physician_name": "",
  "insurance": null,
  "patient_info": {{"first_name": "", "last_name": "", "dob": ""}},
  "provider_info": {{"first_name": "", "last_name": "", "credentials": ""}},
  "details": [{{
    "dos": "MM/DD/YYYY",
    "clinical_conditions": ["Diabetes", "Hypertension"]
  }}]
}}"""

    last_error = None
    content_blocks = _build_converse_content(model_id, file_content, file_ext, prompt, raw_text)
    try:
        response = _invoke_bedrock(model_id, content_blocks)
        text = response['output']['message']['content'][0]['text']
        print(f"[LLM_CHUNKER] Bedrock response length: {len(text)} chars from {model_id}")

        json_match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        json_str = json_match.group(1).strip() if json_match else text.strip()
        json_str = re.sub(r'```json\s*|\s*```', '', json_str).strip()

        # Attempt 1: Parse as-is
        try:
            return json.loads(json_str)
        except JSONDecodeError as je:
            print(f"[LLM_CHUNKER] Initial JSON parse failed: {je}. Attempting repair...")

        # Attempt 2: Repair common Nova Pro JSON issues and retry
        repaired = _repair_json(json_str)
        try:
            result = json.loads(repaired)
            print(f"[LLM_CHUNKER] JSON repair SUCCEEDED for {model_id}")
            return result
        except JSONDecodeError as je2:
            print(f"[LLM_CHUNKER] JSON repair also failed: {je2}. Trying aggressive truncation...")

        # Attempt 3: Aggressively truncate to last valid closing brace
        # Use the already-repaired string for truncation (not the original)
        truncated = _truncate_to_valid_json(repaired)
        if truncated:
            try:
                result = json.loads(truncated)
                print(f"[LLM_CHUNKER] JSON truncation repair SUCCEEDED for {model_id}")
                return result
            except JSONDecodeError:
                pass

        # Attempt 4: Last resort — balance brackets on the original extracted JSON
        balanced = _balance_brackets(_repair_json(json_str))
        try:
            result = json.loads(balanced)
            print(f"[LLM_CHUNKER] JSON bracket balancing SUCCEEDED for {model_id}")
            return result
        except JSONDecodeError:
            pass

        # All parse attempts failed — raise original error to trigger retry
        raise JSONDecodeError(f"All JSON repair attempts failed", json_str[:200], 0)

    except Exception as e:
        last_error = e
        error_text = str(e)
        print(f"[LLM_CHUNKER] Bedrock ERROR from {model_id}: {error_text}")
        
        # Text-only fallback for specific errors if we haven't already
        if "validationexception" in error_text.lower() or "operation not allowed" in error_text.lower():
            print(f"[LLM_CHUNKER] Document attachment failed, retrying in text-only mode...")
            try:
                content_blocks = _build_converse_content(model_id, file_content, file_ext, prompt, raw_text, text_only=True)
                response = _invoke_bedrock(model_id, content_blocks)
                text = response['output']['message']['content'][0]['text']
                # (re-run repair/parse logic here)
                json_match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
                json_str = json_match.group(1).strip() if json_match else text.strip()
                repaired = _repair_json(json_str)
                return json.loads(repaired)
            except Exception as e2:
                print(f"[LLM_CHUNKER] Text-only retry also failed: {e2}")

        raise e

    if last_error:
        raise last_error
    raise RuntimeError("Bedrock invocation failed without a captured error.")

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
                            raw_conditions = entry.get('clinical_conditions', [])
                            explicit_icds = structured_data.get('extracted_icd_codes', [])
                            
                            icd_codes = []
                            # Step 1: Add explicitly found ICD codes via Regex (highest confidence)
                            for code in explicit_icds:
                                icd_codes.append({
                                    "code": code,
                                    "description": "Extracted verbatim from document",
                                    "confidence": 0.99,
                                    "source": "explicit_icd"
                                })
                            
                            # Step 2: Validate LLM extracted clinical conditions via API and map to ICD codes
                            desc_queries = [str(q).strip() for q in raw_conditions if str(q).strip()]
                            if desc_queries:
                                print(f"[CHUNK_PROCESS] Validating {len(desc_queries)} Descriptions for DOS {dos}")
                                mapped_codes = validate_icd_codes(desc_queries)
                                for code in mapped_codes:
                                    if not any(c.get("code") == code for c in icd_codes):
                                        icd_codes.append({
                                            "code": code,
                                            "description": "Mapped from clinical condition text",
                                            "confidence": 0.75,
                                            "source": "diagnosis_mapping"
                                        })
                            
                            # Remove old schema fields
                            if 'clinical_conditions' in entry:
                                del entry['clinical_conditions']
                            
                            # Only keep entry if it has at least one valid code
                            if icd_codes:
                                entry['icdCodes'] = icd_codes
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
                    
                    if not extraction.get("insurance") and structured_data.get("insurance"):
                        extraction["insurance"] = structured_data["insurance"]
                    
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
