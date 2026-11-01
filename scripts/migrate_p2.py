"""Add team ownership to knowledge documents; old rows stay unassigned."""
from sqlalchemy import inspect, text
from backend.config import Settings
from backend.database import build_engine


def migrate(engine):
    existing = {column['name'] for column in inspect(engine).get_columns('knowledge_docs')}
    if 'team_id' not in existing:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE knowledge_docs ADD COLUMN team_id INTEGER NULL'))
        return True
    return False


if __name__ == '__main__':
    engine = build_engine(Settings())
    try:
        print('P2 knowledge ownership column added:', migrate(engine))
    finally:
        engine.dispose()
