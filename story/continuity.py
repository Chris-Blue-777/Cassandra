from django.db import transaction
from openai import OpenAI

from .models import NarrativeMemory


NARRATIVE_CONTEXT_HISTORY_LIMIT = 8
NARRATIVE_CONTEXT_PAST_LIMIT = 5
NARRATIVE_CONTEXT_RAW_LIMIT = 4
NARRATIVE_RAW_RECENT_KEEP = 5
NARRATIVE_RAW_TO_PAST_BATCH_SIZE = 8
NARRATIVE_PAST_RECENT_KEEP = 2
NARRATIVE_PAST_TO_HISTORY_BATCH_SIZE = 5

MODEL_NAME = "gpt-5.4"
client = OpenAI()


NARRATIVE_COMPACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "content": {"type": "string"},
    },
    "required": ["content"],
}

NARRATIVE_COMPACTION_SYSTEM_PROMPT = """
You condense Cassandra's world-level narrative continuity memories.

Return valid JSON matching the schema.
Preserve story trajectory, unresolved pressure, scene direction, and continuity anchors.
Do not merely concatenate the source memories.
"""

NARRATIVE_COMPACTION_DEVELOPER_PROMPT = """
You will receive a JSON payload containing world-level narrative memories.

Task:
Create one compact narrative memory summary.

If target_layer is "past":
- Summarize the recent stretch of scene continuity.
- Emphasize what changed, what remains unresolved, and what pressure should shape upcoming scenes.

If target_layer is "history":
- Summarize multiple Past summaries into a broader durable story trajectory.
- Emphasize what the whole stretch has come to mean for the narrative.

Rules:
- Keep the result concise but useful for future Cassandra drafts.
- Preserve concrete continuity anchors that would prevent contradiction.
- Preserve unresolved tensions and active direction.
- Do not introduce facts not present in the source memories.
- Avoid repeated phrasing from the source memories.
"""


def active_narrative_memories_for_context(world):
    histories = list(
        NarrativeMemory.objects.filter(
            world=world,
            memory_layer=NarrativeMemory.MEMORY_LAYER_HISTORY,
            is_context_active=True,
        ).order_by("-created_at")[:NARRATIVE_CONTEXT_HISTORY_LIMIT]
    )[::-1]

    pasts = list(
        NarrativeMemory.objects.filter(
            world=world,
            memory_layer=NarrativeMemory.MEMORY_LAYER_PAST,
            is_context_active=True,
        ).order_by("-created_at")[:NARRATIVE_CONTEXT_PAST_LIMIT]
    )[::-1]

    raw = list(
        NarrativeMemory.objects.filter(
            world=world,
            memory_layer=NarrativeMemory.MEMORY_LAYER_RAW,
            is_context_active=True,
        ).order_by("-created_at")[:NARRATIVE_CONTEXT_RAW_LIMIT]
    )[::-1]

    return histories + pasts + raw


def _fallback_summary(world, source_memories, target_layer):
    label = "History" if target_layer == NarrativeMemory.MEMORY_LAYER_HISTORY else "Past"
    snippets = [
        str(memory.content or "").strip()
        for memory in source_memories
        if str(memory.content or "").strip()
    ]
    joined = " ".join(snippets)
    if len(joined) > 1600:
        joined = joined[:1600].rstrip() + "..."
    return f"{label} narrative summary for {world.name}: {joined}"


def _narrative_memory_payload(memory):
    return {
        "id": memory.id,
        "content": memory.content,
        "memory_layer": memory.memory_layer,
        "source_turn": (
            memory.source_scene.turn_number
            if memory.source_scene_id
            else None
        ),
        "source_memory_count": memory.source_memory_count,
    }


def _narrative_compaction_task(source_memories, target_layer):
    source_ids = [memory.id for memory in source_memories]

    return {
        "task_id": f"narrative:{target_layer}:{'-'.join(str(i) for i in source_ids)}",
        "target_layer": target_layer,
        "source_memory_ids": source_ids,
        "source_memories": [
            _narrative_memory_payload(memory)
            for memory in source_memories
        ],
    }


def narrative_continuity_maintenance_tasks(world):
    """
    Return narrative compaction tasks for Cassandra's next existing meta call.

    This only decides whether compaction is due; it does not summarize.
    """
    tasks = []

    active_raw = list(
        NarrativeMemory.objects.filter(
            world=world,
            memory_layer=NarrativeMemory.MEMORY_LAYER_RAW,
            is_context_active=True,
        ).order_by("created_at")
    )

    if len(active_raw) >= NARRATIVE_RAW_RECENT_KEEP + NARRATIVE_RAW_TO_PAST_BATCH_SIZE:
        tasks.append(_narrative_compaction_task(
            active_raw[:NARRATIVE_RAW_TO_PAST_BATCH_SIZE],
            NarrativeMemory.MEMORY_LAYER_PAST,
        ))

    active_pasts = list(
        NarrativeMemory.objects.filter(
            world=world,
            memory_layer=NarrativeMemory.MEMORY_LAYER_PAST,
            is_context_active=True,
        ).order_by("created_at")
    )

    if len(active_pasts) >= NARRATIVE_PAST_RECENT_KEEP + NARRATIVE_PAST_TO_HISTORY_BATCH_SIZE:
        tasks.append(_narrative_compaction_task(
            active_pasts[:NARRATIVE_PAST_TO_HISTORY_BATCH_SIZE],
            NarrativeMemory.MEMORY_LAYER_HISTORY,
        ))

    return {
        "narrative_compactions": tasks,
    }


