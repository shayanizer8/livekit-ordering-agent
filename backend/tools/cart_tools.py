"""
cart_tools.py

This module contains functions that a LiveKit voice agent can call to manage
a customer's shopping cart at the Forge & Flame restaurant. It maintains
cart state in memory and interacts with the restaurant's menu data.
"""

import json
import os
import re
from contextvars import ContextVar
from typing import Any

_cart_scope: ContextVar[str] = ContextVar("cart_scope", default="default")
_cart_store: dict[str, dict[str, Any]] = {}


def _default_cart() -> dict[str, Any]:
    return {
        "items": [],
        "total": 0.0,
        "confirmed": False,
    }


def set_cart_scope(scope: str | None) -> None:
    _cart_scope.set(scope or "default")


def _get_cart() -> dict[str, Any]:
    scope = _cart_scope.get()
    if scope not in _cart_store:
        _cart_store[scope] = _default_cart()
    return _cart_store[scope]

# Default Combo mappings for fallback/resolutions
DEFAULT_COMBO_SIDES = {
    "cmb_001": "classic salted fries (regular)",
    "cmb_002": "classic salted fries (regular)",
    "cmb_003": "classic salted fries (large)",
    "cmb_004": "classic salted fries (regular)",
    "cmb_005": "2x large fries"
}

DEFAULT_COMBO_DRINKS = {
    "cmb_001": "regular soft drink",
    "cmb_002": "regular soft drink",
    "cmb_003": "large soft drink",
    "cmb_004": "regular iced tea",
    "cmb_005": "4x regular soft drink"
}

# Load Menu Data
_current_dir = os.path.dirname(os.path.abspath(__file__))
_menu_path = os.path.normpath(os.path.join(_current_dir, "..", "context", "menu.json"))

try:
    with open(_menu_path, "r", encoding="utf-8") as f:
        menu_data: dict[str, Any] = json.load(f)
except Exception as e:
    raise RuntimeError(f"Could not load menu.json from {_menu_path}: {e}")


# ==========================================
# PRIVATE HELPER FUNCTIONS
# ==========================================

def _recalculate_total() -> None:
    """
    Iterates over all items in the cart and recalculates cart["total"] by summing
    all item subtotals. Updates subtotals for each item as well.
    """
    cart = _get_cart()
    total = 0.0
    for item in cart["items"]:
        item["subtotal"] = round(item["unit_price"] * item["quantity"], 2)
        total += item["subtotal"]
    cart["total"] = round(total, 2)


def _find_menu_item(item_id: str) -> dict[str, Any] | None:
    """
    Helper to search the menu_data dict for an item matching item_id.
    """
    menu = menu_data.get("menu", {})
    for category_items in menu.values():
        for item in category_items:
            if item.get("id") == item_id:
                return item
    return None


def _find_menu_item_or_combo(item_id: str) -> dict[str, Any] | None:
    """
    Helper to search both the regular menu items and combos for a matching ID.
    """
    item = _find_menu_item(item_id)
    if item:
        return item
    for combo in menu_data.get("combos", []):
        if combo.get("id") == item_id:
            return combo
    return None


def _resolve_price_and_size(item: dict[str, Any], size: str) -> tuple[float, str]:
    """
    Resolves the correct unit price and size key from an item's price schema.
    If size is not provided, defaults to the first available variant.
    """
    price_field = item.get("price")
    
    # 1. Price is a single flat float/int
    if isinstance(price_field, (int, float)):
        return float(price_field), size or "standard"
        
    # 2. Price is a dictionary of size/variant prices
    if isinstance(price_field, dict):
        if not size:
            # Fallback to the first variant key defined in the menu
            first_key = list(price_field.keys())[0]
            return float(price_field[first_key]), first_key
            
        size_clean = size.lower().strip()
        # Case-insensitive exact match check
        for k, v in price_field.items():
            if k.lower().strip() == size_clean:
                return float(v), k
                
        # Substring/partial match check
        for k, v in price_field.items():
            k_clean = k.lower().strip()
            if size_clean in k_clean or k_clean in size_clean:
                return float(v), k
                
        raise ValueError(
            f"Size '{size}' is not valid for '{item['name']}'. Available: {', '.join(price_field.keys())}"
        )
        
    raise ValueError("Invalid price format in menu data")


