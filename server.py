from fastapi import FastAPI, APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
import uuid
from datetime import datetime, timezone
import asyncio
import json

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")

# --- Models ---

class NfcRead(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    uid: str
    tech_type: str = "Unknown"
    pin: str = ""
    is_accepted: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class NfcReadCreate(BaseModel):
    uid: str
    tech_type: str = "Unknown"
    pin: str = ""

class WhitelistEntry(BaseModel):
    uid: str

class WhitelistResponse(BaseModel):
    uids: List[str]

class AccessKeyValidate(BaseModel):
    key: str

class AccessKeyUpdate(BaseModel):
    current_key: str
    new_key: str

# --- Routes ---

@api_router.get("/")
async def root():
    return {"message": "NFC Reader Server API"}

# --- Whitelist management ---
@api_router.get("/whitelist", response_model=WhitelistResponse)
async def get_whitelist():
    entries = await db.whitelist.find({}, {"_id": 0}).to_list(1000)
    return {"uids": [e["uid"] for e in entries]}

@api_router.post("/whitelist")
async def add_to_whitelist(entry: WhitelistEntry):
    existing = await db.whitelist.find_one({"uid": entry.uid})
    if existing:
        return {"message": "UID already in whitelist", "uid": entry.uid}
    await db.whitelist.insert_one({"uid": entry.uid})
    return {"message": "UID added to whitelist", "uid": entry.uid}

@api_router.delete("/whitelist/{uid}")
async def remove_from_whitelist(uid: str):
    result = await db.whitelist.delete_one({"uid": uid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="UID not found in whitelist")
    return {"message": "UID removed from whitelist", "uid": uid}

@api_router.post("/whitelist/bulk")
async def bulk_add_whitelist(entries: List[WhitelistEntry]):
    added = 0
    for entry in entries:
        existing = await db.whitelist.find_one({"uid": entry.uid})
        if not existing:
            await db.whitelist.insert_one({"uid": entry.uid})
            added += 1
    return {"message": f"{added} UIDs added to whitelist", "total": added}

# --- NFC reads ---
@api_router.post("/reads", response_model=NfcRead)
async def save_read(read_input: NfcReadCreate):
    read_obj = NfcRead(
        uid=read_input.uid,
        tech_type=read_input.tech_type,
        pin=read_input.pin,
        is_accepted=True,
    )
    doc = read_obj.dict()
    await db.nfc_reads.insert_one(doc)
    return read_obj

@api_router.get("/reads", response_model=List[NfcRead])
async def get_reads():
    reads = await db.nfc_reads.find({}, {"_id": 0}).sort("timestamp", -1).to_list(500)
    return reads

@api_router.delete("/reads/{read_id}")
async def delete_read(read_id: str):
    result = await db.nfc_reads.delete_one({"id": read_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Read not found")
    return {"deleted": True}

@api_router.delete("/reads")
async def clear_reads():
    result = await db.nfc_reads.delete_many({})
    return {"deleted": result.deleted_count}

# --- For emulator app: get all unique UIDs to emulate ---
@api_router.get("/uids-to-emulate")
async def get_uids_to_emulate():
    reads = await db.nfc_reads.find({}, {"_id": 0, "uid": 1, "tech_type": 1, "is_accepted": 1}).to_list(500)
    seen = set()
    unique = []
    for r in reads:
        if r["uid"] not in seen:
            seen.add(r["uid"])
            unique.append(r)
    return unique

@api_router.get("/latest-read")
async def get_latest_read():
    read = await db.nfc_reads.find_one(
        {},
        {"_id": 0},
        sort=[("timestamp", -1)]
    )
    if not read:
        return None
    return read

# --- Device Check (IMPORTANTE per XX PAY app) ---
@api_router.get("/check_device")
async def check_device(device_id: str = ""):
    return {
        "status": "ACTIVE",
        "message": "Licenza attiva",
        "expires_at": "2099-12-31"
    }

# Endpoint PHP-style per compatibilità con l'app originale
@app.get("/api/check_device.php")
async def check_device_php(device_id: str = ""):
    return {
        "status": "ACTIVE",
        "message": "Licenza attiva",
        "expires_at": "2099-12-31"
    }

# --- Access Key ---
@api_router.post("/validate-key")
async def validate_key(body: AccessKeyValidate):
    key_doc = await db.settings.find_one({"type": "access_key"}, {"_id": 0})
    if not key_doc:
        raise HTTPException(status_code=500, detail="Access key not configured")
    if body.key == key_doc["key"]:
        return {"valid": True}
    return {"valid": False}

@api_router.put("/access-key")
async def update_access_key(body: AccessKeyUpdate):
    key_doc = await db.settings.find_one({"type": "access_key"})
    if not key_doc:
        raise HTTPException(status_code=500, detail="Access key not configured")
    if body.current_key != key_doc["key"]:
        raise HTTPException(status_code=403, detail="Current key is wrong")
    await db.settings.update_one(
        {"type": "access_key"},
        {"$set": {"key": body.new_key}}
    )
    return {"message": "Key updated", "new_key": body.new_key}

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("startup")
async def seed_whitelist():
    count = await db.whitelist.count_documents({})
    if count == 0:
        default_uids = [
            "04:A2:3B:7C:D1:22:80",
            "04:E5:91:0A:B3:60:81",
            "08:3A:F1:2E",
        ]
        for uid in default_uids:
            await db.whitelist.insert_one({"uid": uid})
        logger.info(f"Seeded {len(default_uids)} default whitelist UIDs")

    key_doc = await db.settings.find_one({"type": "access_key"})
    if not key_doc:
        await db.settings.insert_one({"type": "access_key", "key": "MRROBOT2026"})
        logger.info("Seeded default access key: MRROBOT2026")

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

# ==================== NFC RELAY WEBSOCKET ====================

class NFCRelayManager:
    def __init__(self):
        self.reader_ws: Optional[WebSocket] = None
        self.emulator_ws: Optional[WebSocket] = None
        self.card_connected: bool = False
        self.pending_response: asyncio.Event = asyncio.Event()
        self.last_response: str = ""
        
    async def connect_reader(self, websocket: WebSocket):
        await websocket.accept()
        self.reader_ws = websocket
        logger.info("Reader connected")
        
    async def connect_emulator(self, websocket: WebSocket):
        await websocket.accept()
        self.emulator_ws = websocket
        logger.info("Emulator connected")
        if self.card_connected:
            await websocket.send_text("CARD:CONNECTED")
        else:
            await websocket.send_text("CARD:DISCONNECTED")
    
    async def send_apdu_to_reader(self, apdu: str) -> str:
        if not self.reader_ws or not self.card_connected:
            return "ERROR:NO_CARD"
        
        try:
            self.pending_response.clear()
            self.last_response = ""
            
            await self.reader_ws.send_text(f"APDU:{apdu}")
            
            try:
                await asyncio.wait_for(self.pending_response.wait(), timeout=2.0)
                return self.last_response
            except asyncio.TimeoutError:
                return "ERROR:TIMEOUT"
                
        except Exception as e:
            logger.error(f"Error sending APDU: {e}")
            return "ERROR:SEND_FAILED"
    
    async def handle_reader_message(self, message: str):
        if message.startswith("STATUS:"):
            status = message[7:]
            self.card_connected = (status == "CONNECTED")
            logger.info(f"Card status: {status}")
            
            if self.emulator_ws:
                try:
                    await self.emulator_ws.send_text(f"CARD:{status}")
                except:
                    pass
                    
        elif message.startswith("RESP:"):
            self.last_response = message[5:]
            self.pending_response.set()
    
    async def handle_emulator_message(self, message: str):
        if message.startswith("APDU:"):
            apdu = message[5:]
            response = await self.send_apdu_to_reader(apdu)
            
            if self.emulator_ws:
                try:
                    await self.emulator_ws.send_text(f"RESP:{response}")
                except:
                    pass

relay_manager = NFCRelayManager()

@app.websocket("/ws/reader")
async def websocket_reader(websocket: WebSocket):
    await relay_manager.connect_reader(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await relay_manager.handle_reader_message(data)
    except WebSocketDisconnect:
        relay_manager.reader_ws = None
        relay_manager.card_connected = False
        logger.info("Reader disconnected")
        if relay_manager.emulator_ws:
            try:
                await relay_manager.emulator_ws.send_text("CARD:DISCONNECTED")
            except:
                pass

@app.websocket("/ws/emulator")
async def websocket_emulator(websocket: WebSocket):
    await relay_manager.connect_emulator(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await relay_manager.handle_emulator_message(data)
    except WebSocketDisconnect:
        relay_manager.emulator_ws = None
        logger.info("Emulator disconnected")
