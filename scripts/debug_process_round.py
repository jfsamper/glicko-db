import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
import services.common as common
from app import create_app, initialize_app
import app as appmod

base = os.path.join(os.path.dirname(__file__), '..', 'data')
base = os.path.abspath(base)
os.makedirs(base, exist_ok=True)
path = os.path.join(base, 'tmp_process_debug.db')
if os.path.exists(path):
    os.remove(path)

config.DB_PATH = path
common.DB_PATH = path
appmod.DB_PATH = path
initialize_app()
app = create_app({'TESTING': True, 'WTF_CSRF_ENABLED': False, 'SKIP_INIT_DB': True}, auto_init=False)

conn = sqlite3.connect(path)
conn.row_factory = sqlite3.Row
for i, name in enumerate(['Alice', 'Bob', 'Cara', 'Dan'], 1):
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, rd, volatility, initial_rating, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (i, name, name, name, 1500, 350, 0.06, 1500),
    )
conn.commit()
conn.close()

with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['user_id'] = 1

    response = client.post(
        '/admin/tournaments',
        data={
            'action': 'create',
            'name': 'Test Tour',
            'pairing_system': 'swiss',
            'rounds': '3',
            'bye_points': '1',
            'absent_points': '0',
        },
        follow_redirects=False,
    )
    print('create', response.status_code, response.location)

    tournament_id = 1
    for pid in [1, 2, 3, 4]:
        rr = client.post(
            f'/admin/tournaments/{tournament_id}/participants/add',
            data={'player_id': pid},
            follow_redirects=False,
        )
        print('add', pid, rr.status_code, rr.location)

    rr = client.post(f'/admin/tournaments/{tournament_id}/generate', follow_redirects=False)
    print('generate', rr.status_code, rr.location)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    pairing = conn.execute(
        "SELECT id, round_id FROM tournament_pairings WHERE round_id=(SELECT id FROM tournament_rounds WHERE tournament_id=? ORDER BY round_number LIMIT 1)",
        (tournament_id,),
    ).fetchone()
    print('pairing row', dict(pairing) if pairing else None)
    if pairing:
        rr = client.post(
            f'/admin/tournaments/{tournament_id}/result',
            data={'pairing_id': pairing['id'], 'result': '1-0', 'round_id': pairing['round_id']},
            follow_redirects=False,
        )
        print('result', rr.status_code, rr.location)
    conn.close()

    rr = client.post(
        f'/admin/tournaments/{tournament_id}/process-round',
        data={'round_id': 1, 'match_date': '2026-08-16', 'event': 'Test Tour'},
        follow_redirects=False,
    )
    print('process', rr.status_code, rr.location)
    print('process text', rr.get_data(as_text=True)[:200])

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    print('matches count', conn.execute('SELECT COUNT(*) FROM matches').fetchone()[0])
    print('matches rows', conn.execute('SELECT * FROM matches').fetchall())
    print('pairings', conn.execute('SELECT id, white_player_id, black_player_id, result FROM tournament_pairings').fetchall())
    conn.close()
