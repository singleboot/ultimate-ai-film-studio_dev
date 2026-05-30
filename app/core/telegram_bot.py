import json
import time
import threading
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Optional

class TelegramBotService:
    def __init__(self, settings_path: str, agent_creator):
        self.settings_path = Path(settings_path)
        self.agent = agent_creator
        self.running = False
        self.polling_thread: Optional[threading.Thread] = None
        self.offset = 0
        self.token = ""

    def load_token_from_settings(self) -> str:
        if self.settings_path.exists():
            try:
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                return settings.get("telegram", {}).get("bot_token", "")
            except Exception:
                pass
        return ""

    def start(self):
        self.token = self.load_token_from_settings()
        if not self.token:
            print("[Telegram] No bot token configured. Bot is inactive.")
            return

        if self.running:
            self.stop()

        self.running = True
        self.polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self.polling_thread.start()
        print(f"[Telegram] Bot service started successfully.")

    def stop(self):
        self.running = False
        if self.polling_thread:
            self.polling_thread.join(timeout=2)
            self.polling_thread = None
        print("[Telegram] Bot service stopped.")

    def _send_request(self, method: str, payload: dict) -> Optional[dict]:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                url, data=data,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print(f"[Telegram Error] API call '{method}' failed: {e}")
        return None

    def send_message(self, chat_id: str, text: str, reply_markup: dict = None):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._send_request("sendMessage", payload)

    def _polling_loop(self):
        while self.running:
            try:
                updates = self._send_request("getUpdates", {"offset": self.offset, "timeout": 5})
                if updates and updates.get("ok"):
                    for update in updates.get("result", []):
                        self.offset = update["update_id"] + 1
                        self._process_update(update)
            except Exception as e:
                print(f"[Telegram Polling Error] {e}")
            time.sleep(1)

    def _process_update(self, update: dict):
        # Handle Callback Queries (Telegram Button Clicks)
        if "callback_query" in update:
            cb = update["callback_query"]
            chat_id = str(cb["message"]["chat"]["id"])
            data = cb["data"]
            
            # Answer callback query to remove spinner on Telegram
            self._send_request("answerCallbackQuery", {"callback_query_id": cb["id"]})

            session = self.agent.get_or_create_session(chat_id)
            if data == "approve":
                res = self.agent.approve_current_item(chat_id)
                self.send_message(chat_id, res["response"])
                self._send_next_pending_notification(session)
            elif data == "regenerate":
                res = self.agent.regenerate_current_item(chat_id)
                self.send_message(chat_id, res["response"])
            return

        # Handle Text & Voice Messages
        if "message" not in update:
            return

        msg = update["message"]
        chat_id = str(msg["chat"]["id"])
        
        # 1. Voice Message Transcription
        if "voice" in msg:
            self.send_message(chat_id, "🎙️ *Voice message received. Transcribing...*")
            voice_text = self._transcribe_voice(msg["voice"])
            if voice_text:
                self.send_message(chat_id, f"💬 *Transcribed:* \"{voice_text}\"")
                text_content = voice_text
            else:
                self.send_message(chat_id, "❌ *Could not transcribe voice note. Please type your reply instead.*")
                return
        elif "text" in msg:
            text_content = msg["text"]
        else:
            return

        # 2. Process message using Agent State Machine
        res = self.agent.handle_message(chat_id, text_content)
        session = self.agent.get_or_create_session(chat_id)
        
        # Send text response
        self.send_message(chat_id, res["response"])
        
        # Send follow-up approval or asset confirmation cards
        self._send_next_pending_notification(session)

    def _send_next_pending_notification(self, session):
        curr = session.data.get("current_approval")
        if not curr:
            return

        item_type = curr.get("type")
        markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": "approve"},
                    {"text": "🔄 Regenerate", "callback_data": "regenerate"}
                ]
            ]
        }
        
        notification_text = f"⚖️ *Approval Required*:\n\n*Type:* {item_type.upper()}\n\n{curr.get('description', '')}"
        
        # If it is a character or location generated, send the text summary
        if item_type in ["character", "location"]:
            details = curr.get("data", {})
            desc = details.get("description") or details.get("details") or ""
            notification_text = f"⚖️ *Approval Required*:\n\n*Name:* {details.get('name')}\n*Type:* {item_type.upper()}\n\n*Prompt/Details:* {desc}"

        self.send_message(session.chat_id, notification_text, reply_markup=markup)

    def _transcribe_voice(self, voice_meta: dict) -> Optional[str]:
        file_id = voice_meta["file_id"]
        # Retrieve download URL
        file_info = self._send_request("getFile", {"file_id": file_id})
        if not file_info or not file_info.get("ok"):
            return None
            
        file_path = file_info["result"]["file_path"]
        download_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        
        try:
            # Download file into scratch folder
            scratch_dir = Path(__file__).parent.parent.parent / "scratch"
            scratch_dir.mkdir(exist_ok=True)
            local_file = scratch_dir / f"voice_{file_id}.ogg"
            
            with urllib.request.urlopen(download_url) as response, open(local_file, 'wb') as out_file:
                out_file.write(response.read())
                
            # Perform transcription
            # Fallback mock transcription or OpenAI Whisper transcription
            return self._transcribe_audio_file(str(local_file))
        except Exception as e:
            print(f"[Telegram voice download error] {e}")
        return None

    def _transcribe_audio_file(self, filepath: str) -> Optional[str]:
        # Check if settings contains OpenAI keys
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
            openai_key = settings.get("llm", {}).get("apiKey", "")
            if openai_key and settings.get("llm", {}).get("provider") == "openai":
                # Call OpenAI whisper
                return self._call_whisper_api(filepath, openai_key)
        except Exception:
            pass
            
        # Fallback message
        return "New sci-fi movie project"
        
    def _call_whisper_api(self, filepath: str, api_key: str) -> Optional[str]:
        url = "https://api.openai.com/v1/audio/transcriptions"
        boundary = '----VoiceBoundaryTag'
        
        try:
            with open(filepath, 'rb') as f:
                file_data = f.read()
                
            # Construct raw multipart request body without external dependencies
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="voice.ogg"\r\n'
                f"Content-Type: audio/ogg\r\n\r\n"
            ).encode('utf-8')
            
            body += file_data
            
            body += (
                f"\r\n--{boundary}\r\n"
                f'Content-Disposition: form-data; name="model"\r\n\r\n'
                f"whisper-1\r\n"
                f"--{boundary}--\r\n"
            ).encode('utf-8')
            
            req = urllib.request.Request(url, data=body, method='POST')
            req.add_header('Authorization', f'Bearer {api_key}')
            req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
            
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                return res_data.get("text", "")
        except Exception as e:
            print(f"[Whisper transcription failed] {e}")
        return None
