import os
import time
import json
import subprocess
import boto3
import string
import random
import numpy as np
from datetime import date
from faster_whisper import WhisperModel

# --- CONFIGURATION ---
REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
BUCKET_NAME = os.environ.get('BUCKET_NAME') 
TABLE_NAME = "CouncilMeetings" # Set in your CDK

MODEL_SIZE = "small" 

s3 = boto3.client('s3', region_name=REGION)
ssm = boto3.client('ssm', region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)
dynamodb = boto3.resource('dynamodb', region_name=REGION)
table = dynamodb.Table(TABLE_NAME) #type: ignore


class TranscriptHandler:
    def __init__(self, video_id):
        self.video_id = video_id
        self.full_transcript = [] 
        self.last_upload = time.time()

    def add_segment(self, text):
        timestamp = time.strftime('%H:%M:%S', time.gmtime())
        print(f"[{timestamp}] {text}")
        
        self.full_transcript.append({
            "timestamp": timestamp,
            "text": text
        })

        if time.time() - self.last_upload > 60:
            self.upload_s3()

    def upload_s3(self):
        video_title = ssm.get_parameter(Name='/meeting/current_title')['Parameter']['Value']
        try:
            json_body = json.dumps(self.full_transcript, indent=2)
            txt_lines = [f"[{entry['timestamp']}] {entry['text']}" for entry in self.full_transcript]
            txt_body = "\n".join(txt_lines)

            key_json = f"transcripts/app-data/{date.today()}/{self.video_id}/transcript.json"
            s3.put_object(
                Bucket=BUCKET_NAME, Key=key_json, 
                Body=json_body, ContentType='application/json'
            )

            key_txt = f"transcripts/knowledge-base/{date.today()}/{self.video_id}/transcript.txt"
            s3.put_object(
                Bucket=BUCKET_NAME, Key=key_txt, 
                Body=txt_body, ContentType='text/plain'
            )

            print(f"[S3] Updated {key_json}")
            self.last_upload = time.time()
        except Exception as e:
            print(f"[ERROR] S3 Upload failed: {e}")

def get_stream_url(video_id, proxy_url):
    print(f"🔗 Extracting stream URL for {video_id}...")
    cmd = ["yt-dlp"]
    
    if proxy_url:
        print(f"   ℹ️  Using Proxy: {proxy_url}")
        cmd.extend(["--proxy", proxy_url])
    
    cmd.extend(["-g", f"https://www.youtube.com/watch?v={video_id}"])

    try:
        return subprocess.check_output(cmd).decode('utf-8').strip()
    except subprocess.CalledProcessError:
        print("❌ Error: Could not get stream URL (Check IP/Proxy/VideoID)")
        return None
    
def get_secret(secret_name: str) -> dict:
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
    except Exception as e:
        print(f"Unexpected error fetching secret: {e}")
        raise
    return json.loads(response["SecretString"])

def run_soldier():
    video_id = None
    handler = None
    process = None
    try:
        # 1. Get Video ID from SSM
        video_id = ssm.get_parameter(Name='/meeting/current_video_id')['Parameter']['Value']
        video_title = ssm.get_parameter(Name='/meeting/current_title')['Parameter']['Value']
        # If video ID and title are retrieved, delete the SSM parameters immediately so that it doesn't get picked up on next run.
        if video_id and video_title:
            ssm.put_parameter(
                Name='/meeting/current_video_id',
                Value="0",
                Type='String',
                Overwrite=True
            )
            ssm.put_parameter(
                Name='/meeting/current_title',
                Value="0",
                Type='String',
                Overwrite=True
            )
        print(f"✅ Target Video ID: {video_id} | Title: {video_title}")

        # 2. Get Stream URL
        secrets = get_secret("publicpolitic/proxy_secrets")
        proxy_user = secrets.get("PROXY_USER")
        proxy_pass_base = secrets.get("PROXY_PASS_BASE")
        session_id = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        proxy_pass = f"{proxy_pass_base}_country-us_city-lasvegas_session-{session_id}_lifetime-4h"
        proxy_url = f"http://{proxy_user}:{proxy_pass}@geo.iproyal.com:12321"
        
        stream_url = get_stream_url(video_id, proxy_url)
        if not stream_url:
            return

        # 3. Load Model
        print(f"🤖 Loading Whisper Model ({MODEL_SIZE})...")
        model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

        # 4. Start FFMPEG
        print("🎧 Starting Audio Stream...")
        process = subprocess.Popen(
            ["ffmpeg", 
             "-http_proxy", proxy_url, 
             "-i", stream_url, 
             "-f", "f32le", 
             "-ac", "1", 
             "-ar", "16000", 
             "-vn", 
             "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

        handler = TranscriptHandler(video_id)
        
        # 5. Transcription Loop
        CHUNK_SECONDS = 15
        SAMPLE_RATE = 16000
        CHUNK_SIZE = int(CHUNK_SECONDS * SAMPLE_RATE * 4) 

        # Create the .metadata.json file for this transcription
        # Date format: YYYY-MM-DD
        metadata_attributes = {
            "metadataAttributes": {
            "date": str(date.today())
        }
        }
        key_json = f"transcripts/{date.today()}/{video_id}/transcript.json.metadata.json"
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=key_json, 
            Body=json.dumps(metadata_attributes),
            ContentType='application/json'
        )


        while True:
            raw_bytes = process.stdout.read(CHUNK_SIZE) #type: ignore
            # If YouTube hangs up, or the proxy drops, this will trigger
            if not raw_bytes or len(raw_bytes) == 0:
                print("End of stream.")
                break

            audio_chunk = np.frombuffer(raw_bytes, dtype=np.float32)

            segments, info = model.transcribe(audio_chunk, 
                                              beam_size=5,
                                              language='en',
                                              vad_filter=True,
                                              vad_parameters=dict(min_silence_duration_ms=500),
                                              condition_on_previous_text=False)

            for segment in segments:
                text = segment.text.strip()
                if text:
                    handler.add_segment(text)

    except KeyboardInterrupt:
        print("Stopping...")
    except Exception as e:
        print(f"Critical Error: {e}")
    finally:
        # CLEANUP & NOTIFY
        print("🧹 Cleaning up...")
        if 'handler' in locals() and handler:
            handler.upload_s3()
        if 'process' in locals() and process:
            if process.poll() is None:
                process.terminate()

        # THE CRITICAL STEP FOR THE STEP FUNCTION
        if video_id:
            print("🚦 Stream finished. Setting DynamoDB status to COMPLETED.")
            try:
                table.update_item(
                    Key={'video_id': video_id},
                    UpdateExpression="SET #s = :completed",
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':completed': 'COMPLETED'}
                )
            except Exception as e:
                print(f"Failed to update DynamoDB: {e}")

if __name__ == "__main__":
    run_soldier()