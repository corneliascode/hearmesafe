from datetime import datetime, timedelta, timezone
import jwt
import os
import re
import secrets
import redis
import tempfile
import numpy as np
from typing import Optional, Annotated, Dict, Any
from passlib.context import CryptContext
from fastapi import UploadFile
from google.cloud import speech
import google.generativeai as genai
import asyncio
from pydantic import BaseModel, Field
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email
from python_http_client.exceptions import HTTPError
from pedalboard import Pedalboard, NoiseGate, Compressor, LowShelfFilter, Gain, HighShelfFilter, Limiter
from pedalboard.io import AudioFile

SECRET_KEY = os.environ.get('JWT_SECRET_KEY')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7

sendgrid_api_key = os.environ.get("SENDGRID_API_KEY")
sg = SendGridAPIClient(sendgrid_api_key)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
redis_host = os.environ.get("REDISHOST", "localhost")
redis_port = int(os.environ.get("REDISPORT", 6379))
redis_client = redis.StrictRedis(host=redis_host, port=redis_port)

class AuthManager:
    def __init__(self):
        self.refresh_tokens = {}  # user_id: refresh_token mapping

    def generate_tokens(self, user_id: str) -> dict:
        """Generate both access and refresh tokens"""
        access_token_expires = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token_payload = {
            "user_id": user_id,
            "exp": access_token_expires.timestamp(),
            "iat": datetime.now(timezone.utc).timestamp(),
            "type": "access"
        }
        access_token = jwt.encode(access_token_payload, SECRET_KEY, algorithm=ALGORITHM)
        if isinstance(access_token, bytes):
            access_token = access_token.decode('utf-8')

        return {
            "access_token": access_token,
        }

    async def authenticate(self, token: str) -> Optional[str]:
        """Verify the JWT token and return user_id if valid"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get('type') != 'access':
                return None
            return payload['user_id']

        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return None

    async def refresh_access_token(self, refresh_token: str, user_id: str) -> Optional[dict]:
        """Validate refresh token and generate new access token"""
        stored_refresh = self.refresh_tokens.get(user_id)
        if not stored_refresh or stored_refresh["token"] != refresh_token:
            return None

        if datetime.now(timezone.utc) > stored_refresh["expires"]:
            del self.refresh_tokens[user_id]
            return None

        return self.generate_tokens(user_id)
    
def get_password_hash(password):
    return pwd_context.hash(password)

def save_user(username: str, password: str, user_id: str):
    user_key = f"user:{username}"
    isThreat = b'False'
    # Hash the password
    hashed_password = get_password_hash(password)
    
    redis_client.hset(user_key, "hashed_password", hashed_password)
    # Save the username and hashed password as a Redis hash
    redis_client.hset(user_key, "user_id", user_id)
    redis_client.hset(user_key, "isThreat", isThreat)

    print(f"User '{username}' saved.")

def update_user_settings(user_id: str, settings: dict):
    username = username_from_user_id(user_id)
    user_key = f"user:{username}"
    for key, value in settings.items():
        redis_client.hset(user_key, key, value)
    print(f"Settings updated for user")
    return None

def get_user_settings(user_id: str) -> dict:
    username = username_from_user_id(user_id)
    user_key = f"user:{username}"
    personal_email = redis_client.hget(user_key, "personal_email")
    friend_email = redis_client.hget(user_key, "friend_email")
    safe_word = redis_client.hget(user_key, "safe_word")
    settings = {
        "personal_email": personal_email.decode('utf-8') if personal_email else None,
        "friend_email": friend_email.decode('utf-8') if friend_email else None,
        "safe_word": safe_word.decode('utf-8') if safe_word else None
    }
    print(f"Settings retrieved for user")
    return settings

def create_gmaps_link(gps_dict: dict) -> str:
    lat = gps_dict.get("lat")
    long = gps_dict.get("long")
    if lat and long:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{long}"
    return None

def change_threat_status(user_id: str, status: bool):
    """
    Change the threat status for a user.
    
    Args:
        username (str): The username
        status (bool): The new threat status
    """
    username = username_from_user_id(user_id)
    user_key = f"user:{username}"
    # Convert bool to string 'True' or 'False' and encode to bytes
    status_bytes = str(status).encode('utf-8')
    redis_client.hset(user_key, "isThreat", status_bytes)

    print(f"User status changed.")

def check_threat_status(user_id: str) -> bool:
    username = username_from_user_id(user_id)
    user_key = f"user:{username}"
    status = redis_client.hget(user_key, "isThreat")


    if status == b'True':
        return True
    else:
        return False


def check_user(username: str, password: str) -> bool:
    user_key = f"user:{username}"
    # Retrieve the hashed password from Redis
    hashed_password = redis_client.hget(user_key, "hashed_password")
    if not hashed_password:
        print("User not found.")
        return False
    # Verify the provided password with the stored hashed password
    return pwd_context.verify(password, hashed_password)

def user_id_from_username(username: str) -> str:
    user_key = f"user:{username}"
    user_id = redis_client.hget(user_key, "user_id")
    return user_id.decode('utf-8') if user_id else None

def username_from_user_id(user_id: str) -> str:
    for key in redis_client.scan_iter(match="user:*"):
        # For each user key, check if the user_id matches
        stored_user_id = redis_client.hget(key, "user_id")
        
        if stored_user_id and stored_user_id.decode('utf-8') == user_id:
            # Extract username from the key (remove "user:" prefix)
            username = key.decode('utf-8').split(':')[1]
            return username
        
    return None

def reset_memorystore():
    redis_client.flushall()
    print("Memory store flushed.")
    return None

def check_temp_and_upload_folders():
    temp_base_dir = tempfile.gettempdir()  # /tmp directory on App Engine
    upload_dir = os.path.join(temp_base_dir, "upload")
    if not os.path.exists(temp_base_dir):
        tempfile.mkdtemp(dir='/tmp')
        print(temp_base_dir)
    if not os.path.exists(upload_dir):
        os.makedirs(upload_dir)
        print(upload_dir)
    return upload_dir

async def save_file(file: UploadFile, file_name: str, upload_dir: str):
    file_path = os.path.join(upload_dir, file_name)
    print(f"Saving file to {file_path}")
    file_bytes = await file.read()
    with open(file_path, "wb") as f:
        f.write(file_bytes)
    return file_path


# Only for WAV
def transcribe_audio(audio_path) -> speech.RecognizeResponse:
    # Instantiates a client
    client = speech.SpeechClient()
    # Read audio file content
    with open(audio_path, "rb") as audio_file:
        content = audio_file.read()
    audio = speech.RecognitionAudio(content=content)
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000, ###### Checkthe samplerate from the app TODO:
        language_code="en-US",
    )
    # Detects speech in the audio file
    response = client.recognize(config=config, audio=audio)
    try:
        return {'text': response.results[0].alternatives[0].transcript,
                              'confidence': response.results[0].alternatives[0].confidence}
    except Exception as e:
        return {'text': "No speech detected", 'confidence': 0.0} 


def delete_file(file_path: str):
    os.remove(file_path)
    print(f"File {file_path} deleted.")


def process_labels(labels:str) -> list[dict]:
    """
    Process the labels from the model response.

    Args:
        labels (str): The labels string from the model response.

    Returns:
        dict: The processed labels as a dictionary.
    """    
    # Regular expression to match category names and scores
    pattern = r'<Category "(.*?)" \(displayName= score=(\d+\.\d+)'
    # Find all matches
    matches = re.findall(pattern, labels)
    # Convert matches to a list of dictionaries
    return [{"category": match[0], "score": float(match[1])} for match in matches]


genai.configure(api_key=os.environ['GEMINI_API_KEY'])
genai.GenerationConfig(
  temperature=0
)
model = genai.GenerativeModel('gemini-1.5-flash')


# detect threat function
def detect_threat(message:str, labels: str, user_id: str) -> dict:
    user_settings = get_user_settings(user_id)
    safe_word = user_settings.get("safe_word")
    messages=[
                    {
                        "role": "model",
                        "parts": """
                        Analyze the following situation for potential threats. Take into consideration all types of potential threats.
                        Even if the threat is unknown or there's no explicit threat, if a subject's response sugests a threat or an insistent request for help - it is.
                        Return a JSON response with:
                        - threat_level (0 for no threat or 1 for serious threat)
                        - explanation (short reason for the assessment. Do not use harmful content in the explanation). 
                        Reply only with the threat level and explanation. 
                        Use only the labels resulted from the analysis of the audio that the user sent and the recognized message from the audio.
                        Use the labels only to add more information to the one resulted from the message. 
                        If the safe_words are present in the message, the threat level should be 1.
                        It should be the exact combination of safe_words, not only part of it.
                        Do not mention anything about the safe words in the message. Only explain that there is a threat.
                        Do not consider a threat if only the labels are threatening.
                        There will be no additional information. Send threat level 1 only if you are sure that it is an implicit or explicit threat.
                        The response structure should be {"threat_level":"", "explanation":""} as a plain text.
                        """
                    },
                    {
                        "role": "user",
                        "parts": f"safe_words:{safe_word}, labels: {labels}\n\nmessage: {message}"
                    }
                ]
    
    safe = [
        {
            "category": "HARM_CATEGORY_HARASSMENT",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_NONE",
        },
    ]
    try:
        response = model.generate_content(messages, safety_settings=safe)
        dict_response = eval(response.text)
    except Exception as e:
        print(f'Error: {e}')
        return {"threat_level": 0, "explanation": "Error in threat detection"}
   
    return {"threat_level": dict_response.get("threat_level"), "explanation": dict_response.get("explanation")}

def generate_notif_message_from_explanation(explanation: str):
    message = [
        {
            "role": "model",
            "parts": """
            Create a threat alert in an informal way.
            The idea is that the recipient is alerted about a threat to the sender. 
            Structure it as a short and concise alert email from first person.
            The main idea is to alert the recipient about a potential threat to the sender and to ask him/her to come and take him/her from there.
            DO NOT use requests to call back the sender, or to alert authorities.
            Only the email text body is needed.
            Do not expect for any variables or additional information. Return only the response in plain text.
            Use the following text and generate the alert based on it:
            """
        },
        {
            "role": "user",
            "parts": explanation
        }
    ]

    safe = [
        {
            "category": "HARM_CATEGORY_HARASSMENT",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_NONE",
        },
    ]
    response = model.generate_content(message, safety_settings=safe)
    return response.text

def add_location_to_notification(notification: str, gps: str) -> str:
    gps_dict = {"lat": gps.split(';')[0].split(':')[1].strip(), 
                        "long": gps.split(';')[1].split(':')[1].strip()}
    gmaps_link = create_gmaps_link(gps_dict)
    return f"{notification}\n\nLocation (click to open google maps location): \n{gmaps_link}" if gmaps_link else notification
    
def send_email_alert(user_id: str, alert_message: str) -> dict:
    """
    Creates formatted alert message and sending 
    """
    user_settings = get_user_settings(user_id)

    message = Mail(
        to_emails=user_settings.get("friend_email"),
        from_email=Email(os.environ.get("SENDGRID_SENDER")),
        subject="ALERT: Potential Threat For your Friend Detected",
        plain_text_content=alert_message
        )
    message.add_bcc(user_settings.get("personal_email"))
    try:
        response = sg.send(message)
        print('Email sent')
        return f"email.status_code={response.status_code}"
        #expected 202 Accepted

    except HTTPError as e:
        print(str(e))
        return str(e)