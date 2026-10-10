"""Tenant-scoped knowledge ingestion and operator-managed channel connections.

Provider responses are deliberately reduced before returning them to owners: Evolution's
instance responses can contain its API key, and Chatwoot returns user access tokens.
"""

import hashlib
import hmac
import json
import secrets
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from salma.auth import rate_limit, require_business, require_operator
from salma.db import Knowledge, now, session
from salma.knowledge import MODELS, chunk_text, embedding_model, extract_pdf
from salma.platform_models import Business, Channel, Document
from salma.settings import settings

router = APIRouter(prefix="/api/businesses/{business_id}")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def extract_upload(path: Path):
    if path.suffix.lower() == ".pdf":
        return extract_pdf(path)
    if path.suffix.lower() != ".txt":
        raise ValueError("Upload a text-based PDF or UTF-8 TXT file")
    try:
        content = unicodedata.normalize("NFKC", path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError:
        raise ValueError("TXT files must use UTF-8 encoding") from None
    if not content.strip():
        raise ValueError("The document contains no readable text")
    return [(1, content)]


def cipher():
    key = settings().credential_key.get_secret_value()
    if not key:
        raise HTTPException(
            503, "The operator must configure CREDENTIAL_KEY before connecting channels"
        )
    try:
        return Fernet(key.encode())
    except ValueError:
        raise HTTPException(503, "The channel encryption key is invalid") from None


def encrypt_credentials(value):
    return cipher().encrypt(json.dumps(value).encode()).decode()


def decrypt_credentials(value):
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except InvalidToken, ValueError:
        raise HTTPException(
            503, "Channel credentials cannot be decrypted; ask the operator to repair them"
        ) from None


def get_channel_credentials(channel):
    return decrypt_credentials(channel.credentials)


def document_view(row):
    return {
        key: getattr(row, key) for key in ("id", "filename", "status", "error", "approved", "pages")
    }


def channel_view(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "kind",
            "name",
            "account_id",
            "inbox_id",
            "evolution_instance",
            "live_enabled",
            "status",
            "details",
        )
    }


def tenant_row(db, model, identifier, business_id):
    row = db.scalar(select(model).where(model.id == identifier, model.business_id == business_id))
    if not row:
        raise HTTPException(404, "Resource not found")
    return row


@router.get("/documents")
def documents(request: Request, business_id: str):
    require_business(request, business_id)
    with session() as db:
        rows = db.scalars(select(Document).where(Document.business_id == business_id)).all()
        return {"documents": [document_view(row) for row in rows if row.status != "retired"]}


