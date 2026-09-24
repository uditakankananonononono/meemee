"""HTTP/WebSocket surface for browser sessions and live human takeover."""
from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .browser_sessions import PAGE_ERRORS, BrowserSessionError, BrowserSessionManager


class OpenSessionRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    allowed_domains: list[str] = Field(default_factory=list, max_length=100)
    auto_takeover: bool = True


class TakeoverRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ReleaseRequest(BaseModel):
    takeover_id: str = Field(pattern=r"^bt_[0-9a-f]{32}$")
    token: str = Field(min_length=20, max_length=200)
    note: str | None = Field(default=None, max_length=1000)
    outcome: str = "completed"


VIEWER_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Meemee - take control</title>
<style>
body{margin:0;font-family:system-ui,sans-serif;background:#111;color:#eee}
header{display:flex;gap:.6rem;align-items:center;padding:.5rem .8rem;background:#222;flex-wrap:wrap}
#reason{flex:1;min-width:12rem}#status{font-size:.85rem;color:#9c9}
button,input{font:inherit;padding:.35rem .6rem;border-radius:6px;border:1px solid #555;background:#333;color:#eee}
button.primary{background:#2d6cdf;border-color:#2d6cdf}
#stage{position:relative;margin:.5rem auto;max-width:100%;width:fit-content}
#screen{display:block;max-width:100%;cursor:crosshair;outline:none;border:1px solid #444}
#url{font-size:.8rem;color:#aaa;padding:0 .8rem}
</style></head><body>
<header><strong>Meemee needs a hand</strong><span id="reason"></span><span id="status">connecting...</span>
<input id="text" placeholder="type text, Enter to send" size="24"><button id="done" class="primary">Done - hand back</button>
<button id="decline">Can't do it</button></header>
<div id="url"></div>
<div id="stage"><img id="screen" tabindex="0" alt="live page"></div>
<script>
(function(){
  const parts=(location.hash||'').slice(1).split('.');const tid=parts[0];const token=parts.slice(1).join('.');
  history.replaceState(null,'',location.pathname);
  const img=document.getElementById('screen'),st=document.getElementById('status');
  let vw=1280,vh=800,ws;
  function send(o){if(ws&&ws.readyState===1)ws.send(JSON.stringify(o));}
  const proto=location.protocol==='https:'?'wss':'ws';
  ws=new WebSocket(proto+'://'+location.host+'/v1/browser/takeover/ws');
  ws.onopen=()=>send({takeover_id:tid,token:token});
  ws.onmessage=(m)=>{const d=JSON.parse(m.data);
    if(d.type==='claimed'){vw=d.viewport.width;vh=d.viewport.height;document.getElementById('reason').textContent=d.reason;st.textContent='you have control';}
    else if(d.type==='frame'){img.src='data:image/jpeg;base64,'+d.jpeg;document.getElementById('url').textContent=d.url;}
    else if(d.type==='released'){st.textContent='handed back - you can close this tab';ws.close();}
    else if(d.type==='error'){st.textContent=d.error;}};
  ws.onclose=()=>{if(!st.textContent.startsWith('handed'))st.textContent='disconnected';};
  function pt(e){const r=img.getBoundingClientRect();return{x:(e.clientX-r.left)*vw/r.width,y:(e.clientY-r.top)*vh/r.height};}
  img.addEventListener('click',e=>{const p=pt(e);send({type:'click',x:p.x,y:p.y});img.focus();});
  img.addEventListener('dblclick',e=>{const p=pt(e);send({type:'click',x:p.x,y:p.y,double:true});});
  img.addEventListener('wheel',e=>{e.preventDefault();send({type:'scroll',dx:e.deltaX,dy:e.deltaY});},{passive:false});
  img.addEventListener('keydown',e=>{e.preventDefault();let k=e.key===' '?'Space':e.key;const m=[];
    if(e.ctrlKey)m.push('Control');if(e.altKey)m.push('Alt');if(e.metaKey)m.push('Meta');
    if(k.length===1&&!m.length){send({type:'type',text:e.key});return;}
    if(e.shiftKey&&k.length>1)m.push('Shift');if(['Shift','Control','Alt','Meta'].includes(k))return;send({type:'key',key:m.concat([k]).join('+')});});
  const t=document.getElementById('text');
  t.addEventListener('keydown',e=>{if(e.key==='Enter'&&t.value){send({type:'type',text:t.value});t.value='';}});
  document.getElementById('done').onclick=()=>send({type:'release',outcome:'completed'});
  document.getElementById('decline').onclick=()=>send({type:'release',outcome:'declined'});
})();
</script></body></html>"""


def build_browser_router(manager: BrowserSessionManager, auth, audit=None, frame_interval: float = 0.3) -> APIRouter:
    router = APIRouter(tags=["browser"])
    read = Depends(auth.dependency("jobs:read"))
    write = Depends(auth.dependency("runs:write"))

    def owner_filter(principal) -> str | None:
        return None if "admin" in principal.scopes else principal.id

    def owned(session_id: str, principal) -> dict:
        record = manager.store.get_session(session_id)
        if record is None or (owner_filter(principal) is not None and record["owner_id"] != principal.id):
            raise HTTPException(status_code=404, detail="browser session not found")
        return record

    def audit_event(principal_id: str, action: str, resource: str, detail: dict) -> None:
        if audit is not None:
            audit.append(principal_id, action, resource, "success", detail)

    @router.get("/v1/browser/sessions")
    def list_sessions(limit: int = 100, principal=read):
        return {"sessions": manager.store.list_sessions(owner_filter(principal), limit)}

    @router.post("/v1/browser/sessions", status_code=201)
    async def open_session(request: OpenSessionRequest, principal=write):
        try:
            state = await manager.open(request.url, principal.id, request.allowed_domains, None, request.auto_takeover)
        except BrowserSessionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit_event(principal.id, "browser.session_opened", state["session_id"], {"url": request.url[:500]})
        return state

    @router.get("/v1/browser/sessions/{session_id}")
    def get_session(session_id: str, principal=read):
        record = owned(session_id, principal)
        return {"session": record, "takeovers": manager.store.session_takeovers(session_id), "events": manager.store.events(session_id)}

    @router.post("/v1/browser/sessions/{session_id}/takeover", status_code=201)
    async def request_takeover(session_id: str, request: TakeoverRequest, principal=write):
        owned(session_id, principal)
        try:
            takeover = await manager.request_takeover(session_id, request.reason, principal.id)
        except BrowserSessionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_event(principal.id, "browser.takeover_requested", session_id, {"takeover_id": takeover["takeover_id"]})
        return takeover

    @router.delete("/v1/browser/sessions/{session_id}")
    async def close_session(session_id: str, principal=write):
        owned(session_id, principal)
        try:
            result = await manager.close(session_id, principal.id, "closed_by_operator")
        except BrowserSessionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_event(principal.id, "browser.session_closed", session_id, {})
        return result

    @router.post("/v1/browser/takeover/release")
    async def release(request: ReleaseRequest):
        try:
            result = await manager.release(request.takeover_id, request.token, request.note, request.outcome)
        except BrowserSessionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        audit_event("human", "browser.takeover_released", result["session_id"], {"takeover_id": request.takeover_id, "outcome": request.outcome})
        return result

    @router.get("/browser/takeover", response_class=HTMLResponse, include_in_schema=False)
    def viewer():
        return HTMLResponse(VIEWER_HTML, headers={
            "Content-Security-Policy": ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                                        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"),
            "Referrer-Policy": "no-referrer", "Cache-Control": "no-store", "X-Frame-Options": "DENY",
        })

    @router.websocket("/v1/browser/takeover/ws")
    async def takeover_socket(websocket: WebSocket):
        await websocket.accept()
        try:
            hello = json.loads(await asyncio.wait_for(websocket.receive_text(), 15))
            takeover_id, token = str(hello.get("takeover_id", "")), str(hello.get("token", ""))
            claimed = await manager.claim(takeover_id, token)
        except (BrowserSessionError, ValueError, asyncio.TimeoutError) as exc:
            await websocket.send_json({"type": "error", "error": str(exc) or "invalid handshake"})
            await websocket.close(code=4403)
            return
        except WebSocketDisconnect:
            return
        audit_event("human", "browser.takeover_claimed", claimed["session_id"], {"takeover_id": takeover_id})
        await websocket.send_json({"type": "claimed", **claimed})
        stop = asyncio.Event()

        async def stream():
            while not stop.is_set():
                try:
                    await websocket.send_json(await manager.frame(takeover_id, token))
                except BrowserSessionError as exc:
                    await websocket.send_json({"type": "error", "error": str(exc)})
                    stop.set()
                    return
                except (*PAGE_ERRORS, WebSocketDisconnect, RuntimeError):
                    stop.set()
                    return
                try:
                    await asyncio.wait_for(stop.wait(), frame_interval)
                except asyncio.TimeoutError:
                    pass

        streamer = asyncio.create_task(stream())
        try:
            while not stop.is_set():
                message = json.loads(await websocket.receive_text())
                if message.get("type") == "release":
                    result = await manager.release(takeover_id, token, message.get("note"), message.get("outcome", "completed"))
                    audit_event("human", "browser.takeover_released", result["session_id"], {"takeover_id": takeover_id, "outcome": result["outcome"]})
                    stop.set()
                    await streamer
                    await websocket.send_json({"type": "released", **result})
                    await websocket.close()
                    return
                try:
                    ack = await manager.human_input(takeover_id, token, message)
                    await websocket.send_json({"type": "ack", **ack})
                except BrowserSessionError as exc:
                    await websocket.send_json({"type": "error", "error": str(exc)})
                    if "takeover" in str(exc) or "no longer live" in str(exc):
                        stop.set()
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            stop.set()
            if not streamer.done():
                streamer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await streamer

    return router
