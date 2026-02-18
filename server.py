from fastapi import FastAPI, APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel
from typing import Optional
import asyncio

# Setup
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
db_name = os.environ.get('DB_NAME', 'nfc_relay')
client = AsyncIOMotorClient(mongo_url)
db = client[db_name]

app = FastAPI()
api_router = APIRouter(prefix="/api")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# API ENDPOINTS
# ============================================

@api_router.get("/")
async def root():
    return {"message": "NFC Relay Server API"}

@api_router.get("/check_device")
async def check_device(device_id: str = ""):
    return {"status": "ACTIVE", "message": "Licenza attiva", "expires_at": "2099-12-31"}

@app.get("/api/check_device.php")
async def check_device_php(device_id: str = ""):
    return {"status": "ACTIVE", "message": "Licenza attiva", "expires_at": "2099-12-31"}

app.include_router(api_router)

# ============================================
# WEBSOCKET NFC RELAY
# ============================================

class NFCRelayManager:
    def __init__(self):
        self.reader_ws: Optional[WebSocket] = None
        self.emulator_ws: Optional[WebSocket] = None
        self.card_connected: bool = False
        
    async def connect_reader(self, websocket: WebSocket):
        await websocket.accept()
        self.reader_ws = websocket
        logger.info(">>> READER CONNECTED <<<")
        
    async def connect_emulator(self, websocket: WebSocket):
        await websocket.accept()
        self.emulator_ws = websocket
        logger.info(">>> EMULATOR CONNECTED <<<")
        if self.card_connected:
            await websocket.send_text("CARD:CONNECTED")
        else:
            await websocket.send_text("CARD:DISCONNECTED")
    
    async def handle_reader_message(self, message: str):
        logger.info(f"READER -> SERVER: {message[:50]}")
        
        if message.startswith("STATUS:"):
            status = message[7:]
            self.card_connected = (status == "CONNECTED")
            if self.emulator_ws:
                try:
                    await self.emulator_ws.send_text(f"CARD:{status}")
                except:
                    pass
                    
        elif message.startswith("RESP:"):
            if self.emulator_ws:
                try:
                    await self.emulator_ws.send_text(message)
                    logger.info(f"SERVER -> EMULATOR: {message[:50]}")
                except:
                    pass
    
    async def handle_emulator_message(self, message: str):
        logger.info(f"EMULATOR -> SERVER: {message[:50]}")
        
        if message.startswith("APDU:"):
            if self.reader_ws and self.card_connected:
                try:
                    await self.reader_ws.send_text(message)
                    logger.info(f"SERVER -> READER: {message[:50]}")
                except:
                    if self.emulator_ws:
                        await self.emulator_ws.send_text("RESP:ERROR")
            else:
                if self.emulator_ws:
                    await self.emulator_ws.send_text("RESP:ERROR_NO_CARD")

relay = NFCRelayManager()

@app.websocket("/ws/reader")
async def websocket_reader(websocket: WebSocket):
    await relay.connect_reader(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await relay.handle_reader_message(data)
    except WebSocketDisconnect:
        relay.reader_ws = None
        relay.card_connected = False
        if relay.emulator_ws:
            try:
                await relay.emulator_ws.send_text("CARD:DISCONNECTED")
            except:
                pass

@app.websocket("/ws/emulator")
async def websocket_emulator(websocket: WebSocket):
    await relay.connect_emulator(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await relay.handle_emulator_message(data)
    except WebSocketDisconnect:
        relay.emulator_ws = None

@app.on_event("startup")
async def startup():
    logger.info("NFC RELAY SERVER STARTED")
    logger.info("WebSocket: /ws/reader and /ws/emulator")

@app.on_event("shutdown")
async def shutdown():
    client.close()