@router.post("/documents", status_code=202)
async def upload_document(request: Request, business_id: str, file: UploadFile = File(...)):
    require_business(request, business_id)
    filename = Path((file.filename or "document").replace("\\", "/")).name[:200]
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".txt"}:
        raise HTTPException(422, "Upload a text-based PDF or UTF-8 TXT file")
    identifier = str(uuid4())
    directory = Path(settings().uploads_dir).resolve() / business_id
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{identifier}{suffix}"
    size = 0
    try:
        with destination.open("xb") as stream:
            while chunk := await file.read(64 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Documents must be 20 MB or smaller")
                stream.write(chunk)
        if not size:
            raise HTTPException(422, "The document is empty")
        with session() as db, db.begin():
            row = Document(
                id=identifier,
                business_id=business_id,
                filename=filename,
                storage_path=str(destination),
                status="pending",
                approved=False,
                pages=[],
                details={},
            )
            db.add(row)
            db.flush()
            return document_view(row)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


class DocumentApproval(BaseModel):
    approved: bool


@router.patch("/documents/{document_id}")
def approve_document(request: Request, business_id: str, document_id: str, body: DocumentApproval):
    require_business(request, business_id)
    with session() as db, db.begin():
        row = tenant_row(db, Document, document_id, business_id)
        if body.approved and row.status != "ready":
            raise HTTPException(409, "Wait for successful indexing before approving the document")
        row.approved = body.approved
        business = db.get(Business, business_id)
        draft = dict(business.draft or {})
        ids = list(draft.get("document_ids", []))
        if body.approved and row.id not in ids:
            ids.append(row.id)
        elif not body.approved:
            ids = [identifier for identifier in ids if identifier != row.id]
        draft["document_ids"] = ids
        business.draft = draft
        return document_view(row)


@router.delete("/documents/{document_id}")
def retire_document(request: Request, business_id: str, document_id: str):
    require_business(request, business_id)
    with session() as db, db.begin():
        row = tenant_row(db, Document, document_id, business_id)
        row.status, row.approved = "retired", False
        business = db.get(Business, business_id)
        business.draft = {
            **(business.draft or {}),
            "document_ids": [
                identifier
                for identifier in (business.draft or {}).get("document_ids", [])
                if identifier != document_id
            ],
        }
        # Keep backing data for immutable published versions and rollback.
        return {"retired": True}


@router.get("/documents/{document_id}/download")
def download_document(request: Request, business_id: str, document_id: str):
    require_business(request, business_id)
    with session() as db:
        row = tenant_row(db, Document, document_id, business_id)
        path = Path(row.storage_path).resolve()
        if not path.is_relative_to(Path(settings().uploads_dir).resolve()) or not path.is_file():
            raise HTTPException(404, "The original document is unavailable")
        return FileResponse(path, filename=row.filename, headers={"Cache-Control": "no-store"})


def process_document_job(document_id=None):
    """Claim one durable job. An expired lease allows recovery after a worker crash."""
    claim = str(uuid4())
    with session() as db, db.begin():
        query = select(Document).where(Document.status.in_(["pending", "indexing"]))
        if document_id is not None:
            query = query.where(Document.id == document_id)
        candidates = db.scalars(query.with_for_update(skip_locked=True)).all()
        row = next(
            (
                item
                for item in candidates
                if item.status == "pending"
                or (item.details or {}).get("lease_until", "") < now().isoformat()
            ),
            None,
        )
        if not row:
            return False
        row.status = "indexing"
        row.details = {"claim": claim, "lease_until": (now() + timedelta(minutes=30)).isoformat()}
        identifier, business_id, filename, path = (
            row.id,
            row.business_id,
            row.filename,
            row.storage_path,
        )
    try:
        pages = extract_upload(Path(path))
        model = embedding_model("ar")
        evidence = []
        for page, content in pages:
            for index, chunk in enumerate(
                chunk_text(content, model.tokenizer, limit=model.max_seq_length)
            ):
                evidence.append(
                    Knowledge(
                        id=f"doc:{identifier}:{page}:{index}",
                        business_id=business_id,
                        document_id=identifier,
                        document=filename,
                        page=page,
                        language="ar",
                        model=MODELS["ar"],
                        text=chunk,
                        embedding=model.encode(chunk, normalize_embeddings=True).tolist(),
                    )
                )
        with session() as db, db.begin():
            row = db.get(Document, identifier, with_for_update=True)
            if row.status != "indexing" or (row.details or {}).get("claim") != claim:
                return True
            db.execute(
                delete(Knowledge).where(
                    Knowledge.document_id == identifier, Knowledge.business_id == business_id
                )
            )
            db.add_all(evidence)
            row.pages = [{"page": page, "text": content} for page, content in pages]
            row.status, row.error, row.details = "ready", None, {"chunks": len(evidence)}
    except Exception as failure:
        # Provider/model errors can include paths; do not return tokens or response bodies.
        message = (
            str(failure)
            if isinstance(failure, ValueError)
            else ("Indexing failed. Check embedding model availability and retry the upload.")
        )
        with session() as db, db.begin():
            row = db.get(Document, identifier, with_for_update=True)
            if row.status == "indexing" and (row.details or {}).get("claim") == claim:
                row.status, row.error, row.details = "failed", message[:1000], {}
    return True


class ChannelCreate(BaseModel):
    kind: Literal["whatsapp", "messenger", "instagram"]
    name: str = Field(min_length=1, max_length=100)
    account_id: str = Field(pattern=r"^[1-9][0-9]*$")
    inbox_id: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")
    api_token: str = Field(min_length=1, max_length=500)


class ChannelCredentials(BaseModel):
    api_token: str = Field(min_length=1, max_length=500)


@router.patch("/channels/{channel_id}/credentials")
def repair_channel_credentials(
    request: Request, business_id: str, channel_id: str, body: ChannelCredentials
):
    require_operator(request)
    require_business(request, business_id)
    with session() as db, db.begin():
        row = tenant_row(db, Channel, channel_id, business_id)
        secret = secrets.token_urlsafe(32)
        row.credentials = encrypt_credentials(
            {"api_token": body.api_token, "webhook_secret": secret}
        )
        row.webhook_secret_hash = hashlib.sha256(secret.encode()).hexdigest()
        row.live_enabled, row.status = False, "configured"
        row.details = {
            "destinations": (row.details or {}).get("destinations", {}),
            "guidance": "Credentials repaired. Reconnect and verify before activation.",
        }
        return channel_view(row)


@router.get("/channels")
def channels(request: Request, business_id: str):
    require_business(request, business_id)
    with session() as db:
        rows = db.scalars(select(Channel).where(Channel.business_id == business_id)).all()
        return {"channels": [channel_view(row) for row in rows]}


@router.post("/channels", status_code=201)
def create_channel(request: Request, business_id: str, body: ChannelCreate):
    require_operator(request)
    require_business(request, business_id)
    secret = secrets.token_urlsafe(32)
    try:
        with session() as db, db.begin():
            # Serialize account ownership checks, including channels with NULL inbox IDs.
            from sqlalchemy import text

            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"salma-channel-account:{body.account_id}"},
            )
            if db.scalar(
                select(Channel).where(
                    Channel.account_id == body.account_id, Channel.business_id != business_id
                )
            ):
                raise HTTPException(409, "This Chatwoot account belongs to another workspace")
            if body.inbox_id and db.scalar(
                select(Channel).where(
                    Channel.account_id == body.account_id, Channel.inbox_id == body.inbox_id
                )
            ):
                raise HTTPException(409, "This Chatwoot inbox is already connected")
            row = Channel(
                id=str(uuid4()),
                business_id=business_id,
                kind=body.kind,
                name=body.name,
                account_id=body.account_id,
                inbox_id=body.inbox_id,
                credentials=encrypt_credentials(
                    {"api_token": body.api_token, "webhook_secret": secret}
                ),
                webhook_secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
                evolution_instance=None,
                live_enabled=False,
                status="configured",
                details={},
            )
            db.add(row)
            db.flush()
            return channel_view(row)
    except IntegrityError:
        raise HTTPException(409, "This Chatwoot inbox is already connected") from None


