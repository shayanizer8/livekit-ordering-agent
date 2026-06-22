import asyncio
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load backend/.env BEFORE importing plugins that may validate API keys at import time.
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, function_tool, inference
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import cartesia, deepgram, openai
from livekit import rtc

from tools import (
    add_item_to_cart,
    remove_item_from_cart,
    modify_item_in_cart,
    add_combo_to_cart,
    get_cart_summary,
    confirm_order,
    set_cart_scope,
    search_menu,
    get_item_details,
    get_category_items,
    get_combo_details,
    get_all_combos,
    filter_menu_by_allergen,
    get_item_modifiers,
)


AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "forge-flame-agent")
CARTESIA_TTS_MODEL = os.getenv("CARTESIA_TTS_MODEL", "sonic-3.5")
SILERO_VAD_ACTIVATION_THRESHOLD = float(
    os.getenv("SILERO_VAD_ACTIVATION_THRESHOLD", "0.5")
)
TURN_DETECTOR_VERSION = os.getenv("LIVEKIT_TURN_DETECTOR_VERSION")
LIVEKIT_INFERENCE_URL = os.getenv("LIVEKIT_INFERENCE_URL")
LIVEKIT_INFERENCE_API_KEY = os.getenv("LIVEKIT_INFERENCE_API_KEY")
LIVEKIT_INFERENCE_API_SECRET = os.getenv("LIVEKIT_INFERENCE_API_SECRET")


def build_vad() -> inference.VAD:
    return inference.VAD(
        model="silero",
        activation_threshold=SILERO_VAD_ACTIVATION_THRESHOLD,
    )


def build_turn_detector() -> inference.TurnDetector:
    detector_kwargs: dict[str, str] = {}
    if TURN_DETECTOR_VERSION:
        detector_kwargs["version"] = TURN_DETECTOR_VERSION
    elif LIVEKIT_INFERENCE_URL and LIVEKIT_INFERENCE_API_KEY and LIVEKIT_INFERENCE_API_SECRET:
        detector_kwargs["version"] = "v1"
        detector_kwargs["base_url"] = LIVEKIT_INFERENCE_URL
        detector_kwargs["api_key"] = LIVEKIT_INFERENCE_API_KEY
        detector_kwargs["api_secret"] = LIVEKIT_INFERENCE_API_SECRET
    else:
        detector_kwargs["version"] = "v1-mini"
    return inference.TurnDetector(**detector_kwargs)


