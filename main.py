from googleapiclient.discovery import build

YOUTUBE_API_KEY="AIzaSyD1gsaFqftY3bNqx3vel1jrmy10UDkcoWY"
# CHANNEL_ID="UCCs9Fy2QlMXah1JPxRs9Xdw" # LasVegasTV
CHANNEL_ID="UCoMdktPbSTixAyNGwb-UYkQ" # Sky news

youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

search_response = youtube.search().list(
    part='id,snippet',
    channelId=CHANNEL_ID,
    eventType='live',  # Only live streams
    type='video',
    # q='Meeting',  # Filter by title
    maxResults=5
).execute()

items = search_response.get('items', [])

if not items:
    print("No live council meeting found.")
    # print(f"{'status': 'no_meeting'}")
else:
    video = items[0]
    video_id = video['id']['videoId']
    title = video['snippet']['title']

    print(f"Found: {title} ({video_id})")