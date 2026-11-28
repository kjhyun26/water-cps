"""PLC2 보호 시험 (보안 조치 4단계): 가짜 통신으로 plc2.py 의 범위 거부·되돌려 쓰기·ramp 를 본다.
mininet 없이 돈다. 관리자 권한 필요 없음, 1초.

실행: cd ~/water_cps && venv/bin/python tests/test_plc2_offline.py
"""
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/ 의 위 폴더
import plc2  # noqa: E402

FAILS = []


def expect(name, got, want):
    ok = got == want
    print('[%s] %s: got %s' % ('PASS' if ok else 'FAIL', name, got))
    if not ok:
        FAILS.append(name)


class FakePlc2(plc2.Plc2):
    """통신과 상태 표를 사전으로 바꾼 PLC2. main_loop 의 한 주기를 cycle() 로 돌린다."""

    def __init__(self, secure):
        self.tags = {'DOSE_SETPOINT': 17.0, 'P201': 34.0, 'FIT102': 2000.0}
        self.state = {'SIM_TIME': 0.0}
        self.writes = []
        self.out = io.StringIO()
        sys.argv = ['plc2.py'] + (['--secure'] if secure else [])
        with redirect_stdout(self.out):
            self.pre_loop()

    def send(self, tag, value, address):
        self.tags[tag[0]] = value
        self.writes.append((tag[0], value))

    def send_multiple(self, tags, values, address):
        for tag, value in zip(tags, values):
            self.send(tag, value, address)

    def receive(self, tag, address):
        return str(self.tags[tag[0]])

    def get(self, tag):
        return str(self.state[tag[0]])

    def set(self, tag, value):
        self.state[tag[0]] = value

    def cycle(self, write=None, process_seconds=60):
        """누군가 DOSE_SETPOINT 를 쓰고, 공정 시각이 흐른 뒤 PLC2가 한 주기를 돈다."""
        if write is not None:
            self.tags['DOSE_SETPOINT'] = write
        self.state['SIM_TIME'] += process_seconds
        with redirect_stdout(self.out):
            received = plc2.to_number(self.receive(plc2.DOSE_SETPOINT, None), self.dose_setpoint)
            command = self.control(received)     # plc2.py 의 한 주기 판단 그대로
            self.set(plc2.P201, command)
        return command


# ---------------------------------------------------------------- 보안 끔: 예전과 같다
off = FakePlc2(secure=False)
expect('off: dose 110 applied at once (SIS check 4 still possible)', off.cycle(110.0), 220.0)
expect('off: dose 200 applied at once', off.cycle(200.0), 400.0)
expect('off: no protection log', 'DOSE_' in off.out.getvalue(), False)

# ---------------------------------------------------------------- 보안 켬
on = FakePlc2(secure=True)
on.cycle()   # 공정 시각 기준점
expect('on: banner', 'PLC2 protection: on' in on.out.getvalue(), True)

command = on.cycle(200.0)
expect('on: 200 rejected, pump unchanged', command, 34.0)
expect('on: 200 rejected, tag restored to 17.0', on.tags['DOSE_SETPOINT'], 17.0)
expect('on: reject logged', 'DOSE_REJECT' in on.out.getvalue() and 'value=200.0' in on.out.getvalue(), True)
on.cycle(4.9)
expect('on: 4.9 rejected', on.tags['DOSE_SETPOINT'], 17.0)
on.cycle(-1.0)
expect('on: negative rejected', on.tags['DOSE_SETPOINT'], 17.0)

# 위로 ramp: 17 → 25, 공정 1분에 2 mg/L
applied = []
on.cycle(25.0)
applied.append(on.applied_dose)
for _ in range(5):
    on.cycle()
    applied.append(on.applied_dose)
expect('on: ramp up 17 -> 25 by 2/min', applied, [19.0, 21.0, 23.0, 25.0, 25.0, 25.0])
expect('on: pump follows applied dose', round(on.tags['FIT102'] * on.applied_dose / 1000, 4), 50.0)
expect('on: target kept in tag during ramp', on.tags['DOSE_SETPOINT'], 25.0)
text = on.out.getvalue()
expect('on: ramp start/done logged', 'DOSE_RAMP start' in text and 'DOSE_RAMP done' in text, True)

# 공정 30초 주기: 직전 60초 동안 변화가 없었으면 +2, 그다음 30초는 60초 구간이 꽉 차 있어 그대로
on.cycle(30.0, process_seconds=30)
expect('on: 30 s cycle after rest -> +2', on.applied_dose, 27.0)
on.cycle(process_seconds=30)
expect('on: next 30 s -> no change (60 s window already +2)', on.applied_dose, 27.0)
on.cycle(process_seconds=30)
expect('on: 60 s after last step -> +2', on.applied_dose, 29.0)
for _ in range(5):
    on.cycle()