def provider_request(provider, method, path, *, token=None, body=None, timeout=30):
    config = settings()
    base = config.evolution_url if provider == "evolution" else config.chatwoot_url
    key = config.evolution_api_key.get_secret_value() if provider == "evolution" else token
    if not base or not key:
        raise HTTPException(503, f"The operator must configure {provider} credentials")
    headers = {"apikey" if provider == "evolution" else "api_access_token": key}
    try:
        response = httpx.request(
            method, base.rstrip("/") + path, headers=headers, json=body, timeout=timeout
        )
        response.raise_for_status()
        return response.json() if response.content else {}
    except httpx.HTTPError, ValueError:
        raise HTTPException(
            502, f"{provider} could not complete the request; check credentials and connection"
        ) from None


def unwrap_list(value):
    # Chatwoot 4.18 wraps webhooks twice but inboxes once; agents/teams and
    # Evolution instances are direct arrays. Never iterate dictionary field names.
    for _ in range(5):
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if not isinstance(value, dict):
            return []
        if "payload" in value:
            value = value["payload"]
        elif "webhooks" in value:
            value = value["webhooks"]
        else:
            return []
    return []


def provider_tools(rows, operator):
    config = settings()
    return {
        "chatwoot": [
            {
                "account_id": account,
                "url": config.chatwoot_public_url.rstrip("/")
                + f"/app/accounts/{account}/dashboard",
            }
            for account in sorted({row.account_id for row in rows if row.account_id.isdigit()})
        ],
        "evolution_url": config.evolution_public_url.rstrip("/") + "/manager" if operator else None,
    }


