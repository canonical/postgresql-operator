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


def sigterm_handler(_signo, _stack_frame):
    global run
    run = False


def sighup_handler(_signo, _stack_frame):
    read_config_file()


def read_config_file():
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

    read_config_file()

    # Continuously write the record to the database (incrementing it at each iteration).
    while run:
        process = multiprocessing.Process(target=write, args=[write_value])
        process.daemon = True
        process.start()
        process.join(10)
        if process.is_alive():
            process.terminate()
            with open("/tmp/error", "a") as fd:
                fd.write("\n terminated")
                os.fsync(fd)
        else:
            with open("/tmp/error", "a") as fd:
                fd.write(f"\n write_value:{str(write_value)}")
                os.fsync(fd)
            write_value = write_value + 1

    with open("/tmp/last_written_value", "w") as fd:
        fd.write(str(write_value - 1))
        os.fsync(fd)


def write(write_value: int) -> None:
    try:
        with open("/tmp/error", "a") as fd:
            fd.write("\n before alarm set")
            os.fsync(fd)
        signal.alarm(30)
        with open("/tmp/error", "a") as fd:
            fd.write("\n before connect")
            os.fsync(fd)
        with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
            connection.autocommit = True
            with open("/tmp/error", "a") as fd:
                fd.write("\n after connect")
                os.fsync(fd)
            with open("/tmp/error", "a") as fd:
                fd.write("\n before insert connect")
                os.fsync(fd)
            cursor.execute(f"INSERT INTO continuous_writes(number) VALUES({write_value});")
            with open("/tmp/error", "a") as fd:
                fd.write(f"\n after insert connect: {write_value}")
                os.fsync(fd)
        with open("/tmp/error", "a") as fd:
            fd.write("\n after cursor")
            os.fsync(fd)
        with open("/tmp/error", "a") as fd:
            fd.write("\n after connection")
            os.fsync(fd)
    except (
        psycopg2.InterfaceError,
        psycopg2.OperationalError,
        psycopg2.errors.ReadOnlySqlTransaction,
    ) as e:
        # We should not raise any of those exceptions that can happen when a connection failure
        # happens, for example, when a primary is being reelected after a failure on the old
        # primary.
        with open("/tmp/error", "a") as fd:
            fd.write(f"\n continue - {str(e)}")
            os.fsync(fd)
        sleep(200)
    except psycopg2.Error as e:
        # If another error happens, like writing a duplicate number when a connection failed
        # in a previous iteration (but the transaction was already committed), just increment
        # the number.
        with open("/tmp/error", "a") as fd:
            fd.write(f"\n psycopg2.Error - {str(e)}")
            os.fsync(fd)
    except Exception as e:
        with open("/tmp/error", "a") as fd:
            fd.write(f"\n Exception - {str(e)}")
            os.fsync(fd)
    finally:
        with open("/tmp/error", "a") as fd:
            fd.write("\n finally 1")
            os.fsync(fd)
        connection.close()
        with open("/tmp/error", "a") as fd:
            fd.write("\n finally 2")
            os.fsync(fd)


def main():
    starting_number = int(sys.argv[1])
    continuous_writes(starting_number)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGHUP, sighup_handler)
    main()
