"""AI 에이전트: 원수 탁도를 읽고 AI 모델로 주입률 설정값을 정해 PLC2에 전달한다.

HMI와 같은 통신 구조를 쓴다 (설계 문서 5-2절, 6절). hmi 노드에서 hmi.py 와 함께 돈다.
모델은 dataset/train_model.py 로 미리 학습한 pkl 파일을 불러온다 (ai_model.py).

실행:
    python ai_agent.py          자동 제어 루프
    python ai_agent.py status   지금 탁도로 예측값을 한 번만 출력
    python ai_agent.py pause    자동 제어 멈춤 (운영자가 hmi.py dose 로 직접 넣을 때)
    python ai_agent.py resume   자동 제어 다시 시작

설정값 = 예측한 필요 주입률 + 여유(ai_model.SAFETY_MARGIN)
조류 보정값은 logs/algae_sensor.txt 에서 읽는다 (scenario_feeder.py 가 쓰는 조류 센서 흉내).
파일이 없으면 0으로 본다.

보안 조치 (보안 설계 문서 F1·H2, AI 전용 AI1·AI2):
    F1 출력 범위 검사 — 설정값이 5~50 mg/L를 벗어나면 범위 끝값으로 자른다.
                    유형 모델이 없으면 직전 설정값을 유지한다.
    H2 입력 이상 탐지 — 탁도가 한 번에 100 NTU 넘게 바뀌거나 1000 NTU를 넘으면 경보를 내고
                    그 값으로 설정값을 바꾸지 않는다(직전 설정값 유지). 새 값이 5번 연속
                    비슷하게 읽히면(공정 약 5분) 실제 변화로 보고 받아들인다.
    AI1 처음 보는 입력 감지 — 입력이 학습 범위 밖이면 범위 끝으로 잘라 예측하고, 그 값과
                    직전 설정값 중 큰 쪽을 쓴다(모르면 줄이지 않음). 운영자 확인 경보를 낸다.
    AI2 예측 불확실성 감시 — 유형을 확신하지 못하거나 나무들의 예측이 크게 갈리면 넉넉한 쪽으로 넣는다.
    AI1·AI2 의 범위와 기준은 dataset/models/guard.json (ai_model.py 설명 참고).
"""

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

PAUSE_FILE = 'logs/ai_agent.pause'
ALGAE_SENSOR_FILE = 'logs/algae_sensor.txt'
USAGE = 'usage: python ai_agent.py [status | pause | resume]'

# 보안 F1: 출력 범위 (mg/L)
DOSE_MIN = 5.0
DOSE_MAX = 50.0

# 보안 H2: 입력 이상 탐지
TURBIDITY_SPIKE_THRESHOLD = 100.0   # 한 번에 이만큼 넘게 바뀌면 의심 (NTU)
TURBIDITY_MAX_VALID = 1000.0        # 이 값을 넘으면 센서 고장이나 위변조로 본다 (NTU)
SPIKE_CONFIRM_CYCLES = 5            # 의심 값이 이만큼 연속 비슷하게 읽히면 받아들인다

LOG_EVERY_CYCLES = 10


def read_algae():
    try:
        with open(ALGAE_SENSOR_FILE) as f:
            return max(0.0, to_number(f.read().strip(), 0.0))
    except FileNotFoundError:
        return 0.0


def decide_setpoint(turbidity, trend, algae, models, guard, previous_setpoint):
    """(설정값, 판단 결과 dict). AI1·AI2 판단 뒤에 F1 출력 범위 검사를 붙인다."""
    result = ai_model.assess(ai_model.features(turbidity, trend, algae), *models, guard)
    if result['required'] is None:
        # 유형 모델 없음 → 직전 설정값 유지
        return previous_setpoint, result
    setpoint = result['required'] + ai_model.SAFETY_MARGIN
    if result['ood']:
        # AI1: 처음 보는 입력에서는 주입률을 줄이지 않는다
        setpoint = max(setpoint, previous_setpoint)
    if not DOSE_MIN <= setpoint <= DOSE_MAX:
        clipped = min(max(setpoint, DOSE_MIN), DOSE_MAX)
        print('[AI][F1] 설정값 %.2f → %.2f mg/L 로 자름' % (setpoint, clipped))
        result['flags'].append('F1_CLIPPED')
        setpoint = clipped
    return round(setpoint, 4), result


