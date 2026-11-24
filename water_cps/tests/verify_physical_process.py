"""physical_process.py 검증: 실제 step()을 기다림 없이 1,500걸음 돌리고, 걸음 사이에 밖에서 상태 표를 고친다.

실행: cd ~/water_cps && venv/bin/python tests/verify_physical_process.py   (관리자 권한 없이, 몇 초)
"""
import csv
import math
import os
import sqlite3
import sys

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tests/ 의 위 폴더
os.chdir(WORK)
sys.path.insert(0, WORK)

import init  # noqa: E402
import physical_process  # noqa: E402
from config import STATE, TANK_AREA, INITIAL_LEVEL  # noqa: E402

VERIFICATION_LOG = 'logs/process_verification.csv'
TOTAL_STEPS = 1500

# 걸음 번호 → 그 걸음 직전에 밖에서 쓰는 값 (이름, 번호, 값). None 은 NULL
SCRIPT = {
    10: [('AIT101', 1, '300')],
    20: [('XV201', 4, '0')],
    30: [('XV201', 4, '1')],
    40: [('P201', 2, '300')],
    41: [('P201', 2, '-10')],
    42: [('P201', 2, '34.0')],
    50: [('P101', 1, '1')],
    400: [('P101', 1, '2.0')],
    700: [('AIT101', 1, ' 300 ')],
    701: [('AIT101', 1, '3e2')],
    702: [('AIT101', 1, '')],
    703: [('AIT101', 1, 'abc')],
    704: [('AIT101', 1, None)],
    705: [('AIT101', 1, '-5')],
    706: [('AIT101', 1, '0')],
    707: [('AIT101', 1, '20')],
    708: [('AIT101', 1, 'nan')],
    709: [('AIT101', 1, 'inf')],
    710: [('AIT101', 1, '6.0')],
    800: [('XV201', 4, '0.0')],
    801: [('XV201', 4, '1')],
    850: [('P101', 1, '3')],
    851: [('P101', 1, 'abc')],
    852: [('P101', 1, '2')],
}

# 걸음 번호 → 기록 파일에 남아야 하는 해석된 입력 (원수 탁도, 취수 펌프, 펌프 명령, 차단밸브)
EXPECTED_INPUTS = {
    9: (6.0, 2, 34.0, 1), 10: (300.0, 2, 34.0, 1), 20: (300.0, 2, 34.0, 0),
    40: (300.0, 2, 300.0, 1), 41: (300.0, 2, -10.0, 1), 50: (300.0, 1, 34.0, 1),
    400: (300.0, 2, 34.0, 1),
    700: (300.0, 2, 34.0, 1), 701: (300.0, 2, 34.0, 1), 702: (300.0, 2, 34.0, 1),
    703: (300.0, 2, 34.0, 1), 704: (300.0, 2, 34.0, 1), 705: (-5.0, 2, 34.0, 1),
    706: (0.0, 2, 34.0, 1), 707: (20.0, 2, 34.0, 1), 708: (20.0, 2, 34.0, 1),
    709: (20.0, 2, 34.0, 1), 710: (6.0, 2, 34.0, 1), 800: (6.0, 2, 34.0, 0),
    801: (6.0, 2, 34.0, 1), 850: (6.0, 3, 34.0, 1), 851: (6.0, 3, 34.0, 1),
    852: (6.0, 2, 34.0, 1),
}


def put(name, pid, value):
    with sqlite3.connect(STATE['path']) as connection:
        connection.execute('UPDATE water_cps SET value = ? WHERE name = ? AND pid = ?',
                           (value, name, pid))


def table():
    with sqlite3.connect(STATE['path']) as connection:
        return {(name, pid): value for name, pid, value in
                connection.execute('SELECT name, pid, value FROM water_cps')}


class ScriptedProcess(physical_process.WaterProcess):
    def main_loop(self):
        for step_number in range(1, TOTAL_STEPS + 1):
            for action in SCRIPT.get(step_number, []):
                put(*action)
            self.step()
        self.log_file.close()


def independent_model(rows):
    """physical_process.py 를 쓰지 않고 같은 규칙을 다시 짠 계산. 기록된 입력으로 출력을 다시 구한다."""
    level = INITIAL_LEVEL
    queue = [17.0 - (9.8 + 3.0 * math.log(6.0))] * 600
    results = []
    for row in rows:
        raw = float(row['raw_turbidity_ntu'])
        pump = int(row['intake_pump'])
        command = float(row['chemical_pump_command_kg_per_hour'])
        valve = int(row['shutoff_valve'])
        level += ((2400.0 if pump == 2 else 1600.0) - 2000.0) / TANK_AREA * (30 / 3600)
        level = min(max(level, 0.0), 5.0)
        flow = 0.0 if valve == 0 else min(max(command, 0.0), 250.0)
        dose = flow * 1000 / 2000.0
        ph = 7.4 - 0.015 * dose
        need = 9.8 + 3.0 * math.log(max(raw, 0.1))
        queue.append(dose - need)
        difference = queue.pop(0)
        settled = 0.4 + (0.15 * difference ** 2 if difference < 0 else 0.05 * difference)
        results.append((level, dose, need, ph, settled))
    return results


