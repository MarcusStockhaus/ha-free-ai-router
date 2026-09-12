"""``ai_task``-Entities — eine je Profil.

Das ist die Andockstelle: Wer diese Entities bedient, funktioniert mit jeder
Automation und jedem fremden Blueprint, der eine AI-Task-Entity auswaehlen
laesst. Deshalb drei getrennte Entities mit sprechenden Namen statt einer
Entity mit einem Modellparameter — ein Blueprint waehlt eine Entity aus, keine
Option darin.
"""

from __future__ import annotations

import logging

from homeassistant.components import ai_task, conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FreeAIRouterConfigEntry
from .client import NoChannelAvailable
from .const import PROFILE_REASONING, PROFILES
from .entity import RouterEntity
from .router import Requirements
from .task_adapter import (
    attachments_to_images,
    estimate_input_tokens,
    normalize_result,
    structure_to_json_schema,
    to_attachments,
)

_LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Du bist Teil einer Home-Assistant-Automation. Antworte knapp und "
    "ausschliesslich mit dem Verlangten, ohne Einleitung und ohne Rueckfrage."
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FreeAIRouterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(FreeAITaskEntity(entry, profile) for profile in PROFILES)


class FreeAITaskEntity(ai_task.AITaskEntity, RouterEntity):
    """Eine AI-Task-Entity je Profil."""

    _attr_supported_features = (
        ai_task.AITaskEntityFeature.GENERATE_DATA
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
    )

    def __init__(self, entry: FreeAIRouterConfigEntry, profile: str) -> None:
        RouterEntity.__init__(self, entry)
        self._profile = profile
        # Uebersetzungsschluessel statt fester Name: der Entity-Name kommt aus
        # strings.json, die Entity-ID wird dadurch stabil (ai_task.<geraet>_<profil>).
        self._attr_translation_key = profile
        self._attr_unique_id = f"{entry.entry_id}_{profile}"

    async def _async_generate_data(
        self, task: ai_task.GenDataTask, chat_log: conversation.ChatLog
    ) -> ai_task.GenDataTaskResult:
        runtime = self.runtime

        bilder = await attachments_to_images(self.hass, task.attachments)
        images = to_attachments(bilder)
        json_schema = structure_to_json_schema(
            task.structure,
            custom_serializer=(
                chat_log.llm_api.custom_serializer if chat_log.llm_api else None
            ),
        )

        requirements = Requirements.for_profile(
            self._profile,
            has_attachments=bool(images),
            has_structure=json_schema is not None,
            approx_input_tokens=estimate_input_tokens(task.instructions, bilder),
        )

        try:
            execution = await runtime.client.run(
                requirements,
                # Bewusst alle Kanaele: haengt ein Bild an, darf auch ein
                # Modell einspringen, das fuer dieses Profil nicht vorgesehen
                # war. Der Router filtert nach Faehigkeit, nicht nach Etikett.
                runtime.all_channels(),
                instructions=task.instructions,
                system=SYSTEM_PROMPT,
                json_schema=json_schema,
                images=images,
                # Beim Reasoning-Profil ist Nachdenken der Zweck. Bei einer
                # Kameraszene oder einer Klassifikation frisst es nur das
                # Ausgabebudget — gemessen kam dann eine leere Antwort zurueck.
                thinking_budget=None if self._profile == PROFILE_REASONING else 0,
            )
        except NoChannelAvailable as err:
            _LOGGER.warning("%s: %s", self.entity_id, err.report())
            raise HomeAssistantError(err.report()) from err

        self._note_channel(execution.candidate.key, execution.used_reserve)
        self.async_write_ha_state()

        data = normalize_result(execution.response, has_structure=json_schema is not None)
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=self.entity_id,
                content=execution.response.text,
            )
        )
        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )
