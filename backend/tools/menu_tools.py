"""
menu_tools.py

This module contains read-only functions that a LiveKit voice agent can call
to query and search the menu of Forge & Flame restaurant. It loads and references
the restaurant's menu data.
"""

import json
import os
from typing import Any

# Load Menu Data
_current_dir = os.path.dirname(os.path.abspath(__file__))
_menu_path = os.path.normpath(os.path.join(_current_dir, "..", "context", "menu.json"))

try:
    with open(_menu_path, "r", encoding="utf-8") as f:
        menu_data: dict[str, Any] = json.load(f)
except Exception as e:
    raise RuntimeError(f"Could not load menu.json from {_menu_path}: {e}")

# Valid categories from menu
VALID_CATEGORIES = menu_data.get("categories", [
    "burgers", "sandwiches", "pizzas", "wraps", "tenders", "fries", "shakes", "iced_teas", "soft_drinks"
])

# Valid allergen reference list
VALID_ALLERGENS = ["gluten", "dairy", "egg", "nuts", "soy", "fish", "shellfish", "sesame"]


# ==========================================
# PRIVATE HELPER FUNCTIONS
# ==========================================

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


def _has_allergen(item_allergens: list[str], allergen: str) -> bool:
    """
    Helper to determine if a list of item allergens matches a target allergen.
    Provides flexible matching (e.g., 'nuts' will match 'tree nuts' or 'peanuts').
    """
    allergen_clean = allergen.lower().strip()
    for item_allergen in item_allergens:
        ia_clean = item_allergen.lower().strip()
        if allergen_clean in ia_clean or ia_clean in allergen_clean:
            return True
        if allergen_clean == "nuts" and "nut" in ia_clean:
            return True
    return False


# ==========================================
# PUBLIC LLM TOOL FUNCTIONS
# ==========================================

def search_menu(query: str) -> dict[str, Any]:
    """
    Searches across all menu categories for items whose name or description matches the query string.
    
    Parameters:
        query: The search term (e.g., 'spicy', 'chicken', 'truffle'). Case-insensitive.
        
    Returns:
        A dictionary containing:
            - success (bool): True if matching items are found, False otherwise.
            - message (str): Status/guidance message.
            - data (list): List of matching items with id, name, category, price, and description.
    """
    try:
        query_clean = query.lower().strip()
        if not query_clean:
            return {
                "success": False,
                "message": "Query cannot be empty. Please specify a food item, ingredient, or style to search.",
                "data": []
            }

        results = []
        menu = menu_data.get("menu", {})
        
        for category, items in menu.items():
            for item in items:
                name = item.get("name", "")
                desc = item.get("description", "")
                if query_clean in name.lower() or query_clean in desc.lower():
                    results.append({
                        "id": item.get("id"),
                        "name": name,
                        "category": category,
                        "price": item.get("price"),
                        "description": desc
                    })
                    
        if not results:
            return {
                "success": False,
                "message": f"No items found matching '{query}'. Try asking for a specific category like burgers, sandwiches, or pizzas.",
                "data": []
            }
            
        return {
            "success": True,
            "message": f"Found {len(results)} items matching '{query}'.",
            "data": results
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred during menu search: {str(e)}",
            "data": []
        }


def get_item_details(item_id: str) -> dict[str, Any]:
    """
    Retrieves the complete details of a single menu item looked up by its item_id.
    
    Parameters:
        item_id: The unique identifier of the item (e.g., 'brg_001').
        
    Returns:
        A dictionary containing:
            - success (bool): True if found, False otherwise.
            - message (str): Status/outcome message.
            - data (dict/list): Complete details dictionary of the item, or empty list if not found.
    """
    try:
        item = _find_menu_item(item_id)
        if not item:
            return {
                "success": False,
                "message": f"Menu item with ID '{item_id}' not found.",
                "data": []
            }
            
        return {
            "success": True,
            "message": f"Details for '{item.get('name')}' retrieved successfully.",
            "data": item
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "data": []
        }


