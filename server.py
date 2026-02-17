from fastapi import FastAPI, APIRouter, HTTPException
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
from pydantic import BaseModel, Field
from typing import List
import uuid
from datetime import datetime, timezone

mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
db_name = os.environ.get('DB_NAME', 'nfc_db')
client = AsyncIOMotorClient(mongo_url)
db = client[db_name]

app = FastAPI()
api_router = APIRouter(prefix="/api")

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

class AccessKeyUpdate(BaseModel):
    current_key: str
    new_key: str

@api_router.get("/")
async def root():
    return {"message": "NFC Server Online"}

@api_router.post("/reads", response_model=NfcRead)
async def save_read(read_input: NfcReadCreate):
    read_obj = NfcRead(
        uid=read_input.uid,
        tech_type=read_input.tech_type,
        pin=read_input.pin,
        is_accepted=True,
    )
    await db.nfc_reads.insert_one(read_obj.dict())
    return read_obj

@api_router.get("/latest-read")
async def get_latest_read():
    read = await db.nfc_reads.find_one({}, {"_id": 0}, sort=[("timestamp", -1)])
    return read

@api_router.post("/validate-key")
async def validate_key(body: AccessKeyValidate):
    key_doc = await db.settings.find_one({"type": "access_key"})
    if not key_doc:
        return {"valid": body.key == "MRROBOT2026"}
    return {"valid": body.key == key_doc["key"]}

@api_router.put("/access-key")
async def update_access_key(body: AccessKeyUpdate):
    key_doc = await db.settings.find_one({"type": "access_key"})
    current = key_doc["key"] if key_doc else "MRROBOT2026"
    if body.current_key != current:
        raise HTTPException(status_code=403, detail="Wrong key")
    await db.settings.update_one(
        {"type": "access_key"},
        {"$set": {"key": body.new_key}},
        upsert=True
    )
    return {"message": "Key updated"}

app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