def _summarize_narrative_memories(world, source_memories, target_layer):
    # Compaction must not add an extra model call to scene generation.
    # Cassandra's existing draft/aftermath calls create the raw material; this
    # reducer only performs deterministic bookkeeping inside the fixed call budget.
    return _fallback_summary(world, source_memories, target_layer)


def _latest_source_scene(source_memories):
    scenes = [
        memory.source_scene
        for memory in source_memories
        if memory.source_scene_id
    ]

    if not scenes:
        return None

    return sorted(scenes, key=lambda scene: scene.turn_number)[-1]


def _source_memory_count(source_memories):
    total = 0

    for memory in source_memories:
        total += memory.source_memory_count or 1

    return total


def _compact_narrative_memory_batch(
    world,
    source_memories,
    target_layer,
    summary_content=None,
):
    if not source_memories:
        return None

    content = str(summary_content or "").strip()
    if not content:
        content = _summarize_narrative_memories(
            world,
            source_memories,
            target_layer,
        )
    source_ids = [memory.id for memory in source_memories]

    with transaction.atomic():
        compacted_memory = NarrativeMemory.objects.create(
            world=world,
            content=content,
            memory_layer=target_layer,
            is_context_active=True,
            source_scene=_latest_source_scene(source_memories),
            source_memory_ids_json=source_ids,
            source_memory_count=_source_memory_count(source_memories),
        )

        NarrativeMemory.objects.filter(id__in=source_ids).update(
            is_context_active=False,
            compacted_into=compacted_memory,
        )

    print(
        "[story] narrative_memory_compacted",
        "world=",
        world.name,
        "target_layer=",
        target_layer,
        "source_count=",
        len(source_memories),
        "summary_id=",
        compacted_memory.id,
        flush=True,
    )

    return compacted_memory


def _compaction_output_by_task_id(outputs):
    if not isinstance(outputs, list):
        return {}

    output_by_id = {}

    for output in outputs:
        if not isinstance(output, dict):
            continue

        task_id = str(output.get("task_id") or "").strip()
        if task_id:
            output_by_id[task_id] = output

    return output_by_id


def apply_narrative_continuity_maintenance(world, tasks, maintenance_output):
    """
    Persist Cassandra-written compactions from the existing draft call.

    If Cassandra omits a due task, the deterministic fallback still folds the
    batch so context cannot grow forever.
    """
    if not isinstance(tasks, dict):
        return

    if not isinstance(maintenance_output, dict):
        maintenance_output = {}

    output_by_id = _compaction_output_by_task_id(
        maintenance_output.get("narrative_compactions") or []
    )

    for task in tasks.get("narrative_compactions") or []:
        if not isinstance(task, dict):
            continue

        source_ids = task.get("source_memory_ids") or []
        target_layer = task.get("target_layer")
        source_memories = list(
            NarrativeMemory.objects.filter(
                world=world,
                id__in=source_ids,
                is_context_active=True,
            ).order_by("created_at")
        )

        if not source_memories:
            continue

        output = output_by_id.get(task.get("task_id")) or {}
        _compact_narrative_memory_batch(
            world,
            source_memories,
            target_layer,
            summary_content=output.get("content"),
        )


def compact_narrative_memories_if_needed(world):
    active_raw = list(
        NarrativeMemory.objects.filter(
            world=world,
            memory_layer=NarrativeMemory.MEMORY_LAYER_RAW,
            is_context_active=True,
        ).order_by("created_at")
    )

    if len(active_raw) >= NARRATIVE_RAW_RECENT_KEEP + NARRATIVE_RAW_TO_PAST_BATCH_SIZE:
        _compact_narrative_memory_batch(
            world,
            active_raw[:NARRATIVE_RAW_TO_PAST_BATCH_SIZE],
            NarrativeMemory.MEMORY_LAYER_PAST,
        )

    active_pasts = list(
        NarrativeMemory.objects.filter(
            world=world,
            memory_layer=NarrativeMemory.MEMORY_LAYER_PAST,
            is_context_active=True,
        ).order_by("created_at")
    )

    if len(active_pasts) >= NARRATIVE_PAST_RECENT_KEEP + NARRATIVE_PAST_TO_HISTORY_BATCH_SIZE:
        _compact_narrative_memory_batch(
            world,
            active_pasts[:NARRATIVE_PAST_TO_HISTORY_BATCH_SIZE],
            NarrativeMemory.MEMORY_LAYER_HISTORY,
        )
