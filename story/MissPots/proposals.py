

def _normalize_structured_output(data):
    # --- Normalize cast ---
    scene_state_update = data.get("scene_state_update", {})

    cast_list = scene_state_update.get("cast", [])
    if isinstance(cast_list, list):
        cast_dict = {}
        for entry in cast_list:
            slug = entry.get("slug")
            if not slug:
                continue
            cast_dict[slug] = {
                "presence": entry.get("presence"),
                "position": entry.get("position"),
            }
        scene_state_update["cast"] = cast_dict

    # --- Normalize pending_intents ---
    pending_list = data.get("pending_intents", [])
    if isinstance(pending_list, list):
        pending_dict = {}
        for entry in pending_list:
            slug = entry.get("slug")
            if not slug:
                continue
            pending_dict[slug] = {
                "purpose": entry.get("purpose", ""),
                "tone": entry.get("tone", ""),
                "next": entry.get("next", ""),
            }
        data["pending_intents"] = pending_dict

    return data
