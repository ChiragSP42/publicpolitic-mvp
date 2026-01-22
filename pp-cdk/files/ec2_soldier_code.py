import asyncio
import subprocess
import boto3
import json
import time
import os
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent

# CONFIG
BUCKET_NAME = os.environ['BUCKET_NAME']
REGION = os.environ['AWS_DEFAULT_REGION']

s3 = boto3.client('s3', region_name=REGION)
ssm = boto3.client('ssm', region_name=REGION)

class MeetingTranscriber(TranscriptResultStreamHandler):
    def __init__(self, transcript_result_stream, video_id):
        super().__init__(transcript_result_stream)
        self.full_transcript = []
        self.video_id = video_id
        self.last_upload = time.time()

    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        results = transcript_event.transcript.results
        for result in results:
            if not result.is_partial:
                for alt in result.alternatives:
                    entry = {"time": time.time(), "text": alt.transcript}
                    print(f"[TRANSCRIPT] {alt.transcript}")
                    self.full_transcript.append(entry)

        # Upload every 60s
        if time.time() - self.last_upload > 60:
            self.upload_s3()

    def upload_s3(self):
        key = f"transcripts/{self.video_id}/full.json"
        s3.put_object(
            Bucket=BUCKET_NAME, 
            Key=key,
            Body=json.dumps(self.full_transcript, indent=2),
            ContentType='application/json'
        )
        print(f"[S3] Uploaded to s3://{BUCKET_NAME}/{key}")
        self.last_upload = time.time()

async def run_transcriber():
    # 1. Read the Video ID from Parameter Store
    try:
        video_id = ssm.get_parameter(Name='/meeting/current_video_id')['Parameter']['Value']
        print(f"Video ID from Lambda: {video_id}")
    except:
        print("ERROR: No video_id found in Parameter Store. Exiting.")
        return

    # 2. Get Stream URL
    try:
        stream_url = subprocess.check_output(
            ["yt-dlp", "-g", f"https://www.youtube.com/watch?v={video_id}"]
        ).decode('utf-8').strip()
    except:
        print("ERROR: Could not extract stream URL. Video might have ended.")
        return

    # 3. Start FFMPEG
    process = subprocess.Popen(
        ["ffmpeg", "-i", stream_url, "-f", "s16le", "-ac", "1", "-ar", "16000", "-vn", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )

    # 4. Connect to AWS Transcribe
    client = TranscribeStreamingClient(region=REGION)
    stream = await client.start_stream_transcription(
        language_code="en-US", 
        media_sample_rate_hz=16000, 
        media_encoding="pcm"
    )

    handler = MeetingTranscriber(stream.output_stream, video_id)

    async def write_chunks():
        while True:
            chunk = process.stdout.read(1024 * 8)
            if not chunk: break
            await stream.input_stream.send_audio_event(audio_chunk=chunk)
        await stream.input_stream.end_stream()

    # 5. Run transcription
    try:
        await asyncio.gather(write_chunks(), handler.handle_events())
        # Final upload when stream ends
        handler.upload_s3()
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(run_transcriber())
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
    # finally:
    #     print("Shutting down instance...")
    #     os.system("sudo shutdown -h now")
