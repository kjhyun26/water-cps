"""AI 에이전트(AI 노드): 원수 탁도를 읽고 AI 모델로 약품 주입률 추천값을 낸다.

HMI와 같은 통신 구조를 쓴다 (설계 문서 5-2절, 6절). hmi 노드에서 hmi.py 와 함께 돈다.
모델은 dataset/train_model.py 로 미리 학습한 파일을 불러온다 (ai_model.py).

실행:
    python ai_agent.py               자동 추천 루프
    python ai_agent.py status        지금 탁도로 추천값을 한 번만 출력
    python ai_agent.py pause         PLC2 쓰기 멈춤 (운영자가 hmi.py dose 로 직접 넣을 때)
    python ai_agent.py resume        PLC2 쓰기 다시 시작
    python ai_agent.py jartest 20.5  운영자가 자-테스트 결과(mg/L)를 입력

추천 주입률 = 기본 추천 − 여유 조정 + 자-테스트 보정 (ai_model.py 설명, 팀 설계 발표 29장)
    입력은 원수 탁도와 그 변화율뿐이다. 조류처럼 센서에 안 보이는 변화는 하루 한 번
    자-테스트 보정으로 메운다. 자-테스트 값은 logs/jar_test.json 에서 읽는다
    (scenario_feeder.py 가 매일 9시에 쓰거나, 운영자가 jartest 명령으로 넣는다).
    추천은 공정 5분마다 새로 낸다 (팀 설계: 약 5분마다).

예측값 패킷 (중간발표 21쪽):
    추천을 낼 때마다 created_at, seq, model_version, model_hash, inputs 를 붙인 패킷을
    logs/ai_predictions.jsonl 에 한 줄씩 쓴다. 게이트웨이(보안 설계 담당)가 이 패킷으로
    시각·순번·승인 모델·입력 원본을 검사한다.
    팀 설계는 "AI는 PLC에 바로 쓰지 못한다"이다. 게이트웨이가 생기기 전까지는 DIRECT_WRITE
    = True 로 두어 임시로 PLC2에 직접 쓴다. 게이트웨이가 붙으면 False 로 바꾼다.

보안 조치 (보안 설계 문서 F1·H2, AI 전용 AI1·AI2):
    F1 출력 범위 검사 — 추천값이 5~50 mg/L를 벗어나면 범위 끝값으로 자른다.
                    유형 모델이 없으면 직전 추천값을 유지한다.
    H2 입력 이상 탐지 — 탁도가 한 번에 100 NTU 넘게 바뀌거나 1000 NTU를 넘으면 경보를 내고
                    그 값으로 추천을 바꾸지 않는다(직전 값 유지). 새 값이 5번 연속
                    비슷하게 읽히면(공정 약 5분) 실제 변화로 보고 받아들인다.
    AI1 처음 보는 입력 감지 — 입력이 학습 범위 밖이면 범위 끝으로 잘라 예측하고, 그 값과
                    직전 추천값 중 큰 쪽을 쓴다(모르면 줄이지 않음). 운영자 확인 경보를 낸다.
    AI2 예측 불확실성 감시 — 유형을 확신하지 못하거나 나무들의 예측이 크게 갈리면 넉넉한 쪽으로 넣는다.
    AI1·AI2 의 범위와 기준은 dataset/models/guard.json (ai_model.py 설명 참고).
"""

import datetime
import json
import os
import sys
import time

from minicps.devices import HMI

import ai_model
from config import (
    ADDRESS, AIT101, CLIENT_PROTOCOL, DEVICE_SLEEP_SECONDS, DOSE_SETPOINT,
    INITIAL_DOSE_SETPOINT, INITIAL_RAW_TURBIDITY, SIM_TIME_PLC1, STATE, to_number,
)

PLC1_ADDRESS = ADDRESS['plc1']
PLC2_ADDRESS = ADDRESS['plc2']

# 게이트웨이가 생기기 전까지 임시로 PLC2에 직접 쓴다 (팀 설계 규칙은 False)
DIRECT_WRITE = True

PAUSE_FILE = 'logs/ai_agent.pause'
JAR_TEST_FILE = 'logs/jar_test.json'
PREDICTION_LOG = 'logs/ai_predictions.jsonl'
USAGE = 'usage: python ai_agent.py [status | pause | resume | jartest <mg/L>]'

PREDICTION_INTERVAL_SECONDS = 300   # 공정 시각 5분마다 새 추천
PROCESS_NAME = 'chemical'           # 약품 주입 공정

