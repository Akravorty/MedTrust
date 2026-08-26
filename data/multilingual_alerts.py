import os
from pathlib import Path
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs import save

# Load environment variables from .env file
load_dotenv()

# Initialize ElevenLabs client
api_key = os.getenv("ELEVENLABS_API_KEY")
client = ElevenLabs(api_key=api_key) if api_key else None

# Ensure the audio output directory exists under data/audio/
AUDIO_DIR = Path(__file__).parent / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Multilingual message templates for supply chain statuses (HOLD and REJECT)
TRANSLATIONS = {
    "HI": {  # Hindi
        "HOLD": "चेतावनी: बैच को रोक दिया गया है। कृपया आगे की समीक्षा की प्रतीक्षा करें।",
        "REJECT": "गंभीर सूचना: सुरक्षा कारणों से बैच को अस्वीकार कर दिया गया है।",
    },
    "OR": {  # Odia
        "HOLD": "ଚେତାବନୀ: ବ୍ୟାଚ୍‌ଟିକୁ ହୋଲ୍‌ କରାଯାଇଛି। ଦୟାକରି ପରବର୍ତ୍ତୀ ସମୀକ୍ଷା ପାଇଁ ପ୍ରତୀକ୍ଷା କରନ୍ତୁ।",
        "REJECT": "ସତର୍କ ସୂଚନା: ସୁରକ୍ଷା କାରଣରୁ ବ୍ୟାଚ୍‌ଟିକୁ ପ୍ରତ୍ୟାଖ୍ୟାନ କରାଯାଇଛି।"
    }
}


def speak_alert(status: str, language: str = "HI", voice_id: str = "EXAVITQu4vr4xnSDxMaL", output_filename: str = None):
    """
    Generates a multilingual voice alert for MedTrust supply chain batches using ElevenLabs.
    
    Args:
        status (str): "HOLD" or "REJECT"
        language (str): "HI" for Hindi or "OR" for Odia
        voice_id (str): ElevenLabs voice ID
        output_filename (str): Optional custom filename to save the MP3
    """
    if not client:
        raise ValueError("ELEVENLABS_API_KEY is missing from environment variables.")

    text = TRANSLATIONS.get(language, {}).get(status)
    if not text:
        raise ValueError(f"Invalid status '{status}' or language '{language}' specified.")

    print(f"Generating audio for [{language}] - Status: {status}...")

    # Generate speech using the multilingual model
    audio = client.generate(
        text=text,
        voice=voice_id,
        model="eleven_multilingual_v2"
    )

    # Determine file path inside data/audio/
    if not output_filename:
        output_filename = f"alert_{language.lower()}_{status.lower()}.mp3"
    
    file_path = AUDIO_DIR / output_filename
    
    # Save the generated audio file
    save(audio, str(file_path))
    print(f"Audio successfully saved to: {file_path}")
    return file_path


if __name__ == "__main__":
    # Test generation for Hindi and Odia alerts
    try:
        speak_alert("HOLD", language="HI")
        speak_alert("REJECT", language="HI")
        speak_alert("HOLD", language="OR")
        speak_alert("REJECT", language="OR")
    except Exception as e:
        print(f"Error generating alerts: {e}")