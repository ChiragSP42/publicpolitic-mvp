import boto3
import os
import json
from datetime import date
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import requests
import fitz

# --- CONFIG ---
API_KEY = os.environ['YOUTUBE_API_KEY']
CHANNEL_ID = os.environ['CHANNEL_ID']
TABLE_NAME = os.environ['TABLE_NAME']
STATE_MACHINE_ARN = os.environ['STATE_MACHINE_ARN']

# --- CLIENTS ---
ssm = boto3.client('ssm')
sfn = boto3.client('stepfunctions')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME) #type: ignore
youtube = build('youtube', 'v3', developerKey=API_KEY)

def get_city_council_pdf_text(meeting_name, url):
    # 1. Fetch the page using Playwright to render the JavaScript
    print("Loading page and waiting for table to populate...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        
        # Wait for the table body to actually populate with rows
        # We wait for at least one 'tr' to appear inside the tbody
        page.wait_for_selector('#upcomingMeetingsTable tbody tr', timeout=10000)
        
        # Get the fully rendered HTML and close the browser
        html_content = page.content()
        browser.close()

    # Now use BeautifulSoup on the fully rendered HTML
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 2. Locate the specific table
    container = soup.find('div', id='upcomingMeetingsContent')
    table = None
    if container:
        table = container.find('table', id='upcomingMeetingsTable')
    # body = table.find("tbody")
    
    pdf_url = None
    
    # 3. Find the "Planning Commission" row and extract the href
    if table:
        print("Parsing table rows...")
        for row in table.find_all('tr'):
            # Get all text in the row to see if our target phrase is inside
            if meeting_name in row.get_text(): 
                link_tag = row.find('a', href=True)
                if link_tag:
                    # urljoin intelligently combines the base url and the href 
                    # regardless of if the href is relative (/Public/...) or absolute (https://...)
                    pdf_url = urljoin(url, str(link_tag['href'])) 
                    break

    if not pdf_url:
        return "Could not find the Planning Commission PDF link."

    print(f"Found PDF URL: {pdf_url}\nDownloading and extracting text...")

    # 4. Download and Read PDF
    # (Since the PDF itself is a static file, we can still safely use 'requests' here)
    pdf_response = requests.get(pdf_url)
    with fitz.open(stream=pdf_response.content, filetype="pdf") as doc:
        text = ""
        for page in doc:
            text += str(page.get_text())
            
    return text

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

    # 2. IDEMPOTENCY CHECK (Prevent overlapping workflows)
    # Check if we already know about this meeting and if it's currently running.
    response = table.get_item(Key={'video_id': video_id})
    if 'Item' in response:
        if response['Item'].get('status') == 'ACTIVE':
            print(f"Meeting {video_id} is already ACTIVE. Step Function is already managing this. Going back to sleep.")
            return {'status': 'already_running', 'video_id': video_id}
        
    # 2.1 Retrieving planned agenda from govt website
    meeting_name = "Planning Commission"
    agenda_text = get_city_council_pdf_text(meeting_name, "https://lasvegas.primegov.com/public/portal/")

    # 3. Store Video Info in SSM (For the EC2 Soldier to read when it boots)
    print("Updating SSM parameters for Soldier...")
    ssm.put_parameter(
        Name='/meeting/current_video_id',
        Value=video_id,
        Type='String',
        Overwrite=True
    )
    ssm.put_parameter(
        Name='/meeting/current_title',
        Value=title,
        Type='String',
        Overwrite=True
    )

    # 4. Create the DB Record
    print(f"Creating new DB Record for {video_id}")
    table.put_item(
        Item={
            'video_id': video_id,
            'status': 'ACTIVE', # ACTIVE | INACTIVE | COMPLETED
            'start_time': str(date.today()),
            'last_checkpoint_index': 0,
            'planned_agenda': agenda_text,
            'live_agenda': "",
            'summary': ""
        }
    )

    # 5. WAKE UP THE CONDUCTOR (Start the Step Function)
    print(f"Triggering Step Function Orchestrator for {video_id}...")
    
    # We pass the video_id and initial active state into the Step Function payload
    sfn_input = {
        "video_id": video_id,
        "meeting_active": True
    }
    
    sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        # Name is auto-generated by AWS to avoid naming collisions
        input=json.dumps(sfn_input)
    )
    
    return {'status': 'started', 'video_id': video_id, 'title': title}