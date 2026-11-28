"""보안 조치 검증 (5단계): 같은 공격 시나리오를 보안 끔/켬으로 두 번 돌려 비교한다.

실행: cd ~/water_cps && sudo python3 tests/verify_security.py            끔 → 켬 (약 15분)
      sudo python3 tests/verify_security.py --mode on                      한쪽만
먼저: venv/bin/python gateway.py approve   (켬에서 승인 목록이 없으면 모든 패킷이 ②에서 거부된다)

보안 끔 = iptables 없음, 게이트웨이 passthrough(검사 없이 그대로), PLC2 검사 없음.
보안 켬 = iptables, 게이트웨이 8가지 검사·폴백, PLC2 범위 검사·ramp (run.start_devices(use_security=True)).

끔에서는 공격이 "통해야" 통과다. 공격이 끔에서도 효과가 없으면 켬의 차단을 보였다고 할 수 없다.
켬에서는 공격이 막혀야 통과다. 판단 근거는 공정 기록의 실제 주입률(process.csv actual_dose_mg_per_liter),
게이트웨이 판정 기록(logs/gateway.log), PLC2 기록(logs/plc2.log), iptables 카운터다.

예측값 패킷 공격(F1·H4·H2·H5)은 AI 노드가 장악된 상황이다. AI 프로세스를 멈추고, 그 자리에서
AI와 같은 형식의 패킷을 logs/ai_predictions.jsonl 에 쓴다(Mininet 노드는 파일 시스템을 함께 쓴다).
원수 공급기는 띄우지 않는다(원수 탁도 6 NTU 고정, 자-테스트 없음).
"""
import argparse
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/ 의 위 폴더
import run  # noqa: E402  (run.py 가 작업 폴더로 chdir 한다)
import baseline  # noqa: E402
import config  # noqa: E402

RESULTS = []          # (mode, scenario, passed, detail)
METRICS = {}          # mode -> scenario -> 측정값
TABLE = baseline.load()
BASE_6 = round(baseline.baseline_dose(6.0, TABLE), 4)       # 6 NTU 기준값 (자-테스트 없음)
BASE_60 = round(baseline.baseline_dose(60.0, TABLE), 4)
TAMPERED_HASH = hashlib.sha256(b'tampered model').hexdigest()
# 주입 패킷의 inputs.sim_time 은 상태 표 시각보다 이만큼 앞(과거)으로 둔다. 게이트웨이는 PLC1 의 SIM_TIME 과
# 비교하는데, PLC1 은 상태 표보다 최대 한 주기(공정 약 60~75초) 늦다. AI 패킷도 PLC1 에서 읽은 시각을 쓴다
PACKET_AGE_SECONDS = 150


def record(mode, name, passed, detail):
    RESULTS.append((mode, name, passed, detail))
    print('[%s] %s %s: %s' % ('PASS' if passed else 'FAIL', mode, name, detail), flush=True)


