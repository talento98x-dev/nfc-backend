from fastapi import FastAPI, APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from typing import Optional
import asyncio

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
db_name = os.environ.get('DB_NAME', 'nfc_relay')
client = AsyncIOMotorClient(mongo_url)
db = client[db_name]

app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@api_router.get("/")
async def root():
    return {"message": "NFC Relay Server API"}

@api_router.get("/check_device")
async def check_device(device_id: str = ""):
    return {"status": "ACTIVE", "message": "Licenza attiva", "expires_at": "2099-12-31"}

@app.get("/api/check_device.php")
async def check_device_php(device_id: str = ""):
    return {"status": "ACTIVE", "message": "Licenza attiva", "expires_at": "2099-12-31"}

# RELAY HTTP ENDPOINT
@api_router.get("/relay")
async def relay_apdu(apdu: str = ""):
    logger.info(f"RELAY: APDU={apdu[:30]}...")
    
    if not apdu:
        return {"error": "No APDU", "response": ""}
    
    if not relay.reader_ws or not relay.card_connected:
        return {"error": "No card", "response": ""}
    
    try:
        relay.pending_response = None
        relay.response_event = asyncio.Event()
        
        await relay.reader_ws.send_text(f"APDU:{apdu}")
        
        try:
            await asyncio.wait_for(relay.response_event.wait(), timeout=3.0)
            return {"response": relay.pending_response or ""}
        except asyncio.TimeoutError:
            return {"error": "Timeout", "response": ""}
    except Exception as e:
        return {"error": str(e), "response": ""}

app.include_router(api_router)

class NFCRelayManager:
    def __init__(self):
        self.reader_ws: Optional[WebSocket] = None
        self.emulator_ws: Optional[WebSocket] = None
        self.card_connected: bool = False
        self.pending_response: Optional[str] = None
        self.response_event: asyncio.Event = asyncio.Event()
        
    async def connect_reader(self, websocket: WebSocket):
        await websocket.accept()
        self.reader_ws = websocket
        logger.info(">>> READER CONNECTED <<<")
        
    async def connect_emulator(self, websocket: WebSocket):
        await websocket.accept()
        self.emulator_ws = websocket
        logger.info(">>> EMULATOR CONNECTED <<<")
        await websocket.send_text(f"CARD:{'CONNECTED' if self.card_connected else 'DISCONNECTED'}")
    
    async def handle_reader_message(self, message: str):
        logger.info(f"READER: {message[:50]}")
        
        if message.startswith("STATUS:"):
            self.card_connected = (message[7:] == "CONNECTED")
            if self.emulator_ws:
                try:
                    await self.emulator_ws.send_text(f"CARD:{message[7:]}")
                except:
                    pass
        elif message.startswith("RESP:"):
            self.pending_response = message[5:]
            self.response_event.set()
            if self.emulator_ws:
                try:
                    await self.emulator_ws.send_text(message)
                except:
                    pass

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

@app.websocket("/ws/emulator")
async def websocket_emulator(websocket: WebSocket):
    await relay.connect_emulator(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        relay.emulator_ws = None

@app.on_event("startup")
async def startup():
    logger.info("NFC RELAY SERVER STARTED")

@app.on_event("shutdown")
async def shutdown():
    client.close()
