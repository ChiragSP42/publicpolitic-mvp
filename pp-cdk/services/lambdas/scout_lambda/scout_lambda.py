import boto3
import os
import json
import requests
import io
import pypdf
from datetime import date, datetime
from googleapiclient.discovery import build
from tavily import TavilyClient

# --- CONFIG ---
API_KEY = os.environ['YOUTUBE_API_KEY']
CHANNEL_ID = os.environ['CHANNEL_ID']
TABLE_NAME = os.environ['TABLE_NAME']
STATE_MACHINE_ARN = os.getenv('STATE_MACHINE_ARN', "dfg")
TAVILY_API_KEY = os.environ.get('TAVILY_API_KEY')

# --- CLIENTS ---
ssm = boto3.client('ssm')
sfn = boto3.client('stepfunctions')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME) #type: ignore
youtube = build('youtube', 'v3', developerKey=API_KEY)

def get_city_council_agenda_tavily(meeting_name):
    if not TAVILY_API_KEY:
        print("⚠️ TAVILY_API_KEY is missing. Skipping agenda fetch.")
        return "Agenda retrieval skipped (No Tavily Key)."
        
    print(f"🤖 Asking Tavily to hunt down the agenda for: Las Vegas {meeting_name}")
    
    # Initialize the official Tavily client
    tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    
    try:
        # 1. Ask Tavily to search for the PDF using the official SDK
        response = tavily_client.crawl(
            url="https://lasvegas.primegov.com/public/portal/",
            instructions=f"Get only the meeting agenda from the upcoming and current Las Vegas City Council meeting as a downloadable pdf",
            limit=100,
            max_depth=5,
            max_breadth=2,
            extract_depth="advanced",
            allow_external=False,
            include_raw_content=True
        )
        
        results = response.get('results', [])
        if not results:
            return "Tavily could not find any agenda results."
            
        # Get the top result
        best_result = results[0]
        target_url = best_result.get('url', '')
        print(f"🔗 Tavily found a top result: {target_url}")
        
        # 2. If it's a PDF, we download and read it manually using pure Python
        if target_url.lower().endswith('.pdf'):
            print("📄 Result is a PDF. Downloading and parsing...")
            headers = {'User-Agent': 'Mozilla/5.0'}
            pdf_response = requests.get(target_url, headers=headers)
            pdf_response.raise_for_status()
            
            pdf_file = io.BytesIO(pdf_response.content)
            reader = pypdf.PdfReader(pdf_file)
            
            text = ""
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
            return text
            
        # 3. If it's a webpage, we just use Tavily's pre-extracted text
        else:
            print("🌐 Result is a webpage. Using Tavily's extracted text...")
            # Fallback to the snippet if raw_content isn't available
            return best_result.get('raw_content', best_result.get('content', 'No content extracted.'))
            
    except Exception as e:
        print(f"❌ Tavily search failed: {e}")
        return "Error extracting agenda via Tavily."

def lambda_handler(event, context):
    print("Scout waking up to check for live meetings...")
    print(f"Table name: {TABLE_NAME}")
    
    # 1. Search for LIVE Council Meetings on the channel
    try:
        search_response = youtube.search().list(
            part='id,snippet',
            q='Council Meeting',
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

    # 2. IDEMPOTENCY CHECK (Prevent overlapping workflows)
    response = table.get_item(Key={'id': video_id})
    if 'Item' in response:
        if response['Item'].get('status') == 'ACTIVE':
            print(f"Meeting {video_id} is already ACTIVE. Step Function is already managing this. Going back to sleep.")
            return {'status': 'already_running', 'video_id': video_id}
        
    # 3. Retrieve planned agenda using Tavily
    meeting_name = "Planning Commission"
    # agenda_text = get_city_council_agenda_tavily(meeting_name)

    # 4. Store Video Info in SSM (For the EC2 Soldier to read when it boots)
    print("Updating SSM parameters for Soldier...")
    ssm.put_parameter(Name='/meeting/current_video_id', Value=video_id, Type='String', Overwrite=True)
    ssm.put_parameter(Name='/meeting/current_title', Value=title, Type='String', Overwrite=True)

    # 5. Create the DB Record
    print(f"Creating new DB Record for {video_id}")
    table.put_item(
        Item={
            'id': video_id,
            'videoId': video_id,
            'createdAt': datetime.now().replace(tzinfo=None).isoformat(timespec="milliseconds") + "Z",
            'updatedAt': '',
            'date': datetime.now().replace(tzinfo=None).isoformat(timespec="milliseconds") + "Z",
            'status': 'ACTIVE', 
            'startTime': str(date.today()),
            'lastCheckpointIndex': 0,
            'plannedAgenda': "",
            'liveAgenda': "",
            'summary': ""
        }
    )

    # 6. WAKE UP THE CONDUCTOR (Start the Step Function)
    print(f"Triggering Step Function Orchestrator for {video_id}...")
    sfn_input = {
        "video_id": video_id,
        "meeting_active": True
    }
    sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        input=json.dumps(sfn_input)
    )
    
    return {'status': 'started', 'video_id': video_id, 'title': title}

if __name__ == "__main__":
    lambda_handler(event={}, context=None)