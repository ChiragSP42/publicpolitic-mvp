import os
import time
import json
import boto3
import subprocess
import asyncio
import numpy as np
import local_scout
from faster_whisper import WhisperModel

# AWS Imports
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent

# --- CONFIGURATION ---
# Video ID (Hardcoded as requested)
VIDEO_ID = "YDvsBbKfLPA"  # Replace with your target live video ID

# Proxy Configuration (For bypassing YouTube IP blocks on AWS)
#    Leave empty to use your Local IP (Home/Office network).
#    Format: "http://user:pass@host:port"
PROXY_URL = "" 

# Model Size: 'tiny', 'base', 'small', 'medium', 'large-v2'
MODEL_SIZE = "small" 

# Output File
OUTPUT_FILE = "local_transcript.json"

# AWS Region
REGION = "us-west-2"

# Boto3 clients and resources
s3_client = boto3.client('s3', region_name='us-west-2')

# Environment variables
BUCKET_NAME=os.getenv("BUCKET_NAME", "publicpolitic")


class AmazonHandler(TranscriptResultStreamHandler):
    """Handles the stream of events coming back from AWS."""
    def __init__(self, transcript_result_stream):
        super().__init__(transcript_result_stream)
        self.full_transcript = []
        self.last_save = time.time()

    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        results = transcript_event.transcript.results
        for result in results:
            if not result.is_partial:
                if result.alternatives:
                    for alt in result.alternatives:
                        # Check for Speaker Label (if available)
                        # Note: In streaming, speaker labels sometimes arrive in different events
                        # or require parsing the 'items' list for high precision.
                        # For this MVP check, we print the text.
                        timestamp = time.strftime('%X')
                        print(f"[{timestamp}] {alt.transcript}")
                        
                        self.full_transcript.append({
                            "time": timestamp,
                            "text": alt.transcript
                        })

        # Save heartbeat (Simulate S3 upload)
        if time.time() - self.last_save > 10:
            self.save_local()

    def save_local(self):
        try:
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.full_transcript, f, indent=2)
            # print(f"   >>> Saved to {OUTPUT_FILE}")
            self.last_save = time.time()
        except Exception as e:
            print(f"Error saving file: {e}")


def get_stream_url(video_id, proxy=None):
    """Uses yt-dlp to get the raw stream URL."""
    print(f"🔗 Soldier: Extracting stream URL for {video_id}...")
    
    cmd = ["yt-dlp"]
    
    # ADDED: Proxy Logic
    if proxy:
        print(f"   ℹ️  Using Proxy: {proxy}")
        cmd.extend(["--proxy", proxy])
    else:
        print(f"   ℹ️  Using Local IP (No proxy configured)")

    # Standard flags
    cmd.extend([
        "-g", 
        f"https://www.youtube.com/watch?v={video_id}"
    ])

    try:
        # Check output and decode
        url = subprocess.check_output(cmd).decode('utf-8').strip()
        return url
    except subprocess.CalledProcessError as e:
        print("❌ Soldier: yt-dlp failed. Video might be unavailable or IP blocked.")
        return None
    
