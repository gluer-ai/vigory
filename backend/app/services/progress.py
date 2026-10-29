"""Progress reporting for long-running (slow small-model) extraction jobs.

A job writes its current stage onto its own Neo4j node (Document or
IngestBatch); the frontend already polls those nodes, so progress needs no
new transport. Reporting is best-effort: a failed progress write must never
fail the job itself.
"""
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from neo4j import AsyncSession

logger = logging.getLogger(__name__)

# stage text, current step, total steps
ProgressFn = Callable[[str, int | None, int | None], Awaitable[None]]

# Label/id-property pairs are interpolated into Cypher, so only these are allowed.
_TARGETS = {"Document": "document_id", "IngestBatch": "batch_id"}


async def no_progress(stage: str, current: int | None = None, total: int | None = None) -> None:
    return None


def node_progress(session: AsyncSession, label: str, node_id: str) -> ProgressFn:
    id_prop = _TARGETS[label]
    query = (
        f"MATCH (n:{label} {{{id_prop}: $id}}) "
        "SET n.stage = $stage, n.progress_current = $current, "
        "n.progress_total = $total, n.updated_at = $now"
    )

    async def report(stage: str, current: int | None = None, total: int | None = None) -> None:
        try:
            await session.run(
                query,
                id=node_id,
                stage=stage,
                current=current,
                total=total,
                now=datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            logger.warning("progress update failed for %s %s", label, node_id, exc_info=True)

    return report