expect('on: reaches 30', on.applied_dose, 30.0)

# 아래로 ramp
on.cycle(5.0)
expect('on: ramp down 30 -> 28', on.applied_dose, 28.0)

# 범위 끝값은 받아들인다
on2 = FakePlc2(secure=True)
on2.cycle()
on2.cycle(50.0)
expect('on: 50 accepted as target', on2.dose_setpoint, 50.0)
on2.cycle(5.0)
expect('on: 5 accepted as target', on2.dose_setpoint, 5.0)

# 거부 뒤 되돌린 값은 마지막으로 받아들인 목표값 (ramp 중이어도)
on3 = FakePlc2(secure=True)
on3.cycle()
on3.cycle(27.0)
on3.cycle(110.0)
expect('on: reject during ramp restores target 27, not applied', on3.tags['DOSE_SETPOINT'], 27.0)
expect('on: ramp continues after reject', on3.applied_dose, 21.0)

# 공정 시각이 흐르지 않으면 한 번 +2 뒤로는 움직이지 않는다 (같은 60초 구간 안이다)
on4 = FakePlc2(secure=True)
on4.cycle()
on4.cycle(30.0, process_seconds=0)
for _ in range(5):
    on4.cycle(process_seconds=0)
expect('on: no process time, at most +2 in total', on4.applied_dose, 19.0)

# 공정 90초 주기 (verify_security R on 에서 본 경우): 한 주기에 +3 이 아니라 +2
on5 = FakePlc2(secure=True)
on5.cycle()
on5.cycle(18.18)
steps = []
on5.cycle(28.18, process_seconds=90)
steps.append(on5.applied_dose)
for _ in range(6):
    on5.cycle(process_seconds=90)
    steps.append(on5.applied_dose)
expect('on: 90 s cycles move 2 per cycle, not 3', [round(x, 2) for x in steps],
       [20.18, 22.18, 24.18, 26.18, 28.18, 28.18, 28.18])

# 목표값을 받기 전에 흐른 시간은 앞당겨 쓰지 않는다: 오래 쉬었어도 받은 순간 +2, 그 뒤 60초 안에는 더 안 움직임
on6 = FakePlc2(secure=True)
on6.cycle()
on6.cycle(process_seconds=600)
on6.cycle(30.0, process_seconds=1)
first = on6.applied_dose
on6.cycle(process_seconds=59)
expect('on: after long rest, +2 at once then hold for 60 s', (first, on6.applied_dose), (19.0, 19.0))


def window_violations(trace, window=60, limit=2.0):
    """적용값이 계단처럼 바뀌는 기록에서, 어떤 공정 60초 구간 안에 2 넘게 다른 두 값이 함께 있는 경우.

    trace 는 (공정 시각, 그 주기 뒤 적용값). 같은 시각에 여러 주기가 돌았으면 마지막 값이 그 시각의 값이다
    (process.csv 와 같은 기준). 값 v_i 는 다음 기록 시각 t_{i+1} 직전까지 유효하므로, 구간 [t_j - 60, t_j] 에
    v_i 가 들어가는 조건은 t_{i+1} > t_j - 60 이다. 기록 시점끼리만 비교하면 공정 90초 주기의 +3 같은 계단을 놓친다.
    """
    at_time = {}
    for t, v in trace:
        at_time[t] = v
    points = sorted(at_time.items())
    bad = []
    for i in range(len(points) - 1):
        v_i, next_t = points[i][1], points[i + 1][0]
        for t_j, v_j in points[i + 1:]:
            if t_j - next_t >= window:
                break
            if abs(v_j - v_i) > limit + 1e-9:
                bad.append((points[i][0], v_i, t_j, v_j))
    return bad


# 성질 시험: 불규칙한 주기와 목표 변경을 무작위로 섞어도 어느 60초 구간에서도 2 이하
import random  # noqa: E402
random.seed(7)
prop = FakePlc2(secure=True)
prop.cycle()
trace = [(prop.state['SIM_TIME'], prop.applied_dose)]
for _ in range(400):
    write = random.choice([None, None, None, round(random.uniform(5, 50), 2), 200.0])
    prop.cycle(write, process_seconds=random.choice([0, 30, 30, 60, 60, 90]))
    trace.append((prop.state['SIM_TIME'], prop.applied_dose))
violations = window_violations(trace)
expect('on: random cycles, no 60 s window changes more than 2 (400 cycles)', violations[:3], [])
expect('on: random cycles, still reaches targets (moved at least 60 mg/L in total)',
       sum(abs(b[1] - a[1]) for a, b in zip(trace, trace[1:])) > 60, True)

print('\nFAILS:', FAILS)
print('RESULT=%s' % ('PASS' if not FAILS else 'FAIL'))
