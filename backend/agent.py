import asyncio
import os
import json
from pathlib import Path
from dotenv import load_dotenv

from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, function_tool
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import deepgram, openai
from livekit import rtc

from tools import (
    add_item_to_cart,
    remove_item_from_cart,
    modify_item_in_cart,
    add_combo_to_cart,
    get_cart_summary,
    confirm_order,
    search_menu,
    get_item_details,
    get_category_items,
    get_combo_details,
    get_all_combos,
    filter_menu_by_allergen,
    get_item_modifiers,
)


AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "forge-flame-agent")
DEEPGRAM_TTS_MODEL = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-andromeda-en")


def load_context() -> str:
    """Load system prompt and inject menu JSON."""
    base = Path(__file__).parent
    system_prompt_path = base / "context" / "system_prompt.txt"
    menu_path = base / "context" / "menu.json"

    with open(system_prompt_path, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    with open(menu_path, "r", encoding="utf-8") as f:
        menu_data = json.load(f)

    menu_json_str = json.dumps(menu_data, indent=2)
    return system_prompt.replace("{MENU_JSON}", menu_json_str)


class OrderingAgent(Agent):
    """Voice ordering agent for Forge & Flame with cart sync via data channel."""

    def __init__(self):
        super().__init__(instructions=load_context())

    async def on_enter(self) -> None:
        """Greet the guest when the session starts."""
        await self.session.say(
            "Welcome to Forge & Flame! I'm Ember, your ordering assistant. "
            "What can I get started for you today?"
        )

    # For UI changes of cart in real time, we publish cart state updates to a LiveKit data channel.
    def _publish_cart_update(self, cart_state: dict) -> None:
        """Publish cart state to LiveKit room via reliable data channel."""
        if not self._room:
            return
        payload = {
            "type": "cart_update",
            "items": cart_state.get("items", []),
            "total": cart_state.get("total", 0.0),
            "confirmed": cart_state.get("confirmed", False),
        }
        data = json.dumps(payload).encode("utf-8")
        # Use reliable delivery for cart updates
        self._room.local_participant.publish_data(data, reliable=True) # .publish_data sends data over a separte data channel not associated with the audio stream.

    @function_tool
    async def add_item_to_cart(self, item_id: str, quantity: int, size: str, modifiers: list[str]) -> dict:
        """Add a menu item to the cart and sync state."""
        result = add_item_to_cart(item_id, quantity, size, modifiers)
        if result.get("success"):
            self._publish_cart_update(result["cart"])
        return result

    @function_tool
    async def remove_item_from_cart(self, item_id: str, modifiers: list[str]) -> dict:
        """Remove an item from the cart and sync state."""
        result = remove_item_from_cart(item_id, modifiers)
        if result.get("success"):
            self._publish_cart_update(result["cart"])
        return result

    @function_tool
    async def modify_item_in_cart(self, item_id: str, old_modifiers: list[str], new_modifiers: list[str], new_size: str) -> dict:
        """Modify an existing cart item and sync state."""
        result = modify_item_in_cart(item_id, old_modifiers, new_modifiers, new_size)
        if result.get("success"):
            self._publish_cart_update(result["cart"])
        return result

    @function_tool
    async def add_combo_to_cart(self, combo_id: str, burger_id: str, side_upgrade: str, drink_upgrade: str) -> dict:
        """Add a combo meal to the cart and sync state."""
        result = add_combo_to_cart(combo_id, burger_id, side_upgrade, drink_upgrade)
        if result.get("success"):
            self._publish_cart_update(result["cart"])
        return result

    @function_tool
    async def get_cart_summary(self) -> dict:
        """Return current cart contents without modifying state."""
        return get_cart_summary()

    @function_tool
    async def confirm_order(self) -> dict:
        """Finalize the order and sync confirmed state."""
        result = confirm_order()
        if result.get("success"):
            self._publish_cart_update(result["cart"])
            # Speak the farewell, give TTS time to finish, then close the room.
            # session.say() is accessed via the bound AgentSession — we schedule
            # the teardown as a background task so the tool result is returned
            # to the LLM first (avoiding a blocked response).
            async def _farewell_and_disconnect() -> None:
                await self.session.say(
                    "Your order is confirmed. Thank you for choosing Forge and Flame!"
                )
                await asyncio.sleep(3)
                await self._room.disconnect()

            asyncio.ensure_future(_farewell_and_disconnect())
        return result

    @function_tool
    async def search_menu(self, query: str) -> dict:
        """Search menu items by query string."""
        return search_menu(query)

    @function_tool
    async def get_item_details(self, item_id: str) -> dict:
        """Get full details for a single menu item."""
        return get_item_details(item_id)

    @function_tool
    async def get_category_items(self, category: str) -> dict:
        """Get all items in a category."""
        return get_category_items(category)

    @function_tool
    async def get_combo_details(self, combo_id: str) -> dict:
        """Get details for a specific combo."""
        return get_combo_details(combo_id)

    @function_tool
    async def get_all_combos(self) -> dict:
        """Get summary of all available combos."""
        return get_all_combos()

    @function_tool
    async def filter_menu_by_allergen(self, allergen: str) -> dict:
        """Filter menu items excluding an allergen."""
        return filter_menu_by_allergen(allergen)

    @function_tool
    async def get_item_modifiers(self, item_id: str) -> dict:
        """Get available customization modifiers for an item."""
        return get_item_modifiers(item_id)


async def entrypoint(ctx: JobContext) -> None:
    """Main entrypoint: connect to room, start session, send initial greeting."""
    try:
        # Connect to LiveKit room with audio-only subscription
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

        # Initialize AgentSession with STT, LLM, TTS pipeline
        session = AgentSession(
            stt=deepgram.STT(model="nova-2", language="en-US"),
            llm=openai.LLM(
                model="llama-3.1-8b-instant",
                api_key=os.getenv("GROQ_API_KEY"),
                base_url="https://api.groq.com/openai/v1",
            ),
            tts=deepgram.TTS(
                model=DEEPGRAM_TTS_MODEL,
                api_key=os.getenv("DEEPGRAM_API_KEY"),
            ),
        )

        agent = OrderingAgent()

        # Start the session with our agent (greeting is sent from on_enter)
        await session.start(agent=agent, room=ctx.room)

    except Exception as e:
        # Clean error handling - log and re-raise
        print(f"Entrypoint error: {e}")
        raise


if __name__ == "__main__":
    # Load environment variables from .env
    load_dotenv()

    # Run the agent app with CLI
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name=AGENT_NAME))