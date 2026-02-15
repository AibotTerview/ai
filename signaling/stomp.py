import json
from typing import Any

def frame(command: str, headers: dict[str, Any] | None = None, body: str = "") -> str:
    lines = [command]
    for k, v in (headers or {}).items():
        lines.append(f"{k}:{v}")
    lines.append("")
    lines.append(body)
    return "\n".join(lines) + "\x00"

async def send(ws, destination: str, data: dict) -> None:
    body = json.dumps(data)
    await ws.send(frame("SEND", {
        "destination": destination,
        "content-type": "application/json",
        "content-length": len(body),
    }, body))


async def subscribe(ws, destination: str, sub_id: str) -> None:
    await ws.send(frame("SUBSCRIBE", {
        "id": sub_id,
        "destination": destination,
    }))


def parse_body(msg: str) -> dict | None:
    if "\n\n" in msg:
        body = msg.split("\n\n", 1)[1].rstrip("\x00")
        if body:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
    return None
