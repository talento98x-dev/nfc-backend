from fastapi import FastAPI, APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
from pydantic import BaseModel, Field
import uuid
from datetime import datetime, timezone
import json
import asyncio

mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
db_name = os.environ.get('DB_NAME', 'nfc_db')
client = AsyncIOMotorClient(mongo_url)
db = client[db_name]

app = FastAPI()
api_router = APIRouter(prefix="/api")

# Store active WebSocket connections
active_connections = []

class NfcRead(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    uid: str
    tech_type: str = "Unknown"
    pin: str = ""
    is_accepted: bool = True
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class NfcReadCreate(BaseModel):
    uid: str
    tech_type: str = "Unknown"
    pin: str = ""

class AccessKeyValidate(BaseModel):
    key: str

@api_router.get("/")
async def root():
    return {"message": "NFC Server Online"}

@api_router.get("/check_device")
async def check_device(device_id: str = ""):
    return {"status": "valid", "message": "OK", "device_id": device_id}

@api_router.post("/reads", response_model=NfcRead)
async def save_read(read_input: NfcReadCreate):
    read_obj = NfcRead(
        uid=read_input.uid,
        tech_type=read_input.tech_type,
        pin=read_input.pin,
    )
    await db.nfc_reads.insert_one(read_obj.dict())
    # Notify all WebSocket clients
    msg = json.dumps({"type": "card", "uid": read_input.uid, "pin": read_input.pin})
    for ws in active_connections:
        try:
            await ws.send_text(msg)
        except:
            pass
    return read_obj

@api_router.get("/latest-read")
async def get_latest_read():
    read = await db.nfc_reads.find_one({}, {"_id": 0}, sort=[("timestamp", -1)])
    return read

@api_router.post("/validate-key")
async def validate_key(body: AccessKeyValidate):
    return {"valid": body.key == "MRROBOT2026"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Broadcast to all clients
            for ws in active_connections:
                try:
                    await ws.send_text(data)
                except:
                    pass
    except WebSocketDisconnect:
        active_connections.remove(websocket)

app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
