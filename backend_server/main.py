import jwt
from datetime import datetime, timedelta, timezone
import asyncio
import os
import json
import secrets
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
import uvicorn
import requests
import time


from utils import (AuthManager, 
                   save_user, check_user, check_temp_and_upload_folders, 
                   save_file, delete_file, reset_memorystore,
                   transcribe_audio, change_threat_status, check_threat_status,
                   user_id_from_username, detect_threat, send_email_alert,
                   generate_notif_message_from_explanation, update_user_settings,
                   get_user_settings, add_location_to_notification)

app = FastAPI()

SECRET_KEY = os.environ.get('JWT_SECRET_KEY')
ALGORITHM = "HS256"

# Dictionary to store active WebSocket connections by user ID
active_connections: Dict[str, WebSocket] = {}


auth_manager = AuthManager()

async def process_message(message: str, gps: str, labels: str, user_id: str):
    threat_response = detect_threat(message, labels, user_id)
    if threat_response.get('threat_level') == '1':
        print(f"Threat detected for user ")
        change_threat_status(user_id, True)
        await send_message_to_user(user_id, "Threat detected. Please confirm if you are in danger.")
        print("Waiting for user confirmation...")
        await asyncio.sleep(5)
        if check_threat_status(user_id):
            print("Threat confirmed. Sending help.")
            notif_message = generate_notif_message_from_explanation(threat_response.get('explanation'))
            alert_message = add_location_to_notification(notif_message, gps)
            send_email_alert(user_id, alert_message)
        else:
            print("Threat not confirmed. Cancelling.")
    


async def send_message_to_user(user_id: str, message: str):
    websocket = active_connections.get(user_id)
    if websocket:
        try:
            await websocket.send_text(message)
            print('Message sent to user')
        except Exception as e:
            print(f"Error sending message: {e}")
    else:
        print(f"No active WebSocket connection for user {user_id}")

@app.route("/settings", methods=["POST"])
async def settings(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return JSONResponse(content={"error": "Missing authentication data"}, status_code=401)
    
    # Split the "Bearer <token>" format to get the token part
    try:
        token_type, token = auth_header.split(" ")
        if token_type.lower() != "bearer":
            raise ValueError("Incorrect token type")
    except ValueError:
        return JSONResponse(content={"error": "Invalid authorization header format"}, status_code=401)
    
    user_id = await auth_manager.authenticate(token)
    if not user_id:
        return JSONResponse(content={"error": "Invalid or expired token"}, status_code=401)
    # Read user credentials from JSON body
    body = await request.form()
    personal_email = body.get("personal_email")
    friend_email = body.get("friend_email")
    safe_word = body.get("safe_word")

    settings_dict = {"personal_email": personal_email,
                     "friend_email": friend_email,
                     "safe_word": safe_word}
    try:
        update_user_settings(user_id, settings_dict)
    except Exception as e:
        return JSONResponse(content={"error": f"Error updating settings: {e}"}, status_code=400)

    return JSONResponse(content={"message": "Settings updated successfully"})

@app.route("/login", methods=["POST"])
async def login(request: Request):
    # Read user credentials from JSON body
    body = await request.json()
    username = body.get("username")
    password = body.get("password")

    user_id = user_id_from_username(username)

    if not check_user(username, password):
        return JSONResponse(content={"error": "Invalid username or password"}, status_code=401)
    # Assuming `auth_manager.generate_tokens()` returns a token dictionary

    access_token = auth_manager.generate_tokens(user_id).get('access_token')

    user_settings = get_user_settings(user_id)

    to_return = {"access_token": access_token,
                 "user_id": user_id,
                 "personal_email": user_settings.get('personal_email'),
                 "friend_email": user_settings.get('friend_email'),
                 "safe_word": user_settings.get('safe_word')}

    return JSONResponse(content=to_return)


@app.route("/upload", methods=["POST"])
async def upload_audio(request: Request):
    start_upload = time.time()
    # verify jwt token
    auth_header = request.headers.get("Authorization")

    if not auth_header:
        return JSONResponse(content={"error": "Missing authentication data"}, status_code=401)
    
    # Split the "Bearer <token>" format to get the token part
    try:
        token_type, token = auth_header.split(" ")
        if token_type.lower() != "bearer":
            raise ValueError("Incorrect token type")
    except ValueError:
        return JSONResponse(content={"error": "Invalid authorization header format"}, status_code=401)
    
    user_id = await auth_manager.authenticate(token)
    if not user_id:
        return JSONResponse(content={"error": "Invalid or expired token"}, status_code=401)
    # take the body from request
    body = await request.form()

    file = body.get("file")
    audio_file_name = body.get("audioFileName")
    gps = body.get("gps")
    labels = body.get("label")
    
    # Check if the temp and upload folders exist
    upload_dir = check_temp_and_upload_folders()
    print(f'User {user_id} is trying to upload')
    # Save the file to the server
    file_path = await save_file(file, audio_file_name, upload_dir)
    print(f'File saved to {file_path} in {time.time() - start_upload} seconds')

    try:
        text_result = transcribe_audio(file_path)
        print(f"{text_result} recognized in {time.time() - start_upload} seconds")
    
    except Exception as e:
        print(f"Error transcribing audio: {e}")
    
    finally:
        delete_file(file_path)

    print(f'file uploaded and recognized in {time.time() - start_upload} seconds')
    asyncio.create_task(process_message(text_result.get('text'),gps, labels, user_id))  # Doesn't wait

    return JSONResponse(content={
        "message": "File recognized successfully",
        "filename": file.filename,
        "audioFileName": audio_file_name,
        "file_path": file_path
    })

@app.route("/cancel", methods=["POST"])
async def cancel_threat(request: Request):
    # verify jwt token
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return JSONResponse(content={"error": "Missing authentication data"}, status_code=401)
    
    # Split the "Bearer <token>" format to get the token part
    try:
        token_type, token = auth_header.split(" ")
        if token_type.lower() != "bearer":
            raise ValueError("Incorrect token type")
    except ValueError:
        return JSONResponse(content={"error": "Invalid authorization header format"}, status_code=401)
    
    user_id = await auth_manager.authenticate(token)
    if not user_id:
        return JSONResponse(content={"error": "Invalid or expired token"}, status_code=401)
    print(f'User is trying to cancel the threat.')
    change_threat_status(user_id, False)
    return JSONResponse(content={"message": "Threat cancelled successfully"})


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket):
    print("New websocket connection")
    await websocket.accept()
    print("Websocket connection accepted")

    user_id = websocket.path_params.get("user_id")
    active_connections[user_id] = websocket

    # Initial authentication
    auth_message = await websocket.receive_text()
    auth_data = json.loads(auth_message)
    
    user_id = await auth_manager.authenticate(auth_data.get('token'))
    if not user_id:
        await websocket.close(code=1008, reason="Authentication failed")
        return

    try:
        # Handle messages
        while True:
            await asyncio.sleep(0)  # Non-blocking wait

    except WebSocketDisconnect as e:
        print(f"WebSocket disconnected with code {e.code} and reason {e.reason}")
        
    finally:
        # This ensures the connection is closed properly even if there are other exceptions
        if user_id in active_connections:
            del active_connections[user_id]
        await websocket.close()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)