def _normalize_modifiers(modifiers: list[str]) -> list[str]:
    """
    Cleans, lowercases, and sorts a list of modifier strings for consistent comparison.
    """
    if not modifiers:
        return []
    return sorted([m.strip().lower() for m in modifiers])


def _parse_price_diff(diff_str: str) -> float:
    """
    Parses upgrade price differences (e.g. "+1.50", "free", "+3.50 each") into a float.
    """
    diff_str = diff_str.lower().strip()
    if "free" in diff_str:
        return 0.0
    match = re.search(r"[-+]?\d*\.\d+|\d+", diff_str)
    if match:
        return float(match.group())
    return 0.0


def _find_upgrade_option(upgrades_dict: dict[str, Any], upgrade_val: str) -> tuple[str, float] | None:
    """
    Searches the nested upgrades dict for an option key matching the requested upgrade string.
    Returns (matched_option_name, price_diff) if found, otherwise None.
    """
    if not upgrade_val:
        return None
    val_clean = upgrade_val.lower().strip()
    
    # First Pass: Exact Match
    for category, options in upgrades_dict.items():
        for opt_key, diff in options.items():
            if opt_key.lower().strip() == val_clean:
                return opt_key, _parse_price_diff(diff)
                
    # Second Pass: Substring Match
    for category, options in upgrades_dict.items():
        for opt_key, diff in options.items():
            opt_clean = opt_key.lower().strip()
            if val_clean in opt_clean or opt_clean in val_clean:
                return opt_key, _parse_price_diff(diff)
                
    return None


# ==========================================
# PUBLIC LLM TOOL FUNCTIONS
# ==========================================

def add_item_to_cart(item_id: str, quantity: int, size: str, modifiers: list[str]) -> dict[str, Any]:
    """
    Adds a menu item (excluding combo meals) to the shopping cart.
    
    Parameters:
        item_id: The unique string identifier of the menu item (e.g., 'brg_001').
        quantity: The number of items to add. Must be greater than 0.
        size: The size variant (e.g., 'single', 'double', 'small', 'medium', 'large', 'regular').
        modifiers: A list of modification strings (e.g., ['no onions', 'extra pickles']).
        
    Returns:
        A dictionary containing:
            - success (bool): True if the action succeeded, False otherwise.
            - message (str): Description of the outcome.
            - cart (dict): The entire updated cart state.
    """
    try:
        cart = _get_cart()
        # Check if cart is finalized
        if cart["confirmed"]:
            return {
                "success": False,
                "message": "Error: Order has already been confirmed. No further modifications allowed.",
                "cart": cart
            }
            
        if quantity <= 0:
            return {
                "success": False,
                "message": f"Error: Quantity must be greater than zero. Received: {quantity}",
                "cart": cart
            }

        # Retrieve the item details
        item = _find_menu_item(item_id)
        if not item:
            return {
                "success": False,
                "message": f"Item with ID '{item_id}' not found in the menu.",
                "cart": cart
            }
            
        # Resolve size and base price
        try:
            unit_price, resolved_size = _resolve_price_and_size(item, size)
        except ValueError as val_err:
            return {
                "success": False,
                "message": str(val_err),
                "cart": cart
            }
            
        normalized_mods = _normalize_modifiers(modifiers)
        
        # Check if matching item already exists in the cart (same item_id, resolved size, and modifiers)
        found_item = None
        for cart_item in cart["items"]:
            if (cart_item["id"] == item_id and 
                cart_item["size"].lower().strip() == resolved_size.lower().strip() and 
                _normalize_modifiers(cart_item["modifiers"]) == normalized_mods):
                found_item = cart_item
                break
                
        if found_item:
            found_item["quantity"] += quantity
            found_item["subtotal"] = round(found_item["unit_price"] * found_item["quantity"], 2)
            msg = f"Incremented quantity of '{item['name']}' ({resolved_size}) in cart by {quantity}."
        else:
            new_item = {
                "id": item_id,
                "name": item["name"],
                "quantity": quantity,
                "size": resolved_size,
                "modifiers": modifiers,
                "unit_price": unit_price,
                "subtotal": round(unit_price * quantity, 2)
            }
            cart["items"].append(new_item)
            msg = f"Added {quantity} x '{item['name']}' ({resolved_size}) to cart."
            
        _recalculate_total()
        
        return {
            "success": True,
            "message": msg,
            "cart": cart
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "cart": cart
        }