def social_kind(inbox):
    channel_type = str(inbox.get("channel_type", "")).lower()
    if "instagram" in channel_type:
        return "instagram"
    if "facebook" in channel_type:
        return "messenger"
    return None


@router.get("/social-inboxes")
def social_inboxes(request: Request, business_id: str):
    require_business(request, business_id)
    rate_limit(f"social-inboxes:{business_id}", 60)
    with session() as db:
        channels = db.scalars(select(Channel).where(Channel.business_id == business_id)).all()
        accounts = {}
        for channel in channels:
            if channel.account_id.isdigit():
                accounts.setdefault(channel.account_id, channel)
        linked = {(c.account_id, c.inbox_id) for c in channels}
        result = {"accounts": [], "inboxes": []}
        for account_id, channel in accounts.items():
            account = {
                "account_id": account_id,
                "setup_url": settings().chatwoot_public_url.rstrip("/")
                + f"/app/accounts/{account_id}/settings/inboxes/new",
                "error": None,
            }
            result["accounts"].append(account)
            try:
                credentials = get_channel_credentials(channel)
                payload = provider_request(
                    "chatwoot",
                    "GET",
                    f"/api/v1/accounts/{account_id}/inboxes",
                    token=credentials["api_token"],
                    timeout=8,
                )
                for inbox in unwrap_list(payload):
                    kind = social_kind(inbox)
                    if kind and inbox.get("id"):
                        result["inboxes"].append(
                            {
                                "account_id": account_id,
                                "inbox_id": str(inbox["id"]),
                                "name": str(inbox.get("name") or kind)[:100],
                                "kind": kind,
                                "linked": (account_id, str(inbox["id"])) in linked,
                            }
                        )
            except HTTPException:
                account["error"] = (
                    "Cannot load inboxes. Ask the operator to check "
                    "Chatwoot credentials and connectivity."
                )
        return result


class SocialInboxLink(BaseModel):
    account_id: str = Field(pattern=r"^[1-9][0-9]*$")
    inbox_id: str = Field(pattern=r"^[1-9][0-9]*$")


@router.post("/social-channels", status_code=201)
def link_social_inbox(request: Request, business_id: str, body: SocialInboxLink):
    require_business(request, business_id)
    rate_limit(f"social-link:{business_id}", 30)
    try:
        with session() as db, db.begin():
            from sqlalchemy import text

            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"salma-channel-account:{body.account_id}"},
            )
            bound = db.scalar(
                select(Channel).where(
                    Channel.business_id == business_id, Channel.account_id == body.account_id
                )
            )
            if not bound:
                raise HTTPException(404, "Chatwoot account is not linked to this workspace")
            # Account ownership must remain exclusive even if legacy records conflict.
            if db.scalar(
                select(Channel.id).where(
                    Channel.account_id == body.account_id, Channel.business_id != business_id
                )
            ):
                raise HTTPException(409, "Chatwoot account mapping needs operator repair")
            credentials = get_channel_credentials(bound)
            payload = provider_request(
                "chatwoot",
                "GET",
                f"/api/v1/accounts/{body.account_id}/inboxes",
                token=credentials["api_token"],
                timeout=8,
            )
            inbox = next(
                (i for i in unwrap_list(payload) if str(i.get("id")) == body.inbox_id), None
            )
            if not inbox or not social_kind(inbox):
                raise HTTPException(
                    422,
                    "Choose a Messenger or Instagram inbox from this business's Chatwoot account",
                )
            if db.scalar(
                select(Channel.id).where(
                    Channel.account_id == body.account_id, Channel.inbox_id == body.inbox_id
                )
            ):
                raise HTTPException(409, "This inbox is already linked")
            secret = secrets.token_urlsafe(32)
            row = Channel(
                id=str(uuid4()),
                business_id=business_id,
                kind=social_kind(inbox),
                name=str(inbox.get("name") or social_kind(inbox))[:100],
                account_id=body.account_id,
                inbox_id=body.inbox_id,
                credentials=encrypt_credentials(
                    {"api_token": credentials["api_token"], "webhook_secret": secret}
                ),
                webhook_secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
                live_enabled=False,
                status="ready",
                details={
                    "inbox_verified": True,
                    "webhook_registered": True,
                    "guidance": "Inbox linked. Publish your agent, then activate live replies.",
                },
            )
            db.add(row)
            db.flush()
            register_webhook(row, {"api_token": credentials["api_token"], "webhook_secret": secret})
            return channel_view(row)
    except IntegrityError:
        raise HTTPException(409, "This inbox is already linked") from None


