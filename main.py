import boto3
import os
import json

AWS_ACCESS_KEY = os.getenv("LOCAL_AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("LOCAL_AWS_SECRET_KEY")

session = boto3.Session(aws_access_key_id=AWS_ACCESS_KEY,
                        aws_secret_access_key=AWS_SECRET_KEY,
                        region_name='us-west-2')

bedrock_client = session.client("bedrock")

# print(json.dumps(bedrock_client.list_inference_profiles(), indent=2))
print(bedrock_client.list_inference_profiles())