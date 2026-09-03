import os

import psycopg

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@timescaledb:5432/sttdemo"
)


def get_connection() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)
