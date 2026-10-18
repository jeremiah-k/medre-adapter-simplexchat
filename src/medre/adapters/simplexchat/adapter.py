"""Durable group ingress and explicit native local-queue handoff semantics."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from medre.core.contracts.adapter import (
    AdapterCapabilities,
    AdapterContext,
    AdapterContract,
    AdapterHandoffResult,
    AdapterInfo,
    AdapterPermanentError,
    AdapterRole,
    AdapterSendError,
)
from medre.core.rendering.renderer import RenderingResult

from .codec import SimplexChatCodec, chat_channel
from .config import SimplexChatConfig, plain_text
from .session import SessionError, SessionUnavailable, SimplexChatSession
from .state import HandoffLedger, InboxLedger

CAPABILITIES = AdapterCapabilities(
    text=True,
    replies="fallback",
    threads="fallback",
    reactions="unsupported",
    edits="unsupported",
    deletes="unsupported",
    attachments=False,
    metadata_fields=False,
    store_and_forward=True,
    direct_messages=True,
    channels=True,
    identity_encryption=True,
    presence=False,
    topic_rooms=True,
)


def validate_payload(
    result: RenderingResult, config: SimplexChatConfig
) -> tuple[str, int, str]:
    payload = result.payload
    if result.target_adapter != config.adapter_id or not isinstance(payload, Mapping):
        raise AdapterPermanentError("Invalid SimpleX delivery target")
    try:
        kind, number = config.destination(result.target_channel)
    except ValueError:
        raise AdapterPermanentError("Unconfigured SimpleX conversation") from None
    text = payload.get("text")
    if (
        set(payload) != {"chat_type", "chat_id", "text"}
        or payload["chat_type"] != kind
        or type(payload["chat_id"]) is not int
        or payload["chat_id"] != number
        or not plain_text(text)
        or not text.strip()
        or len(text.encode()) > config.max_text_bytes
    ):
        raise AdapterPermanentError("Invalid SimpleX addressing or rendered text")
    return kind, number, text


class SimplexChatAdapter(AdapterContract):
    platform = "simplexchat"
    role = AdapterRole.HYBRID

    def __init__(
        self, config: SimplexChatConfig, *, session_factory=SimplexChatSession
    ) -> None:
        super().__init__()
        self.config = config.validate()
        self.adapter_id = config.adapter_id
        self._capabilities = replace(CAPABILITIES, max_text_bytes=config.max_text_bytes)
        self.ctx = None
        self.session = None
        self.codec = None
        self.ledger = None
        self.session_factory = session_factory
        self.task = None
        self.channels = set(config.chats)
        self.inbox = None
        self.state = "unknown"
        self.admission_failures = 0

    async def start(self, ctx: AdapterContext) -> None:
        if self.session is not None:
            raise RuntimeError("SimpleX adapter is started")
        if (
            not self.config.database_prefix
            or not self.config.state_dir
            or not self.config.chats
        ):
            raise AdapterPermanentError(
                "Configure a SimpleX profile, state directory, and conversations"
            )
        if not all((ctx.admit_inbound, ctx.load_checkpoint, ctx.commit_checkpoint)):
            raise AdapterPermanentError(
                "SimpleX requires durable admission and checkpoint callbacks"
            )
        if not await asyncio.to_thread(
            Path(self.config.database_prefix + "_chat.db").is_file
        ):
            raise AdapterPermanentError(
                "SimpleX profile is missing; run medre-simplexchat init explicitly"
            )
        if ctx.adapter_id != self.adapter_id:
            raise AdapterPermanentError("SimpleX context adapter identity differs")
        self.ctx = ctx
        self._mark_started(ctx)
        self.state = "starting"
        self.session = self.session_factory(self.config)
        try:
            await self.session.start()
            self.codec = SimplexChatCodec(self.config, self.session.lease.namespace)
            self.ledger = HandoffLedger(Path(self.config.state_dir) / "handoffs.sqlite")
            self.inbox = InboxLedger(
                Path(self.config.state_dir) / "inbox.sqlite", self.codec.profile
            )
            for channel in self.config.chats:
                checkpoint = await ctx.load_checkpoint(self._stream(channel))
                initialized = self.inbox.initialized(channel)
                if checkpoint is not None:
                    metadata = json.loads(checkpoint.metadata_json)
                    if (
                        metadata
                        != {"profile_id": self.codec.profile, "mode": "retained_ids_v1"}
                        or checkpoint.cursor != "initialized"
                        or not initialized
                    ):
                        raise AdapterPermanentError(
                            "SimpleX checkpoint or inbox differs from the native profile"
                        )
                # The official secondary controller enables history queries
                # without subscribing chats. Retained IDs also deduplicate any
                # native events interleaved with timestamp-ordered pagination.
                before, anchors = None, set()
                missed_pages: list[int | None] = []
                while True:
                    info, rows = await self._history(channel, before)
                    self._validate_conversation(info, channel)
                    if initialized:
                        # Native history is chronological within a page, but
                        # pagination returns newest pages first. Preserve the
                        # cross-page order of recovered admissions.
                        missed = [
                            row
                            for row in rows
                            if not self.inbox.contains(channel, row["meta"]["itemId"])
                        ]
                        if missed:
                            missed_pages.append(before)
                    else:
                        # On first use the existing history is a baseline, not
                        # an ingress replay. Batch its durable ID writes.
                        self.inbox.admitted_many(
                            channel, [row["meta"]["itemId"] for row in rows]
                        )
                    if len(rows) < 64:
                        break
                    before = rows[0]["meta"]["itemId"]
                    if before in anchors:
                        raise AdapterPermanentError(
                            "SimpleX history pagination did not advance"
                        )
                    anchors.add(before)
                # Do not stop at a fully retained page: backdated events may
                # exist in older pages with higher native IDs.
                for anchor in reversed(missed_pages):
                    # Retain only page anchors, not the entire offline payload
                    # history. Re-read one bounded page for oldest-first ingress.
                    page_info, missed = await self._history(channel, anchor)
                    self._validate_conversation(page_info, channel)
                    admitted = []
                    for row in missed:
                        item_id = await self._ingest(
                            page_info, row, "recovered", checkpoint=False
                        )
                        if item_id is not None:
                            admitted.append(item_id)
                    self.inbox.admitted_many(channel, admitted)
                self.inbox.initialize(channel)
                await self._checkpoint(channel)
            await self.session.call("start")
            self.state = "healthy"
            self.task = asyncio.create_task(self._run())
        except BaseException:
            await self.stop()
            self.state = "failed"
            raise

    def _stream(self, channel: str) -> str:
        return f"simplexchat:{self.config.user_id}:{channel}"

    async def _checkpoint(self, channel: str) -> None:
        await self.ctx.commit_checkpoint(
            self._stream(channel),
            "initialized",
            json.dumps({"profile_id": self.codec.profile, "mode": "retained_ids_v1"}),
        )

    def _validate_conversation(self, info: Mapping, channel: str) -> None:
        if chat_channel(info) != channel:
            raise AdapterPermanentError(
                "SimpleX history returned a different conversation"
            )
        if info["type"] == "group":
            group = info["groupInfo"]
            membership = group.get("membership")
            scope = info.get("groupChatScope")
            if (
                not isinstance(membership, Mapping)
                or membership.get("memberStatus")
                not in {
                    "creator",
                    "connected",
                    "complete",
                }
                or (
                    scope is not None
                    and (not isinstance(scope, Mapping) or scope.get("type") != "main")
                )
            ):
                raise AdapterPermanentError(
                    "SimpleX requires an active ordinary group conversation"
                )
        elif (
            info["contact"].get("chatDeleted")
            or info["contact"].get("contactStatus") != "active"
        ):
            raise AdapterPermanentError("SimpleX contact is not active")

    async def _history(self, channel: str, before: int | None, *, count: int = 64):
        kind, number = self.config.destination(channel)
        result = await self.session.call(
            "history", chat_type=kind, chat_id=number, before=before, count=count
        )
        if (
            not isinstance(result, Mapping)
            or not isinstance(result.get("chatInfo"), Mapping)
            or not isinstance(result.get("chatItems"), list)
        ):
            raise AdapterPermanentError("Malformed SimpleX history response")
        rows = result["chatItems"]
        ids = [
            (
                row["meta"].get("itemId")
                if isinstance(row.get("meta"), Mapping)
                else None
            )
            for row in rows
            if isinstance(row, Mapping)
        ]
        if (
            len(ids) != len(rows)
            or any(type(number) is not int or number <= 0 for number in ids)
            or len(set(ids)) != len(ids)
            or len(rows) > count
        ):
            raise AdapterPermanentError("Malformed SimpleX history item IDs")
        return result["chatInfo"], rows

    async def _ingest(
        self, info: Mapping, row: Mapping, provenance: str, *, checkpoint: bool = True
    ) -> int | None:
        channel = chat_channel(info)
        if channel not in self.channels:
            return
        self._validate_conversation(info, channel)
        meta = row.get("meta")
        item_id = meta.get("itemId") if isinstance(meta, Mapping) else None
        if type(item_id) is not int or item_id <= 0:
            raise AdapterPermanentError("Malformed SimpleX event item ID")
        if self.inbox.contains(channel, item_id):
            return
        if event := self.codec.decode(info, row):
            await self.ctx.admit_inbound(event, provenance)
        # If this write fails, core admission deduplicates the stable UUID
        # on retry. No admitted ID is recorded before successful admission.
        if checkpoint:
            self.inbox.admitted(channel, item_id)
        return item_id

    async def _run(self) -> None:
        pending = []
        while True:
            self.session.wake.clear()
            try:
                if not pending:
                    pending = await self.session.call("events")
                    if not isinstance(pending, list):
                        raise AdapterPermanentError("Malformed SimpleX event batch")
                while pending:
                    record = pending[0]
                    if (
                        not isinstance(record, Mapping)
                        or not isinstance(record.get("chatInfo"), Mapping)
                        or not isinstance(record.get("chatItem"), Mapping)
                    ):
                        raise AdapterPermanentError("Malformed SimpleX event record")
                    await self._ingest(record["chatInfo"], record["chatItem"], "live")
                    pending.pop(0)
                self.state = "healthy"
            except (SessionError, AdapterPermanentError):
                self.state = "failed"
                await self.session.stop()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                self.admission_failures += 1
                self.state = "degraded"
            try:
                await asyncio.wait_for(
                    self.session.wake.wait(), self.config.poll_interval_seconds
                )
            except TimeoutError:
                pass

    async def deliver(self, result: RenderingResult) -> AdapterHandoffResult:
        kind, number, text = validate_payload(result, self.config)
        if (
            self.state not in {"healthy", "degraded"}
            or self.session is None
            or self.ledger is None
        ):
            raise AdapterSendError("SimpleX adapter is not available")
        provenance = result.attempt_provenance
        if (
            provenance is None
            or provenance.target_adapter != self.adapter_id
            or provenance.target_channel != result.target_channel
            or provenance.event_id != result.event_id
        ):
            raise AdapterPermanentError(
                "SimpleX requires matching delivery attempt provenance"
            )
        try:
            reserved, native_id = self.ledger.reserve(
                provenance, result.target_channel, text
            )
            if not reserved and native_id is None:
                raise ValueError(
                    "SimpleX handoff outcome is uncertain; inspect the native profile before retrying"
                )
            if reserved:
                records = await self.session.call(
                    "send", chat_type=kind, chat_id=number, text=text
                )
                if (
                    not isinstance(records, list)
                    or len(records) != 1
                    or chat_channel(records[0].get("chatInfo", {}))
                    != result.target_channel
                ):
                    raise ValueError(
                        "SimpleX returned an unexpected native send result"
                    )
                item = records[0].get("chatItem", {})
                item_id = item.get("meta", {}).get("itemId")
                if (
                    type(item_id) is not int
                    or item_id <= 0
                    or item.get("chatDir", {}).get("type")
                    != ("groupSnd" if kind == "group" else "directSnd")
                    or item.get("content", {}).get("type") != "sndMsgContent"
                    or item.get("content", {}).get("msgContent", {}).get("type")
                    != "text"
                    or item.get("content", {}).get("msgContent", {}).get("text") != text
                ):
                    raise ValueError("SimpleX native send identity or body differs")
                native_id = self.codec.reference(result.target_channel, item_id)
                self.ledger.accepted(provenance, native_id)
        except asyncio.CancelledError:
            # Cancellation after reserving may have reached the native queue.
            raise
        except SessionUnavailable:
            # call() refused before writing to the worker. A retry is safe;
            # do not strand this attempt in the handoff ledger.
            self.ledger.release(provenance)
            raise AdapterSendError(
                "SimpleX session was unavailable before send"
            ) from None
        except Exception:
            # Rejections returned by the SDK are NOT proven unsent: the native
            # API may have enqueued before reporting an error. Fail closed.
            raise AdapterPermanentError(
                "SimpleX handoff is uncertain or invalid; inspect the native profile before retrying"
            ) from None
        return AdapterHandoffResult(
            native_message_id=native_id,
            native_channel_id=result.target_channel,
            confirmation_level="local_queue",
            note="Persisted in the native SimpleX queue; remote receipt is not inferred",
        )

    async def stop(self, timeout: float = 5.0) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.session is not None:
            await self.session.stop()
            self.session = None
        if self.inbox is not None:
            self.inbox.close()
            self.inbox = None
        if self.ledger is not None:
            self.ledger.close()
            self.ledger = None
        self.state = "unknown"

    async def health_check(self) -> AdapterInfo:
        return AdapterInfo(
            self.adapter_id,
            self.platform,
            self.role,
            "0.1.0",
            self._capabilities,
            health=self.state,
        )

    def diagnostics(self) -> dict[str, object]:
        return {
            "adapter_id": self.adapter_id,
            "health": self.state,
            "health_scope": "native_profile",
            "peer_reachability": "not_inferred",
            "configured_conversations": len(self.config.chats),
            "admission_failures": self.admission_failures,
        }
