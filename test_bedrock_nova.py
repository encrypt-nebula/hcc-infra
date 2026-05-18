import boto3
import json

bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

def test_nova():
    try:
        response = bedrock.converse(
            modelId="amazon.nova-pro-v1:0",
            messages=[
                {
                    "role": "user",
                    "content": [{"text": "Hello, world!"}]
                }
            ],
            inferenceConfig={
                "temperature": 0
            }
        )
        print("amazon.nova-pro-v1:0 text SUCCESS")
    except Exception as e:
        print(f"amazon.nova-pro-v1:0 text ERROR: {e}")

    try:
        response = bedrock.converse(
            modelId="us.amazon.nova-pro-v1:0",
            messages=[
                {
                    "role": "user",
                    "content": [{"text": "Hello, world!"}]
                }
            ],
            inferenceConfig={
                "temperature": 0
            }
        )
        print("us.amazon.nova-pro-v1:0 text SUCCESS")
    except Exception as e:
        print(f"us.amazon.nova-pro-v1:0 text ERROR: {e}")

if __name__ == "__main__":
    test_nova()
