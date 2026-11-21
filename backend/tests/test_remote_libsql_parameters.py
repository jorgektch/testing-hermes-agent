"""Regression coverage for Turso Python libsql positional-only SQL parameters."""
import sqlite3
import sys
from types import SimpleNamespace

from app import persistence as db


class PositionalOnlyConnection:
    """SQLite stand-in that enforces the remote libsql execute binding contract."""
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, params=()):
        if not isinstance(params, (tuple, list)):
            raise ValueError('Expected a list or tuple for parameters')
        return self._connection.execute(sql, params)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        return self._connection.close()


def test_initialize_fixture_and_agent_booking_use_remote_compatible_bindings(tmp_path, monkeypatch):
    path = str(tmp_path / 'remote_emulation.sqlite3')
    url = 'libsql://emulated-remote-database'

    def fake_remote_connect(*, database, auth_token):
        assert database == url and auth_token == 'fake-test-token'
        connection = sqlite3.connect(path)
        return PositionalOnlyConnection(connection)

    monkeypatch.setitem(sys.modules, 'libsql', SimpleNamespace(connect=fake_remote_connect))
    monkeypatch.setenv('TURSO_DATABASE_URL', url)
    monkeypatch.setenv('TURSO_DATABASE_TURSO_AUTH_TOKEN', 'fake-test-token')
    db._INITIALIZED.discard(url)

    # Fails in the original implementation because fixture insertion used a dict.
    db.initialize()
    db.initialize()  # fixtures must remain idempotent

    with db.database() as con:
        seeded = con.execute('SELECT COUNT(*) FROM meetings WHERE source=?', ('synthetic_fixture',)).fetchone()[0]
        assert seeded > 0
        assert con.execute("SELECT value FROM schema_meta WHERE key='fixtures_seeded'").fetchone()

    auth = {'role': 'administrator', 'owner_id': 'administrator'}
    lead = db.create_lead(auth, 'Alex', 'Rivera', 'alex@example.test', '+51999999999')
    assert lead['id']

    from app import domain
    state = lead['state']
    from app import scheduling
    from datetime import timedelta
    date = scheduling.local_now().date() + timedelta(days=1)
    if date.weekday() == 6:
        date += timedelta(days=1)
    domain.begin_turn(state, 'I want to visit H-201 on that date')
    domain.track_property_interest(state, 'H-201', 'interested')
    offer = domain.slots(state, date.isoformat(), extra_occupied=db.occupied_by_advisor())
    domain.end_turn(state, 'Here are some visit times')
    option = offer['available_slots'][0]
    db.save_turn(auth, lead['id'], 0, state, [], 'I want to visit H-201 on that date', 'Here are some visit times')

    domain.begin_turn(state, f'I choose {option["start_time"]} for the H-201 visit')
    assert domain.select_visit_time(state, offer['proposal_id'], option['date'], option['start_time'])['ok']
    domain.end_turn(state, 'Please confirm your choice')
    db.save_turn(auth, lead['id'], 1, state, [],
                 f'I choose {option["start_time"]} for the H-201 visit', 'Please confirm your choice')

    domain.begin_turn(state, 'Yes, book that visit')
    booked = domain.book_visit(
        state, 'Alex', 'Rivera', 'alex@example.test', '+51999999999',
        date_str=option['date'], start_time=option['start_time'], proposal_id=offer['proposal_id'], unit_id='H-201',
    )
    assert booked['ok'], booked
    domain.end_turn(state, 'Booked in the simulation')

    # Fails in the original implementation because booking insertion used a dict.
    db.save_turn(auth, lead['id'], 2, state, [], 'Yes, book that visit', 'Booked in the simulation')
    with db.database() as con:
        assert con.execute('SELECT COUNT(*) FROM meetings WHERE lead_id=?', (lead['id'],)).fetchone()[0] == 1
    db._INITIALIZED.discard(url)
