"""``conversation``-Entity fuer Assist.

Assist braucht zwei Dinge, die ein einzuegiger ``ai_task``-Aufruf nicht hat:
den bisherigen Gespraechsverlauf und Werkzeuge, mit denen das Modell Geraete
schaltet. Beides kommt aus dem ``ChatLog``, den HA bereitstellt; hier wird er
in das dialekt-unabhaengige Nachrichtenformat uebersetzt und wieder zurueck.

Die Entity laeuft auf dem Profil ``schnell`` — bei einem Sprachbefehl
entscheidet die Antwortzeit, nicht die letzte Nuance Antwortqualitaet.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from voluptuous_openapi import convert

from . import FreeAIRouterConfigEntry
from .adapters.base import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ToolCall,
    ToolSpec,
)
from .client import NoChannelAvailable
from .const import PROFILE_SCHNELL
from .entity import RouterEntity
from .router import Requirements

_LOGGER = logging.getLogger(__name__)

#: So oft darf das Modell hoechstens Werkzeuge aufrufen, bevor abgebrochen
#: wird. Ohne Deckel dreht ein Modell, das sich verrannt hat, so lange im
#: Kreis, bis das Tageskontingent weg ist.
MAX_TOOL_ITERATIONS = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FreeAIRouterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([FreeAIRouterConversationEntity(entry)])


class FreeAIRouterConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent, RouterEntity
):
    """Assist-Agent ueber die geroutete Anbieterauswahl."""

    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: FreeAIRouterConfigEntry) -> None:
        RouterEntity.__init__(self, entry)
        self._attr_translation_key = "assist"
        self._attr_unique_id = f"{entry.entry_id}_conversation"

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Die Sprache bestimmt das Modell, nicht die Integration."""
        return MATCH_ALL

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        await chat_log.async_provide_llm_data(
            user_input.as_llm_context(self._entry.domain),
            llm.LLM_API_ASSIST,
            None,
            user_input.extra_system_prompt,
        )

        try:
            for _ in range(MAX_TOOL_ITERATIONS):
                await self._async_one_turn(user_input, chat_log)
                if not chat_log.unresponded_tool_results:
                    break
            else:
                _LOGGER.warning(
                    "Assist: nach %s Werkzeugrunden abgebrochen", MAX_TOOL_ITERATIONS
                )
        except NoChannelAvailable as err:
            _LOGGER.warning("Assist: %s", err.report())
            raise HomeAssistantError(err.report()) from err

        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(self._last_text(chat_log))
        return conversation.ConversationResult(
            response=response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=chat_log.continue_conversation,
        )

    # ------------------------------------------------------------ eine Runde
    async def _async_one_turn(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> None:
        runtime = self.runtime
        tools = _tool_specs(chat_log)

        messages = _to_messages(chat_log)
        requirements = Requirements.for_profile(
            PROFILE_SCHNELL,
            needs_tools=bool(tools),
            # Vier Zeichen je Token, wie ueberall — reicht, um ein zu kleines
            # Kontextfenster und ein enges Tokenfenster zu erkennen.
            approx_input_tokens=sum(len(turn.text) for turn in messages) // 4 + 1,
        )
        execution = await runtime.client.run(
            requirements,
            runtime.all_channels(),
            messages=messages,
            tools=tools,
        )

        self._note_channel(execution.candidate.key, execution.used_reserve)
        self.async_write_ha_state()

        response = execution.response
        content = conversation.AssistantContent(
            agent_id=user_input.agent_id or self.entity_id,
            content=response.text or None,
            tool_calls=[
                llm.ToolInput(
                    id=call.call_id or f"call_{index}",
                    tool_name=call.name,
                    tool_args=call.arguments if isinstance(call.arguments, dict) else {},
                )
                for index, call in enumerate(response.tool_calls)
            ]
            or None,
        )

        # Der Generator fuehrt die Werkzeugaufrufe aus und haengt die
        # Ergebnisse an den ChatLog. Durchlaufen genuegt.
        async for _tool_result in chat_log.async_add_assistant_content(content):
            pass

    @staticmethod
    def _last_text(chat_log: conversation.ChatLog) -> str:
        for content in reversed(chat_log.content):
            if content.role == "assistant" and content.content:
                return str(content.content)
        return "Dazu habe ich keine Antwort bekommen."


def _tool_specs(chat_log: conversation.ChatLog) -> tuple[ToolSpec, ...]:
    """HA-Werkzeuge ins dialekt-unabhaengige Format."""
    if chat_log.llm_api is None:
        return ()
    specs: list[ToolSpec] = []
    for tool in chat_log.llm_api.tools:
        parameters = convert(tool.parameters, custom_serializer=chat_log.llm_api.custom_serializer)
        specs.append(
            ToolSpec(
                name=tool.name,
                description=tool.description or "",
                parameters=parameters if isinstance(parameters, dict) else {"type": "object"},
            )
        )
    return tuple(specs)


def _to_messages(chat_log: conversation.ChatLog) -> tuple[Message, ...]:
    """ChatLog -> Nachrichtenfolge des Adapters."""
    messages: list[Message] = []
    for content in chat_log.content:
        role = content.role
        if role == "system":
            messages.append(Message(role=ROLE_SYSTEM, text=str(content.content or "")))
        elif role == "user":
            messages.append(Message(role=ROLE_USER, text=str(content.content or "")))
        elif role == "assistant":
            messages.append(
                Message(
                    role=ROLE_ASSISTANT,
                    text=str(content.content or ""),
                    tool_calls=tuple(
                        ToolCall(
                            name=call.tool_name,
                            arguments=call.tool_args,
                            call_id=call.id,
                        )
                        for call in (content.tool_calls or ())
                    ),
                )
            )
        elif role == "tool_result":
            messages.append(
                Message(
                    role=ROLE_TOOL,
                    text=_stringify(content.tool_result),
                    tool_call_id=content.tool_call_id,
                    tool_name=content.tool_name,
                )
            )
    return tuple(messages)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)
