import json
import boto3
import os
import mimetypes
from botocore.config import Config

s3_client = boto3.client('s3', config=Config(signature_version='s3v4'))

# Environment variables
RAW_DOCS_BUCKET = os.environ.get('RAW_DOCS_BUCKET')

def lambda_handler(event, context):
    """
    Generate a presigned S3 URL for uploading files.
    Expects JSON body: { "project_id": "...", "project_type": "...", "file_name": "..." }
    """
    try:
        # Parse body
        if 'body' in event and event['body']:
            body = json.loads(event['body'])
        else:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing request body'})
            }

        project_id = body.get('project_id')
        project_type = body.get('project_type')
        file_name = body.get('file_name')

        # Validation
        if not all([project_id, project_type, file_name]):
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing required fields: project_id, project_type, file_name'})
            }

        if project_type not in ["PROSPECTIVE", "RETROPROSPECTIVE"]:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Invalid project_type. Must be PROSPECTIVE or RETROPROSPECTIVE'})
            }

        # Construct S3 Key
        # format: uploads/{project_id}/{project_type}/{filename}
        file_key = f"uploads/{project_id}/{project_type}/{file_name}"

        # Detect content type
        content_type, _ = mimetypes.guess_type(file_name)
        if not content_type:
            content_type = 'application/octet-stream'

        # Generate Presigned URL
        # Note: We use put_object for direct uploads
        expiration = 3600
        url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': RAW_DOCS_BUCKET,
                'Key': file_key,
                'ContentType': content_type
            },
            ExpiresIn=expiration
        )

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': json.dumps({
                'upload_url': url,
                'file_key': file_key,
                'expires_in': expiration
            })
        }

    except Exception as e:
        print(f"Error generating presigned URL: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': json.dumps({'error': str(e)})
        }
