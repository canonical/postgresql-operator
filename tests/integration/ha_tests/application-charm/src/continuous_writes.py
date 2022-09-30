# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""This file is meant to run in the background continuously writing entries to PostgreSQL."""
import sys

import psycopg2 as psycopg2


def continuous_writes(connection_string: str, starting_number: int):
    """Continuously writes data do PostgreSQL database.

    Args:
        connection_string: PostgreSQL connection string.
        starting_number: starting number that is used to write to the database and
            is continuously incremented after each write to the database.
    """
    write_value = starting_number

    try:
        # Create the table to write records on and also a unique index to prevent duplicate writes.
        with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
            connection.autocommit = True
            cursor.execute("CREATE TABLE IF NOT EXISTS continuous_writes(number INTEGER);")
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS number ON continuous_writes(number);"
            )
    finally:
        connection.close()

    # Continuously write the record to the database (incrementing it at each iteration).
    while True:
        try:
            with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
                connection.autocommit = True
                cursor.execute(f"INSERT INTO continuous_writes(number) VALUES({write_value});")
        except (
            psycopg2.InterfaceError,
            psycopg2.OperationalError,
            psycopg2.errors.ReadOnlySqlTransaction,
        ):
            # We should not raise any of those exceptions that can happen when a connection failure
            # happens, for example, when a primary is being reelected after a failure on the old
            # primary.
            continue
        except psycopg2.Error:
            # If another error happens, like writing a duplicate number when a connection failed
            # in a previous iteration (but the transaction was already committed), just increment
            # the number.
            pass
        finally:
            connection.close()

        write_value += 1


def main():
    connection_string = sys.argv[1]
    starting_number = int(sys.argv[2])
    continuous_writes(connection_string, starting_number)


if __name__ == "__main__":
    main()
