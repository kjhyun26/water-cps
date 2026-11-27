"""원수 공급기: 원수 시나리오(scenarios.py)를 돌고 있는 시스템에 흘려 넣는다.

원수 탁도는 물리·제어 계층이 정하지 않는 바깥 값이다(설계 문서 1-3절). 이 프로그램이
공정 시각(SIM_TIME)을 읽어 그 시각의 원수 탁도를 상태 표의 AIT101 에 쓴다. 손으로
sqlite3 UPDATE 를 치는 일을 시나리오대로 대신 하는 것이다.

조류 보정값은 상태 표에 태그가 없으므로 logs/algae_sensor.txt 에 쓴다. 조류 센서
(클로로필 측정기)를 흉내 낸 것으로, ai_agent.py 가 이 파일을 읽는다.

실행 (run.py 가 FEED_SCENARIO = True 이면 띄운다):
    venv/bin/python scenario_feeder.py                  공정 0시부터 기본 시나리오
    venv/bin/python scenario_feeder.py --start-hours 119.5
        시나리오 119.5시간(5일차 호우 30분 전)부터 시작. 공정 30분 = 실제 30초 뒤 호우가 온다
"""

import argparse
import os
import sqlite3
import time

from config import AIT101, SIM_TIME, STATE
from scenarios import default_scenario, raw_conditions

ALGAE_SENSOR_FILE = 'logs/algae_sensor.txt'
LOOP_SECONDS = 0.25


def read_sim_time(connection):
    row = connection.execute('SELECT value FROM water_cps WHERE name = ? AND pid = ?',
                             SIM_TIME).fetchone()
    return float(row[0]) if row else 0.0


def write_algae(value):
    temporary_path = ALGAE_SENSOR_FILE + '.tmp'
    with open(temporary_path, 'w') as f:
        f.write('%.4f\n' % value)
    os.replace(temporary_path, ALGAE_SENSOR_FILE)


def main():
    parser = argparse.ArgumentParser(description='원수 시나리오 공급기')
    parser.add_argument('--start-hours', type=float, default=0.0,
                        help='공정 0초에 대응할 시나리오 시각 (시간)')
    args = parser.parse_args()
    offset = args.start_hours * 3600
    scenario = default_scenario()

    print('scenario feeder started, offset %.1f h' % args.start_hours)
    last_label = None
    connection = sqlite3.connect(STATE['path'], timeout=5)
    try:
        while True:
            try:
                sim_time = read_sim_time(connection)
                ntu, algae, label = raw_conditions(sim_time + offset, scenario)
                connection.execute('UPDATE water_cps SET value = ? WHERE name = ? AND pid = ?',
                                   (str(ntu), AIT101[0], AIT101[1]))
                connection.commit()
                write_algae(algae)
                if label != last_label:
                    print('sim_time=%d scenario_hours=%.2f type=%s ntu=%.1f algae=%.1f'
                          % (sim_time, (sim_time + offset) / 3600, label, ntu, algae))
                    last_label = label
            except (sqlite3.OperationalError, ValueError) as error:
                # 공정이 같은 표에 쓰는 중이면 잠금 오류가 날 수 있다. 다음 바퀴에 다시 한다
                print('retry: %s' % error)
            time.sleep(LOOP_SECONDS)
    finally:
        connection.close()


if __name__ == '__main__':
    main()