# 보안 F1: 출력 범위 (mg/L)
DOSE_MIN = 5.0
DOSE_MAX = 50.0

# 보안 H2: 입력 이상 탐지
TURBIDITY_SPIKE_THRESHOLD = 100.0   # 한 번에 이만큼 넘게 바뀌면 의심 (NTU)
TURBIDITY_MAX_VALID = 1000.0        # 이 값을 넘으면 센서 고장이나 위변조로 본다 (NTU)
SPIKE_CONFIRM_CYCLES = 5            # 의심 값이 이만큼 연속 비슷하게 읽히면 받아들인다


def decide(turbidity, trend, models, guard, previous, correction):
    """(추천값, assess 결과, 세 부분). AI1·AI2 판단 → 세 부분 공식 → AI1 줄이지 않음 → F1."""
    result = ai_model.assess(ai_model.features(turbidity, trend), *models, guard)
    setpoint, parts = ai_model.recommend(result, correction)
    if setpoint is None:
        return previous, result, parts          # 유형 모델 없음 → 직전 값 유지
    if result['ood']:
        setpoint = max(setpoint, previous)      # AI1: 처음 보는 입력에서는 줄이지 않는다
    if not DOSE_MIN <= setpoint <= DOSE_MAX:
        clipped = min(max(setpoint, DOSE_MIN), DOSE_MAX)
        print('[AI][F1] 추천값 %.2f → %.2f mg/L 로 자름' % (setpoint, clipped))
        result['flags'].append('F1_CLIPPED')
        setpoint = clipped
    return round(setpoint, 4), result, parts


def describe_ood(ood):
    return ', '.join('%s=%.3g (학습 범위 %.3g~%.3g)' % item for item in ood)