def remove_item_from_cart(item_id: str, modifiers: list[str]) -> dict[str, Any]:
    """
    Removes a matched item from the cart. If the item's quantity is greater than 1,
    decrements its quantity by 1.
    
    Parameters:
        item_id: The unique identifier of the item in the cart.
        modifiers: The exact modifiers list matching the item to be removed.
        
    Returns:
        A dictionary with success status, details message, and the updated cart state.
    """
    try:
        cart = _get_cart()
        if cart["confirmed"]:
            return {
                "success": False,
                "message": "Error: Order has already been confirmed. No further modifications allowed.",
                "cart": cart
            }
            
        normalized_mods = _normalize_modifiers(modifiers)
        target_idx = -1
        
        for i, cart_item in enumerate(cart["items"]):
            if cart_item["id"] == item_id and _normalize_modifiers(cart_item["modifiers"]) == normalized_mods:
                target_idx = i
                break
                
        if target_idx == -1:
            return {
                "success": False,
                "message": f"Item with ID '{item_id}' and matching modifiers not found in cart.",
                "cart": cart
            }
            
        target_item = cart["items"][target_idx]
        name = target_item["name"]
        
        if target_item["quantity"] > 1:
            target_item["quantity"] -= 1
            target_item["subtotal"] = round(target_item["unit_price"] * target_item["quantity"], 2)
            msg = f"Decremented quantity of '{name}' by 1. New quantity: {target_item['quantity']}."
        else:
            cart["items"].pop(target_idx)
            msg = f"Removed '{name}' from cart."
            
        _recalculate_total()
        
        return {
            "success": True,
            "message": msg,
            "cart": cart
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "cart": cart
        }


def modify_item_in_cart(item_id: str, old_modifiers: list[str], new_modifiers: list[str], new_size: str) -> dict[str, Any]:
    """
    Finds a cart item by its ID and old modifiers, and updates its modifiers and/or size.
    
    Parameters:
        item_id: The unique identifier of the item.
        old_modifiers: The modifiers of the item as it currently exists in the cart.
        new_modifiers: The new list of modifiers to set.
        new_size: Optional new size variant to set.
        
    Returns:
        A dictionary with success status, details message, and the updated cart state.
    """
    try:
        cart = _get_cart()
        if cart["confirmed"]:
            return {
                "success": False,
                "message": "Error: Order has already been confirmed. No further modifications allowed.",
                "cart": cart
            }
            
        normalized_old_mods = _normalize_modifiers(old_modifiers)
        target_item = None
        
        for cart_item in cart["items"]:
            if cart_item["id"] == item_id and _normalize_modifiers(cart_item["modifiers"]) == normalized_old_mods:
                target_item = cart_item
                break
                
        if not target_item:
            return {
                "success": False,
                "message": f"Item with ID '{item_id}' and matching modifiers not found in cart.",
                "cart": cart
            }
            
        # Update modifiers
        target_item["modifiers"] = new_modifiers
        
        # Optionally update size and recalculate unit price
        size_msg = ""
        if new_size:
            menu_item = _find_menu_item_or_combo(item_id)
            if not menu_item:
                return {
                    "success": False,
                    "message": f"Could not find menu details for item ID '{item_id}' to update size.",
                    "cart": cart
                }
                
            try:
                new_price, resolved_size = _resolve_price_and_size(menu_item, new_size)
                target_item["unit_price"] = new_price
                target_item["size"] = resolved_size
                size_msg = f" and size to '{resolved_size}'"
            except ValueError as val_err:
                return {
                    "success": False,
                    "message": f"Failed to update size: {str(val_err)}",
                    "cart": cart
                }
                
        # Recalculate subtotal
        target_item["subtotal"] = round(target_item["unit_price"] * target_item["quantity"], 2)
        
        _recalculate_total()
        
        return {
            "success": True,
            "message": f"Modified modifiers{size_msg} for '{target_item['name']}' in cart.",
            "cart": cart
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "cart": cart
        }


