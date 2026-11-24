"""2단계 검증: run.py 의 함수로 전체를 띄우고 설계 문서 10절의 확인 1~6을 사람 손 없이 돌린다.

실행: cd ~/water_cps && sudo python3 tests/verify_system.py     (약 9분)
확인 1~5를 한 번 띄운 상태에서 이어서 한다. 앞 확인의 영향이 남지 않게 순서와 되돌리기를 맞췄다.
"""
import csv
import glob
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/ 의 위 폴더
import run  # noqa: E402  (run.py 가 작업 폴더로 chdir 한다)

RESULTS = []


def record(name, passed, detail):
    RESULTS.append((name, passed, detail))
    print('[%s] %s: %s' % ('PASS' if passed else 'FAIL', name, detail), flush=True)


def rows():
    with open('logs/process.csv', newline='') as log_file:
        return list(csv.DictReader(log_file))


def screen():
    try:
        with open('logs/hmi_screen.txt', encoding='utf-8') as screen_file:
            return screen_file.read()
    except FileNotFoundError:
        return ''


def wait_until(condition, timeout, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def set_raw_turbidity(value):
    with sqlite3.connect('water_cps.sqlite') as connection:
        connection.execute("UPDATE water_cps SET value = ? WHERE name = 'AIT101' AND pid = 1", (value,))


def log_text(name):
    with open('logs/%s.log' % name, errors='replace') as log_file:
        return log_file.read()


def main():
    network = run.build_network()
    hmi = network.get('hmi')

    def hmi_command(arguments):
        return hmi.cmd('%s hmi.py %s' % (run.VENV_PYTHON, arguments))

    try:
        start_wall = time.time()
        run.start_devices(network)

        # 공정이 s1 에서 떴는지 (설계 문서 11절의 미확인 항목)
        started = wait_until(lambda: os.path.exists('logs/process.csv') and len(rows()) >= 10, 30)
        record('process started on s1', started, '%d rows after start' % (len(rows()) if started else 0))
        if not started:
            return

        # 확인 6: 화면 파일이 갱신된다
        wait_until(lambda: '정수장 HMI' in screen(), 15)
        first_mtime = os.path.getmtime('logs/hmi_screen.txt')
        time.sleep(6)
        second_mtime = os.path.getmtime('logs/hmi_screen.txt')
        text = screen()
        record('check 6 screen refresh', second_mtime > first_mtime and '주입률 설정    17.0 mg/L' in text
               and '주입량         34.0 kg/h' in text, 'mtime advanced %.1f s' % (second_mtime - first_mtime))
        print(text, flush=True)

        # SIS 복귀 요청: 밸브가 열려 있으면 받아들이지 않고 지운다
        hmi_command('sis reset')
        ignored = wait_until(lambda: 'SIS reset ignored: valve 1' in log_text('sis'), 6)
        time.sleep(3)  # 지우지 않으면 주기마다 같은 줄이 다시 찍힌다
        ignored_count = log_text('sis').count('SIS reset ignored')
        record('reset ignored while valve open, then cleared', ignored and ignored_count == 1,
               'ignored lines %d' % ignored_count)

        # 확인 1: 수위가 3.25~3.75 m 사이를 오르내린다 (공정 약 60분)
        time.sleep(60)
        data = rows()
        levels = [float(row['level_m']) for row in data]
        pumps = {row['intake_pump'] for row in data}
        record('check 1 level band', min(levels) >= 3.25 and max(levels) <= 3.75 and pumps == {'1', '2'},
               'min %.4f max %.4f pumps %s over %d rows' % (min(levels), max(levels), sorted(pumps), len(data)))

        # 확인 2: 주입률 20 → 주입량 40, pH 7.10. 그 뒤 17로 되돌린다
        hmi_command('dose 20')
        changed = wait_until(lambda: rows()[-1]['chemical_pump_command_kg_per_hour'] == '40.0', 8)
        last = rows()[-1]
        record('check 2 dose 20', changed and last['dosed_ph'] == '7.1',
               'command %s, pH %s' % (last['chemical_pump_command_kg_per_hour'], last['dosed_ph']))
        wait_until(lambda: '주입량         40.0 kg/h' in screen(), 8)
        record('check 2 screen', '주입률 설정    20.0 mg/L' in screen() and '주입량         40.0 kg/h' in screen(), 'hmi_screen.txt')
        hmi_command('dose 17')
        wait_until(lambda: rows()[-1]['chemical_pump_command_kg_per_hour'] == '34.0', 8)

        # 확인 3: 원수 탁도 300 → 공정 5시간 뒤 침전수 탁도 15.14, 그 30분 뒤 경보
        # 주입률 17로 되돌아온 뒤에 바꾼다 (기대값 15.14 는 주입률 17 기준)
        time.sleep(2)
        set_raw_turbidity('300')
        wait_until(lambda: rows()[-1]['raw_turbidity_ntu'] == '300.0', 5)
        change_time = next(int(row['sim_time_seconds']) for row in rows() if row['raw_turbidity_ntu'] == '300.0')
        print('raw turbidity 300 from sim time %d s; waiting about 5.5 simulated hours' % change_time, flush=True)

        def sim_time_reached(target):
            return int(rows()[-1]['sim_time_seconds']) >= target

        # 침전수 탁도가 1.0을 넘은 지 공정 20분: 아직 경보가 없어야 한다 (30분 하한)
        wait_until(lambda: sim_time_reached(change_time + 18000 + 1200), 420, interval=1)
        early = re.search(r'초과 (\d+)분', screen())
        early_minutes = int(early.group(1)) if early else -1
        record('check 3 no alarm before 30 min', '30분 넘게 초과' not in screen() and 10 <= early_minutes < 30,
               'screen shows over %d minutes, no alarm' % early_minutes)
        wait_until(lambda: sim_time_reached(change_time + 18000 + 1800 + 120), 120, interval=2)
        by_time = {int(row['sim_time_seconds']): row for row in rows()}
        before = float(by_time[change_time + 18000 - 30]['settled_turbidity_ntu'])
        after = float(by_time[change_time + 18000]['settled_turbidity_ntu'])
        record('check 3 delay', before < 1.0 and abs(after - 15.1352) < 0.0006,
               'settled at +4:59:30 %.4f, at +5:00:00 %.4f' % (before, after))
        alarmed = wait_until(lambda: '침전수 탁도 30분 넘게 초과' in screen(), 10)
        over = re.search(r'초과 (\d+)분', screen())
        record('check 3 alarm', alarmed, 'screen shows over %s minutes' % (over.group(1) if over else '?'))
        set_raw_turbidity('6.0')

        # 확인 4: 주입률 110 → pH 5.75 → SIS 차단 → pH 7.40
        hmi_command('dose 110')
        tripped = wait_until(lambda: rows()[-1]['shutoff_valve'] == '0', 15)
        data = rows()
        low_ph_rows = [row for row in data if row['dosed_ph'] == '5.75']
        first_low = int(low_ph_rows[0]['sim_time_seconds']) if low_ph_rows else None
        first_closed = next((int(row['sim_time_seconds']) for row in data if row['shutoff_valve'] == '0'), None)
        steps = (first_closed - first_low) // 30 if first_low is not None and first_closed is not None else None
        time.sleep(2)
        # SIS 는 2주기(실제 약 2.3초) 연속을 본다. 공정 한 걸음은 실제 0.5초라 2걸음 이상이어야 한다
        # (2주기 연속 규칙 자체는 tests/verify_control_logic.py 가 센서값을 넣어 정확히 본다)
        record('check 4 SIS trip', tripped and low_ph_rows and steps is not None and steps >= 2
               and rows()[-1]['dosed_ph'] == '7.4' and 'SIS trip' in log_text('sis'),
               'pH 5.75 for %s process steps before valve closed, then pH %s' % (steps, rows()[-1]['dosed_ph']))
        wait_until(lambda: 'SIS 작동' in screen(), 8)
        record('check 4 screen', 'SIS 작동: 약품 차단밸브 닫힘' in screen() and '차단밸브       닫힘' in screen(), 'hmi_screen.txt')

        # 설정을 110으로 둔 채 복귀하면 열렸다가 다시 닫힌다 (설계 문서 10절)
        accepted_before = log_text('sis').count('SIS reset accepted')
        trips_before = log_text('sis').count('SIS trip')
        hmi_command('sis reset')
        reclosed = wait_until(lambda: log_text('sis').count('SIS trip') > trips_before, 15)
        record('reset with dose 110 reopens then closes', reclosed and log_text('sis').count('SIS reset accepted') > accepted_before,
               'accepted %d -> %d, trips %d -> %d' % (accepted_before, log_text('sis').count('SIS reset accepted'),
                                                     trips_before, log_text('sis').count('SIS trip')))

        # 확인 5: 주입률 17 로 되돌리고 주입량 34.0 을 본 뒤 복귀
        hmi_command('dose 17')
        wait_until(lambda: '주입량         34.0 kg/h' in screen(), 10)
        hmi_command('sis reset')
        reopened = wait_until(lambda: rows()[-1]['shutoff_valve'] == '1' and rows()[-1]['dosed_ph'] == '7.145', 10)
        time.sleep(4)
        last = rows()[-1]
        record('check 5 reset', reopened and last['shutoff_valve'] == '1' and last['dosed_ph'] == '7.145',
               'valve %s, pH %s' % (last['shutoff_valve'], last['dosed_ph']))

        # 주기와 실제 배율
        for name in ('plc1', 'plc2', 'plc3', 'sis'):
            cycles = [float(value) for value in re.findall(r'cycle_seconds=([\d.]+)', log_text(name))]
            print('%s cycles %d, average %.3f s, max %.3f s' % (name, len(cycles), sum(cycles) / len(cycles), max(cycles)))
        plc2_cycles = [float(value) for value in re.findall(r'cycle_seconds=([\d.]+)', log_text('plc2'))]
        # 설계 문서 5-5절: 평소 주기로 판단한다. 컴퓨터가 잠깐 멈춰 한 번 튄 값으로는 배율을 바꾸지 않는다
        plc2_average = sum(plc2_cycles) / len(plc2_cycles)
        plc2_slow = sum(1 for value in plc2_cycles if value > 2.0)
        record('PLC2 cycle: average under 1.5 s and under 1% of cycles over 2 s',
               plc2_average < 1.5 and plc2_slow < 0.01 * len(plc2_cycles),
               'average %.3f s, over 2 s %d of %d, max %.3f s' % (plc2_average, plc2_slow, len(plc2_cycles), max(plc2_cycles)))
        elapsed_wall = time.time() - start_wall
        sim_seconds = int(rows()[-1]['sim_time_seconds'])
        print('wall %.0f s, simulated %d s, effective acceleration %.1f' % (elapsed_wall, sim_seconds, sim_seconds / elapsed_wall))

        # 장치 기록의 오류
        for path in sorted(glob.glob('logs/*.log')):
            with open(path, errors='replace') as log_file:
                bad = [line.strip() for line in log_file if 'Traceback' in line or 'ERROR' in line]
            if bad:
                print('%s: %d error lines, first: %s' % (path, len(bad), bad[0]))
        print(screen(), flush=True)
    finally:
        run.stop(network)
        passed = sum(1 for _, ok, _ in RESULTS if ok)
        print('SUMMARY %d/%d passed' % (passed, len(RESULTS)))
        print('RESULT=%s' % ('PASS' if RESULTS and passed == len(RESULTS) else 'FAIL'))


if __name__ == '__main__':
    main()
