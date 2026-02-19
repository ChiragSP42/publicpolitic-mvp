import os
import time
import json
import subprocess
import boto3
import string
import random
import numpy as np
from faster_whisper import WhisperModel

# --- CONFIGURATION ---
REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
BUCKET_NAME = os.environ.get('BUCKET_NAME') 

# Model Size: 'tiny', 'base', 'small', 'medium', 'large-v2'
# Warning: 'medium' requires ~5GB RAM. 'small' fits on t3.medium.
MODEL_SIZE = "small" 

s3 = boto3.client('s3', region_name=REGION)
ssm = boto3.client('ssm', region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)



class TranscriptHandler:
    def __init__(self, video_id):
        self.video_id = video_id
        self.full_transcript = [] # List of dicts
        self.last_upload = time.time()

    def add_segment(self, text):
        timestamp = time.strftime('%H:%M:%S', time.gmtime())
        print(f"[{timestamp}] {text}")
        
        self.full_transcript.append({
            "timestamp": timestamp,
            "text": text
        })

        # Heartbeat: Upload to S3 every 60 seconds
        if time.time() - self.last_upload > 60:
            self.upload_s3()

    def upload_s3(self):
        """Uploads both JSON and TXT formats to S3."""
        video_title = ssm.get_parameter(Name='/meeting/current_title')['Parameter']['Value']
        try:
            # 1. Generate JSON Content
            json_body = json.dumps(self.full_transcript, indent=2)
            
            # 2. Generate TXT Content
            txt_lines = [f"[{entry['timestamp']}] {entry['text']}" for entry in self.full_transcript]
            txt_body = "\n".join(txt_lines)

            # 3. Upload JSON
            key_json = f"transcripts/{self.video_id}-{video_title}/transcript.json"
            s3.put_object(
                Bucket=BUCKET_NAME, Key=key_json, 
                Body=json_body, ContentType='application/json'
            )

            # 4. Upload TXT
            key_txt = f"transcripts/{self.video_id}-{video_title}/transcript.txt"
            s3.put_object(
                Bucket=BUCKET_NAME, Key=key_txt, 
                Body=txt_body, ContentType='text/plain'
            )

            print(f"[S3] Updated {key_json} and {key_txt}")
            self.last_upload = time.time()
        except Exception as e:
            print(f"[ERROR] S3 Upload failed: {e}")

def get_stream_url(video_id, proxy_url):
    """Get the live stream URL using yt-dlp."""
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
    """
    Retrieves a JSON secret from AWS Secrets Manager.
    Returns a dict of all key-value pairs stored in the secret.

    No credentials needed here — boto3 automatically uses the
    EC2 instance's IAM role (fetched from the instance metadata service).
    """

    try:
        response = secrets_client.get_secret_value(
            SecretId=secret_name  # Can be name OR full ARN
        )
    except secrets_client.exceptions.ResourceNotFoundException:
        print(f"Secret '{secret_name}' not found.")
        raise
    except secrets_client.exceptions.AccessDeniedException:
        print("EC2 role does not have permission to read this secret.")
        raise
    except Exception as e:
        print(f"Unexpected error fetching secret: {e}")
        raise

    # SecretString contains the raw JSON string — parse it into a dict
    secret_dict = json.loads(response["SecretString"])
    return secret_dict

def run_soldier():
    # 1. Get Video ID from SSM
    try:
        video_id = ssm.get_parameter(Name='/meeting/current_video_id')['Parameter']['Value']
        video_title = ssm.get_parameter(Name='/meeting/current_title')['Parameter']['Value']
        print(f"✅ Target Video ID: {video_id}")
        print(f"Target Video ID: {video_title}")
    except Exception as e:
        print(f"❌ Error fetching Video ID from SSM: {e}")
        return

    # 2. Get Stream URL
    # Retrieve Proxy username and password from AWS Secrets manager
    secrets = get_secret("publicpolitic/proxy_secrets")
    proxy_user = secrets.get("PROXY_USER")
    proxy_pass_base = secrets.get("PROXY_PASS_BASE")
    session_id = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    proxy_pass = f"{proxy_pass_base}_country-us_city-lasvegas_session-{session_id}_lifetime-4h"
    proxy_url = f"http://{proxy_user}:{proxy_pass}@geo.iproyal.com:12321"
    # Pass constructed Proxy URL to get Stream URL
    stream_url = get_stream_url(video_id, proxy_url)
    if not stream_url:
        return

    # 3. Load Model
    print(f"🤖 Loading Whisper Model ({MODEL_SIZE})...")
    # 'int8' is faster and uses less memory with minimal accuracy loss
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

    # 4. Start FFMPEG
    # We read 16kHz mono audio as raw 32-bit floats
    print("🎧 Starting Audio Stream...")
    process = subprocess.Popen(
        ["ffmpeg", "-i", stream_url, "-f", "f32le", "-ac", "1", "-ar", "16000", "-vn", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    handler = TranscriptHandler(video_id)
    
    # 5. Transcription Loop
    CHUNK_SECONDS = 15
    SAMPLE_RATE = 16000
    # 4 bytes per sample (float32)
    CHUNK_SIZE = int(CHUNK_SECONDS * SAMPLE_RATE * 4) 

    try:
        while True:
            # Read chunk
            raw_bytes = process.stdout.read(CHUNK_SIZE) #type: ignore
            if not raw_bytes or len(raw_bytes) == 0:
                print("End of stream.")
                break

            # Convert to numpy array
            audio_chunk = np.frombuffer(raw_bytes, dtype=np.float32)

            # Transcribe
            # beam_size=5 is standard for accuracy
            segments, info = model.transcribe(audio_chunk, beam_size=5)

            for segment in segments:
                text = segment.text.strip()
                if text:
                    handler.add_segment(text)

    except KeyboardInterrupt:
        print("Stopping...")
    except Exception as e:
        print(f"Critical Error: {e}")
    finally:
        # Final save before dying
        handler.upload_s3()
        if process.poll() is None:
            process.terminate()
        
        # Optional: Shutdown instance to save money
        # os.system("sudo shutdown -h now")

if __name__ == "__main__":
    run_soldier()