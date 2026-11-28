"""게이트웨이 (단순 버전): AI 예측값 패킷을 8가지로 검사하고, 통과한 값만 PLC2에 쓴다.

기준: 게이트웨이 구현안내(AI 파트, 2026-11-28) 8장 뼈대, 중간발표 22쪽 검사, 28장 폴백.
hmi 노드(192.168.1.50)에서 돈다. iptables 가 PLC2 쓰기를 hmi 에서만 받으므로 AI(ai 노드)는
PLC2에 닿지 못하고, 이 게이트웨이를 거쳐야 한다.

실행:
    python gateway.py                검사 루프 (run.USE_SECURITY = True)
    python gateway.py --passthrough  검사 없이 패킷 값을 그대로 PLC2에 쓴다 (보안 끔 비교용)
    python gateway.py resume         폴백 → AI 모드 복귀 요청 (운영자 승인). 도는 게이트웨이가 다음 주기에 받는다
    python gateway.py approve        지금 모델 파일을 승인 목록(dataset/approved_models.json)에 넣는다 (운영자)

입력: logs/ai_predictions.jsonl (ai_agent.py, 한 줄에 패킷 하나)
      PLC1 AIT101·SIM_TIME, PLC2 DOSE_SETPOINT, PLC3 TURBIDITY_ALARM (직접 읽은 원본)
      기준값: baseline.py 조견표 + logs/jar_test.json 자-테스트 갱신 (AI 출력은 쓰지 않는다)
기록: logs/gateway.log, 한 줄에 JSON 하나
      {"sim_time", "seq", "setpoint", "result", "stage", "reason", "mode"}
      result: start | accept | reject | ignored(폴백 중이라 통과해도 적용 안 함) | fallback | baseline | resume
      stage:  1_format 2_model 3_time 3_seq 4_input 5_range 6_step 7_baseline 8_hourly (거부한 검사)
              폴백일 때는 원인 consecutive_rejects | stale | turbidity_alarm
      mode:   ai | fallback | passthrough

뼈대에서 바꾼 점:
    - 승인 목록은 model_info.json 이 아니라 따로 둔 dataset/approved_models.json (운영자 승인 때 approve 로 복사).
      학습 스크립트가 덮어쓰는 파일을 그대로 믿으면 모델을 바꾼 쪽이 승인 목록도 함께 바꿀 수 있다.
    - ④ 탁도 대조는 지금 값이 아니라 패킷의 sim_time 에 가장 가까운 PLC1 값과 한다. 호우 때 탁도가 공정 1분에
      몇 NTU씩 바뀌어, 읽는 시각이 1~2분 어긋나면 정상 패킷이 거부되기 때문이다.
    - PLC 값을 못 읽으면(nan) 검사를 통과시키지 않는다. 뼈대는 nan 과의 비교가 거짓이라 ④⑥을 통과시켰다.
    - 파일 끝의 덜 쓰인 줄은 다음 주기에 읽는다. 시작할 때는 파일 끝부터 읽는다(지난 패킷을 다시 검사하지 않음).
    - 재시작해도 폴백이 풀리지 않는다(logs/gateway_mode.txt 를 이어 씀). 복귀는 resume 으로만.
    - 자-테스트 갱신량은 실험 시각의 탁도로 계산한다 (baseline.jar_correction 의 정의).
"""

import collections
import datetime
import getpass
import json
import math
import os
import sys
import time

import baseline
from config import (ADDRESS, AIT101, CLIENT_PROTOCOL, DEVICE_SLEEP_SECONDS, DOSE_SETPOINT,
                    SIM_TIME_PLC1, STATE, TURBIDITY_ALARM, to_number, to_numbers)

PACKETS = 'logs/ai_predictions.jsonl'
JAR_TEST = 'logs/jar_test.json'
APPROVED = 'dataset/approved_models.json'
MODE_FILE = 'logs/gateway_mode.txt'
GATEWAY_LOG = 'logs/gateway.log'
USAGE = 'usage: python gateway.py [--passthrough | resume | approve]'

