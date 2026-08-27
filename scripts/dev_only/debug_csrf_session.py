import sqlite3
from pathlib import Path

from flask_wtf.csrf import generate_csrf

from app import create_app
import config
import services.common as common


def main():
    db_path = Path('C:/tmp/debug_csrf.db')
    config.DB_PATH = str(db_path)
    common.DB_PATH = str(db_path)
    app = create_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': True,
        'WTF_CSRF_ENABLED_IN_TESTS': True,
        'WTF_CSRF_CHECK_DEFAULT': True,
        'SECRET_KEY': 'test-secret-key',
        'SKIP_INIT_DB': True,
    }, auto_init=False)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session.clear()
            session['user_id'] = 1
            session['user_role'] = 'administrator'
        with app.test_request_context('/admin/login'):
            from flask import session as flask_session
            print('ctx token before', flask_session.get('csrf_token'))
            token = generate_csrf()
            print('generated via request context', token)
            print('ctx token after', flask_session.get('csrf_token'))
        with client.session_transaction() as session:
            print('client token before', session.get('csrf_token'))
            session['csrf_token'] = 'manual'
            print('client token after manual', session.get('csrf_token'))
            token2 = session.get('csrf_token')
        with app.test_request_context('/admin/login'):
            from flask import session as flask_session
            flask_session['csrf_token'] = token2
            print('equal same?', token2 == flask_session.get('csrf_token'))
            print('validate token same', token2)
    
    print('done')

if __name__ == '__main__':
    main()
