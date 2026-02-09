import asyncio
import threading
import uuid
import websocket
from django.conf import settings
from . import stomp
from .webrtc import WebRTCSession

BACK_HOST = settings.BACK_HOST
BACK_PORT = settings.BACK_PORT


def start(room_id: str) -> None:
    threading.Thread(target=_run_loop, args=(room_id,), daemon=True).start()


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
                asyncio.run_coroutine_threadsafe(session.handle_offer(payload), loop)
            elif msg_type == "ICE_CANDIDATE" and session:
                asyncio.run_coroutine_threadsafe(session.handle_ice(payload), loop)
    except websocket.WebSocketConnectionClosedException:
        print("[STOMP 연결 종료]", flush=True)