def whisper_transcription(stream_url, video_id: str, chunk_size: int = 5):
    # --- PHASE 2: LOAD AI MODEL ---
    print(f"🤖 Soldier: Loading Whisper Model ({MODEL_SIZE})...")
    # Run on CPU with INT8 quantization (fast, low memory)
    # If you have a Mac M1/M2/M3, this runs efficiently on CPU.
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

    print(f"🎧 Soldier: Starting Stream Processing...")
    
    # --- PHASE 3: START FFMPEG ---
    # We pipe audio from the URL -> FFMPEG -> STDOUT -> Python
    # -vn: No video
    # -ar 16000: 16kHz sample rate (Whisper standard)
    # -ac 1: Mono audio
    # -f f32le: Float 32 Little Endian (Required raw format for faster-whisper)
    process = subprocess.Popen(
        ["ffmpeg", "-i", stream_url, "-f", "f32le", "-ac", "1", "-ar", "16000", "-vn", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    # --- PHASE 4: TRANSCRIPTION LOOP ---
    # Whisper requires chunks of audio. We buffer 5 seconds.
    CHUNK_SECONDS = chunk_size
    SAMPLE_RATE = 16000
    BYTES_PER_SAMPLE = 4 # float32 = 4 bytes
    CHUNK_SIZE = int(CHUNK_SECONDS * SAMPLE_RATE * BYTES_PER_SAMPLE)

    full_transcript_json = []
    full_transcript_text = ""

    try:
        while True:
            # 1. Read Audio Chunk from FFMPEG
            raw_bytes = process.stdout.read(CHUNK_SIZE) #type: ignore
            
            # If stream ends (or FFMPEG crashes), break loop
            if not raw_bytes or len(raw_bytes) == 0:
                print("End of stream.")
                break

            # 2. Convert to Numpy for Whisper
            # We must explicitly tell numpy this is float32 data
            audio_chunk = np.frombuffer(raw_bytes, dtype=np.float32)

            # 3. Transcribe
            # beam_size=1 is faster; increase to 5 for accuracy
            segments, info = model.transcribe(audio_chunk, beam_size=1)

            # 4. Print & Accumulate
            for segment in segments:
                text = segment.text.strip()
                if text: 
                    timestamp = time.strftime('%X')
                    print(f"[{timestamp}] {text}")
                    
                    full_transcript_json.append({
                        "time": timestamp,
                        "text": text
                    })

                    full_transcript_text = full_transcript_text + "\n" + text

            # 5. Save to File (Simulate S3 Upload Heartbeat)
            # In production, you would upload to S3 here every 60 seconds
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(full_transcript_json, f, indent=2)

            with open('local_transcript.txt', 'w', encoding='utf-8') as f:
                f.write(full_transcript_text)

            s3_client.put_object(Bucket=BUCKET_NAME,
                                 Key=f"transcripts/{video_id}/transcripts.json",
                                 Body=json.dumps(full_transcript_json))
            
            s3_client.put_object(Bucket=BUCKET_NAME,
                                 Key=f'transcripts/{video_id}/transcripts.txt',
                                 Body=full_transcript_text)

    except KeyboardInterrupt:
        print("\n🛑 Soldier stopped by user.")
    finally:
        # Clean up the FFMPEG process so it doesn't hang in the background
        if process.poll() is None:
            process.terminate()
        print(f"📄 Final transcript saved to {OUTPUT_FILE}")

# --- NEW AMAZON FUNCTION ---
async def amazon_transcription(stream_url):
    print(f"☁️  Soldier: Connecting to Amazon Transcribe ({REGION})...")
    
    # 1. Setup Client
    client = TranscribeStreamingClient(region=REGION)

    # 2. Start FFMPEG
    # CRITICAL CHANGE: Amazon needs 's16le' (Signed 16-bit Little Endian), NOT 'f32le'
    process = subprocess.Popen(
        ["ffmpeg", "-i", stream_url, "-f", "s16le", "-ac", "1", "-ar", "16000", "-vn", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    # 3. Start AWS Stream
    try:
        stream = await client.start_stream_transcription(
            language_code="en-US",
            media_sample_rate_hz=16000,
            media_encoding="pcm",
            show_speaker_label=True  # Enable Diarization
        )
    except Exception as e:
        print(f"❌ AWS Connection Error: {e}")
        print("   (Check your AWS credentials, Region, or Account Status)")
        return

    handler = AmazonHandler(stream.output_stream)

    # 4. Define Audio Sender
    async def write_audio():
        print("🎧 Soldier: Streaming audio to AWS...")
        while True:
            # Read 8kb chunks (good size for network streaming)
            chunk = process.stdout.read(1024 * 8) #type: ignore
            if not chunk:
                break
            await stream.input_stream.send_audio_event(audio_chunk=chunk)
        
        await stream.input_stream.end_stream()
        print("--- Audio stream finished ---")

    # 5. Run Loop
    try:
        await asyncio.gather(write_audio(), handler.handle_events())
    except Exception as e:
        print(f"⚠️ Stream Error: {e}")
    finally:
        handler.save_local()
        if process.poll() is None:
            process.terminate()
        print(f"📄 Transcript saved to {OUTPUT_FILE}")

def run_soldier():
    # --- PHASE 1: GET STREAM ---
    scout_results = local_scout.lambda_handler({}, None)
    print(f"Found video:\nTitle: {scout_results['title']}\nVideo ID: {scout_results['video_id']}")
    # stream_url = get_stream_url(VIDEO_ID, PROXY_URL)
    stream_url = get_stream_url(scout_results['video_id'], PROXY_URL)
    if not stream_url:
        return

    # Transcription using Whisper. This is used to validate if pipeline works, i.e., URL -> FFMPEG -> STDOUT -> Python
    whisper_transcription(stream_url=stream_url, 
                          chunk_size=10, 
                          video_id=scout_results['video_id'])

    # Since Amazon SDK is async, we must run it inside an event loop
    # if os.name == 'nt':
    #     asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    # try:
    #     asyncio.run(amazon_transcription(stream_url))
    # except KeyboardInterrupt:
    #     print("\n🛑 Stopped by user.")

    
if __name__ == "__main__":
    run_soldier()