import base64
import math
import os
import struct
import wave
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SAMPLE_RATE = 22050


def generate_ringback_wav(
    filename,
    freq1=440,
    freq2=480,
    ring_duration=0.8,
    pause_duration=0.5,
    count=2,
    sample_rate=SAMPLE_RATE,
    volume=0.5,
):
    """Generate authentic dual-tone telephone ringing sound (440Hz + 480Hz)."""
    with wave.open(str(filename), "w") as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)

        data = bytearray()
        for burst in range(count):
            num_samples = int(sample_rate * ring_duration)
            fade_samples = int(sample_rate * 0.03)
            for index in range(num_samples):
                envelope = 1.0
                if index < fade_samples:
                    envelope = index / fade_samples
                elif index > num_samples - fade_samples:
                    envelope = (num_samples - index) / fade_samples

                t = index / sample_rate
                sample = (
                    (math.sin(2 * math.pi * freq1 * t) + math.sin(2 * math.pi * freq2 * t))
                    * 0.5
                    * volume
                    * envelope
                )
                data.extend(struct.pack("<h", int(sample * 32767)))

            # Longer pause on the last burst before voice starts.
            actual_pause = pause_duration if burst < count - 1 else 0.6
            data.extend(b"\x00" * (int(sample_rate * actual_pause) * 2))

        wav_file.writeframes(data)


def generate_beep_wav(filename, freq=1000, duration_sec=0.5, sample_rate=SAMPLE_RATE, volume=0.8):
    """Generate a clean 1000Hz sine wave with a smooth fade envelope."""
    num_samples = int(sample_rate * duration_sec)
    with wave.open(str(filename), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        fade_samples = int(sample_rate * 0.02)
        data = bytearray()
        for index in range(num_samples):
            envelope = 1.0
            if index < fade_samples:
                envelope = index / fade_samples
            elif index > num_samples - fade_samples:
                envelope = (num_samples - index) / fade_samples

            sample = math.sin(2 * math.pi * freq * (index / sample_rate)) * volume * envelope
            data.extend(struct.pack("<h", int(sample * 32767)))
        wav_file.writeframes(data)


def _wav_format(path):
    with wave.open(str(path), "r") as wav_file:
        return (
            wav_file.getnchannels(),
            wav_file.getsampwidth(),
            wav_file.getframerate(),
        )


def combine_ivr_sequence(ring_path, speech_path, beep_path, output_path, pause_before_beep=0.3):
    """Combine telephone ring -> voice greeting -> pause -> beep tone."""
    ring_format = _wav_format(ring_path)
    speech_format = _wav_format(speech_path)
    beep_format = _wav_format(beep_path)

    if speech_format != ring_format:
        raise ValueError(
            "TTS WAV format does not match the ring/beep format: "
            f"speech={speech_format}, ring={ring_format}. "
            "Regenerate the TTS audio at the requested sample rate/channels."
        )
    if beep_format != ring_format:
        raise ValueError(
            f"Beep WAV format does not match the ring format: beep={beep_format}, ring={ring_format}."
        )

    with wave.open(str(ring_path), "r") as ring_wav:
        channels = ring_wav.getnchannels()
        sample_width = ring_wav.getsampwidth()
        frame_rate = ring_wav.getframerate()
        ring_frames = ring_wav.readframes(ring_wav.getnframes())

    with wave.open(str(speech_path), "r") as speech_wav:
        speech_frames = speech_wav.readframes(speech_wav.getnframes())

    with wave.open(str(beep_path), "r") as beep_wav:
        beep_frames = beep_wav.readframes(beep_wav.getnframes())

    silence_frames = int(frame_rate * pause_before_beep)
    silence = b"\x00" * (silence_frames * channels * sample_width)

    with wave.open(str(output_path), "w") as out_wav:
        out_wav.setnchannels(channels)
        out_wav.setsampwidth(sample_width)
        out_wav.setframerate(frame_rate)
        out_wav.writeframes(ring_frames)
        out_wav.writeframes(speech_frames)
        out_wav.writeframes(silence)
        out_wav.writeframes(beep_frames)


def generate_ivr_audio():
    output_final = BASE_DIR / "frontend" / "static" / "ivr_prompt.wav"
    temp_dir = BASE_DIR / "temp"
    ring_temp = temp_dir / "ring.wav"
    speech_temp = temp_dir / "speech.wav"
    beep_temp = temp_dir / "beep.wav"

    output_final.parent.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("sarvam_api") or os.getenv("SARVAM_API")

    print("Generating telephone ring tone...")
    generate_ringback_wav(
        ring_temp,
        freq1=440,
        freq2=480,
        ring_duration=0.8,
        pause_duration=0.5,
        count=2,
        sample_rate=SAMPLE_RATE,
    )

    text = (
        "Welcome to the Cyber Emergency Response System. "
        "You will be directed to an officer shortly. "
        "Please describe your situation clearly after the beep."
    )

    print(f'Calling Sarvam AI TTS API with text: "{text}"')

    success = False
    if api_key:
        try:
            response = requests.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={
                    "api-subscription-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "inputs": [text],
                    "target_language_code": "en-IN",
                    "speaker": "priya",
                    "pitch": 0,
                    "pace": 1.0,
                    "loudness": 1.5,
                    "speech_sample_rate": SAMPLE_RATE,
                    "enable_preprocessing": True,
                    "model": "bulbul:v3",
                },
                timeout=30,
            )
            if response.status_code == 200:
                audios = response.json().get("audios", [])
                if audios:
                    speech_temp.write_bytes(base64.b64decode(audios[0]))
                    print("Sarvam TTS speech audio synthesized successfully!")
                    success = True
                else:
                    print("Sarvam API returned 200 but no 'audios' in response.")
            else:
                print(f"Sarvam API error: HTTP {response.status_code} - {response.text}")
        except Exception as exc:
            print("Failed to call Sarvam TTS:", exc)

    print("Generating 1000Hz sync beep...")
    generate_beep_wav(beep_temp, freq=1000, duration_sec=0.5, sample_rate=SAMPLE_RATE)

    if success:
        try:
            combine_ivr_sequence(ring_temp, speech_temp, beep_temp, output_final)
            print(f"IVR sequence (Ring -> Speech -> Beep) saved to {output_final}")
            return
        except ValueError as exc:
            print(f"Could not combine TTS audio: {exc}")
            print("Falling back to ring-only prompt.")

    print("Warning: TTS unavailable; combining fallback chime with ring and beep...")
    combine_ivr_sequence(ring_temp, ring_temp, beep_temp, output_final)
    print(f"Fallback IVR sequence saved to {output_final}")


if __name__ == "__main__":
    generate_ivr_audio()
