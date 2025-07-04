# main.py (FastAPI сервер)
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from typing import Dict, List
import json
import uuid
import os
import time
import aiofiles
from datetime import datetime

app = FastAPI()

# Конфигурация
UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# Хранилище данных
rooms: Dict[str, Dict[str, WebSocket]] = {}
chat_history: Dict[str, List[dict]] = {}
user_data: Dict[str, dict] = {}  # Доп. данные пользователей


class ConnectionManager:
    async def connect(self, room_id: str, client_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_id not in rooms:
            rooms[room_id] = {}
            chat_history[room_id] = []

        rooms[room_id][client_id] = websocket
        user_data[client_id] = {
            "room_id": room_id,
            "last_active": datetime.now().isoformat()
        }

        await self.send_chat_history(room_id, client_id)
        await self.broadcast_system_message(room_id, f"Пользователь {client_id} подключился")

    async def disconnect(self, room_id: str, client_id: str):
        if room_id in rooms and client_id in rooms[room_id]:
            del rooms[room_id][client_id]
            await self.broadcast_system_message(room_id, f"Пользователь {client_id} отключился")

            if not rooms[room_id]:
                del rooms[room_id]
                if room_id in chat_history:
                    del chat_history[room_id]

    async def broadcast_system_message(self, room_id: str, text: str):
        message = {
            "type": "system_message",
            "text": text,
            "timestamp": int(time.time())
        }
        await self.add_chat_message(room_id, message)

    async def send_chat_history(self, room_id: str, client_id: str):
        if room_id in chat_history:
            await rooms[room_id][client_id].send_json({
                "type": "chat_history",
                "messages": chat_history[room_id]
            })

    async def broadcast(self, room_id: str, message: dict):
        if room_id in rooms:
            for ws in rooms[room_id].values():
                try:
                    await ws.send_json(message)
                except:
                    continue

    async def add_chat_message(self, room_id: str, message: dict):
        if room_id not in chat_history:
            chat_history[room_id] = []

        # Ограничиваем историю 100 сообщениями
        if len(chat_history[room_id]) >= 100:
            chat_history[room_id].pop(0)

        chat_history[room_id].append(message)
        await self.broadcast(room_id, {
            "type": "new_message",
            "message": message
        })


manager = ConnectionManager()


@app.websocket("/ws/{room_id}/{client_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, client_id: str):
    await manager.connect(room_id, client_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()

            # Обработка WebRTC сигналов
            if data["type"] in ["webrtc_offer", "webrtc_answer", "ice_candidate"]:
                target_id = data["target_id"]
                if room_id in rooms and target_id in rooms[room_id]:
                    await rooms[room_id][target_id].send_json(data)

            # Обработка чата
            elif data["type"] == "chat_message":
                await manager.add_chat_message(room_id, {
                    "type": "user_message",
                    "sender": client_id,
                    "text": data["text"],
                    "timestamp": int(time.time())
                })

            # Метаданные файлов
            elif data["type"] == "file_meta":
                await manager.broadcast(room_id, {
                    "type": "incoming_file",
                    "file_name": data["file_name"],
                    "file_size": data["file_size"],
                    "sender": client_id,
                    "url": f"/uploads/{data['file_id']}"
                })

            # Сообщения управления (запись, screen sharing)
            elif data["type"] == "control_message":
                await manager.broadcast(room_id, {
                    "type": "control_event",
                    "event": data["event"],
                    "sender": client_id,
                    "data": data.get("data")
                })

    except WebSocketDisconnect:
        await manager.disconnect(room_id, client_id)
    except Exception as e:
        print(f"WebSocket error: {e}")


@app.post("/upload/{room_id}/{client_id}")
async def upload_file(room_id: str, client_id: str, file: UploadFile = File(...)):
    # Проверка размера файла
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(400, "File too large")

    # Генерация уникального имени
    file_id = f"{uuid.uuid4()}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, file_id)

    async with aiofiles.open(file_path, 'wb') as buffer:
        while chunk := await file.read(8192):
            await buffer.write(chunk)

    return {"file_id": file_id}


@app.get("/room/{room_id}/users")
async def get_room_users(room_id: str):
    return {
        "users": list(rooms.get(room_id, {}).keys()),
        "total": len(rooms.get(room_id, {}))
    }


app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory="static", html=True), name="static")