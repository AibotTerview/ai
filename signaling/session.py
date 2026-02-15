import asyncio
import logging
import threading
import uuid
import websockets
from django.conf import settings
from signaling import stomp
from signaling.webrtc import WebRTCSession

logger = logging.getLogger(__name__)

BACK_HOST = settings.BACK_HOST
BACK_PORT = settings.BACK_PORT

# 활성 세션 관리 (room_id → WebRTCSession)
_sessions: dict[str, WebRTCSession] = {}
_sessions_lock = threading.Lock()
MAX_CONCURRENT_SESSIONS = 10

def start(room_id: str) -> bool:
    with _sessions_lock:
        if len(_sessions) >= MAX_CONCURRENT_SESSIONS:
            logger.error(f"[세션 제한] 최대 동시 세션({MAX_CONCURRENT_SESSIONS}) 초과, room={room_id} 거부")
            return False

    threading.Thread(target=_run_session, args=(room_id,), daemon=True).start()
    return True

def _run_session(room_id: str) -> None:
    asyncio.run(_session(room_id))

async def _session(room_id: str) -> None:
    ws_url = f"ws://{BACK_HOST}:{BACK_PORT}/ws-native"
    session: WebRTCSession | None = None

    try:
        async with websockets.connect(ws_url, subprotocols=["v12.stomp"]) as ws:
            await ws.send(stomp.frame("CONNECT", {"accept-version": "1.2", "host": BACK_HOST}))
            await ws.recv()

            await stomp.send(ws, "/app/signal/join", {
                "type": "JOIN",
                "roomId": room_id,
                "traceId": str(uuid.uuid4()),
            })

            await stomp.subscribe(ws, f"/topic/webrtc/offer/{room_id}", "sub-offer")
            await stomp.subscribe(ws, f"/topic/webrtc/ice/{room_id}", "sub-ice")

            async for raw in ws:
                data = stomp.parse_body(raw)
                if not data:
                    continue

                msg_type: str | None = data.get("type")
                payload: dict | None = data.get("payload")

                if msg_type == "WEBRTC_OFFER":
                    session = WebRTCSession(room_id, ws)
                    with _sessions_lock:
                        _sessions[room_id] = session
                    logger.info(f"[세션 생성] room={room_id}, 활성 세션 수={len(_sessions)}")
                    await session.handle_offer(payload)
                elif msg_type == "ICE_CANDIDATE" and session:
                    await session.handle_ice(payload)

    except websockets.ConnectionClosed:
        logger.info(f"[STOMP 연결 종료] room={room_id}")
    except Exception as e:
        logger.error(f"[세션 오류] room={room_id}: {e}", exc_info=True)

    if session:
        await session.closed.wait()

def active_session_count() -> int:
    with _sessions_lock:
        return len(_sessions)

def get_session(room_id: str) -> WebRTCSession | None:
    with _sessions_lock:
        return _sessions.get(room_id)


def remove_session(room_id: str) -> None:
    with _sessions_lock:
        session = _sessions.pop(room_id, None)
    if session:
        session.cleanup()
        logger.info(f"[세션 제거] room={room_id}")