@router.get("/provider-tools")
def provider_tools_access(request: Request, business_id: str):
    require_business(request, business_id)
    with session() as db:
        rows = db.scalars(select(Channel).where(Channel.business_id == business_id)).all()
        return provider_tools(rows, request.state.user.role == "operator")


def inspect_channel_access(channel, *, operator):
    """Read-only, redacted checks of actual provider mappings; never pair or activate."""
    if (
        channel.kind == "whatsapp"
        and (getattr(channel, "details", {}) or {}).get("transport") == "evolution"
    ):
        from salma.evolution_channel import inspect_direct

        return inspect_direct(channel, operator=operator)
    links = provider_tools([channel], operator)
    chatwoot = {
        "status": "unavailable",
        "url": links["chatwoot"][0]["url"],
        "inbox_verified": False,
        "webhook_verified": False,
        "detail": "Cannot verify Chatwoot. Ask the operator to check credentials and connectivity.",
    }
    evolution = {
        "status": "not_configured",
        "url": links["evolution_url"],
        "bridge_verified": False,
        "detail": "No WhatsApp instance linked to this channel.",
    }
    try:
        credentials = get_channel_credentials(channel)
    except HTTPException:
        chatwoot["detail"] = "Channel credentials need repair by the operator."
        return {
            "chatwoot": chatwoot,
            "evolution": evolution,
            "live_enabled": channel.live_enabled,
            "checked_at": now().isoformat(),
        }

    def check_chatwoot():
        try:
            path = f"/api/v1/accounts/{channel.account_id}"
            inboxes = provider_request(
                "chatwoot", "GET", path + "/inboxes", token=credentials["api_token"], timeout=8
            )
            chatwoot["inbox_verified"] = any(
                str(item.get("id")) == channel.inbox_id for item in unwrap_list(inboxes)
            )
            hooks = provider_request(
                "chatwoot", "GET", path + "/webhooks", token=credentials["api_token"], timeout=8
            )
            expected = settings().public_api_url.rstrip("/") + (
                f"/webhooks/channels/{channel.id}/{credentials.get('webhook_secret', '')}"
            )
            chatwoot["webhook_verified"] = any(
                item.get("url") == expected
                and {"message_created", "message_updated"}.issubset(item.get("subscriptions") or [])
                for item in unwrap_list(hooks)
            )
            verified = chatwoot["inbox_verified"] and chatwoot["webhook_verified"]
            chatwoot.update(
                status="connected" if verified else "needs_setup",
                detail="Account, inbox and Salma webhook verified."
                if verified
                else "Chatwoot is reachable; inbox or Salma webhook needs setup.",
            )
        except HTTPException:
            pass

    def check_evolution():
        if channel.kind != "whatsapp" or not channel.evolution_instance:
            return
        evolution.update(
            status="unavailable",
            detail="Cannot verify Evolution. Check API credentials and connectivity.",
        )
        try:
            instance = channel.evolution_instance
            payload = provider_request(
                "evolution", "GET", f"/instance/connectionState/{instance}", timeout=8
            )
            state = payload.get("instance", {}).get("state", "unknown")
            evolution["connection_state"] = state
            bridge = provider_request("evolution", "GET", f"/chatwoot/find/{instance}", timeout=8)
            verified = (
                bridge.get("enabled") is True
                and str(bridge.get("accountId")) == channel.account_id
                and str(bridge.get("url", "")).rstrip("/") == settings().chatwoot_url.rstrip("/")
                and bridge.get("token") == credentials["api_token"]
                and bridge.get("importMessages") is False
            )
            evolution["bridge_verified"] = verified
            evolution.update(
                status=("connected" if state == "open" else "awaiting_pairing")
                if verified
                else "needs_setup",
                detail=(
                    "WhatsApp paired and Chatwoot bridge verified."
                    if state == "open"
                    else "API reachable and bridge verified. Scan the WhatsApp QR code."
                )
                if verified
                else "API reachable; Chatwoot bridge settings need repair.",
            )
        except HTTPException:
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(check_chatwoot), pool.submit(check_evolution)]
        for task in tasks:
            task.result()
    return {
        "chatwoot": chatwoot,
        "evolution": evolution,
        "live_enabled": channel.live_enabled,
        "checked_at": now().isoformat(),
    }


