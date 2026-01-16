# agent.py (INBOUND + OUTBOUND VOICE AI + CALL RECORDING TO S3)

from dotenv import load_dotenv
load_dotenv(".env")

import os
import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import gspread
from google.oauth2.service_account import Credentials

from livekit import agents, rtc, api
from livekit.agents import AgentSession, Agent, room_io
from livekit.plugins import (
    silero,
    deepgram,
    openai,
    sarvam,
    noise_cancellation,
)

from prompts import AGENT_INSTRUCTION
KEY_PATH = "/keys/voiceaiagenteye-da36fae0a1bf.json"

MIN_SUCCESS_DURATION = 60  # seconds

# --------------------------------------------------
# FORCE SINGLE WORKER
# --------------------------------------------------
os.environ["LIVEKIT_AGENT_WORKERS"] = "1"

# --------------------------------------------------
# OUTBOUND SIP TRUNK
# --------------------------------------------------
OUTBOUND_TRUNK_ID = "ST_B9KqLtVKFD4w"

# --------------------------------------------------
# ASSISTANT
# --------------------------------------------------
class Assistant(Agent):
    def __init__(self):
        super().__init__(instructions=AGENT_INSTRUCTION)

# --------------------------------------------------
# GOOGLE SHEET HELPERS
# --------------------------------------------------
def update_recording_in_sheet(sheet_row: int, recording_url: str):
    if not sheet_row:
        return

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_file(
        KEY_PATH,
        scopes=SCOPES,
    )

    sheet = (
        gspread.authorize(creds)
        .open_by_key("1SeqbI70ShoNtMO4tIiP5BqQTw53FzugDfAAvc4AXqaE")
        .sheet1
    )

    # ✅ Column E = Recording Link
    sheet.update_cell(sheet_row, 5, recording_url)


def update_call_status(sheet_row: int, status: str):
    if not sheet_row:
        return

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_file(
        KEY_PATH,
        scopes=SCOPES,
    )

    sheet = gspread.authorize(creds) \
        .open_by_key("1SeqbI70ShoNtMO4tIiP5BqQTw53FzugDfAAvc4AXqaE") \
        .sheet1

    sheet.update_cell(sheet_row, 4, status)

# --------------------------------------------------
# DISPATCH ENTRYPOINT
# --------------------------------------------------
async def entrypoint(ctx: agents.JobContext):

    phone_number = None
    patient_hi = "जी"
    sheet_row = None
    call_start_time = None

    if ctx.job and ctx.job.metadata:
        try:
            data = json.loads(ctx.job.metadata)
            phone_number = data.get("phone_number")
            patient_hi = data.get("patient_name", "जी")
            sheet_row = data.get("sheet_row")
        except Exception:
            phone_number = ctx.job.metadata.strip()

    is_outbound = bool(phone_number)

    # --------------------------------------------------
    # START RECORDING
    # --------------------------------------------------
    ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    timestamp = ist_now.strftime("%Y%m%d-%I_%M_%S%p")
    safe_room = ctx.room.name.replace("/", "_")
    s3_key = f"recordings/{timestamp}_{safe_room}.mp3"
    recording_url = (
    "https://recording-bucket-voiceai.s3.ap-south-1.amazonaws.com/"
    + s3_key
    )
    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_HTTP_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    try:
        await lkapi.egress.start_room_composite_egress(
            api.RoomCompositeEgressRequest(
                room_name=ctx.room.name,
                audio_only=True,
                file_outputs=[
                    api.EncodedFileOutput(
                        file_type=api.EncodedFileType.MP3,
                        filepath=s3_key,
                        disable_manifest=True,
                        s3=api.S3Upload(
                            bucket=os.getenv("AWS_BUCKET_NAME"),
                            region=os.getenv("AWS_REGION"),
                            access_key=os.getenv("AWS_ACCESS_KEY_ID"),
                            secret=os.getenv("AWS_SECRET_ACCESS_KEY"),
                        ),
                    )
                ],
            )
        )
        update_recording_in_sheet(sheet_row, recording_url)
    except Exception:
        pass

    # --------------------------------------------------
    # AGENT SESSION
    # --------------------------------------------------
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="hi"),
        llm=openai.LLM(model="gpt-4o-mini", temperature=0),
        tts=sarvam.TTS(
            target_language_code="hi-IN",
            speaker="anushka",
            model="bulbul:v2",
        ),
        vad=silero.VAD.load(
            activation_threshold=0.75,
            min_speech_duration=0.35,
            min_silence_duration=0.4,
            prefix_padding_duration=0.25,
        ),
        allow_interruptions=False,  # ✅ FIXED
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_options=room_io.RoomOptions(close_on_disconnect=False),
    )

    # --------------------------------------------------
    # SIP LIFECYCLE TRACKING
    # --------------------------------------------------
    @ctx.room.on("participant_connected")
    def on_participant_connected(p):
        nonlocal call_start_time
        if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            call_start_time = datetime.now(ZoneInfo("Asia/Kolkata"))

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(p):
        if p.kind != rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return

        if not call_start_time:
            update_call_status(sheet_row, "NO_ANSWER")
            return

        duration = (datetime.now(ZoneInfo("Asia/Kolkata")) - call_start_time).total_seconds()
        update_call_status(sheet_row, "SUCCESS" if duration >= MIN_SUCCESS_DURATION else "FAILED")

    # --------------------------------------------------
    # OUTBOUND SIP
    # --------------------------------------------------
    if is_outbound:
        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=OUTBOUND_TRUNK_ID,
                    sip_call_to=phone_number,
                    participant_identity=f"sip_{phone_number}",
                    wait_until_answered=False,
                )
            )
        except Exception:
            update_call_status(sheet_row, "FAILED")
            return

    # --------------------------------------------------
    # GREETING
    # --------------------------------------------------
    await session.say(
        "नमस्कार जी। मैं सुष्रुत आई हॉस्पिटल से बात कर रही हूँ। "
        f"क्या मेरी बात {patient_hi} जी से हो रही है, ",
        allow_interruptions=False,
    )

    # --------------------------------------------------
    # KEEP ALIVE
    # --------------------------------------------------
    try:
        while True:
            await asyncio.sleep(0.5)
    finally:
        await lkapi.aclose()

# --------------------------------------------------
# START AGENT
# --------------------------------------------------
if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(
            agent_name="sushrut-outbound-caller",
            entrypoint_fnc=entrypoint,
        )
    )
