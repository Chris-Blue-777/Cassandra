VALID_COVERAGE_MODES = {"hidden_objective", "split_screen"}
VALID_REVEAL_POLICIES = {
    "show_resolved_spaces_now",
    "withhold_until_user_or_arc_changes",
    "withhold_until_explicit_reveal",
}
VALID_CUE_ACCESS = {
    "direct_partial",
    "mediated_audio",
    "mediated_text",
    "inferred",
    "none",
}


def clean_space_id(value):
    if not isinstance(value, str):
        return "unknown_space"

    value = value.strip().lower()
    value = value.replace("'", "")
    value = value.replace('"', "")
    value = "_".join(value.split())

    cleaned = "".join(ch for ch in value if ch.isalnum() or ch == "_")
    return cleaned or "unknown_space"


def clean_space_id_list(value):
    if value in ("", None):
        return []

    if isinstance(value, str):
        value = [
            item.strip()
            for item in value.replace("\n", ",").split(",")
            if item.strip()
        ]

    if not isinstance(value, list):
        return []

    cleaned = []
    for item in value:
        space_id = clean_space_id(item)
        if space_id and space_id not in cleaned:
            cleaned.append(space_id)

    return cleaned


def _clean_coverage_mode(value, fallback="split_screen"):
    value = str(value or "").strip().lower()
    return value if value in VALID_COVERAGE_MODES else fallback


def _clean_reveal_policy(value, coverage_mode):
    value = str(value or "").strip().lower()
    if value in VALID_REVEAL_POLICIES:
        return value

    if coverage_mode == "hidden_objective":
        return "withhold_until_user_or_arc_changes"

    return "show_resolved_spaces_now"


def _clean_cue_access(value):
    value = str(value or "").strip().lower()
    return value if value in VALID_CUE_ACCESS else "none"


def normalize_cue_channels(value):
    if not isinstance(value, list):
        return []

    channels = []
    for item in value:
        if not isinstance(item, dict):
            continue

        from_space_id = clean_space_id(item.get("from_space_id"))
        to_space_id = clean_space_id(item.get("to_space_id"))

        if not from_space_id or not to_space_id:
            continue

        channels.append({
            "from_space_id": from_space_id,
            "to_space_id": to_space_id,
            "access": _clean_cue_access(item.get("access")),
            "description": str(item.get("description") or "").strip(),
        })

    return channels


def infer_default_coverage_mode(camera_scope, active_space_ids):
    camera_scope = str(camera_scope or "").strip().lower()

    if camera_scope == "omniscient_multi_space":
        return "split_screen"

    if len(active_space_ids or []) > 1:
        return "hidden_objective"

    return "split_screen"


def normalize_narrative_frame(raw_frame, spaces=None):
    raw_frame = raw_frame if isinstance(raw_frame, dict) else {}
    spaces = spaces if isinstance(spaces, dict) else {}

    camera_scope = str(raw_frame.get("camera_scope") or "single_space").strip()

    active_space_ids = clean_space_id_list(raw_frame.get("active_space_ids"))
    if not active_space_ids:
        active_space_ids = list(spaces.keys())

    fallback_coverage = infer_default_coverage_mode(camera_scope, active_space_ids)
    coverage_mode = _clean_coverage_mode(
        raw_frame.get("coverage_mode"),
        fallback=fallback_coverage,
    )

    resolved_space_ids = clean_space_id_list(raw_frame.get("resolved_space_ids"))
    if not resolved_space_ids:
        resolved_space_ids = list(active_space_ids)

    reader_visible_space_ids = clean_space_id_list(
        raw_frame.get("reader_visible_space_ids")
    )
    if not reader_visible_space_ids:
        if coverage_mode == "split_screen":
            reader_visible_space_ids = list(resolved_space_ids)
        elif active_space_ids:
            reader_visible_space_ids = [active_space_ids[0]]
        else:
            reader_visible_space_ids = []

    cue_channels = normalize_cue_channels(raw_frame.get("cue_channels"))
    reveal_policy = _clean_reveal_policy(
        raw_frame.get("reveal_policy"),
        coverage_mode,
    )

    return {
        "summary_location": raw_frame.get("summary_location"),
        "camera_scope": camera_scope,
        "active_space_ids": active_space_ids,
        "coverage_mode": coverage_mode,
        "resolved_space_ids": resolved_space_ids,
        "reader_visible_space_ids": reader_visible_space_ids,
        "cue_channels": cue_channels,
        "reveal_policy": reveal_policy,
    }
