"""Chatlytics action surface — every method is a thin POST to /api/v1/actions.

Phase 168 Half B. Mixed into ChatlyticsAdapter. Keeping this in a sidecar
keeps adapter.py focused on lifecycle/inbound while letting IDEs and tests
navigate the action vocabulary as a flat surface.

Hard rule: this module NEVER calls WAHA endpoints. All I/O goes through the
Chatlytics gateway via the inherited ``self._client`` and ``_action`` helper.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from gateway.platforms.base import SendResult

logger = logging.getLogger(__name__)

# Valid JID suffixes Chatlytics accepts. ``@s.whatsapp.net`` is the legacy
# form WAHA emits in some envelopes; we accept it on input but normalize
# device suffix only — never rewrite the domain.
_VALID_JID_SUFFIXES = (
    "@c.us",
    "@g.us",
    "@lid",
    "@newsletter",
    "@s.whatsapp.net",
    "@broadcast",
)

# Action names that exist in src/action-catalog.ts but have no public
# adapter wrapper. Each must justify its absence — admin-only, not yet
# wired in Half A, or covered by a different surface.
INTENTIONALLY_UNMAPPED = frozenset({
    # API key CRUD — admin/operator surface, never exposed to LLM.
    "createApiKey", "getApiKeys", "updateApiKey", "deleteApiKey",
    # Policy edit — operator-driven, requires actorId; not safe as LLM tool.
    "editPolicy",
    # Standard-action duplicates of message-ops methods (gateway-recognized
    # short names). We map them via the long names: editMessage / pinMessage
    # etc. — see IMPLEMENTED_ACTIONS. The short names live in the gateway
    # MESSAGE_ACTION_TARGET_MODE map, NOT in ACTION_HANDLERS, so they
    # shouldn't surface here anyway — listed defensively.
    "edit", "unsend", "pin", "unpin", "read", "delete", "reply",
    # Channel search browse views — niche, exposed via raw _action() if needed.
    "searchChannelsByView", "getChannelSearchViews",
    "getChannelSearchCountries", "getChannelSearchCategories",
    # Helper actions for utility plumbing.
    "resolveTarget",  # exposed via search() instead.
    "getNewMessageId", "getGroupJoinInfo", "refreshGroups",
    "convertVoice", "convertVideo",
    "createOrUpdateContact", "checkContactExists",
    "getChatPicture", "getGroupPicture", "deleteGroupPicture",
    "deleteGroup", "deleteChannel", "createChannel", "getChannel",
    "getGroupsCount", "getParticipants",
    "setInfoAdminOnly", "getInfoAdminOnly",
    "setMessagesAdminOnly", "getMessagesAdminOnly",
    "demoteToMember",  # alias of demoteFromAdmin (we expose demote_admin).
    "sendButtonsReply", "sendList", "sendLinkPreview", "sendEvent",
    "sendPollVote",
    "getChatsOverview", "getChatMessages", "getChatMessage",
    "getMessageById",
    "setPresenceStatus",  # alias of setPresence; we map setPresence.
    "sendMulti",  # multi-recipient — niche, exposed via raw _action().
    # Aliases of clearChatMessages / readChatMessages — handler map
    # contains both. We expose the long form via clear_chat / mark_read.
    "clearMessages",
    # readMessages is a slim-format helper (returns 6-field array). Niche
    # for LLM context-loading — exposed via raw _action() if needed; not
    # a first-class wrapper.
    "readMessages",
})


class ChatlyticsActionsMixin:
    """Action wrappers mixed into :class:`ChatlyticsAdapter`.

    Every public method below funnels through :meth:`_action`. Validation
    of JID/messageId shape happens at the wrapper boundary so a malformed
    LLM tool call surfaces as ``ValueError`` before hitting the network.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_connected(self) -> None:
        if getattr(self, "_client", None) is None:
            raise RuntimeError("ChatlyticsAdapter not connected")

    def _validate_chat_id(self, chat_id: str) -> str:
        if not isinstance(chat_id, str) or not chat_id:
            raise ValueError("chat_id must be a non-empty string")
        if not any(chat_id.endswith(suf) for suf in _VALID_JID_SUFFIXES):
            raise ValueError(
                f"chat_id has unknown JID suffix: {chat_id!r} "
                f"(expected one of {_VALID_JID_SUFFIXES})"
            )
        # `@lid` JIDs DO NOT carry the `:N` device suffix in practice — but
        # the strip is idempotent on LIDs since ":" never appears in the
        # local part. Safe to apply unconditionally.
        return self._strip_device_suffix(chat_id)  # type: ignore[attr-defined]

    def _validate_group_id(self, group_id: str) -> str:
        if not isinstance(group_id, str) or not group_id.endswith("@g.us"):
            raise ValueError(f"group_id must end with @g.us, got: {group_id!r}")
        return group_id

    def _validate_contact_id(self, contact_id: str) -> str:
        if not isinstance(contact_id, str) or not contact_id:
            raise ValueError("contact_id must be a non-empty string")
        if not (contact_id.endswith("@c.us") or contact_id.endswith("@lid")
                or contact_id.endswith("@s.whatsapp.net")):
            raise ValueError(
                f"contact_id must end with @c.us, @lid, or @s.whatsapp.net, "
                f"got: {contact_id!r}"
            )
        return self._strip_device_suffix(contact_id)  # type: ignore[attr-defined]

    def _validate_channel_id(self, channel_id: str) -> str:
        if not isinstance(channel_id, str) or not channel_id:
            raise ValueError("channel_id must be a non-empty string")
        return channel_id

    def _validate_message_id(self, message_id: str) -> str:
        # WAHA message IDs are formatted ``true_chatId_shortId`` (or
        # ``false_...`` for inbound). The catalog's react/forward handlers
        # require this shape — silently accepting a bare shortId would
        # reach WAHA and fail at the engine. Reject early.
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("message_id must be a non-empty string")
        parts = message_id.split("_")
        if len(parts) < 3 or parts[0] not in ("true", "false"):
            raise ValueError(
                "message_id must be in WAHA format "
                "'true_chatId_shortId' or 'false_chatId_shortId', "
                f"got: {message_id!r}"
            )
        return message_id

    async def _action(
        self,
        name: str,
        params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        self._check_connected()
        body: Dict[str, Any] = {"action": name, "params": params or {}}
        session = getattr(self, "_default_session", None)
        if session:
            body["session"] = session
        client = getattr(self, "_client")
        # Use full path; httpx already has base_url but we pass Headers
        # explicitly so the parity test catches any auth-header drift.
        headers = self._auth_headers()  # type: ignore[attr-defined]
        if extra_headers:
            # Caller-controlled headers take precedence — currently only
            # X-In-Reply-To (phase 168.5 Half B).
            headers = {**headers, **extra_headers}
        resp = await client.post(
            "/api/v1/actions",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text}
        return data if isinstance(data, dict) else {"result": data}

    async def _action_send_result(
        self,
        name: str,
        params: Dict[str, Any],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> SendResult:
        try:
            data = await self._action(name, params, extra_headers=extra_headers)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            return SendResult(
                success=False,
                error=f"HTTP {status}: {exc}",
                retryable=status >= 500 or status == 429,
            )
        except httpx.HTTPError as exc:
            return SendResult(success=False, error=str(exc), retryable=True)
        message_id = (
            data.get("messageId") if isinstance(data, dict) else None
        ) or (data.get("id") if isinstance(data, dict) else None)
        return SendResult(
            success=True,
            message_id=str(message_id) if message_id else None,
            raw_response=data,
        )

    # ------------------------------------------------------------------
    # Outbound — media & rich content (sendImage/Video/etc. already exist
    # on the adapter from phase 166; we only ADD missing ones here).
    # ------------------------------------------------------------------

    def _resolve_in_reply_to_for(
        self,
        chat_id: str,
        metadata: Optional[Dict[str, Any]],
        explicit: Optional[str],
    ) -> Optional[Dict[str, str]]:
        # Phase 168.5 Half B — bridges actions.py wrappers to the
        # _take_in_reply_to() machinery on the host adapter (adapter.py).
        # Returns None when no reply context exists so the caller can pass
        # ``extra_headers=None`` and not perturb the auth-header set.
        take = getattr(self, "_take_in_reply_to", None)
        if take is None:
            return None
        mid = take(chat_id, metadata, explicit)  # type: ignore[misc]
        return {"X-In-Reply-To": mid} if mid else None

    async def send_sticker(
        self,
        chat_id: str,
        file: str,
        metadata: Optional[Dict[str, Any]] = None,
        in_reply_to: Optional[str] = None,
    ) -> SendResult:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action_send_result(
            "sendImage",  # Chatlytics routes stickers through sendImage with
                          # MIME-detected webp; no first-class sendSticker action
                          # exists yet. See INTENTIONALLY_UNMAPPED note.
            {"chatId": chat_id, "file": file},
            extra_headers=self._resolve_in_reply_to_for(chat_id, metadata, in_reply_to),
        )

    async def send_location(
        self,
        chat_id: str,
        latitude: float,
        longitude: float,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        in_reply_to: Optional[str] = None,
    ) -> SendResult:
        chat_id = self._validate_chat_id(chat_id)
        params: Dict[str, Any] = {
            "chatId": chat_id,
            "latitude": float(latitude),
            "longitude": float(longitude),
        }
        if name:
            params["title"] = name
        return await self._action_send_result(
            "sendLocation",
            params,
            extra_headers=self._resolve_in_reply_to_for(chat_id, metadata, in_reply_to),
        )

    async def send_contact(
        self,
        chat_id: str,
        contact_id: str,
        name: str,
        metadata: Optional[Dict[str, Any]] = None,
        in_reply_to: Optional[str] = None,
    ) -> SendResult:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action_send_result(
            "sendContactVcard",
            {
                "chatId": chat_id,
                "contacts": [{"phoneNumber": contact_id, "fullName": name}],
            },
            extra_headers=self._resolve_in_reply_to_for(chat_id, metadata, in_reply_to),
        )

    async def send_poll(
        self,
        chat_id: str,
        name: str,
        options: List[str],
        multiple: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        in_reply_to: Optional[str] = None,
    ) -> SendResult:
        # Per CLAUDE.md: WAHA's sendPoll expects a `poll: {}` wrapper. The
        # Chatlytics handler in src/channel.ts:223 forwards the flat fields
        # but we keep the wrapper shape so a future schema change to the
        # gateway (which would mirror WAHA more closely) doesn't break us.
        chat_id = self._validate_chat_id(chat_id)
        return await self._action_send_result(
            "sendPoll",
            {
                "chatId": chat_id,
                "poll": {
                    "name": name,
                    "options": list(options),
                    "multipleAnswers": bool(multiple),
                },
            },
            extra_headers=self._resolve_in_reply_to_for(chat_id, metadata, in_reply_to),
        )

    # ------------------------------------------------------------------
    # Message ops
    # ------------------------------------------------------------------

    # Phase 168.5 Half B note: editMessage / deleteMessage / pinMessage /
    # unpinMessage / forwardMessage / react MUST NOT set X-In-Reply-To.
    # They operate on an existing message — they are not new replies — so
    # Chatlytics's presence pipeline (Half A) must not drain a stashed
    # controller for them. None of these helpers passes ``extra_headers``.

    async def edit_message(
        self, chat_id: str, message_id: str, text: str
    ) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action(
            "editMessage",
            {"chatId": chat_id, "messageId": message_id, "text": text},
        )

    async def unsend_message(self, chat_id: str, message_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action(
            "deleteMessage",
            {"chatId": chat_id, "messageId": message_id},
        )

    async def delete_message(self, chat_id: str, message_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action(
            "deleteMessage",
            {"chatId": chat_id, "messageId": message_id},
        )

    async def pin_message(
        self,
        chat_id: str,
        message_id: str,
        duration: Optional[int] = None,
    ) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        params: Dict[str, Any] = {"chatId": chat_id, "messageId": message_id}
        if duration is not None:
            params["duration"] = int(duration)
        return await self._action("pinMessage", params)

    async def unpin_message(self, chat_id: str, message_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action(
            "unpinMessage",
            {"chatId": chat_id, "messageId": message_id},
        )

    async def forward_message(
        self, chat_id: str, message_id: str, target_chat_id: str
    ) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        target_chat_id = self._validate_chat_id(target_chat_id)
        # WAHA's forward expects (target chatId, source messageId).
        return await self._action(
            "forwardMessage",
            {"chatId": target_chat_id, "messageId": message_id, "from": chat_id},
        )

    async def react(
        self, chat_id: str, message_id: str, emoji: str
    ) -> Dict[str, Any]:
        # WAHA requires the full ``true_chatId_shortId`` form here. Use the
        # gateway's standard "react" action name (mode "to") so target
        # resolution stays aligned with the LLM tool name. Reactions are not
        # replies — see the X-In-Reply-To note above.
        chat_id = self._validate_chat_id(chat_id)
        message_id = self._validate_message_id(message_id)
        return await self._action(
            "react",
            {"chatId": chat_id, "messageId": message_id, "emoji": emoji},
        )

    async def mark_read(self, chat_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action("readChatMessages", {"chatId": chat_id})

    async def mark_unread(self, chat_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action("unreadChat", {"chatId": chat_id})

    async def star_message(self, chat_id: str, message_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action(
            "starMessage",
            {"chatId": chat_id, "messageId": message_id, "star": True},
        )

    async def unstar_message(self, chat_id: str, message_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action(
            "starMessage",
            {"chatId": chat_id, "messageId": message_id, "star": False},
        )

    # ------------------------------------------------------------------
    # Search / listing
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        type: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        # Special case: search has its own GET shorthand at /api/v1/search.
        # Phase 167 confirmed the route exists; we still pass through the
        # generic action surface for sessions that expose only /api/v1/actions.
        self._check_connected()
        params: Dict[str, Any] = {"query": query}
        if type:
            params["scope"] = type
        if limit:
            params["limit"] = int(limit)
        client = getattr(self, "_client")
        try:
            resp = await client.get(
                "/api/v1/search",
                params=params,
                headers=self._auth_headers(),  # type: ignore[attr-defined]
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {"results": data}
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status != 404:
                raise
            # Fall back to the generic action when the shorthand isn't deployed.
            return await self._action("search", params)

    async def list_groups(self) -> Dict[str, Any]:
        return await self._action("getGroups")

    async def list_chats(self) -> Dict[str, Any]:
        return await self._action("getChats")

    async def list_contacts(self) -> Dict[str, Any]:
        return await self._action("getContacts")

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    async def get_group(self, group_id: str) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action("getGroup", {"groupId": group_id})

    async def create_group(self, name: str, participants: List[str]) -> Dict[str, Any]:
        return await self._action(
            "createGroup",
            {"name": name, "participants": list(participants)},
        )

    async def add_participants(
        self, group_id: str, participants: List[str]
    ) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action(
            "addParticipants",
            {"groupId": group_id, "participants": list(participants)},
        )

    async def remove_participants(
        self, group_id: str, participants: List[str]
    ) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action(
            "removeParticipants",
            {"groupId": group_id, "participants": list(participants)},
        )

    async def promote_admin(self, group_id: str, participant: str) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action(
            "promoteToAdmin",
            {"groupId": group_id, "participants": [participant]},
        )

    async def demote_admin(self, group_id: str, participant: str) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action(
            "demoteFromAdmin",
            {"groupId": group_id, "participants": [participant]},
        )

    async def leave_group(self, group_id: str) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action("leaveGroup", {"groupId": group_id})

    async def rename_group(self, group_id: str, name: str) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action(
            "setGroupSubject",
            {"groupId": group_id, "subject": name},
        )

    async def set_group_description(
        self, group_id: str, description: str
    ) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action(
            "setGroupDescription",
            {"groupId": group_id, "description": description},
        )

    async def set_group_picture(self, group_id: str, image: str) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action(
            "setGroupPicture",
            {"groupId": group_id, "file": image},
        )

    async def get_invite_code(self, group_id: str) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action("getInviteCode", {"groupId": group_id})

    async def revoke_invite_code(self, group_id: str) -> Dict[str, Any]:
        group_id = self._validate_group_id(group_id)
        return await self._action("revokeInviteCode", {"groupId": group_id})

    async def join_group(self, invite_code: str) -> Dict[str, Any]:
        # Per CLAUDE.md quirk #joinGroup: WAHA accepts ``code`` only — but
        # Chatlytics catalog entry uses ``inviteCode``. The src/channel.ts
        # handler reads ``p.inviteCode`` and forwards as ``code`` to WAHA.
        # We send what the catalog expects.
        if not isinstance(invite_code, str) or not invite_code:
            raise ValueError("invite_code must be a non-empty string")
        return await self._action("joinGroup", {"inviteCode": invite_code})

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def get_contact(self, contact_id: str) -> Dict[str, Any]:
        contact_id = self._validate_contact_id(contact_id)
        return await self._action("getContact", {"contactId": contact_id})

    async def block_contact(self, contact_id: str) -> Dict[str, Any]:
        contact_id = self._validate_contact_id(contact_id)
        return await self._action("blockContact", {"contactId": contact_id})

    async def unblock_contact(self, contact_id: str) -> Dict[str, Any]:
        contact_id = self._validate_contact_id(contact_id)
        return await self._action("unblockContact", {"contactId": contact_id})

    async def get_contact_about(self, contact_id: str) -> Dict[str, Any]:
        contact_id = self._validate_contact_id(contact_id)
        return await self._action("getContactAbout", {"contactId": contact_id})

    async def get_contact_picture(self, contact_id: str) -> Dict[str, Any]:
        contact_id = self._validate_contact_id(contact_id)
        return await self._action("getContactPicture", {"contactId": contact_id})

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    async def list_labels(self) -> Dict[str, Any]:
        return await self._action("getLabels")

    async def create_label(
        self, name: str, color: Optional[int] = None
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"name": name}
        if color is not None:
            params["color"] = int(color)
        return await self._action("createLabel", params)

    async def update_label(
        self,
        label_id: str,
        name: Optional[str] = None,
        color: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"labelId": label_id}
        if name is not None:
            params["name"] = name
        if color is not None:
            params["color"] = int(color)
        return await self._action("updateLabel", params)

    async def delete_label(self, label_id: str) -> Dict[str, Any]:
        return await self._action("deleteLabel", {"labelId": label_id})

    async def set_chat_labels(
        self, chat_id: str, labels: List[str]
    ) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        # Catalog declares ``labels: object[]``. Accept caller's list of label
        # ids and wrap each in ``{id: ...}`` so the gateway handler is happy.
        wrapped = [{"id": str(lid)} for lid in labels]
        return await self._action(
            "setChatLabels",
            {"chatId": chat_id, "labels": wrapped},
        )

    async def get_chat_labels(self, chat_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action("getChatLabels", {"chatId": chat_id})

    async def get_chats_by_label(self, label_id: str) -> Dict[str, Any]:
        return await self._action("getChatsByLabel", {"labelId": label_id})

    # ------------------------------------------------------------------
    # Presence (typing wired separately on the adapter — see send_typing)
    # ------------------------------------------------------------------

    async def set_presence(self, status: str) -> Dict[str, Any]:
        if status not in ("online", "offline"):
            raise ValueError(f"status must be 'online' or 'offline', got: {status!r}")
        return await self._action("setPresence", {"status": status})

    async def subscribe_presence(self, contact_id: str) -> Dict[str, Any]:
        contact_id = self._validate_contact_id(contact_id)
        return await self._action("subscribePresence", {"contactId": contact_id})

    async def get_presence(self, contact_id: str) -> Dict[str, Any]:
        contact_id = self._validate_contact_id(contact_id)
        return await self._action("getPresence", {"contactId": contact_id})

    async def get_all_presence(self) -> Dict[str, Any]:
        return await self._action("getAllPresence")

    async def start_typing(self, chat_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action("startTyping", {"chatId": chat_id})

    async def stop_typing(self, chat_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action("stopTyping", {"chatId": chat_id})

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    async def get_profile(self) -> Dict[str, Any]:
        return await self._action("getProfile")

    async def set_profile_name(self, name: str) -> Dict[str, Any]:
        return await self._action("setProfileName", {"name": name})

    async def set_profile_status(self, status: str) -> Dict[str, Any]:
        return await self._action("setProfileStatus", {"status": status})

    async def set_profile_picture(self, image: str) -> Dict[str, Any]:
        return await self._action("setProfilePicture", {"file": image})

    async def delete_profile_picture(self) -> Dict[str, Any]:
        return await self._action("deleteProfilePicture")

    # ------------------------------------------------------------------
    # Channels / newsletters
    # ------------------------------------------------------------------

    async def list_channels(self) -> Dict[str, Any]:
        return await self._action("getChannels")

    async def search_channels(self, query: str) -> Dict[str, Any]:
        return await self._action("searchChannelsByText", {"query": query})

    async def preview_channel(self, channel_or_invite: str) -> Dict[str, Any]:
        # Per CLAUDE.md WAHA quirk: invite codes (whatsapp.com/channel/CODE)
        # are NOT newsletter JIDs. previewChannelMessages expects the full
        # @newsletter JID. If caller passes an invite code, the gateway
        # resolves it via GET /channels/{code} first — let the gateway
        # handle that; we just forward whatever string we got.
        return await self._action(
            "previewChannelMessages",
            {"channelId": channel_or_invite},
        )

    async def follow_channel(self, channel_id: str) -> Dict[str, Any]:
        channel_id = self._validate_channel_id(channel_id)
        return await self._action("followChannel", {"channelId": channel_id})

    async def unfollow_channel(self, channel_id: str) -> Dict[str, Any]:
        channel_id = self._validate_channel_id(channel_id)
        return await self._action("unfollowChannel", {"channelId": channel_id})

    async def mute_channel(self, channel_id: str) -> Dict[str, Any]:
        channel_id = self._validate_channel_id(channel_id)
        return await self._action("muteChannel", {"channelId": channel_id})

    async def unmute_channel(self, channel_id: str) -> Dict[str, Any]:
        channel_id = self._validate_channel_id(channel_id)
        return await self._action("unmuteChannel", {"channelId": channel_id})

    # ------------------------------------------------------------------
    # Status / stories
    # ------------------------------------------------------------------

    async def send_status_text(self, text: str) -> SendResult:
        return await self._action_send_result("sendTextStatus", {"text": text})

    async def send_status_image(
        self, file: str, caption: Optional[str] = None
    ) -> SendResult:
        params: Dict[str, Any] = {"image": file}
        if caption:
            params["caption"] = caption
        return await self._action_send_result("sendImageStatus", params)

    async def send_status_video(
        self, file: str, caption: Optional[str] = None
    ) -> SendResult:
        params: Dict[str, Any] = {"video": file}
        if caption:
            params["caption"] = caption
        return await self._action_send_result("sendVideoStatus", params)

    async def send_status_voice(self, voice: str) -> SendResult:
        return await self._action_send_result("sendVoiceStatus", {"voice": voice})

    async def delete_status(self, status_id: str) -> Dict[str, Any]:
        return await self._action("deleteStatus", {"id": status_id})

    # ------------------------------------------------------------------
    # Calls
    # ------------------------------------------------------------------

    async def reject_call(self, call_id: str) -> Dict[str, Any]:
        return await self._action("rejectCall", {"callId": call_id})

    # ------------------------------------------------------------------
    # LID resolution
    # ------------------------------------------------------------------

    async def phone_to_lid(self, phone: str) -> Dict[str, Any]:
        return await self._action("findLidByPhone", {"phone": phone})

    async def lid_to_phone(self, lid: str) -> Dict[str, Any]:
        return await self._action("findPhoneByLid", {"lid": lid})

    # ------------------------------------------------------------------
    # Chat ops
    # ------------------------------------------------------------------

    async def archive_chat(self, chat_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action("archiveChat", {"chatId": chat_id})

    async def unarchive_chat(self, chat_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action("unarchiveChat", {"chatId": chat_id})

    async def delete_chat(self, chat_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action("deleteChat", {"chatId": chat_id})

    async def clear_chat(self, chat_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action("clearChatMessages", {"chatId": chat_id})

    async def mute_chat(
        self, chat_id: str, duration: Optional[int] = None
    ) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        params: Dict[str, Any] = {"chatId": chat_id}
        if duration is not None:
            params["duration"] = int(duration)
        return await self._action("muteChat", params)

    async def unmute_chat(self, chat_id: str) -> Dict[str, Any]:
        chat_id = self._validate_chat_id(chat_id)
        return await self._action("unmuteChat", {"chatId": chat_id})


# Set of action names this mixin maps. Keep in sync with public methods —
# the parity test in tests/test_action_parity.py asserts that every name in
# src/action-catalog.ts is either here or in INTENTIONALLY_UNMAPPED.
IMPLEMENTED_ACTIONS = frozenset({
    # Outbound (also: send/sendImage/sendVideo/sendFile/sendVoice on adapter.py)
    "send", "sendImage", "sendVideo", "sendFile", "sendVoice",
    "sendLocation", "sendContactVcard", "sendPoll",
    # Message ops
    "editMessage", "deleteMessage", "pinMessage", "unpinMessage",
    "starMessage", "react", "readChatMessages", "unreadChat",
    "forwardMessage",
    # Search/listing
    "search", "getGroups", "getChats", "getContacts",
    # Groups
    "getGroup", "createGroup", "addParticipants", "removeParticipants",
    "promoteToAdmin", "demoteFromAdmin", "leaveGroup", "setGroupSubject",
    "setGroupDescription", "setGroupPicture", "getInviteCode",
    "revokeInviteCode", "joinGroup",
    # Contacts
    "getContact", "blockContact", "unblockContact", "getContactAbout",
    "getContactPicture",
    # Labels
    "getLabels", "createLabel", "updateLabel", "deleteLabel",
    "setChatLabels", "getChatLabels", "getChatsByLabel",
    # Presence + typing
    "setPresence", "subscribePresence", "getPresence", "getAllPresence",
    "startTyping", "stopTyping",
    # Profile
    "getProfile", "setProfileName", "setProfileStatus",
    "setProfilePicture", "deleteProfilePicture",
    # Channels
    "getChannels", "searchChannelsByText", "previewChannelMessages",
    "followChannel", "unfollowChannel", "muteChannel", "unmuteChannel",
    # Status
    "sendTextStatus", "sendImageStatus", "sendVideoStatus",
    "sendVoiceStatus", "deleteStatus",
    # Calls
    "rejectCall",
    # LID
    "findLidByPhone", "findPhoneByLid", "getAllLids",
    # Chat ops
    "archiveChat", "unarchiveChat", "deleteChat", "clearChatMessages",
    "muteChat", "unmuteChat",
})
