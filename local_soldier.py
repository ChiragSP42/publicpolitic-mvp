import os
import time
import json
import subprocess
import numpy as np
from faster_whisper import WhisperModel

# --- CONFIGURATION ---
# 1. Video ID (Hardcoded as requested)
VIDEO_ID = "QGkzerxK15w"  # Replace with your target live video ID

# 2. Proxy Configuration (For bypassing YouTube IP blocks on AWS)
#    Leave empty to use your Local IP (Home/Office network).
#    Format: "http://user:pass@host:port"
PROXY_URL = "" 

# 3. Model Size: 'tiny', 'base', 'small', 'medium', 'large-v2'
MODEL_SIZE = "tiny" 

# 4. Output File
OUTPUT_FILE = "local_transcript.json"


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

def run_soldier():
    # --- PHASE 1: GET STREAM ---
    stream_url = get_stream_url(VIDEO_ID, PROXY_URL)
    if not stream_url:
        return

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
    CHUNK_SECONDS = 5
    SAMPLE_RATE = 16000
    BYTES_PER_SAMPLE = 4 # float32 = 4 bytes
    CHUNK_SIZE = int(CHUNK_SECONDS * SAMPLE_RATE * BYTES_PER_SAMPLE)

    full_transcript = []

    try:
        while True:
            # 1. Read Audio Chunk from FFMPEG
            raw_bytes = process.stdout.read(CHUNK_SIZE)
            
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
                    
                    full_transcript.append({
                        "time": timestamp,
                        "text": text
                    })

            # 5. Save to File (Simulate S3 Upload Heartbeat)
            # In production, you would upload to S3 here every 60 seconds
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(full_transcript, f, indent=2)

    except KeyboardInterrupt:
        print("\n🛑 Soldier stopped by user.")
    finally:
        # Clean up the FFMPEG process so it doesn't hang in the background
        if process.poll() is None:
            process.terminate()
        print(f"📄 Final transcript saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    run_soldier()