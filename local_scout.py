import boto3
import os
import json
from datetime import datetime
from dotenv import load_dotenv
from googleapiclient.discovery import build
load_dotenv(override=True)

# CONFIG
API_KEY = os.environ['YOUTUBE_API_KEY']
CHANNEL_ID = os.environ['CHANNEL_ID']
TABLE_NAME = os.getenv("TABLE_NAME")
SSM_PARAM_NAME = '/meeting/current_video_id'

if os.getenv("LOCAL_AWS_ACCESS_KEY") and os.getenv("LOCAL_AWS_SECRET_KEY"):
    local_aws_access_key = os.getenv("LOCAL_AWS_ACCESS_KEY")
    local_aws_secret_key = os.getenv("LOCAL_AWS_SECRET_KEY")
    session = boto3.Session(aws_access_key_id=local_aws_access_key,
                            aws_secret_access_key=local_aws_secret_key,
                            region_name='us-west-2')
    
    ec2 = session.client("ec2")
    ssm = session.client("ssm")
    dynamodb = session.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME) #type: ignore

ec2 = boto3.client('ec2', region_name='us-west-2')
ssm = boto3.client('ssm', region_name='us-west-2')
youtube = build('youtube', 'v3', developerKey=API_KEY)

def lambda_handler(event, context):
    # 1. Search for LIVE Council Meetings on the channel
    # Costs 100 quota units (You have 10,000/day, so 100 checks/day is safe)
    search_response = youtube.search().list(
        part='id,snippet',
        channelId=CHANNEL_ID,
        eventType='live',  # Only live streams
        type='video',
        q='Watch Sky News',  # Filter by title
        maxResults=1
    ).execute()

    items = search_response.get('items', [])
    
    if not items:
        print("No live council meeting found.")
        return {'status': 'no_meeting'}
    else:
        video = items[0]
        video_id = video['id']['videoId']
        title = video['snippet']['title']
    
        print(f"Found: {title} ({video_id})")
        print(f"Creating DB Record for {video_id}")
        table.put_item(
            Item={
                'video_id': video_id,
                'status': 'ACTIVE',
                'start_time': datetime.now().isoformat(),
                'last_checkpoint_index': 0,
                'summary': '' # Start empty
            }
        )

    # 2. Check EC2 State
    # status = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    # state = status['Reservations'][0]['Instances'][0]['State']['Name']
    
    # if state == 'running':
    #     print("EC2 already processing. Skipping.")
    #     return {'status': 'already_running'}
    
    # # 3. Store Video Info for EC2 to read
    # ssm.put_parameter(
    #     Name=SSM_PARAM_NAME,
    #     Value=video_id,
    #     Type='String',
    #     Overwrite=True
    # )
    
    # # Optional: Store title too
    # ssm.put_parameter(
    #     Name='/meeting/current_title',
    #     Value=title,
    #     Type='String',
    #     Overwrite=True
    # )

    # # 4. Wake Up The Soldier
    # print(f"Starting EC2 instance {INSTANCE_ID}")
    # ec2.start_instances(InstanceIds=[INSTANCE_ID])
    
    return {'status': 'started', 'video_id': video_id, 'title': title}

if __name__ == "__main__":
    print(f"{json.dumps(lambda_handler({}, None), indent=2)}")