import boto3
import io
import sys
import os

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    print("pypdf not installed")
    sys.exit(1)

def test_extraction(pdf_path):
    textract = boto3.client('textract', region_name='us-east-1')
    
    print(f"Testing {pdf_path}")
    with open(pdf_path, 'rb') as f:
        pdf_bytes = f.read()
        
    reader = PdfReader(io.BytesIO(pdf_bytes))
    print(f"Total pages: {len(reader.pages)}")
    
    # Test first 3 pages
    for i in range(min(3, len(reader.pages))):
        print(f"\n--- PAGE {i+1} ---")
        
        # 1. Try pypdf text extraction
        text = reader.pages[i].extract_text() or ""
        print(f"PyPDF text length: {len(text.strip())}")
        
        # 2. If empty, try Textract
        if len(text.strip()) < 50:
            print("Text is empty or very short, trying Textract...")
            
            # Extract just this single page as bytes
            writer = PdfWriter()
            writer.add_page(reader.pages[i])
            page_buffer = io.BytesIO()
            writer.write(page_buffer)
            page_bytes = page_buffer.getvalue()
            
            print(f"Single page PDF bytes length: {len(page_bytes)}")
            
            try:
                response = textract.detect_document_text(Document={'Bytes': page_bytes})
                
                extracted_text = []
                for item in response['Blocks']:
                    if item['BlockType'] == 'LINE':
                        extracted_text.append(item['Text'])
                
                full_text = "\n".join(extracted_text)
                print(f"Textract text length: {len(full_text)}")
                print(f"Sample: {full_text[:100]}")
            except Exception as e:
                print(f"Textract error: {e}")

if __name__ == "__main__":
    pdf_path = "/Users/harshsharma/HCC-Suite/CEN_MP2023W1_5224034.pdf"
    test_extraction(pdf_path)
