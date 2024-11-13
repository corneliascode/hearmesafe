# HearMeSafe 🛡️

## What is HearMeSafe?

**HearMeSafe** is a Flutter app that listens for distress signals in your voice, analyzes them, and checks if you’re in danger. If something’s off, it alerts a trusted contact with your location — but only if you don’t stop it first.

## How It Works 🚨

1. **Voice Analysis**  
   HearMeSafe uses **MediaPipe** to analyze your voice/audio files and classify it.

2. **Danger Detection**  
   The app sends your GPS location, audio, and classification labels to a **FastAPI server**.

3. **Threat Mode**  
   If danger is detected, the server sends a message via **WebSocket** to the app:  
   - A **10-second countdown** starts, with your phone buzzing 3 times to alert you.  
   - You can cancel the alert at any time during or after the countdown.

4. **Alert Delivery**  
   If the alert isn’t canceled, an **email** with your exact GPS location is sent to your trusted contact.

5. **Always On**  
   The app works in both **foreground** and **background**, so it’s always listening and ready to help.

## Built With ❤️

- **Flutter**: For a smooth cross-platform experience.  
- **MediaPipe**: Handles audio classification.  
- **WebSockets**: Enables instant communication between app and server.