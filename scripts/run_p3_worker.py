"""Run one independent P3 worker process after queue tables are migrated."""

import asyncio
import logging
import os
import socket
from uuid import uuid4

from backend.main import create_app
from backend.services.durable_agent_adapter import execute_attempt
from backend.services.durable_worker import run_one


async def main() -> None:
    worker_id = f"{socket.gethostname()[:64]}-{os.getpid()}-{uuid4().hex[:8]}"
    app = create_app(reconcile_approvals=False)
    async with app.router.lifespan_context(app):
        logging.info("P3 worker started: %s", worker_id)
        while True:
            try:
                worked = await asyncio.to_thread(
                    run_one, app.state.session_factory, worker_id,
                    lambda execution: execute_attempt(app.state, execution),
                )
            except Exception:
                logging.exception("P3_WORKER_POLL_FAILED")
                await asyncio.sleep(3)
                continue
            if not worked:
                await asyncio.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