def load_context() -> str:
    """Load system prompt and inject a compact menu summary."""
    base = Path(__file__).parent
    system_prompt_path = base / "context" / "system_prompt.txt"
    menu_path = base / "context" / "menu.json"

    with open(system_prompt_path, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    with open(menu_path, "r", encoding="utf-8") as f:
        menu_data = json.load(f)

    summary_lines: list[str] = []
    summary_lines.append(f"Restaurant: {menu_data.get('restaurant', 'Unknown')}")
    summary_lines.append(f"Currency: {menu_data.get('currency', 'USD')}")
    summary_lines.append(
        "Categories: " + ", ".join(menu_data.get("categories", []))
    )
    summary_lines.append("")
    summary_lines.append("Menu reference:")

    for category, items in menu_data.get("menu", {}).items():
        summary_lines.append(f"{category}:")
        for item in items:
            price = item.get("price")
            if isinstance(price, dict):
                price_text = ", ".join(f"{key} ${value:.2f}" for key, value in price.items())
            else:
                price_text = f"${float(price):.2f}"

            allergens = item.get("allergens", [])
            modifiers = item.get("available_modifiers", [])
            summary_lines.append(
                f"- {item.get('id')} | {item.get('name')} | {price_text}"
                f" | allergens: {', '.join(allergens) if allergens else 'none'}"
                f" | modifiers: {', '.join(modifiers) if modifiers else 'none'}"
            )

    combos = menu_data.get("combos", [])
    if combos:
        summary_lines.append("")
        summary_lines.append("Combos:")
        for combo in combos:
            price = combo.get("price")
            if isinstance(price, dict):
                price_text = ", ".join(f"{key} ${value:.2f}" for key, value in price.items())
            else:
                price_text = f"${float(price):.2f}"
            summary_lines.append(
                f"- {combo.get('id')} | {combo.get('name')} | {price_text}"
                f" | included: {', '.join(combo.get('included', []))}"
            )

    menu_summary = "\n".join(summary_lines)
    return system_prompt.replace("{MENU_JSON}", menu_summary)


class OrderingAgent(Agent):
    """Voice ordering agent for Forge & Flame with cart sync via data channel."""

    def __init__(self):
        super().__init__(instructions=load_context())

    def _get_room(self):
        room_io = getattr(self.session, "room_io", None)
        if not room_io:
            return None
        return room_io.room

    def _set_cart_scope(self) -> None:
        room = self._get_room()
        room_name = getattr(room, "name", None) if room else None
        set_cart_scope(room_name or "default")

    async def on_enter(self) -> None:
        """Greet the guest when the session starts."""
        await self.session.say(
            "Welcome to Forge & Flame! I'm Ember, your ordering assistant. "
            "What can I get started for you today?"
        )

    # For UI changes of cart in real time, we publish cart state updates to a LiveKit data channel.
    async def _publish_cart_update(self, cart_state: dict) -> None:
        """Publish cart state to LiveKit room via reliable data channel."""
        room = self._get_room()
        if not room:
            return
        # Transform items to frontend format
        frontend_items = []
        for item in cart_state.get("items", []):
            mods = ", ".join(item.get("modifiers", [])) if item.get("modifiers") else ""
            frontend_items.append({
                "name": item.get("name", ""),
                "quantity": item.get("quantity", 1),
                "modifiers": mods,
                "price": f"${item.get('subtotal', 0):.2f}",
            })
        payload = {
            "type": "cart_update",
            "items": frontend_items,
            "total": f"${cart_state.get('total', 0):.2f}",
            "confirmed": cart_state.get("confirmed", False),
        }
        data = json.dumps(payload).encode("utf-8")
        await room.local_participant.publish_data(data, reliable=True)

    @function_tool
    async def add_item_to_cart(self, item_id: str, quantity: int, size: str, modifiers: list[str]) -> dict:
        """Add a menu item to the cart and sync state."""
        self._set_cart_scope()
        result = add_item_to_cart(item_id, quantity, size, modifiers)
        if result.get("success"):
            await self._publish_cart_update(result["cart"])
        return result

    @function_tool
    async def remove_item_from_cart(self, item_id: str, modifiers: list[str]) -> dict:
        """Remove an item from the cart and sync state."""
        self._set_cart_scope()
        result = remove_item_from_cart(item_id, modifiers)
        if result.get("success"):
            await self._publish_cart_update(result["cart"])
        return result

    @function_tool
    async def modify_item_in_cart(self, item_id: str, old_modifiers: list[str], new_modifiers: list[str], new_size: str) -> dict:
        """Modify an existing cart item and sync state."""
        self._set_cart_scope()
        result = modify_item_in_cart(item_id, old_modifiers, new_modifiers, new_size)
        if result.get("success"):
            await self._publish_cart_update(result["cart"])
        return result

    @function_tool
    async def add_combo_to_cart(self, combo_id: str, burger_id: str, side_upgrade: str, drink_upgrade: str) -> dict:
        """Add a combo meal to the cart and sync state."""
        self._set_cart_scope()
        result = add_combo_to_cart(combo_id, burger_id, side_upgrade, drink_upgrade)
        if result.get("success"):
            await self._publish_cart_update(result["cart"])
        return result

    @function_tool
    async def get_cart_summary(self) -> dict:
        """Return current cart contents without modifying state."""
        self._set_cart_scope()
        return get_cart_summary()

    @function_tool
    async def confirm_order(self) -> dict:
        """Finalize the order and sync confirmed state."""
        self._set_cart_scope()
        result = confirm_order()
        if result.get("success"):
            await self._publish_cart_update(result["cart"])
            # Speak the farewell, give TTS time to finish, then close the room.

            async def _disconnect_after_delay() -> None:
            # Give the LLM 5 seconds to say its farewell before hanging up
                await asyncio.sleep(7) 
                room = self._get_room()
                if room:
                    await room.disconnect()

            asyncio.ensure_future(_disconnect_after_delay())
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

    async def handle_client_data(self, payload: bytes) -> None:
        """Handle incoming room data messages from the frontend."""
        self._set_cart_scope()
        try:
            message = json.loads(payload.decode("utf-8"))
        except Exception:
            return

        if not isinstance(message, dict):
            return

        if message.get("type") == "confirm_order":
            await self.confirm_order()


async def entrypoint(ctx: JobContext) -> None:
    """Main entrypoint: connect to room, start session, send initial greeting."""
    try:
        # Connect to LiveKit room with audio-only subscription
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

        # Initialize AgentSession with STT, LLM, TTS pipeline
        session = AgentSession(
            stt=deepgram.STT(model="nova-2", language="en-US"),
            vad=build_vad(),
            llm=openai.LLM(
                model="ministral-8b-latest",
                api_key=os.getenv("MISTRAL_API_KEY"),
                base_url="https://api.mistral.ai/v1",
            ),
            tts=cartesia.TTS(
                model=CARTESIA_TTS_MODEL,
                api_key=os.getenv("CARTESIA_API_KEY"),
            ),
            turn_handling={
                "turn_detection": build_turn_detector(),
                "endpointing": {
                    "mode": "fixed",
                    "min_delay": 0.6,
                    "max_delay": 3.0,
                },
                "interruption": {
                    "enabled": True,
                    "mode": "adaptive",
                    "discard_audio_if_uninterruptible": True,
                    "min_duration": 0.5,
                    "resume_false_interruption": True,
                    "false_interruption_timeout": 2.0,
                },
            },
        )

        agent = OrderingAgent()
        room = ctx.room

        def _on_room_data(data_packet) -> None:
            payload = getattr(data_packet, "data", None)
            if not isinstance(payload, (bytes, bytearray, memoryview)):
                return
            asyncio.create_task(agent.handle_client_data(bytes(payload)))

        room.on("data_received", _on_room_data)

        # Start the session with our agent (greeting is sent from on_enter)
        await session.start(agent=agent, room=room)

    except Exception as e:
        # Clean error handling - log and re-raise
        print(f"Entrypoint error: {e}")
        raise


if __name__ == "__main__":
    # Run the agent app with CLI
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name=AGENT_NAME))
