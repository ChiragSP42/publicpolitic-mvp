import boto3
import os
import json

# Environment variables
BUCKET_NAME = os.getenv("BUCKET_NAME", "publicpolitic")
MODEL_ARN = 'arn:aws:bedrock:us-west-2:674171865978:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0'
KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID")

# Define boto3 clients
s3_client = boto3.client("s3")
bedrock_agent_runtime_client = boto3.client("bedrock-agent-runtime")

def lambda_handler(event, context):
    """The payload is of the following format:
    {
        "query": string,
        "date_filter": string
    }

    This can be standalone or wrapped in the body parameter.
    """
    print(f"Recieved payload: {json.dumps(event, indent=2)}")
    try:
        if 'body' in event and event['body'] is not None:
            payload = json.loads(event.get('body'))
        else:
            # Direct invocation (AWS Console test)
            payload = event
    except Exception as e:
        print(f"Failed to parse event: {e}")
        message = {
            "status": "FAILED",
            "message": f"Failed to parse event: {e}"
        }
        return return_response(status_code=400, message=message)
    
    user_query = payload.get("query")
    date_filter = None
    kb_config = {
        'knowledgeBaseId': KNOWLEDGE_BASE_ID,
        'modelArn': MODEL_ARN
    }
    if payload.get("date_filter") is not None:
        date_filter = payload.get("date_filter")
        kb_config["retrievalConfiguration"] = {
            'vectorSearchConfiguration': {
                'filter': {
                    'equals': {
                        'key': 'date_filter',
                        'value': date_filter
                    }
                }
            }
        }


    # Bedrock RAG call
    response = bedrock_agent_runtime_client.retrieve_and_generate(
        input={
            'text': user_query
        },
        retrieveAndGenerateConfiguration={
            'type': 'KNOWLEDGE_BASE',
            'knowledgeBaseConfiguration':kb_config
        }
    )

    return response["output"]

def return_response(status_code: int, message: dict):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(message)
    }