@router.get("/channels/{channel_id}/access")
def channel_access(request: Request, business_id: str, channel_id: str):
    require_business(request, business_id)
    rate_limit("channel-access:" + business_id, 60)
    with session() as db:
        row = tenant_row(db, Channel, channel_id, business_id)
        return inspect_channel_access(row, operator=request.state.user.role == "operator")


def available_destinations(channel):
    credentials = get_channel_credentials(channel)
    path = f"/api/v1/accounts/{channel.account_id}"
    result = {}
    for kind in ("teams", "agents"):
        payload = provider_request(
            "chatwoot", "GET", path + "/" + kind, token=credentials["api_token"]
        )
        result[kind] = [
            {"id": item["id"], "name": item.get("name", "Staff")} for item in unwrap_list(payload)
        ]
    return result


@router.get("/channels/{channel_id}/destinations")
def handoff_destinations(request: Request, business_id: str, channel_id: str):
    require_business(request, business_id)
    with session() as db:
        channel = tenant_row(db, Channel, channel_id, business_id)
        return {
            **available_destinations(channel),
            "destinations": (channel.details or {}).get("destinations", {}),
        }


class DestinationAssignment(BaseModel):
    team_id: int | None = Field(default=None, gt=0)
    assignee_id: int | None = Field(default=None, gt=0)


class DestinationMappings(BaseModel):
    destinations: dict[str, DestinationAssignment] = Field(max_length=50)


@router.patch("/channels/{channel_id}/destinations")
def configure_destinations(
    request: Request, business_id: str, channel_id: str, body: DestinationMappings
):
    require_business(request, business_id)
    with session() as db, db.begin():
        channel = tenant_row(db, Channel, channel_id, business_id)
        available = available_destinations(channel)
        teams = {int(item["id"]) for item in available["teams"]}
        agents = {int(item["id"]) for item in available["agents"]}
        mappings = {}
        for label, assignment in body.destinations.items():
            if (
                not label.strip()
                or len(label) > 100
                or not (assignment.team_id or assignment.assignee_id)
            ):
                raise HTTPException(422, "Each destination needs a name and a team or agent")
            if (assignment.team_id and assignment.team_id not in teams) or (
                assignment.assignee_id and assignment.assignee_id not in agents
            ):
                raise HTTPException(
                    422, "Choose a team or agent in this business's Chatwoot account"
                )
            mappings[label] = assignment.model_dump(exclude_none=True)
        channel.details = {**(channel.details or {}), "destinations": mappings}
        return channel_view(channel)


def verify_inbox(channel, credentials):
    account_path = f"/api/v1/accounts/{channel.account_id}"
    payload = provider_request(
        "chatwoot", "GET", account_path + "/inboxes", token=credentials["api_token"]
    )
    inboxes = unwrap_list(payload)
    matches = [
        item
        for item in inboxes
        if (
            str(item.get("id")) == channel.inbox_id
            if channel.inbox_id
            else item.get("name") == channel.evolution_instance
        )
    ]
    if len(matches) != 1:
        raise HTTPException(
            409, "Chatwoot inbox is not verified. Complete channel setup and try again."
        )
    inbox = matches[0]
    # Never map a manually configured social channel onto an unrelated provider inbox.
    kind = str(inbox.get("channel_type", "")).lower()
    expected = {"messenger": "facebook", "instagram": "instagram"}.get(channel.kind)
    if expected and expected not in kind:
        raise HTTPException(409, f"Choose the {channel.kind} inbox created in Chatwoot")
    return str(inbox["id"])


