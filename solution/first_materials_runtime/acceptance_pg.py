"""Изолированный PostgreSQL-адаптер для CP2 B8-04.

Он хранит только собственное транзакционное состояние wrapper-а. Внешние
F129/F130/F115-владельцы остаются синтетическими портами стенда до CP3.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .api import create_service as create_materials_service


_SCHEMA_RE = re.compile(r"b8_accept_[a-z0-9_]{1,40}\Z")
_MIGRATION = Path(__file__).with_name("migrations") / "0001.sql"


def _schema_identifier(schema):
    if type(schema) is not str or _SCHEMA_RE.fullmatch(schema) is None:
        raise ValueError("isolated b8_accept_* schema required")

    from psycopg import sql

    return sql.Identifier(schema)


def _scope_key(scope):
    return json.dumps(
        scope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _rollback_quietly(connection):
    """Cleanup до начала эффектов не должен заменять причину отказа."""
    if connection is not None:
        try:
            connection.rollback()
        except Exception:
            pass


def _close_quietly(connection):
    """Освобождение ресурса не меняет уже установленный исход транзакции.

    Здесь нет retry, commit или повторного вызова зависимости. Ошибка close
    не доказывает потерю уже полученного подтверждения commit/rollback.
    """
    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass


def initialize(connect_factory, schema):
    """Создаём только собственную схему и таблицы B8-04."""
    from psycopg import sql

    identifier = _schema_identifier(schema)
    template = _MIGRATION.read_text(encoding="utf-8")
    statement = sql.SQL(template).format(schema=identifier)

    connection = None
    try:
        connection = connect_factory()
        connection.execute(statement)
        connection.commit()
    except BaseException:
        _rollback_quietly(connection)
        raise
    finally:
        _close_quietly(connection)


def seed(connect_factory, schema, fixture):
    """Загружаем выданный synthetic seed перед before_restart."""
    from psycopg import sql

    initialize(connect_factory, schema)
    identifier = _schema_identifier(schema)
    scope = fixture["fixture_scope"]
    scope_key = _scope_key(scope)
    state = fixture.get("seed", {})

    connection = None
    try:
        connection = connect_factory()
        connection.execute(
            sql.SQL("DELETE FROM {}.state").format(identifier)
        )
        connection.execute(
            sql.SQL("DELETE FROM {}.scope_locks").format(identifier)
        )
        connection.execute(
            sql.SQL(
                "INSERT INTO {}.scope_locks(scope_key) VALUES (%s)"
            ).format(identifier),
            (scope_key,),
        )
        for bucket, rows in state.items():
            for item_key, value in rows.items():
                connection.execute(
                    sql.SQL(
                        "INSERT INTO {}.state"
                        "(scope_key,bucket,item_key,value) "
                        "VALUES (%s,%s,%s,%s::jsonb)"
                    ).format(identifier),
                    (scope_key, bucket, item_key, _json(value)),
                )
        connection.commit()
    except BaseException:
        _rollback_quietly(connection)
        raise
    finally:
        _close_quietly(connection)


class PostgresTransaction:
    """Одна транзакция с явной последовательностью запросов внутри scope."""

    def __init__(self, owner, scope):
        self.owner = owner
        self.scope_key = _scope_key(scope)
        self.connection = None
        self._delegate_state = None
        self._delegate_writes = None

    def __enter__(self):
        from psycopg import Error, sql

        if self.owner._active is not None:
            raise RuntimeError("nested B8-04 transactions are forbidden")

        try:
            # Соединение может не быть создано вовсе. До успешного входа
            # в контекст бизнес-порты и их эффекты ещё не вызываются.
            self.connection = self.owner.connect_factory()
            # READ COMMITTED здесь намеренно сочетается с FOR UPDATE.
            # Ожидающий запрос после снятия блокировки обязан увидеть replay,
            # который только что зафиксировал первый запрос. SERIALIZABLE
            # сохранил бы старый snapshot и завершился SerializationFailure.
            self.connection.execute(
                "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"
            )
            self.connection.execute(
                sql.SQL(
                    "INSERT INTO {}.scope_locks(scope_key) VALUES (%s) "
                    "ON CONFLICT(scope_key) DO NOTHING"
                ).format(self.owner.identifier),
                (self.scope_key,),
            )
            self.connection.execute(
                sql.SQL(
                    "SELECT scope_key FROM {}.scope_locks "
                    "WHERE scope_key=%s FOR UPDATE"
                ).format(self.owner.identifier),
                (self.scope_key,),
            ).fetchone()

            delegate = self.owner.delegate
            if hasattr(delegate, "state"):
                self._delegate_state = copy.deepcopy(delegate.state)
            if hasattr(delegate, "writes"):
                self._delegate_writes = len(delegate.writes)
        except BaseException as error:
            _rollback_quietly(self.connection)
            self._release()
            if isinstance(error, Error):
                raise ConnectionError(
                    "PostgreSQL transaction could not be opened"
                ) from error
            raise

        self.owner._active = self
        return self

    def __exit__(self, exc_type, exc, tb):
        connection = self.connection
        try:
            if exc_type is not None:
                try:
                    connection.rollback()
                except Exception as rollback_error:
                    self._restore_delegate()
                    raise TimeoutError(
                        "transaction rollback outcome is unknown"
                    ) from rollback_error
                self._restore_delegate()
                return False

            try:
                connection.commit()
            except Exception as commit_error:
                self._restore_delegate()
                raise TimeoutError(
                    "transaction commit outcome is unknown"
                ) from commit_error
            return False
        finally:
            self._release()

    def _release(self):
        """Отсоединяем завершённую/повреждённую сессию на любом пути."""
        connection = self.connection
        self.connection = None
        if self.owner._active is self:
            self.owner._active = None
        self._delegate_state = None
        self._delegate_writes = None
        _close_quietly(connection)

    def _restore_delegate(self):
        delegate = self.owner.delegate
        if self._delegate_state is not None:
            delegate.state = copy.deepcopy(self._delegate_state)
        if self._delegate_writes is not None:
            del delegate.writes[self._delegate_writes:]

    def get(self, bucket, key):
        from psycopg import sql

        row = self._execute(
            sql.SQL(
                "SELECT value FROM {}.state "
                "WHERE scope_key=%s AND bucket=%s AND item_key=%s"
            ).format(self.owner.identifier),
            (self.scope_key, bucket, key),
        ).fetchone()
        return copy.deepcopy(row[0]) if row is not None else None

    def put(self, bucket, key, value):
        from psycopg import sql

        self._execute(
            sql.SQL(
                "INSERT INTO {}.state"
                "(scope_key,bucket,item_key,value) "
                "VALUES (%s,%s,%s,%s::jsonb) "
                "ON CONFLICT(scope_key,bucket,item_key) "
                "DO UPDATE SET value=excluded.value"
            ).format(self.owner.identifier),
            (self.scope_key, bucket, key, _json(value)),
        )

    def remove(self, bucket, key):
        from psycopg import sql

        self._execute(
            sql.SQL(
                "DELETE FROM {}.state "
                "WHERE scope_key=%s AND bucket=%s AND item_key=%s"
            ).format(self.owner.identifier),
            (self.scope_key, bucket, key),
        )

    def scan(self, bucket):
        from psycopg import sql

        rows = self._execute(
            sql.SQL(
                "SELECT item_key,value FROM {}.state "
                "WHERE scope_key=%s AND bucket=%s ORDER BY item_key"
            ).format(self.owner.identifier),
            (self.scope_key, bucket),
        ).fetchall()
        return [(key, copy.deepcopy(value)) for key, value in rows]

    def _execute(self, statement, parameters):
        """DB-сбой в теле откатится и станет публичной ошибкой зависимости."""
        from psycopg import Error

        try:
            return self.connection.execute(statement, parameters)
        except Exception as error:
            if isinstance(error, Error):
                raise ConnectionError("PostgreSQL statement failed") from error
            raise


class PostgresPorts:
    """Сохраняем поведение harness ports, заменяя только state backend."""

    def __init__(self, connect_factory, schema, delegate):
        self.connect_factory = connect_factory
        self.schema = schema
        self.identifier = _schema_identifier(schema)
        self.delegate = delegate
        self.auth = delegate.auth
        self._active = None

    def transaction(self, scope):
        return PostgresTransaction(self, scope)

    def call(self, name, request):
        if self._active is None:
            raise RuntimeError("dependency call outside transaction")

        writes = getattr(self.delegate, "writes", None)
        before = len(writes) if writes is not None else 0
        result = self.delegate.call(name, request)

        if writes is not None:
            for bucket, key, value in writes[before:]:
                if value is None:
                    self._active.remove(bucket, key)
                else:
                    self._active.put(bucket, key, value)
        return result

    def __getattr__(self, name):
        return getattr(self.delegate, name)


def create_service(connect_factory, schema, ports):
    """Подключаем настоящую MaterialsService к PostgreSQL state backend."""
    initialize(connect_factory, schema)
    return create_materials_service(
        PostgresPorts(connect_factory, schema, ports)
    )