REQUIRED = ('process', 'setpoint', 'unit', 'created_at', 'seq', 'model_hash', 'inputs')
RANGE = (8.0, 45.0)              # ⑤ 게이트웨이 범위 (PLC 한계 5~50보다 좁게)
MAX_STEP = 5.0                   # ⑥ PLC2 현재 설정값과의 차이
BELOW_BASELINE = 3.5             # ⑦ 기준값보다 이만큼 넘게 낮으면 거부
HOUR_LIMIT = 10.0                # ⑧ 공정 1시간 전 적용값과의 차이
HOUR_SECONDS = 3600
STALE_SECONDS = 900              # ③ 공정 15분 넘은 패킷 거부, 통과한 예측이 15분 없으면 폴백
TURBIDITY_ABS_TOLERANCE = 5.0    # ④ max(5 NTU, 10%)
TURBIDITY_REL_TOLERANCE = 0.10
TURBIDITY_MATCH_SECONDS = 120    # ④ 패킷 시각과 이만큼 안쪽에 PLC1 기록이 없으면 지금 값과 비교
REJECTS_TO_FALLBACK = 3          # 연속 거부 횟수
HISTORY_SECONDS = 2 * 3600       # 적용값·탁도 기록 보관 (공정 시각)


def finite(value):
    return isinstance(value, float) and math.isfinite(value)


def json_safe(value):
    """nan 은 JSON 이 아니므로 null 로 쓴다."""
    return value if not isinstance(value, float) or math.isfinite(value) else None