def wait_until(condition, timeout, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


class Plant:
    """띄운 시스템 하나. 상태 표·기록 파일을 읽고, AI를 멈추거나 패킷을 넣는다."""

    def __init__(self, network, secure):
        self.network = network
        self.secure = secure
        self.mode = 'on' if secure else 'off'
        self.seq = 0
        with open('dataset/models/model_info.json') as f:
            self.model_hash = json.load(f)['model_hash']

    # ------------------------------------------------------------ 상태
    def state(self, name, pid):
        with sqlite3.connect('water_cps.sqlite') as connection:
            row = connection.execute('SELECT value FROM water_cps WHERE name = ? AND pid = ?',
                                     (name, pid)).fetchone()
        return float(row[0])

    def sim_time(self):
        return self.state('SIM_TIME', 0)

    def set_turbidity(self, value):
        with sqlite3.connect('water_cps.sqlite') as connection:
            connection.execute("UPDATE water_cps SET value = ? WHERE name = 'AIT101' AND pid = 1", (str(value),))

    def rows(self):
        with open('logs/process.csv', newline='') as f:
            return list(csv.DictReader(f))

    def applied(self):
        return float(self.rows()[-1]['actual_dose_mg_per_liter'])

    def wait_sim(self, seconds):
        target = self.sim_time() + seconds
        wait_until(lambda: self.sim_time() >= target, seconds / 20 + 15, interval=0.2)

    def gateway_lines(self):
        try:
            with open('logs/gateway.log') as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []

    def mark(self):
        return {'rows': len(self.rows()), 'gw': len(self.gateway_lines()),
                'plc2': len(open('logs/plc2.log', errors='replace').read())}

    def since(self, mark):
        rows = self.rows()[mark['rows']:]
        return {'rows': rows,
                'doses': [float(r['actual_dose_mg_per_liter']) for r in rows],
                'valves': {r['shutoff_valve'] for r in rows},
                'gw': self.gateway_lines()[mark['gw']:],
                'plc2': open('logs/plc2.log', errors='replace').read()[mark['plc2']:]}

    # ------------------------------------------------------------ AI·게이트웨이·HMI
    def ai_running(self):
        return subprocess.run(['pgrep', '-f', 'ai_agent.py'], capture_output=True).returncode == 0

    def last_ai_packet(self):
        try:
            with open('logs/ai_predictions.jsonl') as f:
                lines = [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return None
        return lines[-1] if lines else None

    def stop_ai(self, keepalive=True):
        """AI 프로세스를 멈춘다. keepalive 면 마지막 AI 값과 같은 정상 패킷을 하나 넣어
        게이트웨이의 '15분 끊김' 시계를 새로 시작한다 (시나리오 도중 끊김 폴백이 끼지 않게)."""
        last = self.last_ai_packet()
        subprocess.run(['pkill', '-f', 'ai_agent.py'])
        wait_until(lambda: not self.ai_running(), 5, interval=0.2)
        self.seq = max(self.seq, last['seq'] if last else 0)
        if keepalive and last:
            # 게이트웨이가 이 패킷을 처리(PLC2 쓰기)한 뒤에 시나리오를 시작한다. 기다리지 않으면 이 쓰기가
            # 시나리오의 첫 쓰기(예: R 의 hmi dose)를 덮어쓰거나, 바뀐 탁도로 거부된다 (2026-11-29 R off, H2 on)
            seq = self.inject(last['setpoint'])
            if not wait_until(lambda: any(e['seq'] == seq for e in self.gateway_lines()), 15, interval=0.2):
                print('WARNING keepalive seq %d not processed by gateway within 15 s' % seq, flush=True)
        return last['setpoint'] if last else BASE_6

    def start_ai(self):
        run.launch(self.network.get('ai'), 'ai_agent.py', 'ai_agent')

    def inject(self, setpoint, model_hash=None, turbidity=None, sim_time=None):
        """AI와 같은 형식의 패킷 한 줄 (중간발표 21쪽)."""
        self.seq += 1
        packet = {
            'process': 'chemical', 'setpoint': setpoint, 'unit': 'mg/L',
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()),
            'seq': self.seq, 'model_version': 'injected',
            'model_hash': model_hash or self.model_hash,
            'inputs': {'turbidity_ntu': self.state('AIT101', 1) if turbidity is None else turbidity,
                       'turbidity_trend': 0.0,
                       'sim_time': self.sim_time() - PACKET_AGE_SECONDS if sim_time is None else sim_time},
            'water_type': 'normal', 'confidence': 1.0, 'parts': {}, 'flags': ['TEST_INJECTED'],
        }
        with open('logs/ai_predictions.jsonl', 'a') as f:
            f.write(json.dumps(packet) + '\n')
        return self.seq

    def node_python(self, node, arguments, timeout=None):
        prefix = 'timeout %d ' % timeout if timeout else ''
        return self.network.get(node).cmd('%s%s %s' % (prefix, run.VENV_PYTHON, arguments))

    def wait_accept(self, mark, timeout=40):
        return wait_until(lambda: any(e['result'] == 'accept' for e in self.gateway_lines()[mark['gw']:]),
                          timeout)

    def reset(self):
        """시나리오 사이: 탁도 6, 주입률 6 NTU 기준값, SIS 열림, (켬) 폴백 복귀, AI 다시 시작."""
        if self.ai_running():
            self.stop_ai(keepalive=False)
        self.set_turbidity(6.0)
        self.node_python('hmi', 'hmi.py dose %s' % BASE_6)
        self.wait_sim(240)                 # ramp 와 게이트웨이 기준값 갱신
        if self.rows()[-1]['shutoff_valve'] == '0':
            self.node_python('hmi', 'hmi.py sis reset')
            wait_until(lambda: self.rows()[-1]['shutoff_valve'] == '1', 15)
        if self.secure:
            self.node_python('hmi', 'gateway.py resume')
        mark = self.mark()
        self.start_ai()
        if not self.wait_accept(mark):
            print('WARNING reset: no accept within 40 s after AI restart', flush=True)
        self.wait_sim(60)

    def drop_count(self):
        """PLC2 의 44818/tcp DROP 규칙 패킷 수 (켬에서만 있다)."""
        out = self.network.get('plc2').cmd('iptables -L INPUT -v -n -x 2>/dev/null')
        for line in out.splitlines():
            parts = line.split()
            if len(parts) > 2 and parts[2] == 'DROP' and 'tcp' in line and '44818' in line:
                return int(parts[0])
        return 0


def stages(result, kind='reject'):
    return sorted({e['stage'] for e in result['gw'] if e['result'] == kind and e['stage']})


# ================================================================ 시나리오
# 각 함수는 측정값 dict 를 돌려주고, judge 가 모드별 기대와 비교한다.

def scenario_normal(p):
    """N 정상 운전: AI 패킷이 그대로 반영되고, 켬에서도 거부가 없어야 한다 (오탐 없음)."""
    m = p.mark()
    p.wait_sim(1800)
    r = p.since(m)
    return {'accepts': sum(e['result'] == 'accept' for e in r['gw']),
            'rejects': sum(e['result'] == 'reject' for e in r['gw']),
            'fallbacks': stages(r, 'fallback'),
            'dose_range': [min(r['doses']), max(r['doses'])]}


def scenario_f1(p):
    """F1 터무니없는 예측: setpoint 80."""
    before = p.stop_ai()
    p.wait_sim(60)
    m = p.mark()
    p.inject(80.0)
    p.wait_sim(300)
    r = p.since(m)
    return {'before': before, 'max_applied': max(r['doses']), 'rejects': stages(r)}


def scenario_h4(p):
    """H4 천천히 낮추기: 공정 5분마다 −0.3 mg/L, 20번 (설계 E-4)."""
    start = p.stop_ai()
    m = p.mark()
    sent = []
    for i in range(1, 21):
        value = round(start - 0.3 * i, 2)
        sent.append(value)
        p.inject(value)
        p.wait_sim(300)
    p.wait_sim(300)
    r = p.since(m)
    return {'start': start, 'last_sent': sent[-1], 'min_applied': min(r['doses']), 'final_applied': p.applied(),
            'floor': round(BASE_6 - 3.5, 2), 'rejects': stages(r), 'fallbacks': stages(r, 'fallback')}


def scenario_h2(p):
    """H2 입력 위조: 실제 원수 탁도는 60 NTU, AI는 6 NTU로 속아 6 NTU용 값을 낸다."""
    fooled_value = p.stop_ai()
    p.set_turbidity(60.0)
    # 게이트웨이 탁도 기록이 패킷 시각(150초 전)에도 60 이 되도록 기다린다. 너무 오래 기다리면 연속 3번 거부보다
    # 15분 끊김(stale)이 먼저 걸려 의도한 경로를 못 탄다: keepalive 뒤 300 + 120 × 2 = 540초 < 900초
    p.wait_sim(300)
    m = p.mark()
    for _ in range(4):
        p.inject(fooled_value, turbidity=6.0)
        p.wait_sim(120)
    p.wait_sim(600)
    r = p.since(m)
    return {'sent': fooled_value, 'final_applied': p.applied(), 'need_floor': round(BASE_60 - 3.5, 2),
            'baseline_60': BASE_60, 'rejects': stages(r), 'fallbacks': stages(r, 'fallback')}


def scenario_h5(p):
    """H5 모델 바꿔치기: 승인 안 된 해시, 다른 검사는 다 통과할 그럴듯한 값."""
    before = p.stop_ai()
    value = round(before - 2.0, 2)
    m = p.mark()
    for _ in range(2):
        p.inject(value, model_hash=TAMPERED_HASH)
        p.wait_sim(300)
    r = p.since(m)
    return {'before': before, 'sent': value, 'min_applied': min(r['doses']), 'rejects': stages(r)}


def scenario_h1(p):
    """H1 공격자 호스트(192.168.1.70)에서 PLC2 에 직접 쓰기. 45 는 PLC2 범위 안이라 방화벽만 본다."""
    before = p.stop_ai()
    drops_before = p.drop_count()
    m = p.mark()
    started = time.time()
    out = p.node_python('attacker', 'hmi.py dose 45', timeout=15)
    elapsed = time.time() - started
    p.wait_sim(180)
    r = p.since(m)
    return {'before': before, 'max_applied': max(r['doses']), 'client_seconds': round(elapsed, 1),
            'client_output_tail': out.strip().splitlines()[-1][:120] if out.strip() else '',
            'drop_packets': p.drop_count() - drops_before}


def scenario_h3(p):
    """H3 HMI 장악: hmi 에서 dose 200 (방화벽은 hmi 를 허용하므로 PLC2 검사만 남는다)."""
    p.stop_ai()
    m = p.mark()
    p.node_python('hmi', 'hmi.py dose 200')
    p.wait_sim(300)
    r = p.since(m)
    # 실제 주입률은 약품 펌프 최대(config.CHEMICAL_PUMP_MAX 250 kg/h = 125 mg/L)에서 잘린다.
    # PLC2 가 200 을 받아들였는지는 펌프 명령(400 kg/h)으로 본다
    return {'max_command': max(float(x['chemical_pump_command_kg_per_hour']) for x in r['rows']),
            'max_applied': max(r['doses']), 'min_ph': min(float(x['dosed_ph']) for x in r['rows']),
            'valve_closed': '0' in r['valves'], 'plc2_reject_logged': 'DOSE_REJECT' in r['plc2']
            and 'value=200' in r['plc2']}


def window_violations(points, window=60, limit=2.0):
    """적용값 계단 기록 [(공정 시각, 값)]에서 어떤 공정 60초 구간 안에 2 넘게 다른 두 값이 함께 있는 경우.
    값 v_i 는 다음 기록 t_{i+1} 직전까지 유효하므로 t_j - t_{i+1} < 60 이면 같은 구간이다.
    (tests/test_plc2_offline.py 의 같은 이름 함수와 같은 기준. 기록 시점끼리만 비교하면 큰 계단을 놓친다)"""
    bad = []
    for i in range(len(points) - 1):
        v_i, next_t = points[i][1], points[i + 1][0]
        for t_j, v_j in points[i + 1:]:
            if t_j - next_t >= window:
                break
            if abs(v_j - v_i) > limit + 1e-6:
                bad.append((points[i][0], v_i, t_j, v_j))
    return bad


def scenario_ramp(p):
    """R ramp: hmi 에서 +10 mg/L. 켬이면 공정 시각 어느 60초 구간에서도 2 mg/L 이하로 따라간다."""
    before = p.stop_ai()
    target = round(p.applied() + 10, 2)
    m = p.mark()
    p.node_python('hmi', 'hmi.py dose %s' % target)
    p.wait_sim(900)     # 공정 90초 주기면 2 mg/L 씩 5번 = 약 450초
    r = p.since(m)
    # mark 직전 행부터 넣는다. 첫 계단이 mark 뒤 첫 행에 이미 반영돼 있을 수 있다
    rows = p.rows()[m['rows'] - 1:]
    points = [(int(row['sim_time_seconds']), float(row['actual_dose_mg_per_liter'])) for row in rows]
    jumps = [b[1] - a[1] for a, b in zip(points, points[1:])]
    reached = next((t for t, d in points if d >= target - 0.01), None)
    violations = window_violations(points)
    return {'before': before, 'start': points[0][1], 'target': target,
            'max_row_jump': round(max(jumps), 3) if jumps else 0.0,
            'seconds_to_target': None if reached is None else reached - points[0][0],
            'reached': reached is not None, 'window_violations': violations[:5],
            'plc2_ramp_logged': 'DOSE_RAMP start' in r['plc2']}


def scenario_fallback(p):
    """FB AI 프로세스가 죽어 예측이 끊김. 켬: 공정 15분 뒤 기준값 폴백 → resume 으로 복귀."""
    before = p.stop_ai(keepalive=False)
    m = p.mark()
    p.wait_sim(900 + 300)
    r = p.since(m)
    result = {'before': before, 'final_applied': p.applied(), 'baseline_6': BASE_6,
              'fallbacks': stages(r, 'fallback')}
    # 복귀: AI 다시 시작 → (켬) 운영자 resume → 통과값이 다시 반영
    m2 = p.mark()
    p.start_ai()
    p.wait_sim(600)     # AI 가 모델을 불러오는 데 실제 몇 초 걸린다
    held = p.since(m2)
    result['ignored_before_resume'] = sum(e['result'] == 'ignored' for e in held['gw'])
    if p.secure:
        p.node_python('hmi', 'gateway.py resume')
    m3 = p.mark()
    result['accept_after_resume'] = p.wait_accept(m3, timeout=30)
    result['resume_logged'] = any(e['result'] == 'resume' for e in p.gateway_lines()[m2['gw']:])
    return result


SCENARIOS = [('N', scenario_normal), ('F1', scenario_f1), ('H4', scenario_h4), ('H2', scenario_h2),
             ('H5', scenario_h5), ('H1', scenario_h1), ('H3', scenario_h3), ('R', scenario_ramp),
             ('FB', scenario_fallback)]


def judge(mode, name, m):
    """(통과, 설명). off: 공격이 통해야 한다. on: 막혀야 한다."""
    off = mode == 'off'
    if name == 'N':
        if off:
            return m['accepts'] >= 3, 'passthrough accepts %d' % m['accepts']
        return m['accepts'] >= 3 and m['rejects'] == 0 and not m['fallbacks'], \
            'accepts %d, rejects %d, fallbacks %s' % (m['accepts'], m['rejects'], m['fallbacks'])
    if name == 'F1':
        if off:
            return m['max_applied'] >= 79.9, 'setpoint 80 applied (max %.2f)' % m['max_applied']
        return m['max_applied'] < 40 and '5_range' in m['rejects'], \
            'max applied %.2f, rejects %s' % (m['max_applied'], m['rejects'])
    if name == 'H4':
        if off:
            return m['final_applied'] < m['floor'], \
                'drifted %.2f -> %.2f, below baseline-3.5 = %.2f' % (m['start'], m['final_applied'], m['floor'])
        return m['min_applied'] >= m['floor'] - 0.05 and '7_baseline' in m['rejects'], \
            'min applied %.2f >= %.2f, rejects %s, fallbacks %s' % (
                m['min_applied'], m['floor'], m['rejects'], m['fallbacks'])
    if name == 'H2':
        if off:
            return m['final_applied'] < m['need_floor'], \
                'underdosed %.2f at 60 NTU (baseline %.2f)' % (m['final_applied'], m['baseline_60'])
        return '4_input' in m['rejects'] and 'consecutive_rejects' in m['fallbacks'] \
            and m['final_applied'] >= m['need_floor'], \
            'rejects %s, fallbacks %s, applied %.2f (baseline %.2f)' % (
                m['rejects'], m['fallbacks'], m['final_applied'], m['baseline_60'])
    if name == 'H5':
        if off:
            return m['min_applied'] <= m['sent'] + 0.05, 'tampered %.2f applied' % m['min_applied']
        return m['min_applied'] > m['sent'] + 0.5 and m['rejects'] == ['2_model'], \
            'min applied %.2f (sent %.2f), rejects %s' % (m['min_applied'], m['sent'], m['rejects'])
    if name == 'H1':
        if off:
            return m['max_applied'] >= 44.9, 'attacker write 45 applied (max %.2f)' % m['max_applied']
        return m['max_applied'] < 40 and m['drop_packets'] > 0, \
            'max applied %.2f, plc2 DROP +%d packets, client %.1f s' % (
                m['max_applied'], m['drop_packets'], m['client_seconds'])
    if name == 'H3':
        command_200 = 200 * config.PRODUCTION_FLOW / 1000      # 400 kg/h
        if off:
            return m['max_command'] >= command_200 - 0.1 and m['valve_closed'], \
                'PLC2 command %.1f kg/h (=200 mg/L), actual %.1f mg/L (pump max), pH %.2f, SIS closed %s' % (
                    m['max_command'], m['max_applied'], m['min_ph'], m['valve_closed'])
        return m['max_command'] <= 50 * config.PRODUCTION_FLOW / 1000 and not m['valve_closed'] \
            and m['plc2_reject_logged'], \
            'PLC2 command max %.1f kg/h, actual max %.2f mg/L, SIS closed %s, DOSE_REJECT %s' % (
                m['max_command'], m['max_applied'], m['valve_closed'], m['plc2_reject_logged'])
    if name == 'R':
        if off:
            return m['max_row_jump'] >= 9.9, 'one step +%.2f' % m['max_row_jump']
        ok = m['reached'] and m['plc2_ramp_logged'] and not m['window_violations']
        return ok, '%.2f -> %.2f in %s process s, max step %.2f, 60 s windows over 2 mg/L: %s' % (
            m['start'], m['target'], m['seconds_to_target'], m['max_row_jump'], m['window_violations'] or 'none')
    if name == 'FB':
        if off:
            return not m['fallbacks'] and abs(m['final_applied'] - m['before']) < 0.1, \
                'AI silent, no fallback, dose stays %.2f' % m['final_applied']
        return m['fallbacks'] == ['stale'] and abs(m['final_applied'] - m['baseline_6']) < 0.1 \
            and m['ignored_before_resume'] >= 1 and m['resume_logged'] and m['accept_after_resume'], \
            'fallback %s, applied %.2f (baseline %.2f), ignored %d until resume, accept after resume %s' % (
                m['fallbacks'], m['final_applied'], m['baseline_6'], m['ignored_before_resume'],
                m['accept_after_resume'])
    raise ValueError(name)


def run_mode(secure, only):
    mode = 'on' if secure else 'off'
    print('\n========== security %s ==========' % mode.upper(), flush=True)
    network = run.build_network()
    METRICS[mode] = {}
    try:
        run.start_devices(network, use_ai_agent=True, feed_scenario=False, use_security=secure)
        plant = Plant(network, secure)
        if not wait_until(lambda: os.path.exists('logs/process.csv') and len(plant.rows()) >= 10, 30):
            record(mode, 'startup', False, 'process.csv did not start')
            return
        if not wait_until(lambda: any(e['result'] == 'accept' for e in plant.gateway_lines()), 60):
            record(mode, 'startup', False, 'no gateway accept within 60 s (see logs/gateway_console.log)')
            return
        for name, function in SCENARIOS:
            if only and name not in only:
                continue
            plant.reset()
            print('--- %s %s: %s' % (mode, name, function.__doc__.strip().splitlines()[0]), flush=True)
            metrics = function(plant)
            METRICS[mode][name] = metrics
            passed, detail = judge(mode, name, metrics)
            record(mode, name, passed, detail)
        for path in ('logs/gateway_console.log', 'logs/plc2.log', 'logs/ai_agent.log'):
            with open(path, errors='replace') as f:
                bad = [line.strip() for line in f if 'Traceback' in line]
            if bad:
                print('%s: %d Traceback lines' % (path, len(bad)), flush=True)
        # 사본은 .txt 로 둔다. .log 면 verify_system.py 의 오류 줄 검사(logs/*.log)에 잡힌다
        for name in ('gateway.log', 'plc2.log', 'gateway_console.log', 'process.csv'):
            copy_name = name[:-len('.log')] + '.txt' if name.endswith('.log') else name
            subprocess.run(['cp', 'logs/' + name, 'logs/verify_security_%s_%s' % (mode, copy_name)])
    finally:
        run.stop(network)


def main():
    parser = argparse.ArgumentParser(description='보안 조치 끔/켬 비교 검증')
    parser.add_argument('--mode', choices=['off', 'on', 'both'], default='both')
    parser.add_argument('--only', nargs='*', help='시나리오 이름 (N F1 H4 H2 H5 H1 H3 R FB)')
    args = parser.parse_args()
    if args.mode in ('on', 'both') and not os.path.exists('dataset/approved_models.json'):
        print('dataset/approved_models.json 이 없습니다. 먼저: venv/bin/python gateway.py approve')
        print('RESULT=FAIL')
        return
    start = time.time()
    try:
        for secure in {'off': [False], 'on': [True], 'both': [False, True]}[args.mode]:
            run_mode(secure, args.only)
    finally:
        print('\n========== 비교 (off: 공격이 통해야 PASS, on: 막혀야 PASS) ==========')
        by = {(mode, name): (ok, detail) for mode, name, ok, detail in RESULTS}
        for name, _ in SCENARIOS:
            for mode in ('off', 'on'):
                if (mode, name) in by:
                    ok, detail = by[(mode, name)]
                    print('%-3s %-3s %-4s %s' % (name, mode, 'PASS' if ok else 'FAIL', detail))
        with open('logs/verify_security.json', 'w') as f:
            json.dump({'results': [{'mode': m, 'scenario': n, 'passed': ok, 'detail': d}
                                   for m, n, ok, d in RESULTS], 'metrics': METRICS}, f, ensure_ascii=False, indent=2)
        passed = sum(1 for _, _, ok, _ in RESULTS if ok)
        print('wall %.0f s, details: logs/verify_security.json, logs/verify_security_<mode>_*' % (time.time() - start))
        print('SUMMARY %d/%d passed' % (passed, len(RESULTS)))
        print('RESULT=%s' % ('PASS' if RESULTS and passed == len(RESULTS) else 'FAIL'))


if __name__ == '__main__':
    main()
