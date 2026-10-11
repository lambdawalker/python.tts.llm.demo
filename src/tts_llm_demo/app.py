"""Single-workspace web application and background run lifecycle."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock
from starlette.middleware.trustedhost import TrustedHostMiddleware
from tts_api_client import APIError

from .agent import Agent
from .llm import LLM
from .schemas import Action, DeleteAssets, Message, Plan, SaveVoice, Settings
from .store import AssetInUse, Store, now, uid
from .tts import TTS

LOG = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def create_app(config, *, tts_client=None, llm=None):
    @asynccontextmanager
    async def lifespan(app):
        config.storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = FileLock(config.storage_dir / "workspace.lock", thread_local=False)
        lock.acquire(timeout=0)
        store = Store(config.storage_dir)
        tts = TTS(config, store, client=tts_client)
        language_model = llm or LLM(config.llm)
        app.state.store, app.state.tts = store, tts
        app.state.agent = Agent(config, store, tts, language_model)
        app.state.task = None
        app.state.cancel = asyncio.Event()
        app.state.run_id = None
        try:
            yield
        finally:
            if app.state.task and not app.state.task.done():
                app.state.cancel.set()
                app.state.task.cancel()
                await asyncio.gather(app.state.task, return_exceptions=True)
            await tts.close()
            if hasattr(language_model, "close"):
                await language_model.close()
            store.close()
            lock.release()

    app = FastAPI(title="TTS Studio", lifespan=lifespan, docs_url=None, redoc_url=None)
    # testserver is only added for injected test transports; not in a deployed app.
    hosts = config.allowed_hosts + (["testserver"] if tts_client is not None else [])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host')}"
            if request.headers.get("x-demo-request") != "1" or (origin and origin != expected):
                return JSONResponse(
                    {"detail": "Use the same-origin demo interface"}, status_code=403
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": "Resource not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    def idle():
        if app.state.task and not app.state.task.done():
            raise HTTPException(409, "A task is already running. Wait or cancel it first.")

    def start(chat_id, work):
        idle()
        store = app.state.store
        store.get("chat", chat_id)
        run = store.put(
            "run",
            dict(
                id=uid(),
                chat_id=chat_id,
                status="running",
                stage="Starting",
                created_at=now(),
                completed=0,
                total=None,
            ),
        )
        app.state.cancel = asyncio.Event()
        app.state.run_id = run["id"]

        def progress(**fields):
            store.update("run", run["id"], **fields)

        async def execute():
            try:
                await work(progress, app.state.cancel)
                if app.state.cancel.is_set():
                    raise asyncio.CancelledError()
                progress(status="succeeded", stage="Complete")
            except asyncio.CancelledError:
                progress(status="cancelled", stage="Cancelled; completed assets are retained.")
                store.message(
                    chat_id,
                    "status",
                    "Cancelled. Completed clips remain in Assets. "
                    "An already accepted remote request may still finish.",
                )
            except Exception as exc:
                if isinstance(exc, APIError):
                    error = f"TTS API returned HTTP {exc.status_code}. "
                    error += (
                        "Session expired or was revoked. Use Reset TTS session for a fresh "
                        "session; local assets are retained."
                        if exc.status_code == 401
                        else "Check the TTS server and requested model capabilities."
                    )
                elif isinstance(exc, (ValueError, TimeoutError)):
                    error = str(exc)
                elif isinstance(exc, httpx.HTTPError):
                    error = "Cannot reach LLM server. Check its address and server status."
                else:
                    error = f"{type(exc).__name__}: operation failed. Check service connectivity."
                # Avoid logging upstream request bodies or credentials.
                LOG.warning("Run %s failed (%s)", run["id"], type(exc).__name__)
                progress(status="failed", stage=error)
                store.message(chat_id, "status", error)

        app.state.task = asyncio.create_task(execute())
        return JSONResponse(run, status_code=202)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    async def state():
        store = app.state.store
        return {
            "settings": store.settings(),
            "jobs": store.list("job"),
            "chats": store.list("chat"),
            "messages": store.list("message"),
            "assets": store.list("asset"),
            "voices": store.list("voice"),
            "runs": store.list("run")[-50:],
            "playback": store.list("playback")[-20:],
        }

    @app.post("/api/chats")
    async def new_chat():
        idle()
        return app.state.store.new_chat()

    @app.delete("/api/chats/{chat_id}")
    async def delete_chat(chat_id: str):
        idle()
        app.state.store.delete_chat(chat_id)
        return {"status": "deleted"}

    @app.put("/api/settings")
    async def settings(body: Settings):
        idle()
        return app.state.store.put("settings", {"id": "workspace", **body.model_dump()})

    @app.post("/api/chats/{chat_id}/messages")
    async def message(chat_id: str, body: Message):
        return start(
            chat_id,
            lambda progress, cancel: app.state.agent.respond(chat_id, body.text, progress, cancel),
        )

    @app.put("/api/chats/{chat_id}/plan")
    async def edit_plan(chat_id: str, plan: Plan):
        idle()
        chat = app.state.store.get("chat", chat_id)
        if chat["pending"] is None:
            raise HTTPException(409, "No pending plan")
        # Runtime preflight repeats immediately before execution; editing is local/instant.
        return app.state.store.update("chat", chat_id, pending=plan.model_dump())

    @app.post("/api/chats/{chat_id}/actions")
    async def action(chat_id: str, body: Action):
        idle()
        chat = app.state.store.get("chat", chat_id)
        names = {
            "generate": "generate_pending",
            "restore": "restore_plan",
            "discard": "discard_pending",
            "save": "save_voice",
            "play": "play_asset",
        }
        args = {}
        if body.action in {"generate", "discard"} and chat["pending"] is None:
            raise HTTPException(409, "No pending plan")
        if body.action in {"save", "play"}:
            if not body.asset_id:
                raise HTTPException(422, "Choose an asset")
            app.state.store.get("asset", body.asset_id)
            args = {"asset_id": body.asset_id}
        if body.action == "save":
            args.update(name=body.name, model=body.model)
            SaveVoice.model_validate(args)
        return start(
            chat_id,
            lambda progress, cancel: app.state.agent.tool(
                chat_id, names[body.action], args, progress, cancel, explicit=True
            ),
        )

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        run = app.state.store.get("run", run_id)
        if run["status"] != "running":
            return run
        if run_id != app.state.run_id:
            raise HTTPException(409, "Run is not active")
        app.state.cancel.set()
        app.state.store.update("run", run_id, status="cancelling", stage="Cancelling…")
        app.state.task.cancel()
        return {"status": "cancelling"}

    @app.post("/api/tts/reset-session")
    async def reset_session():
        idle()
        await app.state.tts.reset_session()
        return {
            "status": "reset",
            "message": "Next task will create a new session. Local assets remain.",
        }

    def remove_assets(identifiers):
        idle()
        try:
            return app.state.store.delete_assets(identifiers)
        except AssetInUse as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/assets/delete")
    async def delete_assets(body: DeleteAssets):
        return remove_assets(body.asset_ids)

    @app.delete("/api/assets/{asset_id}")
    async def delete_asset(asset_id: str):
        return remove_assets([asset_id])

    @app.get("/api/assets/{asset_id}/audio")
    async def audio(asset_id: str, download: bool = False):
        app.state.store.get("asset", asset_id)
        path = app.state.store.audio_path(asset_id)
        if not path.is_file():
            raise HTTPException(404, "Audio file is missing from local storage")
        return FileResponse(
            path,
            media_type="audio/wav",
            filename=f"{asset_id}.wav",
            content_disposition_type="attachment" if download else "inline",
        )

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
