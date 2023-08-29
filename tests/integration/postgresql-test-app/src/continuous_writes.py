# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""This file is meant to run in the background continuously writing entries to PostgreSQL."""
import multiprocessing
import os
import signal
import sys
from time import sleep

import psycopg2 as psycopg2

run = True
connection_string = None


def _sigterm_handler(_signo, _stack_frame):
    global run
    run = False


def _read_config_file():
    with open("/tmp/continuous_writes_config") as fd:
        global connection_string
        connection_string = fd.read().strip()


def continuous_writes(starting_number: int):
    """Continuously writes data do PostgreSQL database.

    Args:
        starting_number: starting number that is used to write to the database and
            is continuously incremented after each write to the database.
    """
    write_value = starting_number

    _read_config_file()

    # Continuously write the record to the database (incrementing it at each iteration).
    while run:
        process = multiprocessing.Process(target=write, args=[write_value])
        process.daemon = True
        process.start()
        process.join(10)
        if process.is_alive():
            process.terminate()
        else:
            write_value = write_value + 1

    with open("/tmp/last_written_value", "w") as fd:
        fd.write(str(write_value - 1))
        os.fsync(fd)


def write(write_value: int) -> None:
    """Writes to the database and handles expected errors."""
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
        # primary. In this case, force a timeout to not increment the written number.
        sleep(30)
    except psycopg2.Error:
        # If another error happens, like writing a duplicate number when a connection failed
        # in a previous iteration (but the transaction was already committed), just increment
        # the number.
        pass
    finally:
        connection.close()


def main():
    """Main executor."""
    starting_number = int(sys.argv[1])
    continuous_writes(starting_number)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _sigterm_handler)
    main()
