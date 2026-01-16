import asyncio
import json
import os
import random

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from livekit import api
from indic_transliteration import sanscript
from indic_transliteration.sanscript import transliterate

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
MAX_CONCURRENT_CALLS = 2        # number of docker containers
CALL_GAP_SECONDS = 10           # small buffer after dispatch (NOT concurrency)
KEY_PATH = "keys/voiceaiagenteye-da36fae0a1bf.json"
SPREADSHEET_ID = "1SeqbI70ShoNtMO4tIiP5BqQTw53FzugDfAAvc4AXqaE"

# --------------------------------------------------
# ENV
# --------------------------------------------------
load_dotenv(".env")

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# --------------------------------------------------
# NAME TRANSLITERATION (STRICT HINDI)
# --------------------------------------------------

def to_hindi_name(name: str) -> str:
    if not name:
        return ""
    return transliterate(name.strip(), sanscript.ITRANS, sanscript.DEVANAGARI)

# --------------------------------------------------
# GOOGLE SHEETS
# --------------------------------------------------
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
gs_client = gspread.authorize(creds)
sheet = gs_client.open_by_key(SPREADSHEET_ID).sheet1

# --------------------------------------------------
# LIVEKIT CALL DISPATCH
# --------------------------------------------------
async def place_call(patient_name_hi: str, phone_number: str, sheet_row: int):
    lk_api = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    room_name = f"call-{phone_number}-{random.randint(1000,9999)}"

    print(f"\n📞 Calling {patient_name_hi}")
    print(f"📱 Phone : +{phone_number}")
    print(f"🏷 Room  : {room_name}")

    dispatch_request = api.CreateAgentDispatchRequest(
        agent_name="sushrut-outbound-caller",
        room=room_name,
        metadata=json.dumps({
            "phone_number": f"+{phone_number}",
            "patient_name": patient_name_hi,
            "sheet_row": sheet_row,
        })
    )

    await lk_api.agent_dispatch.create_dispatch(dispatch_request)
    await lk_api.aclose()

# --------------------------------------------------
# CONCURRENCY CONTROL
# --------------------------------------------------
semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

async def bounded_call(patient_hi, phone, row_idx):
    async with semaphore:
        try:
            sheet.update_cell(row_idx, 4, "IN_PROGRESS")

            await place_call(patient_hi, phone, row_idx)

            await asyncio.sleep(CALL_GAP_SECONDS)

            print(f"📤 Call dispatched for row {row_idx}")

        except Exception as e:
            sheet.update_cell(row_idx, 4, "FAILED")
            print(f"❌ Call failed for row {row_idx}: {e}")

# --------------------------------------------------
# MAIN
# --------------------------------------------------
async def main():
    rows = sheet.get_all_records()
    print(f"\nTotal rows fetched: {len(rows)}")

    tasks = []

    for idx, row in enumerate(rows, start=2):
        status = row.get("Call_status")
        op_type = row.get("Operation_type")

        if status not in ("PENDING", "FAILED"):
            continue

        if op_type != "Cataract":
            continue

        patient_hi = to_hindi_name(row.get("Patient_name"))
        phone = str(row.get("Phone_number"))

        task = asyncio.create_task(
            bounded_call(patient_hi, phone, idx)
        )
        tasks.append(task)

    print(f"🚀 Dispatching {len(tasks)} calls (max {MAX_CONCURRENT_CALLS} at a time)")

    await asyncio.gather(*tasks)

    print("\n🎯 All pending calls processed.")

# --------------------------------------------------
# ENTRYPOINT
# --------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
