# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""This file is meant to run in the background continuously writing entries to PostgreSQL."""
import logging
import sys

import psycopg2 as psycopg2

logger = logging.getLogger(__name__)


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
        logger.error("starting loop again...")
        f = open("/tmp/demofile0.txt", "a")
        f.write(str(write_value))
        f.write("\n\n")
        f.close()
        try:
            with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
                connection.autocommit = True
                cursor.execute(f"INSERT INTO continuous_writes(number) VALUES({write_value});")
        except (
            psycopg2.InterfaceError,
            psycopg2.OperationalError,
            psycopg2.errors.ReadOnlySqlTransaction,
        ) as e:
            # We should not raise any of those exceptions that can happen when a connection failure
            # happens, for example, when a primary is being reelected after a failure on the old
            # primary.
            f = open("/tmp/demofile1.txt", "a")
            f.write(str(write_value))
            f.write("\n")
            f.write(str(e))
            f.write("\n")
            f.write(str(type(e)))
            f.write("\n\n")
            f.close()
            continue
        except psycopg2.Error as e:
            # If another error happens, like writing a duplicate number when a connection failed
            # in a previous iteration (but the transaction was already committed), just increment
            # the number.
            f = open("/tmp/demofile2.txt", "a")
            f.write(str(write_value))
            f.write("\n")
            f.write(str(e))
            f.write("\n")
            f.write(str(type(e)))
            f.write("\n\n")
            f.close()
            pass
        except Exception as e:
            f = open("/tmp/demofile3.txt", "a")
            f.write(str(write_value))
            f.write("\n")
            f.write(str(e))
            f.write("\n")
            f.write(str(type(e)))
            f.write("\n\n")
            f.close()
            pass
        finally:
            connection.close()

        f = open("/tmp/demofile4.txt", "a")
        f.write(str(write_value))
        f.write("\n\n")
        f.close()
        write_value += 1


def main():
    connection_string = sys.argv[1]
    starting_number = int(sys.argv[2])
    continuous_writes(connection_string, starting_number)


if __name__ == "__main__":
    main()
