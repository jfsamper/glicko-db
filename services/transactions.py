"""One transaction per command: helper commits are deferred until the outer command ends."""
import functools
from contextlib import contextmanager

# Keyed by id(): sqlite3.Connection objects cannot be weakly referenced. The caller
# keeps each connection alive for the whole command, so the id stays unique.
_commands_in_progress = set()


def commit(conn):
    """Commit, unless ``conn`` is inside a command that will commit as one unit."""
    if id(conn) not in _commands_in_progress:
        conn.commit()


@contextmanager
def command(conn):
    """Run a write command as one transaction; nested commands join the outer one."""
    if id(conn) in _commands_in_progress:
        yield conn
        return
    _commands_in_progress.add(id(conn))
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        _commands_in_progress.discard(id(conn))


def transactional(function):
    """Decorate a service function whose first argument is the connection."""
    @functools.wraps(function)
    def wrapper(conn, *args, **kwargs):
        with command(conn):
            return function(conn, *args, **kwargs)
    return wrapper