def read_jar_test():
    try:
        with open(JAR_TEST_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def write_jar_test(dose, sim_time):
    temporary_path = JAR_TEST_FILE + '.tmp'
    with open(temporary_path, 'w') as f:
        json.dump({'sim_time': sim_time, 'dose_mg_per_l': dose, 'by': 'operator',
                   'entered_at': time.time()}, f)
    os.replace(temporary_path, JAR_TEST_FILE)


class TurbidityGuard:
    """H2 입력 이상 탐지. accept(값) → (믿을 탁도, 경보 여부)."""

    def __init__(self, initial):
        self.trusted = initial
        self.suspect = None
        self.streak = 0

    def accept(self, reading):
        if reading > TURBIDITY_MAX_VALID or reading < 0:
            print('[AI][H2] 탁도 %.1f NTU 는 허용 범위(0~%.0f) 밖. 무시하고 %.1f NTU 로 계속'
                  % (reading, TURBIDITY_MAX_VALID, self.trusted))
            self.suspect, self.streak = None, 0
            return self.trusted, True
        if abs(reading - self.trusted) <= TURBIDITY_SPIKE_THRESHOLD:
            self.trusted = reading
            self.suspect, self.streak = None, 0
            return reading, False
        if self.suspect is not None and abs(reading - self.suspect) <= TURBIDITY_SPIKE_THRESHOLD:
            self.streak += 1
        else:
            self.streak = 1
        self.suspect = reading
        if self.streak >= SPIKE_CONFIRM_CYCLES:
            print('[AI][H2] 탁도 %.1f NTU 가 %d번 연속 읽혀 실제 변화로 받아들임'
                  % (reading, self.streak))
            self.trusted = reading
            self.suspect, self.streak = None, 0
            return reading, True
        print('[AI][H2] 탁도 급변 %.1f → %.1f NTU (%d/%d). 직전 추천값 유지'
              % (self.trusted, reading, self.streak, SPIKE_CONFIRM_CYCLES))
        return self.trusted, True


class AiAgent(HMI):

    def setup(self):
        try:
            self.models = ai_model.load_models()
            self.guard_config = ai_model.load_guard()
        except FileNotFoundError as error:
            print('[AI] 모델 파일이 없습니다 (%s). 먼저 학습하세요:' % error.filename)
            print('     venv/bin/python dataset/build_dataset.py && venv/bin/python dataset/train_model.py')
            sys.exit(1)
        info = ai_model.load_model_info()
        self.model_version = info.get('model_version', 'unknown')
        self.model_hash = ai_model.model_hash()
        if info.get('model_hash') and info['model_hash'] != self.model_hash:
            print('[AI] 경고: 모델 파일 해시가 학습 때 기록과 다릅니다 (보안 설계 H5 확인 필요)')
        print('[AI] 모델 로드 완료: %s, %s, sha256 %s…'
              % (', '.join(sorted(self.models[2])), self.model_version, self.model_hash[:12]))
        print('[AI] PLC2 쓰기: %s' % ('직접 (게이트웨이 없음, 임시)' if DIRECT_WRITE else '안 함 (패킷만)'))
        self.guard = TurbidityGuard(INITIAL_RAW_TURBIDITY)
        self.trend = ai_model.TurbidityTrend()
        self.turbidity_trend = 0.0
        self.setpoint = INITIAL_DOSE_SETPOINT
        self.correction = 0.0
        self.last_jar_key = None
        self.last_prediction_time = None
        self.seq = int(time.time() * 1000)   # 재시작해도 줄지 않게 (게이트웨이 ③ 순번 검사)
        self.paused = False
        self.ood_active = False

    def main_loop(self):
        # MiniCPS의 HMI 클래스는 PLC와 달리 pre_loop()을 부르지 않는다. 여기서 준비한다.
        arguments = sys.argv[1:]
        if arguments == ['pause']:
            open(PAUSE_FILE, 'w').close()
            print('AI의 PLC2 쓰기 멈춤. 다시 시작: python ai_agent.py resume')
            return
        if arguments == ['resume']:
            if os.path.exists(PAUSE_FILE):
                os.remove(PAUSE_FILE)
            print('AI의 PLC2 쓰기 다시 시작')
            return
        if len(arguments) == 2 and arguments[0] == 'jartest':
            dose = to_number(arguments[1], None)
            if dose is None or not 0 < dose < 100:
                print(USAGE)
                return
            sim_time = to_number(self.receive(SIM_TIME_PLC1, PLC1_ADDRESS), 0.0)
            write_jar_test(dose, sim_time)
            print('자-테스트 %.2f mg/L 입력됨' % dose)
            return
        if arguments not in ([], ['status']):
            print(USAGE)
            return
        self.setup()
        if arguments == ['status']:
            self.status_once()
        else:
            self.control_loop()

    def read_turbidity(self):
        return to_number(self.receive(AIT101, PLC1_ADDRESS), self.guard.trusted)

    def check_jar_test(self, turbidity):
        """새 자-테스트 값이 있으면 보정을 다시 계산한다."""
        jar = read_jar_test()
        if not jar:
            return
        key = (jar.get('sim_time'), jar.get('dose_mg_per_l'), jar.get('entered_at'))
        if key == self.last_jar_key:
            return
        self.last_jar_key = key
        result = ai_model.assess(ai_model.features(turbidity, self.turbidity_trend),
                                 *self.models, self.guard_config)
        if result['required'] is None:
            return
        self.correction = ai_model.jar_correction(float(jar['dose_mg_per_l']), result['required'])
        print('[AI] 자-테스트 %.2f mg/L (%s) vs 모델 예측 %.2f → 보정 %+.2f mg/L (다음 실험까지)'
              % (jar['dose_mg_per_l'], jar.get('by', '?'), result['required'], self.correction))

    def write_packet(self, sim_time, turbidity, result, parts):
        self.seq += 1
        packet = {
            'process': PROCESS_NAME,
            'setpoint': self.setpoint,
            'unit': 'mg/L',
            'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
            'seq': self.seq,
            'model_version': self.model_version,
            'model_hash': self.model_hash,
            'inputs': {'turbidity_ntu': turbidity, 'turbidity_trend': round(self.turbidity_trend, 4),
                       'sim_time': sim_time},
            'water_type': result.get('water_type'),
            'confidence': round(result.get('confidence', 0.0), 3),
            'parts': parts,
            'flags': result.get('flags', []),
        }
        with open(PREDICTION_LOG, 'a') as f:
            f.write(json.dumps(packet, ensure_ascii=False) + '\n')

    def control_loop(self):
        print('[AI] 추천 루프 시작 (공정 %d분마다 추천)' % (PREDICTION_INTERVAL_SECONDS // 60))
        open(PREDICTION_LOG, 'w').close()
        while True:
            cycle_start = time.time()

            paused = os.path.exists(PAUSE_FILE)
            if paused != self.paused:
                print('[AI] PLC2 쓰기 %s' % ('멈춤 (운영자 수동)' if paused else '다시 시작'))
                self.paused = paused

            reading = self.read_turbidity()
            sim_time = to_number(self.receive(SIM_TIME_PLC1, PLC1_ADDRESS), 0.0)
            turbidity, anomaly = self.guard.accept(reading)
            if not anomaly:
                # 변화율 기록에는 H2 검사를 통과한 값만 넣는다
                self.turbidity_trend = self.trend.update(sim_time, turbidity)
                self.check_jar_test(turbidity)

            due = (self.last_prediction_time is None or sim_time < self.last_prediction_time
                   or sim_time - self.last_prediction_time >= PREDICTION_INTERVAL_SECONDS)
            if due and not anomaly:
                self.last_prediction_time = sim_time
                self.setpoint, result, parts = decide(
                    turbidity, self.turbidity_trend, self.models, self.guard_config,
                    self.setpoint, self.correction)
                self.report(sim_time, reading, result, parts)
                self.write_packet(sim_time, turbidity, result, parts)

            if DIRECT_WRITE and not self.paused:
                self.send(DOSE_SETPOINT, self.setpoint, PLC2_ADDRESS)

            time.sleep(max(0.0, DEVICE_SLEEP_SECONDS - (time.time() - cycle_start)))

    def report(self, sim_time, reading, result, parts):
        flags = result['flags']
        ood = bool(result['ood'])
        if ood and not self.ood_active:
            print('[AI][AI1] 처음 보는 입력: %s. 학습 범위로 잘라 예측하고 주입률을 줄이지 않음. '
                  '운영자 확인 필요' % describe_ood(result['ood']))
        elif self.ood_active and not ood:
            print('[AI][AI1] 입력이 학습 범위로 돌아옴. AI 예측 다시 사용')
        self.ood_active = ood
        if 'AI2_TYPE' in flags:
            print('[AI][AI2] 유형 확신 %.2f 낮음. 두 유형 중 큰 예측 사용' % result['confidence'])
        if 'AI2_SPREAD' in flags:
            print('[AI][AI2] 나무 예측 편차 %.2f mg/L 큼. 편차의 %.0f배 더 넣음'
                  % (result['spread'], self.guard_config['spread_factor']))
        minutes = int(sim_time % 86400) // 60
        print('[AI] %d일차 %02d:%02d 탁도=%.1f NTU 변화율=%+.2f 유형=%s(%.2f) 추천=%.2f mg/L '
              '(기본 %.2f − 여유조정 %.1f + 자-테스트 %+.2f)%s%s'
              % (int(sim_time // 86400) + 1, minutes // 60, minutes % 60, reading,
                 self.turbidity_trend, result['water_type'], result['confidence'], self.setpoint,
                 parts.get('base', float('nan')), parts.get('margin_reduction', 0.0),
                 parts.get('jar_correction', 0.0),
                 ' [' + ','.join(flags) + ']' if flags else '',
                 ' (멈춤: PLC2에 안 씀)' if self.paused else ''))

    def status_once(self):
        reading = self.read_turbidity()
        jar = read_jar_test()
        if jar:
            self.check_jar_test(reading)
        # 한 번만 읽으므로 변화율을 알 수 없다. 0(변화 없음)으로 본다
        setpoint, result, parts = decide(reading, 0.0, self.models, self.guard_config,
                                         self.setpoint, self.correction)
        print('탁도: %.1f NTU' % reading)
        print('유형: %s (확신 %.2f)' % (result['water_type'], result['confidence']))
        if result['ood']:
            print('AI1 처음 보는 입력: %s' % describe_ood(result['ood']))
        print('AI 추천값: %.2f mg/L  (기본 %s − 여유조정 %s + 자-테스트 %s)'
              % (setpoint, parts.get('base'), parts.get('margin_reduction'), parts.get('jar_correction')))
        print('PLC2 현재 설정값: %.2f mg/L'
              % to_number(self.receive(DOSE_SETPOINT, PLC2_ADDRESS), float('nan')))
        print('PLC2 쓰기: %s' % ('멈춤' if os.path.exists(PAUSE_FILE) else
                                ('직접 (임시)' if DIRECT_WRITE else '안 함')))
        print('모델: %s, sha256 %s…' % (self.model_version, self.model_hash[:12]))
        if result['flags']:
            print('플래그: %s' % ', '.join(result['flags']))


if __name__ == '__main__':
    AiAgent(name='hmi', state=STATE, protocol=CLIENT_PROTOCOL)