def register_webhook(channel, credentials):
    base = settings().public_api_url.rstrip("/")
    if not base:
        raise HTTPException(
            503, "The operator must configure PUBLIC_API_URL reachable from Chatwoot"
        )
    url = f"{base}/webhooks/channels/{channel.id}/{credentials['webhook_secret']}"
    path = f"/api/v1/accounts/{channel.account_id}/webhooks"
    existing = provider_request("chatwoot", "GET", path, token=credentials["api_token"])
    if any(item.get("url") == url for item in unwrap_list(existing)):
        return
    provider_request(
        "chatwoot",
        "POST",
        path,
        token=credentials["api_token"],
        body={"url": url, "subscriptions": ["message_created", "message_updated"]},
    )


@router.post("/channels/{channel_id}/connect")
def connect_channel(request: Request, business_id: str, channel_id: str):
    require_business(request, business_id)
    with session() as db:
        existing_channel = tenant_row(db, Channel, channel_id, business_id)
        if existing_channel.kind == "whatsapp":
            from salma.evolution_channel import configure_direct

            return configure_direct(business_id, channel_id)
    with session() as db, db.begin():
        channel = tenant_row(db, Channel, channel_id, business_id)
        credentials = decrypt_credentials(channel.credentials)
        if channel.kind == "whatsapp":
            instance = channel.evolution_instance or "salma-" + channel.id.replace("-", "")
            if not channel.evolution_instance:
                # Save identity before external creation so an uncertain response can be repaired.
                channel.evolution_instance = instance
                channel.status = "connecting"
        else:
            instance = None
    try:
        if instance:
            # fetchInstances is safe to retry when an earlier create timed out.
            existing = provider_request("evolution", "GET", "/instance/fetchInstances")
            if not any(
                (item.get("name") or item.get("instance", {}).get("instanceName")) == instance
                for item in unwrap_list(existing)
            ):
                provider_request(
                    "evolution",
                    "POST",
                    "/instance/create",
                    body={
                        "instanceName": instance,
                        "qrcode": True,
                        "integration": "WHATSAPP-BAILEYS",
                    },
                )
            provider_request(
                "evolution",
                "POST",
                f"/chatwoot/set/{instance}",
                body={
                    "enabled": True,
                    "accountId": channel.account_id,
                    "token": credentials["api_token"],
                    "url": settings().chatwoot_url.rstrip("/"),
                    "signMsg": False,
                    "reopenConversation": True,
                    "conversationPending": False,
                    "nameInbox": instance,
                    "autoCreate": True,
                    "importContacts": False,
                    "importMessages": False,
                    "daysLimitImportMessages": 0,
                },
            )
        inbox_id = verify_inbox(channel, credentials)
        register_webhook(channel, credentials)
        with session() as db, db.begin():
            row = tenant_row(db, Channel, channel_id, business_id)
            if db.scalar(
                select(Channel).where(
                    Channel.account_id == row.account_id,
                    Channel.inbox_id == inbox_id,
                    Channel.id != row.id,
                )
            ):
                raise HTTPException(409, "This inbox is already connected")
            row.inbox_id, row.status = inbox_id, "awaiting_pairing" if instance else "ready"
            row.details = {
                "destinations": (row.details or {}).get("destinations", {}),
                "inbox_verified": True,
                "webhook_registered": True,
                "guidance": "Scan the WhatsApp QR code, then activate."
                if instance
                else "Linked to the configured Chatwoot inbox. Publish settings before activation.",
            }
            return channel_view(row)
    except HTTPException as failure:
        with session() as db, db.begin():
            row = tenant_row(db, Channel, channel_id, business_id)
            row.status = "setup_error"
            row.details = {
                "destinations": (row.details or {}).get("destinations", {}),
                "error": failure.detail,
                "guidance": "Check provider settings, then retry Connect.",
            }
        raise


