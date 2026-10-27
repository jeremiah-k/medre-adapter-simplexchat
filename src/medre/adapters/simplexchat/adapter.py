"""Durable group ingress and explicit native local-queue handoff semantics."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
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
from typing import Any

from medre.core.ingress import IngressProvenance
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
        self,
        config: SimplexChatConfig,
        *,
        session_factory: Callable[[SimplexChatConfig], SimplexChatSession] = (
            SimplexChatSession
        ),
    ) -> None:
        super().__init__()
        self.config = config.validate()
        self.adapter_id = config.adapter_id
        self._capabilities = replace(CAPABILITIES, max_text_bytes=config.max_text_bytes)
        self.ctx: AdapterContext | None = None
        self.session: SimplexChatSession | None = None
        self.codec: SimplexChatCodec | None = None
        self.ledger: HandoffLedger | None = None
        self.session_factory: Callable[[SimplexChatConfig], SimplexChatSession] = (
            session_factory
        )
        self.task: asyncio.Task[None] | None = None
        self.channels = set(config.chats)
        self.inbox: InboxLedger | None = None
        self.state = "unknown"
        self._health_epoch = 0
        self._health_active = False
        self._reported_health: str | None = None
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
        load = ctx.load_checkpoint
        if ctx.admit_inbound is None or load is None or ctx.commit_checkpoint is None:
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
        self._health_epoch += 1
        health_epoch = self._health_epoch
        self._health_active = False
        self._reported_health = None
        self.ctx = ctx
        self._mark_started(ctx)
        self._set_state("starting")
        session = self.session_factory(self.config)
        self.session = session
        try:
            await session.start()
            lease = session.lease
            if lease is None:
                raise SessionError("SimpleX profile lease is unavailable")
            self.codec = SimplexChatCodec(self.config, lease.namespace)
            self.ledger = HandoffLedger(Path(self.config.state_dir) / "handoffs.sqlite")
            self.inbox = InboxLedger(
                Path(self.config.state_dir) / "inbox.sqlite", self.codec.profile
            )
            inbox = self.inbox
            for channel in self.config.chats:
                checkpoint = await load(self._stream(channel))
                initialized = inbox.initialized(channel)
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
                before: int | None = None
                anchors: set[int] = set()
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
                            if not inbox.contains(channel, row["meta"]["itemId"])
                        ]
                        if missed:
                            missed_pages.append(before)
                    else:
                        # On first use the existing history is a baseline, not
                        # an ingress replay. Batch its durable ID writes.
                        inbox.admitted_many(
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
                    inbox.admitted_many(channel, admitted)
                inbox.initialize(channel)
                await self._checkpoint(channel)
            await session.call("start")
            self._health_active = True
            self._set_state("healthy")
            self.task = asyncio.create_task(self._run())
            self.task.add_done_callback(
                lambda task: self._native_task_done(task, health_epoch)
            )
        except BaseException:
            await self.stop()
            self._set_state("failed")
            raise

    def _stream(self, channel: str) -> str:
        return f"simplexchat:{self.config.user_id}:{channel}"

    async def _checkpoint(self, channel: str) -> None:
        ctx = self.ctx
        codec = self.codec
        if ctx is None or codec is None or ctx.commit_checkpoint is None:
            raise AdapterPermanentError("SimpleX checkpoints are unavailable")
        await ctx.commit_checkpoint(
            self._stream(channel),
            "initialized",
            json.dumps({"profile_id": codec.profile, "mode": "retained_ids_v1"}),
        )

    def _validate_conversation(self, info: Mapping[str, Any], channel: str) -> None:
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

    async def _history(
        self, channel: str, before: int | None, *, count: int = 64
    ) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
        kind, number = self.config.destination(channel)
        session = self.session
        if session is None:
            raise SessionError("SimpleX session is unavailable")
        result = await session.call(
            "history", chat_type=kind, chat_id=number, before=before, count=count
        )
        if (
            not isinstance(result, Mapping)
            or not isinstance(result.get("chatInfo"), Mapping)
            or not isinstance(result.get("chatItems"), list)
        ):
            raise AdapterPermanentError("Malformed SimpleX history response")
        # Every surviving row is a Mapping: the ID validation below rejects
        # any page whose row count diverges from the Mapping-filtered IDs.
        rows: list[Mapping[str, Any]] = result["chatItems"]
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
        chat_info: Mapping[str, Any] = result["chatInfo"]
        return chat_info, rows

    async def _ingest(
        self,
        info: Mapping[str, Any],
        row: Mapping[str, Any],
        provenance: IngressProvenance,
        *,
        checkpoint: bool = True,
    ) -> int | None:
        codec = self.codec
        inbox = self.inbox
        ctx = self.ctx
        if codec is None or inbox is None or ctx is None or ctx.admit_inbound is None:
            raise AdapterPermanentError("SimpleX ingress is not started")
        channel = chat_channel(info)
        if channel not in self.channels:
            return None
        self._validate_conversation(info, channel)
        meta = row.get("meta")
        item_id = meta.get("itemId") if isinstance(meta, Mapping) else None
        if type(item_id) is not int or item_id <= 0:
            raise AdapterPermanentError("Malformed SimpleX event item ID")
        if inbox.contains(channel, item_id):
            return None
        if event := codec.decode(info, row):
            await ctx.admit_inbound(event, provenance)
        # If this write fails, core admission deduplicates the stable UUID
        # on retry. No admitted ID is recorded before successful admission.
        if checkpoint:
            inbox.admitted(channel, item_id)
        return item_id

    async def _run(self) -> None:
        epoch = self._health_epoch
        session = self.session
        if session is None:
            raise SessionError("SimpleX session is unavailable")
        pending: list[Any] = []
        while True:
            session.wake.clear()
            try:
                if not pending:
                    pending = await session.call("events")
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
                self._set_state("healthy", epoch=epoch)
            except (SessionError, AdapterPermanentError):
                self._set_state("failed", epoch=epoch)
                await session.stop()
                return
            except asyncio.CancelledError:
                if self._health_active:
                    self._set_state("failed", epoch=epoch)
                raise
            except Exception:
                self.admission_failures += 1
                self._set_state("degraded", epoch=epoch)
            try:
                await asyncio.wait_for(
                    session.wake.wait(), self.config.poll_interval_seconds
                )
            except TimeoutError:
                pass

    async def deliver(self, result: RenderingResult) -> AdapterHandoffResult:
        kind, number, text = validate_payload(result, self.config)
        # validate_payload admits only configured conversation channels, so
        # the target is a concrete channel string from this point on.
        channel = result.target_channel
        if channel is None:
            raise AdapterPermanentError("Unconfigured SimpleX conversation")
        codec = self.codec
        if (
            self.state not in {"healthy", "degraded"}
            or self.session is None
            or self.ledger is None
            or codec is None
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
            reserved, native_id = self.ledger.reserve(provenance, channel, text)
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
                native_id = codec.reference(channel, item_id)
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
            note=(
                "Persisted in the native SimpleX queue; remote receipt is not inferred"
                if reserved
                else "Reused an earlier persisted native handoff; no new text was submitted"
            ),
        )

    async def stop(self, timeout: float = 5.0) -> None:
        self._health_active = False
        self._health_epoch += 1
        self._reported_health = None
        # Even cancellation of task gathering must not readmit native sends.
        self._set_state("failed")
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.session is not None:
            try:
                await self.session.stop()
            except BaseException:
                self._set_state("failed")
                raise
            self.session = None
        if self.inbox is not None:
            self.inbox.close()
            self.inbox = None
        if self.ledger is not None:
            self.ledger.close()
            self.ledger = None
        self._set_state("unknown")

    def _native_task_done(self, task: asyncio.Task[None], epoch: int) -> None:
        """Observe unexpected native-owner exit, including cancellation before first execution."""
        if not self._health_active or epoch != self._health_epoch:
            return
        if task.cancelled() or task.exception() is not None:
            self._set_state("failed", epoch=epoch)

    def _set_state(self, state: str, *, epoch: int | None = None) -> None:
        """Publish existing native-owner observations without adding polling or send authority."""
        if epoch is not None and epoch != self._health_epoch:
            return
        self.state = state
        if (
            not self._health_active
            or self.ctx is None
            or state not in {"healthy", "degraded", "failed"}
        ):
            return
        report = getattr(self.ctx, "report_health", None)
        if report is None or self._reported_health == state:
            return
        try:
            report(self._health_info())
            self._reported_health = state
        except Exception as exc:
            self.ctx.logger.warning(
                "simplexchat health notification failed (%s)", type(exc).__name__
            )

    async def health_check(self) -> AdapterInfo:
        """Read the native owner's retained local health without another native call."""
        return self._health_info()

    def _health_info(self) -> AdapterInfo:
        """Use the same native-owner state for explicit probes and reports."""
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