def add_combo_to_cart(combo_id: str, burger_id: str, side_upgrade: str, drink_upgrade: str) -> dict[str, Any]:
    """
    Adds a combo meal to the cart, automatically applying side and drink upgrades.
    
    Parameters:
        combo_id: The ID of the combo package (e.g., 'cmb_001').
        burger_id: The ID of the burger choice for the combo (e.g., 'brg_001'). Optional/if applicable.
        side_upgrade: The upgraded side request (e.g., 'truffle fries').
        drink_upgrade: The upgraded drink request (e.g., 'shake (regular)').
        
    Returns:
        A dictionary with success status, details message, and the updated cart state.
    """
    try:
        cart = _get_cart()
        if cart["confirmed"]:
            return {
                "success": False,
                "message": "Error: Order has already been confirmed. No further modifications allowed.",
                "cart": cart
            }

        combo = None
        for c in menu_data.get("combos", []):
            if c.get("id") == combo_id:
                combo = c
                break
                
        if not combo:
            return {
                "success": False,
                "message": f"Combo meal with ID '{combo_id}' not found in the menu.",
                "cart": cart
            }
            
        base_price = float(combo["price"])
        extra_price = 0.0
        modifiers_list = []
        
        # 1. Burger selection (if provided)
        if burger_id:
            burger = _find_menu_item(burger_id)
            if not burger:
                return {
                    "success": False,
                    "message": f"Burger ID '{burger_id}' not found in menu.",
                    "cart": cart
                }
            modifiers_list.append(f"Burger: {burger['name']}")
            
        # 2. Side selection/upgrade
        upgrades = combo.get("upgrades", {})
        side_match = _find_upgrade_option(upgrades, side_upgrade)
        if side_match:
            side_name, side_diff = side_match
            modifiers_list.append(f"Side: {side_name}")
            extra_price += side_diff
        else:
            if side_upgrade:
                modifiers_list.append(f"Side: {side_upgrade}")
            else:
                def_side = DEFAULT_COMBO_SIDES.get(combo_id, "regular fries")
                modifiers_list.append(f"Side: {def_side}")
                
        # 3. Drink selection/upgrade
        drink_match = _find_upgrade_option(upgrades, drink_upgrade)
        if drink_match:
            drink_name, drink_diff = drink_match
            modifiers_list.append(f"Drink: {drink_name}")
            extra_price += drink_diff
        else:
            if drink_upgrade:
                modifiers_list.append(f"Drink: {drink_upgrade}")
            else:
                def_drink = DEFAULT_COMBO_DRINKS.get(combo_id, "regular soft drink")
                modifiers_list.append(f"Drink: {def_drink}")
                
        unit_price = round(base_price + extra_price, 2)
        normalized_mods = _normalize_modifiers(modifiers_list)
        
        # Check if matching combo already in cart
        found_item = None
        for cart_item in cart["items"]:
            if (cart_item["id"] == combo_id and 
                _normalize_modifiers(cart_item["modifiers"]) == normalized_mods):
                found_item = cart_item
                break
                
        if found_item:
            found_item["quantity"] += 1
            found_item["subtotal"] = round(found_item["unit_price"] * found_item["quantity"], 2)
            msg = f"Incremented quantity of '{combo['name']}' in cart by 1."
        else:
            new_item = {
                "id": combo_id,
                "name": combo["name"],
                "quantity": 1,
                "size": "regular",
                "modifiers": modifiers_list,
                "unit_price": unit_price,
                "subtotal": unit_price
            }
            cart["items"].append(new_item)
            msg = f"Added combo '{combo['name']}' to cart."
            
        _recalculate_total()
        
        return {
            "success": True,
            "message": msg,
            "cart": cart
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "cart": cart
        }


def get_cart_summary() -> dict[str, Any]:
    """
    Returns the current contents and summary of the cart.
    
    Returns:
        A dictionary with success status, details message, and the current cart state.
    """
    try:
        cart = _get_cart()
        return {
            "success": True,
            "message": "Cart summary retrieved successfully.",
            "cart": cart
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "cart": cart
        }


def confirm_order() -> dict[str, Any]:
    """
    Finalizes the order, confirming the cart contents. After confirmation,
    no more items can be added, modified, or removed.
    
    Returns:
        A dictionary with success status, details message, and the final cart state.
    """
    try:
        cart = _get_cart()
        if cart["confirmed"]:
            return {
                "success": False,
                "message": "Error: Order has already been confirmed.",
                "cart": cart
            }
            
        cart["confirmed"] = True
        return {
            "success": True,
            "message": "Order confirmed successfully.",
            "cart": cart
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "cart": cart
        }