@router.get("/channels/{channel_id}/qr")
def channel_qr(request: Request, business_id: str, channel_id: str):
    require_business(request, business_id)
    with session() as db:
        row = tenant_row(db, Channel, channel_id, business_id)
        if row.kind != "whatsapp" or not row.evolution_instance:
            raise HTTPException(409, "Connect the WhatsApp channel first")
        payload = provider_request(
            "evolution", "GET", f"/instance/connect/{row.evolution_instance}"
        )
        qr = payload.get("base64") or (payload.get("qrcode") or {}).get("base64")
        if qr and not qr.startswith("data:image/png;base64,"):
            qr = "data:image/png;base64," + qr
        return {"qr_code": qr, "status": "scan_qr" if qr else "refresh_status"}


@router.get("/channels/{channel_id}/status")
def channel_status(request: Request, business_id: str, channel_id: str):
    require_business(request, business_id)
    with session() as db, db.begin():
        row = tenant_row(db, Channel, channel_id, business_id)
        if row.kind == "whatsapp" and row.evolution_instance:
            payload = provider_request(
                "evolution", "GET", f"/instance/connectionState/{row.evolution_instance}"
            )
            state = payload.get("instance", {}).get("state", "unknown")
            row.status = "ready" if state == "open" else "awaiting_pairing"
            row.details = {**(row.details or {}), "connection_state": state}
        return channel_view(row)


class Activation(BaseModel):
    enabled: bool


@router.post("/channels/{channel_id}/activate")
def activate_channel(request: Request, business_id: str, channel_id: str, body: Activation):
    require_business(request, business_id)
    with session() as db, db.begin():
        row = tenant_row(db, Channel, channel_id, business_id)
        if body.enabled:
            if not db.get(Business, business_id).published_version:
                raise HTTPException(409, "Publish the business configuration before activation")
            direct = row.kind == "whatsapp" and (row.details or {}).get("transport") == "evolution"
            credentials = decrypt_credentials(row.credentials)
            if not (row.details or {}).get("webhook_registered") or (
                not direct and not row.inbox_id
            ):
                raise HTTPException(409, "Complete and verify channel setup before activation")
            if not direct:
                verify_inbox(row, credentials)
            if row.kind == "whatsapp":
                payload = provider_request(
                    "evolution", "GET", f"/instance/connectionState/{row.evolution_instance}"
                )
                if payload.get("instance", {}).get("state") != "open":
                    raise HTTPException(
                        409, "Scan the QR code and connect WhatsApp before activation"
                    )
        row.live_enabled = body.enabled
        return channel_view(row)


def resolve_channel_webhook(channel_id, supplied_secret, *, allow_paused=False):
    """Only a verified mapping, never payload text, determines the tenant."""
    with session() as db:
        row = db.get(Channel, channel_id)
        supplied_hash = hashlib.sha256(supplied_secret.encode()).hexdigest()
        if not row or not hmac.compare_digest(row.webhook_secret_hash, supplied_hash):
            raise HTTPException(401, "Invalid webhook authentication")
        if not row.live_enabled and not allow_paused:
            raise HTTPException(503, "Channel automation is paused")
        return row


def notify_handoff(channel, conversation, summary, destination):
    """One best-effort private staff notification; caller durably records its result.

    Never retry uncertain provider responses here: a note or assignment may already
    have been accepted. Ownership remains human regardless of notification outcome.
    """
    result = {"note": "failed", "assignment": "unconfigured"}
    try:
        credentials = get_channel_credentials(channel)
    except HTTPException:
        return result
    path = f"/api/v1/accounts/{channel.account_id}/conversations/{conversation.external_id}"
    try:
        provider_request(
            "chatwoot",
            "POST",
            path + "/messages",
            token=credentials["api_token"],
            body={"content": str(summary)[:10000], "message_type": "outgoing", "private": True},
        )
        result["note"] = "accepted"
    except HTTPException:
        pass
    configured = (channel.details or {}).get("destinations", {}).get(destination, {})
    assignment = {
        key: value
        for key, value in configured.items()
        if key in {"team_id", "assignee_id"} and isinstance(value, int) and value > 0
    }
    if assignment:
        result["assignment"] = "failed"
        try:
            provider_request(
                "chatwoot",
                "POST",
                path + "/assignments",
                token=credentials["api_token"],
                body=assignment,
            )
            result["assignment"] = "accepted"
        except HTTPException:
            pass
    return result