def describe_ood(ood):
    return ', '.join('%s=%.3g (학습 범위 %.3g~%.3g)' % item for item in ood)


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
        print('[AI][H2] 탁도 급변 %.1f → %.1f NTU (%d/%d). 직전 설정값 유지'
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
        print('[AI] 모델 로드 완료: %s' % ', '.join(sorted(self.models[2])))
        self.ood_active = False
        self.guard = TurbidityGuard(INITIAL_RAW_TURBIDITY)
        self.trend = ai_model.TurbidityTrend()
        self.turbidity_trend = 0.0
        self.setpoint = INITIAL_DOSE_SETPOINT
        self.cycle_count = 0
        self.paused = False

    def main_loop(self):
        # MiniCPS의 HMI 클래스는 PLC와 달리 pre_loop()을 부르지 않는다. 여기서 준비한다.
        arguments = sys.argv[1:]
        if arguments == ['pause']:
            open(PAUSE_FILE, 'w').close()
            print('AI 자동 제어 멈춤. 다시 시작: python ai_agent.py resume')
            return
        if arguments == ['resume']:
            if os.path.exists(PAUSE_FILE):
                os.remove(PAUSE_FILE)
            print('AI 자동 제어 다시 시작')
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

    def control_loop(self):
        print('[AI] 자동 제어 루프 시작')
        while True:
            cycle_start = time.time()
            self.cycle_count += 1

            paused = os.path.exists(PAUSE_FILE)
            if paused != self.paused:
                print('[AI] 자동 제어 %s' % ('멈춤 (운영자 수동)' if paused else '다시 시작'))
                self.paused = paused

            reading = self.read_turbidity()
            sim_time = to_number(self.receive(SIM_TIME_PLC1, PLC1_ADDRESS), 0.0)
            turbidity, anomaly = self.guard.accept(reading)
            algae = read_algae()
            flags = ['H2_HOLD'] if anomaly else []

            if anomaly:
                water_type = '-'
            else:
                # 변화율 기록에는 H2 검사를 통과한 값만 넣는다
                self.turbidity_trend = self.trend.update(sim_time, turbidity)
                self.setpoint, result = decide_setpoint(
                    turbidity, self.turbidity_trend, algae, self.models, self.guard_config,
                    self.setpoint)
                water_type = '%s(%.2f)' % (result['water_type'], result['confidence'])
                flags += result['flags']
                ood = bool(result['ood'])
                if ood and not self.ood_active:
                    print('[AI][AI1] 처음 보는 입력: %s. 학습 범위로 잘라 예측하고 주입률을 줄이지 않음. '
                          '운영자 확인 필요' % describe_ood(result['ood']))
                elif self.ood_active and not ood:
                    print('[AI][AI1] 입력이 학습 범위로 돌아옴. AI 예측 다시 사용')
                self.ood_active = ood
                if 'AI2_TYPE' in result['flags']:
                    print('[AI][AI2] 유형 확신 %.2f 낮음. 두 유형 중 큰 예측 사용' % result['confidence'])
                if 'AI2_SPREAD' in result['flags']:
                    print('[AI][AI2] 나무 예측 편차 %.2f mg/L 큼. 편차의 %.0f배 더 넣음'
                          % (result['spread'], self.guard_config['spread_factor']))

            if not self.paused:
                self.send(DOSE_SETPOINT, self.setpoint, PLC2_ADDRESS)

            if self.cycle_count % LOG_EVERY_CYCLES == 1 or flags:
                minutes = int(sim_time % 86400) // 60
                print('[AI] %d일차 %02d:%02d 탁도=%.1f NTU 변화율=%+.2f 조류=%.1f 유형=%s 설정=%.2f mg/L%s%s'
                      % (int(sim_time // 86400) + 1, minutes // 60, minutes % 60, reading,
                         self.turbidity_trend, algae,
                         water_type, self.setpoint,
                         ' [' + ','.join(flags) + ']' if flags else '',
                         ' (멈춤: 보내지 않음)' if self.paused else ''))

            time.sleep(max(0.0, DEVICE_SLEEP_SECONDS - (time.time() - cycle_start)))

    def status_once(self):
        reading = self.read_turbidity()
        algae = read_algae()
        # 한 번만 읽으므로 변화율을 알 수 없다. 0(변화 없음)으로 본다
        setpoint, result = decide_setpoint(reading, 0.0, algae, self.models, self.guard_config,
                                           self.setpoint)
        flags = result['flags']
        print('탁도: %.1f NTU' % reading)
        print('조류 보정: %.1f mg/L' % algae)
        print('유형: %s (확신 %.2f)' % (result['water_type'], result['confidence']))
        if result['ood']:
            print('AI1 처음 보는 입력: %s' % describe_ood(result['ood']))
        print('AI 설정값: %.2f mg/L%s' % (setpoint, ' (유형 모델 없음, 직전 값)' if result['required'] is None else ''))
        print('PLC2 현재 설정값: %.2f mg/L'
              % to_number(self.receive(DOSE_SETPOINT, PLC2_ADDRESS), float('nan')))
        print('자동 제어: %s' % ('멈춤' if os.path.exists(PAUSE_FILE) else '동작'))
        if flags:
            print('플래그: %s' % ', '.join(flags))


if __name__ == '__main__':
    AiAgent(name='hmi', state=STATE, protocol=CLIENT_PROTOCOL)