def write_atomic(path, text):
    temporary_path = path + '.tmp'
    with open(temporary_path, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(temporary_path, path)


def read_mode():
    try:
        with open(MODE_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def load_approved():
    """승인된 model_hash 집합. 파일이 없거나 깨졌으면 빈 집합(모든 패킷이 ②에서 거부된다)."""
    try:
        with open(APPROVED) as f:
            entries = json.load(f).get('approved', [])
        return {entry['model_hash'] for entry in entries if isinstance(entry, dict) and 'model_hash' in entry}
    except FileNotFoundError:
        return set()
    except (ValueError, AttributeError, TypeError, KeyError):
        print('[GW] 경고: %s 를 읽지 못함. 승인 모델 없음으로 본다' % APPROVED)
        return set()


def approve():
    """운영자 승인: 지금 모델 파일의 해시를 계산해 학습 기록과 같으면 승인 목록에 넣는다."""
    import ai_model
    info = ai_model.load_model_info()
    actual = ai_model.model_hash()
    if info.get('model_hash') != actual:
        print('승인 거부: 모델 파일 해시 %s… 가 학습 기록(model_info.json) %s… 와 다릅니다'
              % (actual[:12], str(info.get('model_hash'))[:12]))
        return 1
    try:
        with open(APPROVED) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {'approved': []}
    if any(entry.get('model_hash') == actual for entry in data['approved']):
        print('이미 승인됨: %s sha256 %s…' % (info.get('model_version'), actual[:12]))
        return 0
    data['approved'].append({
        'model_version': info.get('model_version'),
        'model_hash': actual,
        'approved_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'by': os.environ.get('SUDO_USER') or getpass.getuser(),
    })
    write_atomic(APPROVED, json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print('승인: %s sha256 %s… → %s' % (info.get('model_version'), actual[:12], APPROVED))
    return 0


def log_event(sim_time, seq, setpoint, result, stage, reason, mode):
    record = {'sim_time': json_safe(sim_time), 'seq': seq, 'setpoint': json_safe(setpoint),
              'result': result, 'stage': stage, 'reason': reason, 'mode': mode}
    with open(GATEWAY_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    print('[GW] %s' % json.dumps(record, ensure_ascii=False), flush=True)


def nearest(history, when):
    """(시각, 값) 기록에서 when 에 가장 가까운 것. 없으면 None."""
    if not history:
        return None
    return min(history, key=lambda item: abs(item[0] - when))


# minicps 는 장치 노드의 가상환경에만 있다. approve·resume 은 minicps 없이도 돌게 아래에서 불러온다
if __name__ == '__main__' and sys.argv[1:] in ([], ['--passthrough']):
    from minicps.devices import HMI
else:
    HMI = object


class Gateway(HMI):

    def main_loop(self):
        # MiniCPS의 HMI 클래스는 pre_loop()을 부르지 않는다. 여기서 준비한다
        self.passthrough = sys.argv[1:] == ['--passthrough']
        self.table = baseline.load()
        self.approved = load_approved()
        self.turbidity = self.sim_time = self.current = float('nan')
        self.alarm = 0.0
        self.turbidity_history = collections.deque()   # (공정 시각, PLC1 탁도)
        self.applied_history = collections.deque()     # (공정 시각, PLC2 설정값)
        self.jar_key = None
        self.correction = 0.0
        self.last_seq = None
        self.rejects = 0
        self.logged_baseline = None
        # 시작할 때는 파일 끝부터 읽는다. 지난 실행의 패킷을 다시 검사하지 않는다
        self.offset = os.path.getsize(PACKETS) if os.path.exists(PACKETS) else 0

        if self.passthrough:
            self.mode = 'passthrough'
        else:
            # 재시작해도 폴백은 풀리지 않는다 (복귀는 운영자 resume 으로만)
            self.mode = 'fallback' if read_mode() == 'fallback' else 'ai'
            write_atomic(MODE_FILE, self.mode)
        self.read_plcs()
        self.last_ok_time = self.sim_time
        detail = 'passthrough: 검사 없이 그대로 씀' if self.passthrough else (
            '승인 모델 %d개, 범위 %.0f~%.0f, 변화 ±%.0f, 기준값 −%.1f, 1시간 ±%.0f'
            % (len(self.approved), RANGE[0], RANGE[1], MAX_STEP, BELOW_BASELINE, HOUR_LIMIT))
        log_event(self.sim_time, None, None, 'start', None, detail, self.mode)
        if not self.passthrough and not self.approved:
            print('[GW] 경고: 승인된 모델이 없습니다. 모든 패킷이 ②에서 거부됩니다 '
                  '(승인: python gateway.py approve)', flush=True)

        while True:
            cycle_start = time.time()
            self.cycle()
            time.sleep(max(0.0, DEVICE_SLEEP_SECONDS - (time.time() - cycle_start)))

    # ---------------------------------------------------------------- 읽기

    def read_plcs(self):
        """PLC 원본 값을 읽는다. 못 읽은 값은 직전 값을 쓰고, 처음부터 못 읽었으면 nan 으로 둔다."""
        self.turbidity, self.sim_time = to_numbers(
            self.receive_multiple([AIT101, SIM_TIME_PLC1], ADDRESS['plc1']), [self.turbidity, self.sim_time])
        self.current = to_number(self.receive(DOSE_SETPOINT, ADDRESS['plc2']), self.current)
        self.alarm = to_number(self.receive(TURBIDITY_ALARM, ADDRESS['plc3']), self.alarm)
        if finite(self.sim_time):
            for history, value in ((self.turbidity_history, self.turbidity),
                                   (self.applied_history, self.current)):
                if finite(value) and (not history or self.sim_time > history[-1][0]):
                    history.append((self.sim_time, value))
                while history and self.sim_time - history[0][0] > HISTORY_SECONDS:
                    history.popleft()

    def new_lines(self):
        """새로 붙은 완전한 줄. 덜 쓰인 마지막 줄은 다음 주기에 읽는다."""
        if not os.path.exists(PACKETS):
            return []
        if os.path.getsize(PACKETS) < self.offset:     # AI 재시작으로 파일이 새로 생김
            self.offset = 0
        with open(PACKETS, 'rb') as f:
            f.seek(self.offset)
            data = f.read()
        end = data.rfind(b'\n')
        if end < 0:
            return []
        self.offset += end + 1
        return [line for line in data[:end + 1].decode('utf-8', 'replace').splitlines() if line.strip()]

    def turbidity_at(self, when):
        """when 시각의 PLC1 탁도. 가까운 기록이 없으면 지금 값."""
        match = nearest(self.turbidity_history, when)
        if match and abs(match[0] - when) <= TURBIDITY_MATCH_SECONDS:
            return match[1]
        return self.turbidity

    def baseline_now(self):
        """기준값 = 조견표(지금 탁도) + 자-테스트 갱신량. 탁도를 모르면 None."""
        if not finite(self.turbidity):
            return None
        try:
            with open(JAR_TEST) as f:
                jar = json.load(f)
            key = (jar.get('sim_time'), jar.get('dose_mg_per_l'), jar.get('entered_at'))
            if key != self.jar_key:
                self.jar_key = key
                at_test = self.turbidity_at(float(jar['sim_time']))
                self.correction = baseline.jar_correction(float(jar['dose_mg_per_l']), at_test, self.table)
                print('[GW] 자-테스트 %.2f mg/L (%s, 탁도 %.1f NTU) → 기준값 갱신 %+.2f mg/L'
                      % (float(jar['dose_mg_per_l']), jar.get('by', '?'), at_test, self.correction), flush=True)
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError) as error:
            print('[GW] 자-테스트 파일을 읽지 못함 (%s). 직전 갱신량 유지' % error, flush=True)
        return baseline.baseline_dose(self.turbidity, self.table, self.correction)

    # ---------------------------------------------------------------- 검사

    def check(self, packet, base):
        """(stage, reason). 통과하면 (None, None)."""
        if not isinstance(packet, dict) or any(key not in packet for key in REQUIRED) \
                or not isinstance(packet['inputs'], dict):
            return '1_format', '필수 필드 누락'
        if packet['unit'] != 'mg/L' or packet['process'] != 'chemical':
            return '1_format', '공정·단위가 chemical, mg/L 가 아님'
        seq = packet['seq']
        if not isinstance(seq, int) or isinstance(seq, bool):
            return '1_format', 'seq 가 정수가 아님'
        try:
            value = float(packet['setpoint'])
            sent_time = float(packet['inputs']['sim_time'])
            sent_turbidity = float(packet['inputs']['turbidity_ntu'])
        except (KeyError, TypeError, ValueError):
            return '1_format', 'setpoint·inputs.sim_time·inputs.turbidity_ntu 가 숫자가 아님'
        if not all(map(finite, (value, sent_time, sent_turbidity))) or isinstance(packet['setpoint'], bool):
            return '1_format', '숫자가 nan·inf'

        # ② 출처·모델 (H5)
        if packet['model_hash'] not in self.approved:
            return '2_model', '승인 목록에 없는 모델 %s… (H5)' % str(packet['model_hash'])[:12]

        # ③ 시각·순번 (F1, 재전송)
        if not finite(self.sim_time):
            return '3_time', 'PLC1 공정 시각을 읽지 못함'
        age = self.sim_time - sent_time
        if not 0 <= age <= STALE_SECONDS:
            return '3_time', '공정 시각 차이 %.0f초 (허용 0~%d)' % (age, STALE_SECONDS)
        if self.last_seq is not None and seq <= self.last_seq:
            return '3_seq', 'seq %d ≤ 직전 통과 %d (재전송)' % (seq, self.last_seq)

        # ④ 입력 센서 (H2): AI가 본 탁도와 같은 시각의 PLC1 원본
        original = self.turbidity_at(sent_time)
        if not finite(original):
            return '4_input', 'PLC1 탁도를 읽지 못함'
        tolerance = max(TURBIDITY_ABS_TOLERANCE, TURBIDITY_REL_TOLERANCE * original)
        if abs(sent_turbidity - original) > tolerance:
            return '4_input', 'AI 탁도 %.1f vs PLC1 %.1f NTU (허용 ±%.1f, H2)' % (sent_turbidity, original, tolerance)

        # ⑤ 범위 (F1)
        if not RANGE[0] <= value <= RANGE[1]:
            return '5_range', '%.2f mg/L 가 %.0f~%.0f 밖 (F1)' % (value, RANGE[0], RANGE[1])

        # ⑥ 변화량
        if not finite(self.current):
            return '6_step', 'PLC2 현재 설정값을 읽지 못함'
        if abs(value - self.current) > MAX_STEP:
            return '6_step', '현재 %.2f → %.2f mg/L, 차이 %.2f > %.1f' % (
                self.current, value, abs(value - self.current), MAX_STEP)

        # ⑦ 기준값 차이 (H2, H4, 조류)
        if base is None:
            return '7_baseline', '기준값을 계산하지 못함 (탁도 모름)'
        if value < base - BELOW_BASELINE:
            return '7_baseline', '%.2f mg/L 가 기준값 %.2f 보다 %.2f 낮음 (허용 %.1f)' % (
                value, base, base - value, BELOW_BASELINE)

        # ⑧ 누적 변화 (H4): 공정 1시간 전 적용값과 비교. 게이트웨이가 1시간 넘게 돌아야 기록이 있다
        past = [applied for when, applied in self.applied_history if self.sim_time - when >= HOUR_SECONDS]
        if past and abs(value - past[-1]) > HOUR_LIMIT:
            return '8_hourly', '1시간 전 %.2f → %.2f mg/L, 차이 %.2f > %.1f (H4)' % (
                past[-1], value, abs(value - past[-1]), HOUR_LIMIT)
        return None, None

    # ---------------------------------------------------------------- 주기

    def cycle(self):
        # 패킷을 먼저 읽고 PLC를 나중에 읽는다. PLC1 SIM_TIME 은 늘기만 하므로, 이 순서면 정직한 패킷의
        # inputs.sim_time 이 게이트웨이가 읽은 시각보다 뒤일 수 없다. 거꾸로 읽으면 PLC를 읽는 동안(실제 약 1초)
        # AI가 더 새 시각으로 쓴 패킷을 ③ "미래"로 거부한다 (verify_security N on, 2026-11-29)
        lines = self.new_lines()
        self.read_plcs()
        if self.passthrough:
            for line in lines:
                self.pass_through(line)
            return

        base = self.baseline_now()
        if self.mode == 'fallback' and read_mode() == 'ai':
            self.mode = 'ai'
            self.rejects = 0
            self.last_ok_time = self.sim_time
            self.logged_baseline = None
            log_event(self.sim_time, None, None, 'resume', None, '운영자 승인으로 AI 모드 복귀', self.mode)

        for line in lines:
            self.handle(line, base)

        # 폴백 조건: 연속 거부, 통과한 예측 15분 없음, 침전수 탁도 경보. 내려가는 길은 자동
        if self.mode == 'ai':
            trigger = None
            if self.rejects >= REJECTS_TO_FALLBACK:
                trigger = ('consecutive_rejects', '연속 %d번 거부' % self.rejects)
            elif finite(self.sim_time) and finite(self.last_ok_time) \
                    and self.sim_time - self.last_ok_time > STALE_SECONDS:
                trigger = ('stale', '통과한 예측이 공정 %.0f분 없음' % ((self.sim_time - self.last_ok_time) / 60))
            elif int(self.alarm) == 1:
                trigger = ('turbidity_alarm', 'PLC3 침전수 탁도 경보')
            if trigger:
                self.mode = 'fallback'
                write_atomic(MODE_FILE, self.mode)
                log_event(self.sim_time, None, base, 'fallback', trigger[0],
                          trigger[1] + '. 기준값 사용 (복귀: python gateway.py resume)', self.mode)
                self.logged_baseline = base

        if self.mode == 'fallback' and base is not None:
            self.send(DOSE_SETPOINT, round(base, 4), ADDRESS['plc2'])
            if self.logged_baseline is None or abs(base - self.logged_baseline) >= 0.01:
                log_event(self.sim_time, None, round(base, 4), 'baseline', None,
                          '폴백 중 기준값 씀 (자-테스트 갱신 %+.2f)' % self.correction, self.mode)
                self.logged_baseline = base

    def handle(self, line, base):
        try:
            packet = json.loads(line)
        except ValueError:
            packet = None
        fields = packet if isinstance(packet, dict) else {}
        seq, setpoint = fields.get('seq'), fields.get('setpoint')
        if packet is None:
            stage, reason = '1_format', 'JSON 아님'
        else:
            stage, reason = self.check(packet, base)
        if stage:
            self.rejects += 1
            log_event(self.sim_time, seq, setpoint, 'reject', stage,
                      '%s (연속 %d)' % (reason, self.rejects), self.mode)
            return
        self.rejects = 0
        self.last_seq = seq
        self.last_ok_time = self.sim_time
        value = round(float(setpoint), 4)
        if self.mode == 'ai':
            self.send(DOSE_SETPOINT, value, ADDRESS['plc2'])
            log_event(self.sim_time, seq, value, 'accept', None, '8가지 검사 통과 → PLC2', self.mode)
        else:
            log_event(self.sim_time, seq, value, 'ignored', None,
                      '검사 통과했지만 폴백 중이라 적용 안 함 (복귀: python gateway.py resume)', self.mode)

    def pass_through(self, line):
        """보안 끔: 검사 없이 그대로 쓴다. 숫자로 못 바꾸는 값만 건너뛴다(PLC2에 쓸 수가 없다)."""
        try:
            packet = json.loads(line)
            seq, setpoint = packet.get('seq'), packet.get('setpoint')
            value = round(float(setpoint), 4)
            if not math.isfinite(value):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            log_event(self.sim_time, None, None, 'reject', None, '숫자 setpoint 없음 (쓸 수 없음)', self.mode)
            return
        self.send(DOSE_SETPOINT, value, ADDRESS['plc2'])
        log_event(self.sim_time, seq, value, 'accept', None, '검사 없이 그대로 씀', self.mode)


if __name__ == '__main__':
    arguments = sys.argv[1:]
    if arguments == ['resume']:
        write_atomic(MODE_FILE, 'ai')
        print('AI 모드 복귀 요청 (운영자 승인). 도는 게이트웨이가 다음 주기에 반영하고 logs/gateway.log 에 남긴다')
    elif arguments == ['approve']:
        sys.exit(approve())
    elif arguments in ([], ['--passthrough']):
        Gateway(name='gateway', state=STATE, protocol=CLIENT_PROTOCOL)
    else:
        print(USAGE)
