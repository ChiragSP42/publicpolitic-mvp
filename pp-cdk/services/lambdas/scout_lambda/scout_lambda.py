import boto3
import os
from googleapiclient.discovery import build

# --- CONFIG ---
API_KEY = os.environ['YOUTUBE_API_KEY']
CHANNEL_ID = os.environ['CHANNEL_ID']
INSTANCE_ID = os.environ['INSTANCE_ID']
SSM_PARAM_NAME = '/meeting/current_video_id'

# --- CLIENTS ---
ssm = boto3.client('ssm')
sfn = boto3.client('stepfunctions')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME) #type: ignore
youtube = build('youtube', 'v3', developerKey=API_KEY)

def lambda_handler(event, context):
    print("Scout waking up to check for live meetings...")
    
    # 1. Search for LIVE Council Meetings on the channel
    try:
        search_response = youtube.search().list(
            part='id,snippet',
            channelId=CHANNEL_ID,
            eventType='live',  
            type='video',
            maxResults=1
        ).execute()
    except Exception as e:
        print(f"Error querying YouTube API: {e}")
        return {'status': 'error', 'message': str(e)}

    items = search_response.get('items', [])
    
    if not items:
        print("No live council meeting found.")
        return {'status': 'no_meeting'}
        
    # We found a meeting!
    video = items[0]
    video_id = video['id']['videoId']
    title = video['snippet']['title']
    print(f"Found Live Video: {title} ({video_id})")

    # 2. Check EC2 State
    status = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    state = status['Reservations'][0]['Instances'][0]['State']['Name']
    
    if state == 'running':
        print("EC2 already processing. Skipping.")
        return {'status': 'already_running'}
    
    # 3. Store Video Info for EC2 to read
    ssm.put_parameter(
        Name=SSM_PARAM_NAME,
        Value=video_id,
        Type='String',
        Overwrite=True
    )
    
    # Optional: Store title too
    ssm.put_parameter(
        Name='/meeting/current_title',
        Value=title,
        Type='String',
        Overwrite=True
    )

    # 4. Wake Up The Soldier
    print(f"Starting EC2 instance {INSTANCE_ID}")
    ec2.start_instances(InstanceIds=[INSTANCE_ID])
    
    return {'status': 'started', 'video_id': video_id, 'title': title}