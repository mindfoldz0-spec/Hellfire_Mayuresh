import os
import math
import struct
import wave
import base64
import requests

def load_env(env_path=".env"):
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key.strip()] = val.strip()
    return env_vars

def generate_beep_wav(filename, freq=1000, duration_sec=0.5, sample_rate=22050, volume=0.7):
    """Generate a clean sine wave beep tone."""
    num_samples = int(sample_rate * duration_sec)
    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        
        # Add smooth attack and decay envelope to prevent audio click/pop
        fade_samples = int(sample_rate * 0.02)
        
        data = bytearray()
        for i in range(num_samples):
            # Calculate envelope
            env = 1.0
            if i < fade_samples:
                env = i / fade_samples
            elif i > num_samples - fade_samples:
                env = (num_samples - i) / fade_samples
                
            sample = math.sin(2 * math.pi * freq * (i / sample_rate)) * volume * env
            sample_int = int(sample * 32767)
            data.extend(struct.pack('<h', sample_int))
        wav_file.writeframes(data)

def combine_wav_files(speech_path, beep_path, output_path, pause_sec=0.2):
    """Combine speech audio with a silence pause and the beep tone."""
    with wave.open(speech_path, 'r') as speech_wav:
        channels = speech_wav.getnchannels()
        sampwidth = speech_wav.getsampwidth()
        framerate = speech_wav.getframerate()
        speech_frames = speech_wav.readframes(speech_wav.getnframes())

    with wave.open(beep_path, 'r') as beep_wav:
        beep_rate = beep_wav.getframerate()
        if beep_rate != framerate:
            generate_beep_wav(beep_path, freq=1000, duration_sec=0.5, sample_rate=framerate)
            with wave.open(beep_path, 'r') as re_beep:
                beep_frames = re_beep.readframes(re_beep.getnframes())
        else:
            beep_frames = beep_wav.readframes(beep_wav.getnframes())

    # Create silence between speech and beep
    silence_frames_count = int(framerate * pause_sec)
    silence = b'\x00' * (silence_frames_count * channels * sampwidth)

    with wave.open(output_path, 'w') as out_wav:
        out_wav.setnchannels(channels)
        out_wav.setsampwidth(sampwidth)
        out_wav.setframerate(framerate)
        out_wav.writeframes(speech_frames)
        out_wav.writeframes(silence)
        out_wav.writeframes(beep_frames)

def generate_ivr_audio():
    os.makedirs("frontend/static", exist_ok=True)
    os.makedirs("temp", exist_ok=True)
    
    env = load_env()
    api_key = env.get("sarvam_api") or os.environ.get("sarvam_api")
    
    output_final = "frontend/static/ivr_prompt.wav"
    speech_temp = "temp/speech.wav"
    beep_temp = "temp/beep.wav"
    
    text = (
        "Welcome to the Cyber Emergency Response System. "
        "Please describe your situation clearly after the tone. "
        "We are connecting you to an officer."
    )
    
    print(f"Calling Sarvam AI TTS API with key: {api_key[:6]}...{api_key[-4:] if api_key else 'None'}")
    
    success = False
    if api_key:
        try:
            url = "https://api.sarvam.ai/text-to-speech"
            headers = {
                "api-subscription-key": api_key,
                "Content-Type": "application/json"
            }
            payload = {
                "inputs": [text],
                "target_language_code": "en-IN",
                "speaker": "priya",
                "pitch": 0,
                "pace": 1.0,
                "loudness": 1.5,
                "speech_sample_rate": 22050,
                "enable_preprocessing": True,
                "model": "bulbul:v3"
            }
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                res_data = response.json()
                audios = res_data.get("audios", [])
                if audios:
                    audio_b64 = audios[0]
                    audio_bytes = base64.b64decode(audio_b64)
                    with open(speech_temp, "wb") as f:
                        f.write(audio_bytes)
                    print("Sarvam TTS audio fetched successfully!")
                    success = True
                else:
                    print("Sarvam API returned 200 but no 'audios' in response:", res_data)
            else:
                print(f"Sarvam API error: HTTP {response.status_code} - {response.text}")
        except Exception as e:
            print("Failed to call Sarvam TTS:", e)

    sample_rate = 22050
    generate_beep_wav(beep_temp, freq=1000, duration_sec=0.5, sample_rate=sample_rate)

    if success:
        combine_wav_files(speech_temp, beep_temp, output_final)
        print(f"Combined IVR + Beep audio saved to {output_final}")
    else:
        print("Falling back to standard emergency prompt tone generator...")
        with wave.open(output_final, 'w') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            data = bytearray()
            
            # Alert chime: 800Hz for 0.25s then 1200Hz for 0.35s
            for freq, dur in [(800, 0.25), (1200, 0.35)]:
                ns = int(sample_rate * dur)
                for i in range(ns):
                    env = (1 - (i / ns)) * 0.6
                    s = int(math.sin(2 * math.pi * freq * (i / sample_rate)) * 32767 * env)
                    data.extend(struct.pack('<h', s))
            
            # Silence
            data.extend(b'\x00' * int(sample_rate * 0.4 * 2))
            
            # Beep tone 1000Hz for 0.5s
            ns = int(sample_rate * 0.5)
            for i in range(ns):
                s = int(math.sin(2 * math.pi * 1000 * (i / sample_rate)) * 32767 * 0.8)
                data.extend(struct.pack('<h', s))
                
            wav_file.writeframes(data)
        print(f"Emergency prompt saved to {output_final}")

if __name__ == "__main__":
    generate_ivr_audio()
