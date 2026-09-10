import os
import sys

sys.path.insert(0, os.getcwd())
import sqlite3

import app as A
from services.notifications import get_unread_count, notify_user

db = A.get_db() if False else None
with A.app.app_context():
    db = A.get_db()
    conn = sqlite3.connect(A.DB_PATH)
    cur = conn.execute("INSERT INTO users (name,email,password_hash,role,phone) "
                       "VALUES ('NotifTest','nf_t@test.com','x','farmer','9999999999')")
    uid = cur.lastrowid
    conn.commit(); conn.close()

    n = notify_user(db, uid, 'Test title', 'Test message', ntype='order')
    cnt = get_unread_count(db, uid)
    print('notify_user:', n, '| unread:', cnt)
    assert cnt == 1

    n2 = notify_user(db, uid, 'Second', 'Second msg', ntype='delivery')
    print('unread after 2:', get_unread_count(db, uid))
    assert get_unread_count(db, uid) == 2

    db.execute('DELETE FROM notifications WHERE user_id=?', (uid,))
    db.execute('DELETE FROM users WHERE id=?', (uid,))
    db.commit()
print('NOTIFICATION CYCLE OK, cleanup done')