"""상태 표(SQLite)를 새로 만든다. 실행할 때마다 이전 표를 지우고 처음 값으로 채운다.
지난 실행의 공정 기록과 HMI 화면 파일도 지운다. 남겨 두면 새 실행이 쓰기 전까지 옛 값이 보인다."""

import os
import sqlite3

from config import HMI_SCREEN, PROCESS_LOG, SCHEMA, SCHEMA_INIT, STATE


def create_state():
    for path in (STATE['path'], PROCESS_LOG, HMI_SCREEN):
        if os.path.exists(path):
            os.remove(path)
    os.makedirs('logs', exist_ok=True)
    with sqlite3.connect(STATE['path']) as connection:
        connection.executescript(SCHEMA)
        connection.executescript(SCHEMA_INIT)
    print('%s created' % STATE['path'])


if __name__ == '__main__':
    create_state()
