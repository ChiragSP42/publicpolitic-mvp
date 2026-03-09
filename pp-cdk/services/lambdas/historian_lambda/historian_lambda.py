import boto3
import os
import json
from datetime import date
from typing import List, Dict

# --- CONFIG ---
TABLE_NAME = os.environ.get("TABLE_NAME", "CouncilMeetings")
BUCKET_NAME = os.environ.get("BUCKET_NAME")

# Using the correct Model ID for Claude 3.5 Sonnet
MODEL_ID = 'us.anthropic.claude-sonnet-4-5-20250929-v1:0' 

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
    s3_key = f"transcripts/app-data/{date.today()}/{video_id}/transcript.json" 
    
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

    planned_agenda = item.get("planned_agenda", "")
    previous_live_agenda = item.get("live_agenda", "")
    new_live_agenda = generate_agenda(planned_agenda, previous_live_agenda, new_summary)

    # 6. Update DynamoDB with the new Summary and Checkpoint
    try:
        table.update_item(
            Key={'video_id': video_id},
            UpdateExpression='SET summary = :s, last_checkpoint_index = :i, live_agenda = :a',
            ExpressionAttributeValues={
                ':s': new_summary,
                ':i': current_length,
                ':a': new_live_agenda
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
        llm_prompt = f"The meeting has just started. Here is the first transcription:\n<new_transcript>\n{transcript_text}\n</new_transcript>\n\nPlease generate a concise summary of these opening events."

    print("Calling Bedrock (Claude 4.5 Sonnet)...")
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
    
def generate_agenda(planned_agenda: str, previous_live_agenda: str, new_summary: str) -> str:
    """
    Generates a live agenda tracker by cross-referencing:
      - planned_agenda: official agenda scraped from govt website (stored in DynamoDB)
      - previous_live_agenda: Historian's last agenda output (empty string on first run)
      - new_summary: the freshly updated meeting summary from generate_summary()
    """
    system_prompt = (
        "You are an expert secretary tracking a live city council meeting against its planned agenda. "
        "Your task is to maintain a structured live agenda tracker that shows citizens exactly "
        "where the meeting stands relative to the official published agenda."
    )

    if previous_live_agenda:
        # Subsequent runs — update the existing tracker
        llm_prompt = (
            f"Below is the official planned agenda published by the council before the meeting:\n\n"
            f"{planned_agenda}\n\n"
            f"---\n\n"
            f"Here is the live agenda tracker from your last update:\n\n"
            f"{previous_live_agenda}\n\n"
            f"---\n\n"
            f"Here is the updated meeting summary reflecting the last 15 minutes of the meeting:\n\n"
            f"{new_summary}\n\n"
            f"---\n\n"
            f"Please update the live agenda tracker. For each agenda item use one of these statuses:\n"
            f"  ✅ COMPLETED — item was fully discussed and resolved\n"
            f"  🔄 IN PROGRESS — item is currently being discussed\n"
            f"  ⏳ UPCOMING — item has not been reached yet\n"
            f"  ➕ UNPLANNED — item that came up but was not on the planned agenda\n\n"
            f"Keep each item to one line with its status, title, and a brief note if relevant. "
            f"Do not remove completed items — the tracker should be a full running record."
        )
    else:
        # First run — bootstrap the tracker from the planned agenda
        llm_prompt = (
            f"Below is the official planned agenda published by the council before the meeting:\n\n"
            f"{planned_agenda}\n\n"
            f"---\n\n"
            f"The meeting has just started. Here is the initial summary of what has happened so far:\n\n"
            f"{new_summary}\n\n"
            f"---\n\n"
            f"Please create the initial live agenda tracker. For each agenda item use one of these statuses:\n"
            f"  ✅ COMPLETED — item was fully discussed and resolved\n"
            f"  🔄 IN PROGRESS — item is currently being discussed\n"
            f"  ⏳ UPCOMING — item has not been reached yet\n"
            f"  ➕ UNPLANNED — item that came up but was not on the planned agenda\n\n"
            f"Keep each item to one line with its status, title, and a brief note if relevant."
        )

    print("🤖 Calling Bedrock for live agenda...")
    try:
        response = bedrock_runtime_client.converse(
            modelId=MODEL_ID,
            messages=[{'role': 'user', 'content': [{'text': llm_prompt}]}],
            system=[{'text': system_prompt}],
            inferenceConfig={'maxTokens': 2000}  # Agenda is shorter than a full summary
        )
        return response['output']['message']['content'][0]['text']
    except Exception as e:
        print(f"❌ Bedrock Error (agenda): {e}")
        return previous_live_agenda  # Fallback — keep old agenda rather than lose data