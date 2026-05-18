from __future__ import annotations



import logging

from typing import Protocol



from weclaw.core.types import ConversationRef, build_conversation_key



logger = logging.getLogger(__name__)





class MessageSender(Protocol):

    async def send_text(self, target_id: str, text: str) -> None: ...

    async def send_file(self, target_id: str, file_data: bytes, file_name: str) -> None: ...





async def send_sender_text(sender: MessageSender, target_id: str | int, text: str) -> None:

    await sender.send_text(str(target_id), text)





async def send_sender_file(sender: MessageSender, target_id: str | int, file_data: bytes, file_name: str) -> None:

    await sender.send_file(str(target_id), file_data, file_name)





class DeliveryRouter:

    def __init__(self) -> None:

        self._conversation_senders: dict[str, MessageSender] = {}

        self._channel_senders: dict[str, MessageSender] = {}



    def register(self, channel: str, sender: MessageSender) -> None:

        self.register_channel(channel, sender)



    def register_channel(self, channel: str, sender: MessageSender) -> None:

        self._channel_senders[channel] = sender

        logger.info("Registered channel sender: channel=%s", channel)



    def register_conversation(self, conversation: ConversationRef, sender: MessageSender) -> None:

        self._conversation_senders[conversation.conversation_key] = sender

        logger.info("Registered conversation sender: key=%s", conversation.conversation_key)



    def unregister(self, channel: str) -> None:

        self.unregister_channel(channel)



    def unregister_channel(self, channel: str) -> None:

        self._channel_senders.pop(channel, None)

        logger.info("Unregistered channel sender: channel=%s", channel)



    def unregister_conversation(self, conversation: ConversationRef) -> None:

        self._conversation_senders.pop(conversation.conversation_key, None)

        logger.info("Unregistered conversation sender: key=%s", conversation.conversation_key)



    def has(self, channel_or_conversation: str | ConversationRef) -> bool:

        if isinstance(channel_or_conversation, ConversationRef):

            return channel_or_conversation.conversation_key in self._conversation_senders or channel_or_conversation.channel in self._channel_senders

        return channel_or_conversation in self._channel_senders



    def get(self, channel_or_conversation: str | ConversationRef) -> MessageSender:

        if isinstance(channel_or_conversation, ConversationRef):

            sender = self._conversation_senders.get(channel_or_conversation.conversation_key)

            if sender is not None:

                return sender

            sender = self._channel_senders.get(channel_or_conversation.channel)

            if sender is not None:

                logger.info(

                    "Falling back to channel sender: key=%s channel=%s",

                    channel_or_conversation.conversation_key,

                    channel_or_conversation.channel,

                )

                return sender

            missing_label = channel_or_conversation.conversation_key

        else:

            sender = self._channel_senders.get(channel_or_conversation)

            if sender is not None:

                return sender

            missing_label = channel_or_conversation

        if sender is None:

            logger.warning("No sender registered for route: %s", missing_label)

            raise RuntimeError(f"No sender registered for route: {missing_label}")

        return sender



    def build_key(self, channel: str, target_id: str) -> str:

        return build_conversation_key(channel, target_id)



    def owns(self, conversation: ConversationRef) -> bool:

        if conversation.conversation_key in self._conversation_senders:

            return True

        return conversation.channel in self._channel_senders



    def can_route_precisely(self, conversation: ConversationRef) -> bool:

        return conversation.conversation_key in self._conversation_senders



    async def send_text(self, conversation: ConversationRef, text: str) -> None:

        logger.info("Routing message to key=%s", conversation.conversation_key)

        await self.get(conversation).send_text(conversation.target_id, text)
