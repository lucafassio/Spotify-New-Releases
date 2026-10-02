from importlib import resources

from app.db import connect

CREATE_MIGRATIONS_TABLE = '''
CREATE TABLE IF NOT EXISTS schema_migrations (
    name text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
'''

# clave fija del lock: la web al arrancar en Render y el Tick de Actions pueden migrar a la vez
MIGRATIONS_LOCK = 37958581


def list_migrations():
    '''
    Lista los archivos .sql de migraciones en orden de nombre.
        Returns:
            migrations (list): tuplas (nombre, sql) ordenadas
    '''
    folder = resources.files('app') / 'migrations'
    names = sorted(f.name for f in folder.iterdir() if f.name.endswith('.sql'))
    migrations = [(name, (folder / name).read_text(encoding='utf-8')) for name in names]
    return migrations


def apply_migrations(conn):
    '''
    Aplica las migraciones pendientes, cada una en su propia transaccion.
        Args:
            conn (psycopg.Connection): conexion apuntada al schema destino
        Returns:
            applied_now (list): nombres de las migraciones aplicadas en esta llamada
    '''
    # lock de sesion, sobrevive a los commits de cada migracion; el segundo que llega espera y no encuentra nada pendiente
    conn.execute('SELECT pg_advisory_lock(%s)', (MIGRATIONS_LOCK,))

    try:
        conn.execute(CREATE_MIGRATIONS_TABLE)
        applied = {row[0] for row in conn.execute('SELECT name FROM schema_migrations')}
        conn.commit()
        applied_now = []

        for name, migration_sql in list_migrations():
            if name in applied:
                continue

            # DDL en Postgres es transaccional: si falla a la mitad no queda nada aplicado
            conn.execute(migration_sql)
            conn.execute('INSERT INTO schema_migrations (name) VALUES (%s)', (name,))
            conn.commit()
            applied_now.append(name)
    finally:
        conn.rollback()
        conn.execute('SELECT pg_advisory_unlock(%s)', (MIGRATIONS_LOCK,))
        conn.commit()

    return applied_now


def main():
    '''
    Aplica las migraciones pendientes sobre la DB de DATABASE_URL.
    '''
    with connect() as conn:
        applied_now = apply_migrations(conn)

    if applied_now:
        print(f'migraciones aplicadas: {", ".join(applied_now)}')
    else:
        print('sin migraciones pendientes')


if __name__ == '__main__':
    main()
