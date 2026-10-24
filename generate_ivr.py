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


def synthesize_speech(text: str, output_path: Path, api_key: str, sample_rate=SAMPLE_RATE) -> bool:
    """Call Sarvam AI TTS to generate audio at requested sample rate."""
    if not api_key:
        return False
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
                "pace": 0.95,
                "loudness": 1.5,
                "speech_sample_rate": sample_rate,
                "enable_preprocessing": True,
                "model": "bulbul:v3",
            },
            timeout=30,
        )
        if response.status_code == 200:
            audios = response.json().get("audios", [])
            if audios:
                output_path.write_bytes(base64.b64decode(audios[0]))
                return True
        print(f"Sarvam API status {response.status_code}: {response.text}")
    except Exception as exc:
        print(f"Sarvam TTS exception: {exc}")
    return False


def generate_hold_music(output_path: Path, sample_rate=SAMPLE_RATE, duration_sec=12.0):
    """Generate a gentle, calming harmonic hold music loop (Cmaj7 -> Fmaj7 -> Am9)."""
    num_samples = int(sample_rate * duration_sec)
    # Define chord progression notes in Hz
    # Cmaj7 (C4=261.63, E4=329.63, G4=392.00, B4=493.88)
    # Fmaj7 (F3=174.61, A3=220.00, C4=261.63, E4=329.63)
    # Am7 (A3=220.00, C4=261.63, E4=329.63, G4=392.00)
    # Gsus4 (G3=196.00, C4=261.63, D4=293.66, G4=392.00)
    chords = [
        [261.63, 329.63, 392.00, 493.88],  # 0-3s
        [174.61, 220.00, 261.63, 329.63],  # 3-6s
        [220.00, 261.63, 329.63, 392.00],  # 6-9s
        [196.00, 261.63, 293.66, 392.00],  # 9-12s
    ]
    chord_duration = duration_sec / len(chords)

    with wave.open(str(output_path), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        data = bytearray()
        for idx in range(num_samples):
            t = idx / sample_rate
            chord_idx = min(int(t / chord_duration), len(chords) - 1)
            active_chord = chords[chord_idx]

            # Envelope within the chord
            local_t = t % chord_duration
            attack = 0.4
            release = 0.4
            if local_t < attack:
                env = local_t / attack
            elif local_t > chord_duration - release:
                env = (chord_duration - local_t) / release
            else:
                env = 1.0

            # Subtle chorus / vibrato (4Hz)
            vibrato = 1.0 + 0.008 * math.sin(2 * math.pi * 4.0 * t)

            sample_val = 0.0
            for note_idx, freq in enumerate(active_chord):
                weight = 0.35 if note_idx == 0 else 0.22
                sample_val += weight * math.sin(2 * math.pi * freq * vibrato * t)

            # Master volume
            sample_val = sample_val * env * 0.45
            sample_int = max(-32767, min(32767, int(sample_val * 32767)))
            data.extend(struct.pack("<h", sample_int))

        wav_file.writeframes(data)


def generate_all_ivr_prompts():
    static_dir = BASE_DIR / "frontend" / "static"
    temp_dir = BASE_DIR / "temp"
    static_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    ivr_final = static_dir / "ivr_prompt.wav"
    recorded_final = static_dir / "recorded_prompt.wav"
    hold_music_final = static_dir / "hold_music.wav"

    ring_temp = temp_dir / "ring.wav"
    speech_temp = temp_dir / "speech.wav"
    recorded_temp = temp_dir / "recorded_speech.wav"
    beep_temp = temp_dir / "beep.wav"

    api_key = os.getenv("sarvam_api") or os.getenv("SARVAM_API")

    print("1. Generating telephone ringback tones...")
    generate_ringback_wav(
        ring_temp,
        freq1=440,
        freq2=480,
        ring_duration=0.8,
        pause_duration=0.5,
        count=2,
        sample_rate=SAMPLE_RATE,
    )

    print("2. Generating 1000Hz synchronization beep...")
    generate_beep_wav(beep_temp, freq=1000, duration_sec=0.5, sample_rate=SAMPLE_RATE)

    # 1. Main Greeting
    greeting_text = (
        "Welcome to Mayuresh Cybercrime Bureau. "
        "This is the automated bot. The officer will connect shortly. "
        "Please speak your message after the beep."
    )
    print(f'3. Synthesizing bureau greeting: "{greeting_text}"...')
    tts_success = synthesize_speech(greeting_text, speech_temp, api_key)
    if tts_success:
        combine_ivr_sequence(ring_temp, speech_temp, beep_temp, ivr_final)
        print(f" -> Successfully saved: {ivr_final}")
    else:
        print("TTS failed or no key; generating fallback ring + chime.")
        combine_ivr_sequence(ring_temp, ring_temp, beep_temp, ivr_final)

    # 2. Confirmation prompt (when speech finishes / 5s silence)
    confirmation_text = "Your message has been recorded. The officer is connecting."
    print(f'4. Synthesizing confirmation prompt: "{confirmation_text}"...')
    rec_success = synthesize_speech(confirmation_text, recorded_final, api_key)
    if rec_success:
        print(f" -> Successfully saved: {recorded_final}")
    else:
        # Fallback gentle chime if TTS unavailable
        generate_beep_wav(recorded_final, freq=587, duration_sec=1.5, sample_rate=SAMPLE_RATE, volume=0.5)
        print(f" -> Generated fallback chime: {recorded_final}")

    # 3. Looping Hold Music
    print("5. Generating soothing harmonic hold music loop (12 seconds)...")
    generate_hold_music(hold_music_final, sample_rate=SAMPLE_RATE, duration_sec=12.0)
    print(f" -> Successfully saved hold music: {hold_music_final}")
    print("\nAll IVR prompts and hold music generated successfully!")


if __name__ == "__main__":
    generate_all_ivr_prompts()
