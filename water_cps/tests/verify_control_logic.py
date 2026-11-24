"""SIS 와 PLC3 의 한 주기(cycle) 판정을 통신 없이 시험한다. 센서값을 순서대로 넣고 결과를 본다.

실행: cd ~/water_cps && venv/bin/python tests/verify_control_logic.py   (관리자 권한 없이, 1초 안)
설계 문서 5-3절(PLC3), 5-4절(SIS)의 규칙이 그대로 지켜지는지 본다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/ 의 위 폴더

from config import AIT202, AIT301, SIM_TIME, SIS_RESET_REQUEST, TURBIDITY_ALARM, XV201  # noqa: E402
import plc3  # noqa: E402
import sis  # noqa: E402

FAILURES = []


def check(label, actual, expected):
    passed = actual == expected
    print('%-62s %-8s expected %-8s %s' % (label, actual, expected, 'ok' if passed else 'FAIL'))
    if not passed:
        FAILURES.append(label)


class FakeDevice:
    """get·set·receive·send_multiple 을 가로채 기록한다. 장치 객체에 그대로 붙인다."""

    def __init__(self, device):
        self.table = {}
        self.server = {}
        self.sent = []
        self.writes = []
        device.get = lambda tag: self.table.get(tag)
        device.set = lambda tag, value: (self.table.__setitem__(tag, value), self.writes.append(tag))
        device.receive = lambda tag, address: self.server.get(tag, '0')
        device.send_multiple = lambda tags, values, address: self.sent.append(dict(zip(tags, values)))


def new_sis():
    device = sis.Sis.__new__(sis.Sis)  # 통신 서버를 띄우지 않으려고 생성자를 건너뛴다
    device.ph, device.valve, device.out_of_range_count = 7.145, 1, 0
    return device, FakeDevice(device)


def run_sis(ph_values, reset_at=()):
    device, fake = new_sis()
    valves = []
    for index, ph in enumerate(ph_values):
        fake.table[AIT202] = ph
        fake.server[SIS_RESET_REQUEST] = '1' if index in reset_at else '0'
        device.cycle()
        valves.append(fake.table[XV201])
    return valves, fake


def test_sis():
    valves, _ = run_sis(['5.75', '7.0', '5.75'])
    check('SIS: out, in, out does not trip (count resets)', valves, [1, 1, 1])
    valves, _ = run_sis(['5.75', '5.75'])
    check('SIS: two in a row trips on the second', valves, [1, 0])
    valves, _ = run_sis(['5.8', '5.8', '8.5', '8.5'])
    check('SIS: 5.8 and 8.5 are inside the range', valves, [1, 1, 1, 1])
    valves, _ = run_sis(['8.51', '8.51'])
    check('SIS: above 8.5 trips too', valves, [1, 0])
    valves, _ = run_sis(['5.75', None, 'abc', '7.0'])
    check('SIS: broken readings keep the previous pH (still out of range)', valves, [1, 0, 0, 0])

    valves, fake = run_sis(['5.75', '5.75', '5.75', '7.4', '7.4'], reset_at=(2,))
    check('SIS: reset while pH out of range is ignored', valves, [1, 0, 0, 0, 0])
    check('SIS: ignored request is cleared on the server', fake.sent[2].get(SIS_RESET_REQUEST), 0)
    check('SIS: no request -> request tag not written', SIS_RESET_REQUEST in fake.sent[3], False)

    valves, fake = run_sis(['5.75', '5.75', '7.4', '7.4', '5.75'], reset_at=(3,))
    check('SIS: reset with pH in range reopens', valves, [1, 0, 0, 1, 1])
    check('SIS: accepted request is cleared on the server', fake.sent[3].get(SIS_RESET_REQUEST), 0)
    check('SIS: count restarts after reset (one bad reading keeps it open)', valves[-1], 1)
    check('SIS: valve written to the table every cycle', fake.writes.count(XV201), 5)
    check('SIS: status sent to the server every cycle', len(fake.sent), 5)


def run_plc3(samples):
    """samples: (침전수 탁도, 공정 시각 초) 목록. 주기마다 (초과 분, 경보) 를 돌려준다."""
    device = plc3.Plc3.__new__(plc3.Plc3)
    device.settled_turbidity, device.previous_sim_time, device.over_minutes = 0.49, 0.0, 0.0
    fake = FakeDevice(device)
    results = []
    for turbidity, sim_time in samples:
        fake.table[AIT301], fake.table[SIM_TIME] = turbidity, sim_time
        device.cycle()
        sent = fake.sent[-1]
        results.append((sent[plc3.TURBIDITY_OVER_MINUTES], sent[TURBIDITY_ALARM]))
    return results


def test_plc3():
    # 공정 30초마다 한 번 읽는다고 보고, 1.0 초과를 59번(29.5분) → 60번(30분)
    samples = [('2.0', 30 * step) for step in range(1, 62)]
    results = run_plc3(samples)
    check('PLC3: first reading over 1.0 counts from the previous time', results[0], (0.5, 0))
    check('PLC3: 29.5 minutes over -> no alarm', results[58], (29.5, 0))
    check('PLC3: 30 minutes over -> alarm', results[59], (30.0, 1))

    results = run_plc3([('2.0', 30 * step) for step in range(1, 70)] + [('1.0', 70 * 30), ('2.0', 71 * 30)])
    check('PLC3: exactly 1.0 resets the count and the alarm', results[-2], (0.0, 0))
    check('PLC3: counting restarts from zero after reset', results[-1], (0.5, 0))

    results = run_plc3([('2.0', 30), ('abc', 60), (None, 90)])
    check('PLC3: broken readings keep the previous turbidity', results[-1], (1.5, 0))
    results = run_plc3([('2.0', 30), ('2.0', 'abc'), ('2.0', 90)])
    check('PLC3: broken time adds nothing, next good time catches up', [minutes for minutes, _ in results], [0.5, 0.5, 1.5])


if __name__ == '__main__':
    test_sis()
    test_plc3()
    print('RESULT=%s' % ('PASS' if not FAILURES else 'FAIL'))
    for failure in FAILURES:
        print('  -', failure)
