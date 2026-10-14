import sys
from collections import Counter

from obslog import RunConfig, get_logger, start_run
from psycopg.rows import dict_row

from app import canales, deezer, runs, spotify, users
from app.db import connect

# el repo es publico y los logs de Actions tambien: se loguean ids internos, nunca nombres ni ids de Spotify del User
log = get_logger('TICK')
run_log = get_logger('RUN')
delivery_log = get_logger('DELIVERY')

# resultados que dejan el Tick en rojo para que Actions mande el mail de workflow fallido
FAILED_OUTCOMES = ('fallido', 'quota')


def main():
    '''
    Punto de entrada de `python -m app.tick`: corre el Tick con obslog y sale con 1 si algun Run fallo.
    '''
    with start_run(RunConfig(logs_dir='logs')) as session:
        summary = run_tick()
        session.add_summary(**summary)

    if summary['runs fallidos']:
        sys.exit(1)


def run_tick(today=None, search_path=None):
    '''
    Recorre a todos los Users y corre el Run de los que lo necesitan; cada User falla por separado (ADR 0002/0004).
        Args:
            today (date): fecha ART del Tick, la pasan los tests
            search_path (str): schema a usar en vez de public, lo usan los tests
        Returns:
            summary (dict): conteos del Tick para el summary de obslog
    '''
    today = today or runs.get_today()

    with connect(search_path=search_path) as conn:
        user_list = conn.cursor(row_factory=dict_row).execute(f'SELECT {users.USER_COLUMNS} FROM users ORDER BY id').fetchall()

    log.info(f'Tick del {today} con {len(user_list)} Users', event='tick_start', date=str(today), users=len(user_list))
    spotify_before, deezer_before = spotify.api_calls, deezer.api_calls
    outcomes = Counter()

    for n, user in enumerate(user_list, 1):
        with log.step(n, len(user_list), f'User {user["id"]}', event='user_start', user_id=user['id']):
            try:
                outcome = tick_user(user, today, search_path)
            except Exception as error:
                # algo fuera del Run, como la conexion a Neon: este User falla y el Tick sigue con el resto
                log.failure('user_failed', error)
                outcome = 'fallido'

        outcomes[outcome] += 1

    summary = {
        'fecha': str(today),
        'users': len(user_list),
        'runs exitosos': outcomes['exitoso'],
        'runs fallidos': sum(outcomes[outcome] for outcome in FAILED_OUTCOMES),
        'al dia': outcomes['al dia'],
        'ocupados': outcomes['ocupado'],
        'desconectados': outcomes['desconectado'],
        'quota de spotify agotada': outcomes['quota'] > 0,
        'llamadas a spotify': spotify.api_calls - spotify_before,
        'llamadas a deezer': deezer.api_calls - deezer_before,
    }
    return summary


def tick_user(user, today, search_path):
    '''
    Corre el Run de un User si el guard lo pide y loguea como termino el y cada uno de sus Canales.
        Args:
            user (dict): fila de users
            today (date): fecha ART del Tick
            search_path (str): schema a usar en vez de public, lo usan los tests
        Returns:
            outcome (str): exitoso, fallido, quota, al dia, ocupado o desconectado
    '''
    if user['refresh_token_enc'] is None:
        log.info('Desconectado, no corre hasta que vuelva a entrar', event='user_skipped', reason='desconectado', disconnected_at=str(user['disconnected_at']))
        return 'desconectado'

    # conexion propia por User: si Neon corta una, el resto del Tick sigue
    with connect(search_path=search_path) as conn:
        if not needs_run(conn, user['id'], today):
            log.info('Ya exploro hoy y no tiene Entregas pendientes', event='user_skipped', reason='al_dia')
            return 'al dia'

        started_at = conn.execute('SELECT clock_timestamp()').fetchone()[0]
        canales_before = canales.list_canales(conn, user['id'])
        spotify_before, deezer_before = spotify.api_calls, deezer.api_calls
        conn.commit()

        try:
            report = runs.run_user(conn, user, today)
        except runs.RunBusy:
            log.info('Otro Run de este User esta en curso, queda para el proximo Tick', event='user_skipped', reason='ocupado')
            return 'ocupado'
        except users.UserDisconnected:
            log.warning('Spotify rechazo su refresh token: queda Desconectado hasta que vuelva a entrar', event='user_disconnected')
            return 'desconectado'
        except spotify.QuotaExceeded as error:
            run_log.failure('quota_exceeded', error, f'Spotify corto por quota diaria agotada, Retry-After {error} s', retry_after=str(error))
            return 'quota'
        except Exception as error:
            run_log.failure('run_failed', error)
            return 'fallido'
        finally:
            spotify_calls, deezer_calls = spotify.api_calls - spotify_before, deezer.api_calls - deezer_before
            run_log.info(f'Gasto {spotify_calls} llamadas a Spotify y {deezer_calls} a Deezer', event='run_calls', spotify_calls=spotify_calls, deezer_calls=deezer_calls)

        outcome = 'fallido' if report['failures'] else 'exitoso'
        run_log.info(f'Run {outcome} con {report["found"]} Tracks nuevos en los Lotes', event='run_done', status=outcome, found=report['found'])
        log_canales(conn, canales_before, report['failures'], started_at)

    return outcome


