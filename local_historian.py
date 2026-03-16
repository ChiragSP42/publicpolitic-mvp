import boto3
from botocore.config import Config
import os
from dotenv import load_dotenv
import json
from typing import (
    List,
    Dict
)
load_dotenv(override=True)

TABLE_NAME = os.getenv("TABLE_NAME", "CouncilMeetings")
BUCKET_NAME = os.getenv("BUCKET_NAME")
MODEL_ID = 'us.anthropic.claude-sonnet-4-5-20250929-v1:0'
# Config
config = Config(
    read_timeout=300,
    connect_timeout=60,
    retries={
        'total_max_attempts': 5,
        'mode': 'adaptive'
    }
)

# Setup Boto3 clients and resources
s3_client = boto3.client('s3', region_name='us-west-2')
ssm_client = boto3.client('ssm', region_name='us-west-2')
bedrock_runtime_client = boto3.client('bedrock-runtime', region_name='us-west-2', config=config)
dynamodb = boto3.resource('dynamodb', region_name='us-west-2')
table = dynamodb.Table(TABLE_NAME) #type: ignore

def lambda_handler(event, context):
    # Get video ID from SSM Parameter Store
    try:
        video_id = ssm_client.get_parameter(Name='/meeting/current_video_id')['Parameter']['Value']
        print(f"Getting video ID {video_id} from SSM Parameter Store")
    except:
        print(f"Failed to retrieve video ID from SSM")
        message = {
            "status": "FAILED",
            "message": f"Failed to retrieve video ID from SSM"
        }
        return return_response(status_code=400, message=message)

    # Retrieve record for video ID from DynamoDB
    print("Retrieve record for video ID from DynamoDB")
    response = table.get_item(Key={'id': video_id})

    if 'Item' not in response:
        print(f"Failed to retrieve video ID from DynamoDB")
        message = {
            "status": "FAILED",
            "message": f"Failed to retrieve video ID from DynamoDB"
        }
        return return_response(status_code=400, message=message)
    else:
        item = response["Item"]

    if item.get("status") == 'INACTIVE':
        print(f"Meeting inactive")
        message = {
            "status": "FAILED",
            "message": f"Meeting inactive"
        }
        return return_response(status_code=400, message=message)
    
    # If status IN_PROGRESS, initiate summarization process
    elif item.get("status") == 'IN_PROGRESS':
        print(f"Meeting in progress, extracting transcript from S3 path: {BUCKET_NAME}/transcripts/{video_id}/transcripts.json")
        # Retrieve Raw transcript from S3
        try:
            response = s3_client.get_object(Bucket=BUCKET_NAME, Key=f'transcripts/{video_id}/transcripts.json')
            transcript = json.loads(response['Body'].read().decode('utf-8'))
        except Exception as e:
            print(f"Failed to extract transcript from S3: {str(e)}")
            message = {
                "status": "FAILED",
                "message": f"Failed to extract transcript from S3: {str(e)}"
            }
            return return_response(status_code=400, message=message)

        last_index = int(item.get('last_checkpoint_index', 0))
        if len(transcript) <= last_index:
            print(f"Nothing new")
            message = {
                "status": "NO CHANGE",
                "message": f"Nothing new"
            }
            return return_response(status_code=400, message=message)
        else:
            new_chunk = transcript[last_index:]
            print("Starting summarization function")
            new_summary = summarization(transcript_chunk=new_chunk,
                                        video_id=video_id)
            print(new_summary)

        try:
            table.update_item(
                Key={'id': video_id},
                UpdateExpression='SET summary = :s, last_checkpoint_index = :i',
                ExpressionAttributeValues={
                    ':s': new_summary,
                    ':i': len(transcript)
                }
            )
        except:
            print(f"Failed to update record")
            message = {
                "status": "FAILED",
                "message": f"Failed to update record"
            }
            return return_response(status_code=400, message=message)
        
    elif item.get("status") == 'COMPLETED':
        table.update_item(
            Key={"id": video_id},
            UpdateExpression='SET #s = :inactive',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':inactive': 'INACTIVE'}
        )

def summarization(transcript_chunk: List[Dict],
                  video_id: str):
    # Retrieve latest summary from DB
    response = table.get_item(
        Key={'id': video_id},
        ProjectionExpression='summary'
    )
    if 'Item' not in response:
        print(f"Failed to retrieve video ID from DynamoDB")
        message = {
            "status": "FAILED",
            "message": f"Failed to retrieve video ID from DynamoDB"
        }
        return return_response(status_code=400, message=message)
    else:
        item = response["Item"]
        last_summary = item.get("summary")
    
    # Concatenate transcript as text
    transcript = ""
    for chunk in transcript_chunk:
        transcript = transcript + "\n" + chunk['text']

    # Load system prompt and LLM prompt
    with open("summarization_system_prompt.md", "r") as f:
        system_prompt = f.read()
    
    with open("summarization_prompt.md", "r") as f:
        llm_prompt = f.read()

    # Fill LLM prompt with transcript and summary (if present)
    if last_summary:
        if_previous_summary = "You are updating an ongoing summary of a Las Vegas City Council meeting. Review the previous summary above to understand the context and continuing discussions. Integrate the new transcript segment below into a comprehensive updated summary."
        llm_prompt = llm_prompt.replace("{{PREVIOUS_SUMMARY}}", last_summary)
        llm_prompt = llm_prompt.replace("{{#if PREVIOUS_SUMMARY}}", if_previous_summary)
    
    llm_prompt = llm_prompt.replace("{{TRANSCRIPT}}", transcript)

    # Local dev testing
    system_prompt = "You are an expert summarization bot. You're task is to summarize transcripts of a live youtube video. The last summarized text maybe also be present. Use that as context for what has happened so far and write a new summary with information of the latest transcription chunk and the previous summary if present."

    llm_prompt = f"The previous summmary (if present):\n{last_summary}\nThe transcript:\n{transcript}. Generate the summary below:"

    # Generate new summary
    response = bedrock_runtime_client.converse(modelId=MODEL_ID,
                                                messages=[
                                                    {
                                                        'role': 'user',
                                                        'content': [
                                                            {
                                                                'text': llm_prompt
                                                            }
                                                        ]
                                                    }
                                                ],
                                                system=[
                                                    {
                                                        'text': system_prompt
                                                    }
                                                ],
                                                inferenceConfig={
                                                    'maxTokens': 63000
                                                })

    new_summary = response['output']['message']['content'][0]['text']
    
    return new_summary
    
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

if __name__ == "__main__":
    lambda_handler({}, None)