def main():
    init.create_state()
    physical_process.PROCESS_LOG = VERIFICATION_LOG
    ScriptedProcess(name='process', protocol=None, state=STATE, section=TANK_AREA, level=INITIAL_LEVEL)

    with open(VERIFICATION_LOG, newline='') as log_file:
        rows = list(csv.DictReader(log_file))
    failures = []

    # 가. 줄 수와 공정 시각
    if len(rows) != TOTAL_STEPS:
        failures.append('row count %d' % len(rows))
    for number, row in enumerate(rows, start=1):
        if int(row['sim_time_seconds']) != number * 30:
            failures.append('sim_time at step %d' % number)
            break

    # 나. 깨진 값과 변형 표기의 해석
    for step_number, expected in EXPECTED_INPUTS.items():
        row = rows[step_number - 1]
        actual = (float(row['raw_turbidity_ntu']), int(row['intake_pump']),
                  float(row['chemical_pump_command_kg_per_hour']), int(row['shutoff_valve']))
        if actual != expected:
            failures.append('inputs at step %d: %r != %r' % (step_number, actual, expected))

    # 다. 모든 줄을 따로 짠 계산과 대조
    names = ('level_m', 'actual_dose_mg_per_liter', 'required_dose_mg_per_liter', 'dosed_ph',
             'settled_turbidity_ntu')
    mismatches = 0
    for number, (row, expected) in enumerate(zip(rows, independent_model(rows)), start=1):
        for name, value in zip(names, expected):
            if abs(float(row[name]) - value) > 0.0006:
                mismatches += 1
                if mismatches <= 5:
                    failures.append('step %d %s: log %s, independent %.4f' % (number, name, row[name], value))
    print('rows compared: %d, values compared: %d, mismatches: %d'
          % (len(rows), len(rows) * len(names), mismatches))

    # 라. 설계 문서의 기대값
    def value(step_number, name):
        return float(rows[step_number - 1][name])

    checks = [
        ('step 609 settled (before 5 h)', value(609, 'settled_turbidity_ntu'), 0.4912),
        ('step 610 settled (5 h after 300 NTU)', value(610, 'settled_turbidity_ntu'), 15.1352),
        ('step 20 dose with valve closed', value(20, 'actual_dose_mg_per_liter'), 0.0),
        ('step 20 pH with valve closed', value(20, 'dosed_ph'), 7.4),
        ('step 40 dose with command 300', value(40, 'actual_dose_mg_per_liter'), 125.0),
        ('step 40 pH with command 300', value(40, 'dosed_ph'), 5.525),
        ('step 41 dose with command -10', value(41, 'actual_dose_mg_per_liter'), 0.0),
        ('step 49 level rising with pump 2', value(49, 'level_m'), 3.5 + 49 / 60),
        ('step 90 level falling with pump 1', value(90, 'level_m'), 3.5 + 49 / 60 - 41 / 60),
        ('step 399 level clamped at 0', value(399, 'level_m'), 0.0),
        ('step 705 required with -5 NTU', value(705, 'required_dose_mg_per_liter'), 9.8 + 3.0 * math.log(0.1)),
        ('step 1305 settled from -5 NTU water', value(1305, 'settled_turbidity_ntu'), 0.4 + 0.05 * (17 - (9.8 + 3.0 * math.log(0.1)))),
        ('step 1310 settled from step 710 water', value(1310, 'settled_turbidity_ntu'), 0.4912),
    ]
    for label, actual, expected in checks:
        status = 'ok' if abs(actual - expected) < 0.0006 else 'FAIL'
        print('%-42s %10.4f  expected %10.4f  %s' % (label, actual, expected, status))
        if status == 'FAIL':
            failures.append(label)

    nan_rows = [row for row in rows if 'nan' in ','.join(row.values()).lower() or 'inf' in ','.join(row.values()).lower()]
    print('rows containing nan or inf: %d' % len(nan_rows))
    if nan_rows:
        failures.append('nan or inf in log')

    # 마. 상태 표의 마지막 값 = 기록 파일의 마지막 줄
    last = rows[-1]
    state = table()
    pairs = [(('LIT101', 1), 'level_m'), (('AIT202', 4), 'dosed_ph'), (('AIT301', 3), 'settled_turbidity_ntu'),
             (('SIM_TIME', 0), 'sim_time_seconds')]
    for key, column in pairs:
        if abs(float(state[key]) - float(last[column])) > 1e-9:
            failures.append('table %s=%s, log %s=%s' % (key, state[key], column, last[column]))
    if float(state[('FIT102', 1)]) != 2000.0:
        failures.append('table FIT102=%s, expected 2000.0' % state[('FIT102', 1)])
    print('table at end:', {key[0]: state[key] for key, _ in pairs}, 'FIT102=', state[('FIT102', 1)])

    print('RESULT=%s' % ('PASS' if not failures else 'FAIL'))
    for failure in failures:
        print('  -', failure)


if __name__ == '__main__':
    main()
