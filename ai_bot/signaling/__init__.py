import asyncio
import logging
import threading
import uuid
import websocket
from django.conf import settings
from . import stomp
from .webrtc import WebRTCSession

logger = logging.getLogger(__name__)

BACK_HOST = settings.BACK_HOST
BACK_PORT = settings.BACK_PORT

# 활성 세션 관리 (room_id → WebRTCSession)
_sessions: dict[str, WebRTCSession] = {}
_sessions_lock = threading.Lock()

MAX_CONCURRENT_SESSIONS = 10


def start(room_id: str) -> bool:
    """세션 시작. 성공 시 True, 동시 세션 초과 시 False."""
    with _sessions_lock:
        if len(_sessions) >= MAX_CONCURRENT_SESSIONS:
            logger.error(f"[세션 제한] 최대 동시 세션({MAX_CONCURRENT_SESSIONS}) 초과, room={room_id} 거부")
            return False
    threading.Thread(target=_run_loop, args=(room_id,), daemon=True).start()
    return True


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


def _run_loop(room_id: str) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    threading.Thread(target=_recv_loop, args=(room_id, loop), daemon=True).start()

    loop.run_forever()


def _recv_loop(room_id: str, loop: asyncio.AbstractEventLoop) -> None:
    ws = websocket.WebSocket()
    ws_url = f"ws://{BACK_HOST}:{BACK_PORT}/ws-native"
    ws.connect(ws_url, subprotocols=["v12.stomp"])

    ws.send(stomp.frame("CONNECT", {"accept-version": "1.2", "host": BACK_HOST}))
    ws.recv()

    stomp.send(ws, "/app/signal/join", {
        "type": "JOIN",
        "roomId": room_id,
        "traceId": str(uuid.uuid4()),
    })

    stomp.subscribe(ws, f"/topic/webrtc/offer/{room_id}", "sub-offer")
    stomp.subscribe(ws, f"/topic/webrtc/ice/{room_id}", "sub-ice")

    session: WebRTCSession | None = None

    try:
        while True:
            data = stomp.parse_body(ws.recv())
            if not data:
                continue

            msg_type: str | None = data.get("type")
            payload: dict | None = data.get("payload")

            if msg_type == "WEBRTC_OFFER":
                session = WebRTCSession(room_id, ws)
                with _sessions_lock:
                    _sessions[room_id] = session
                logger.info(f"[세션 생성] room={room_id}, 활성 세션 수={len(_sessions)}")
                asyncio.run_coroutine_threadsafe(session.handle_offer(payload), loop)
            elif msg_type == "ICE_CANDIDATE" and session:
                asyncio.run_coroutine_threadsafe(session.handle_ice(payload), loop)
    except websocket.WebSocketConnectionClosedException:
        logger.info(f"[STOMP 연결 종료] room={room_id}")
