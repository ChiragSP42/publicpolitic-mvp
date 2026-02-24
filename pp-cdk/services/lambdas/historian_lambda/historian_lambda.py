import boto3
import os
import json
from typing import List, Dict

# --- CONFIG ---
TABLE_NAME = os.environ.get("TABLE_NAME", "CouncilMeetings")
BUCKET_NAME = os.environ.get("BUCKET_NAME")

# Using the correct Model ID for Claude 3.5 Sonnet
MODEL_ID = 'anthropic.claude-3-5-sonnet-20241022-v2:0' 

# Setup Boto3 clients (Let Lambda inherit the region automatically)
s3_client = boto3.client('s3')
bedrock_runtime_client = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME) #type: ignore

def lambda_handler(event, context):
    print(f"📥 Received event from Step Function: {json.dumps(event)}")
    
    # 1. Extract data from Step Function payload
    video_id = event.get('video_id')
    meeting_active = event.get('meeting_active', True)

    if not video_id:
        print("❌ Error: No video_id provided in event.")
        return {"video_id": None, "meeting_active": False}

    # 2. Retrieve DB Record
    response = table.get_item(Key={'video_id': video_id})
    if 'Item' not in response:
        print(f"❌ Error: Record for {video_id} not found in DB.")
        return {"video_id": video_id, "meeting_active": False}
        
    item = response["Item"]
    current_status = item.get("status")
    print(f"🚥 Current DB Status: {current_status}")

    # GUARD CLAUSES
    if current_status == 'INACTIVE':
        print("Meeting is INACTIVE. Telling Step Function to stop.")
        return {"video_id": video_id, "meeting_active": False}

    # 3. Read Transcript from S3
    # Your EC2 saves it with the title in the folder name now: {video_id}-{title}
    # To keep this simple, let's assume we read the title from the DB or event if needed,
    # but for now, let's look for the transcript.json based on how EC2 saves it.
    # Note: If your EC2 saves to "transcripts/VIDEOID-TITLE/transcript.json", 
    # you might need to query S3 with a prefix to find the exact path.
    # Let's assume the EC2 was updated to just use the video_id for the folder path 
    # to make it easier for the Historian to find.
    s3_key = f"transcripts/{video_id}/transcript.json" 
    
    try:
        s3_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
        transcript = json.loads(s3_response['Body'].read().decode('utf-8'))
    except Exception as e:
        print(f"⚠️ Transcript not found or error reading S3: {e}")
        # Return True so the Step Function waits another 15 mins and tries again
        return {"video_id": video_id, "meeting_active": meeting_active}

    # 4. Check for New Lines
    last_index = int(item.get('last_checkpoint_index', 0))
    current_length = len(transcript)
    
    if current_length <= last_index:
        print("No new transcription lines found this round.")
        # If the stream ended AND there's no new text, we are officially done.
        if current_status == 'COMPLETED':
            return finalize_meeting(video_id)
        return {"video_id": video_id, "meeting_active": True}

    # 5. We have new text! Let's summarize it.
    new_chunk = transcript[last_index:]
    print(f"📝 Found {len(new_chunk)} new lines. Starting summarization...")
    
    # Pass the previous summary to our helper function
    previous_summary = item.get("summary", "")
    new_summary = generate_summary(new_chunk, previous_summary)

    # 6. Update DynamoDB with the new Summary and Checkpoint
    try:
        table.update_item(
            Key={'video_id': video_id},
            UpdateExpression='SET summary = :s, last_checkpoint_index = :i',
            ExpressionAttributeValues={
                ':s': new_summary,
                ':i': current_length
            }
        )
        print(f"✅ Successfully updated DB checkpoint to line {current_length}")
    except Exception as e:
        print(f"❌ Failed to update DB: {e}")

    # 7. Check if this was the final run
    if current_status == 'COMPLETED':
        return finalize_meeting(video_id)

    # Otherwise, tell Step Function to loop again
    return {"video_id": video_id, "meeting_active": True}

def finalize_meeting(video_id):
    """Marks the DB as INACTIVE and tells Step Function to stop."""
    print("🟡 Stream is COMPLETED. Doing final cleanup.")
    try:
        table.update_item(
            Key={"video_id": video_id},
            UpdateExpression='SET #s = :inactive',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':inactive': 'INACTIVE'}
        )
        print("🔴 Set DB status to INACTIVE.")
    except Exception as e:
        print(f"Failed to update final status: {e}")
        
    return {"video_id": video_id, "meeting_active": False}

def generate_summary(transcript_chunk: List[Dict], previous_summary: str) -> str:
    # Concatenate transcript as text
    transcript_text = "\n".join([chunk['text'] for chunk in transcript_chunk])

    # System Prompt
    system_prompt = "You are an expert secretary recording the minutes of a live city council meeting. Your task is to maintain a running, cohesive summary of the meeting."

    # Build the Prompt
    if previous_summary:
        llm_prompt = f"Here is the summary of the meeting so far:\n<previous_summary>\n{previous_summary}\n</previous_summary>\n\nHere is the newly transcribed audio from the last 15 minutes:\n<new_transcript>\n{transcript_text}\n</new_transcript>\n\nPlease integrate the new events into the existing summary to create one cohesive, updated document. Keep it concise and focus on motions, votes, and key arguments."
    else:
        llm_prompt = f"The meeting has just started. Here is the first 15 minutes of transcription:\n<new_transcript>\n{transcript_text}\n</new_transcript>\n\nPlease generate a concise summary of these opening events."

    print("Calling Bedrock (Claude 3.5 Sonnet)...")
    try:
        response = bedrock_runtime_client.converse(
            modelId=MODEL_ID,
            messages=[{'role': 'user', 'content': [{'text': llm_prompt}]}],
            system=[{'text': system_prompt}],
            inferenceConfig={'maxTokens': 4000} # Kept reasonable to save costs
        )
        return response['output']['message']['content'][0]['text']
    except Exception as e:
        print(f"❌ Bedrock Error: {e}")
        return previous_summary # Fallback to the old summary if AI fails