def needs_run(conn, user_id, today):
    '''
    Guard del Tick: corre si el User no exploro hoy o tiene alguna Entrega o retiro pendiente (ADR 0004).
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            today (date): fecha ART del Tick
        Returns:
            needed (bool): True si hay que correr el Run
    '''
    needed = conn.execute(
        '''
        SELECT NOT EXISTS (SELECT 1 FROM runs WHERE user_id = %s AND date = %s AND status = 'exitoso')
            OR EXISTS (
                SELECT 1 FROM lotes l JOIN canales c ON c.id = l.canal_id
                WHERE c.user_id = %s AND l.status = 'cerrado' AND l.delivered_at IS NULL
            )
        ''',
        (user_id, today, user_id),
    ).fetchone()[0]
    return needed


def log_canales(conn, canales_before, failures, started_at):
    '''
    Loguea como quedo cada Canal despues del Run: Entrega hecha, fallida, pendiente o Canal borrado.
        Args:
            conn (psycopg.Connection): conexion abierta
            canales_before (list): Canales del User antes del Run
            failures (list): Canales cuya Entrega fallo, como los devuelve el Run
            started_at (datetime): momento en que arranco el Run
    '''
    errors = {failure['canal']['id']: failure['error'] for failure in failures}

    for canal in canales_before:
        fields = {'canal_id': canal['id']}

        if canal['id'] in errors:
            delivery_log.failure('entrega_failed', errors[canal['id']], f'Canal {canal["id"]}: la Entrega fallo y se reintenta en el proximo Tick', **fields)
            continue

        row = conn.execute(
            '''
            SELECT
                count(*) FILTER (WHERE l.delivered_at >= %s AND i.status = 'insertado'),
                count(*) FILTER (WHERE l.delivered_at >= %s AND i.status = 'descartado'),
                count(*) FILTER (WHERE l.status = 'abierto' AND i.status = 'pendiente'),
                bool_or(l.delivered_at >= %s),
                (SELECT target_total FROM canales WHERE id = %s)
            FROM lotes l LEFT JOIN lote_items i ON i.lote_id = l.id
            WHERE l.canal_id = %s
            ''',
            (started_at, started_at, started_at, canal['id'], canal['id']),
        ).fetchone()
        inserted, discarded, pending, delivered, total = row

        if total is None:
            # la Entrega no encontro la Target Playlist y borro el Canal con su Lote (ADR 0005)
            delivery_log.warning(f'Canal {canal["id"]}: su Target Playlist ya no existe, el Canal se borro', event='canal_deleted', **fields)
        elif delivered:
            delivery_log.info(
                f'Canal {canal["id"]}: Entrega con {inserted} Tracks insertados y {discarded} descartados, la Target quedo en {total}',
                event='entrega_done', inserted=inserted, discarded=discarded, target_total=total, **fields,
            )
        else:
            delivery_log.info(f'Canal {canal["id"]}: {pending} Tracks esperando la Entrega del viernes', event='canal_pending', pending=pending, **fields)

    conn.commit()


if __name__ == '__main__':
    main()