def get_category_items(category: str) -> dict[str, Any]:
    """
    Retrieves all items in a given category. Valid categories include:
    'burgers', 'sandwiches', 'pizzas', 'wraps', 'tenders', 'fries', 'shakes', 'iced_teas', 'soft_drinks'.
    
    Parameters:
        category: The category name to retrieve. Case-insensitive.
        
    Returns:
        A dictionary containing:
            - success (bool): True if category exists and contains items, False otherwise.
            - message (str): Status/outcome message.
            - data (list): Summary list of items in the category, each containing id, name, price, and description.
    """
    try:
        cat_clean = category.lower().strip()
        menu = menu_data.get("menu", {})
        
        # Check if the category exists directly or loosely matches
        matched_category = None
        for key in menu.keys():
            if key.lower().strip() == cat_clean:
                matched_category = key
                break
                
        if not matched_category:
            categories_str = ", ".join(VALID_CATEGORIES)
            return {
                "success": False,
                "message": f"Category '{category}' is invalid. Valid categories: {categories_str}.",
                "data": []
            }
            
        items = menu[matched_category]
        summary_items = []
        
        for item in items:
            summary_items.append({
                "id": item.get("id"),
                "name": item.get("name"),
                "price": item.get("price"),
                "description": item.get("description")
            })
            
        return {
            "success": True,
            "message": f"Found {len(summary_items)} items in category '{matched_category}'.",
            "data": summary_items
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "data": []
        }


def get_combo_details(combo_id: str) -> dict[str, Any]:
    """
    Retrieves full details of a specific combo meal, including base price, included items,
    and available upgrades.
    
    Parameters:
        combo_id: The unique identifier of the combo (e.g., 'cmb_001').
        
    Returns:
        A dictionary containing:
            - success (bool): True if found, False otherwise.
            - message (str): Status/outcome message.
            - data (dict/list): Complete combo details dictionary, or empty list if not found.
    """
    try:
        combos = menu_data.get("combos", [])
        combo = None
        for c in combos:
            if c.get("id") == combo_id:
                combo = c
                break
                
        if not combo:
            return {
                "success": False,
                "message": f"Combo meal with ID '{combo_id}' not found.",
                "data": []
            }
            
        return {
            "success": True,
            "message": f"Details for combo '{combo.get('name')}' retrieved successfully.",
            "data": combo
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "data": []
        }


def get_all_combos() -> dict[str, Any]:
    """
    Retrieves a summary list of all available combo deals on the menu.
    
    Returns:
        A dictionary containing:
            - success (bool): True if combos are loaded, False otherwise.
            - message (str): Status/outcome message.
            - data (list): List of combo summaries, each containing id, name, included, price, and base_price.
    """
    try:
        combos = menu_data.get("combos", [])
        summary_list = []
        
        for c in combos:
            summary_list.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "included": c.get("included"),
                "price": c.get("price"),
                "base_price": c.get("price")
            })
            
        return {
            "success": True,
            "message": f"Found {len(summary_list)} combo deals.",
            "data": summary_list
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "data": []
        }


def filter_menu_by_allergen(allergen: str) -> dict[str, Any]:
    """
    Filters the menu to return all items that do NOT contain the specified allergen.
    Valid allergens include: 'gluten', 'dairy', 'egg', 'nuts', 'soy', 'fish', 'shellfish', 'sesame'.
    
    Parameters:
        allergen: The allergen to filter out (case-insensitive).
        
    Returns:
        A dictionary containing:
            - success (bool): True if items are found, False otherwise.
            - message (str): Status/outcome message.
            - data (dict/list): Dictionary grouping allergen-free items by category, or empty list if no items matched.
    """
    try:
        allergen_clean = allergen.lower().strip()
        menu = menu_data.get("menu", {})
        filtered_menu = {}
        
        total_found = 0
        for category, items in menu.items():
            safe_items = []
            for item in items:
                allergens_list = item.get("allergens", [])
                if not _has_allergen(allergens_list, allergen_clean):
                    safe_items.append({
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "price": item.get("price"),
                        "description": item.get("description")
                    })
            if safe_items:
                filtered_menu[category] = safe_items
                total_found += len(safe_items)
                
        if total_found == 0:
            return {
                "success": False,
                "message": f"No items found that are safe from allergen '{allergen}'.",
                "data": []
            }
            
        return {
            "success": True,
            "message": f"Found {total_found} items safe from '{allergen}' grouped by category.",
            "data": filtered_menu
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "data": []
        }


def get_item_modifiers(item_id: str) -> dict[str, Any]:
    """
    Retrieves the available customization modifiers for a specific menu item.
    
    Parameters:
        item_id: The unique identifier of the menu item (e.g., 'brg_001').
        
    Returns:
        A dictionary containing:
            - success (bool): True if the item is found, False otherwise.
            - message (str): Status/outcome message.
            - data (list): List of customizable modifier strings, or empty list if not found.
    """
    try:
        item = _find_menu_item(item_id)
        if not item:
            return {
                "success": False,
                "message": f"Menu item with ID '{item_id}' not found.",
                "data": []
            }
            
        modifiers = item.get("available_modifiers", [])
        return {
            "success": True,
            "message": f"Retrieved modifiers for '{item.get('name')}'.",
            "data": modifiers
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}",
            "data": []
        }
