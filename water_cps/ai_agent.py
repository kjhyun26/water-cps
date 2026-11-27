"""AI 에이전트: 원수 탁도를 읽고 AI 모델로 주입률 설정값을 PLC2에 전달한다.

HMI와 같은 통신 구조를 사용한다 (설계 문서 5-2절, 6절).
모델은 dataset/train_model.py 로 사전 학습된 pkl 파일을 불러온다.

실행:
    python ai_agent.py          AI 자동 제어 루프
    python ai_agent.py status   현재 AI 예측값 한 번만 출력

보안 조치 (보안 설계 문서 F1·H2):
    F1 — 출력 범위 검사: 예측값이 허용 범위를 벗어나면 안전 기본값 사용
    H2 — 입력 이상 탐지: 탁도가 급격히 변하면 경보 출력 (센서 위변조 의심)
"""

import math
import os
import pickle
import sys
import time

from minicps.devices import HMI

from config import (
    ADDRESS, AIT101, CLIENT_PROTOCOL, DEVICE_SLEEP_SECONDS, DOSE_SETPOINT,
    INITIAL_DOSE_SETPOINT, INITIAL_RAW_TURBIDITY, STATE, SIM_TIME_PLC1, to_number,
)

# ---------------------------------------------------------------------------
# 경로 설정
# ---------------------------------------------------------------------------
WATER_CPS_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(WATER_CPS_DIR, 'dataset', 'models')

PLC1_ADDRESS = ADDRESS['plc1']
PLC2_ADDRESS = ADDRESS['plc2']

# ---------------------------------------------------------------------------
# 보안 F1: 출력 범위 허용값 (mg/L)
# ---------------------------------------------------------------------------
DOSE_MIN = 5.0
DOSE_MAX = 50.0
DOSE_SAFE_DEFAULT = INITIAL_DOSE_SETPOINT  # 모델 오류 시 사용할 안전 기본값

# ---------------------------------------------------------------------------
# 보안 H2: 입력 이상 탐지 임계값
# ---------------------------------------------------------------------------
TURBIDITY_SPIKE_THRESHOLD = 100.0   # 한 걸음에 이만큼 오르면 경보
TURBIDITY_MAX_VALID = 1000.0        # 이 값 초과면 센서 고장으로 판단


# ---------------------------------------------------------------------------
# 모델 로드
# ---------------------------------------------------------------------------
def load_models(model_dir: str = MODEL_DIR):
    """scaler, classifier, 유형별 regressor를 모두 불러온다."""
    def load(name):
        path = os.path.join(model_dir, name)
        with open(path, 'rb') as f:
            return pickle.load(f)

    scaler = load('scaler.pkl')
    clf = load('classifier.pkl')

    # 유형 목록은 분류기의 classes_ 에서 읽는다
    regressors = {}
    for label in clf.classes_:
        pkl_path = os.path.join(model_dir, f'model_{label}.pkl')
        if os.path.exists(pkl_path):
            regressors[label] = load(f'model_{label}.pkl')

    print(f"[AI] 모델 로드 완료: {list(regressors.keys())}")
    return scaler, clf, regressors


# ---------------------------------------------------------------------------
# 추론 (보안 조치 포함)
# ---------------------------------------------------------------------------
def predict_dose(raw_turbidity_ntu: float, algae_extra: float,
                 scaler, clf, regressors) -> tuple[float, str, str]:
    """주입률 설정값을 예측한다.

    Returns:
        (dose_mg_per_l, water_type, security_flag)
        security_flag: '' 정상 | 'F1_CLIPPED' 범위 초과 클리핑 | 'F1_FALLBACK' 모델 없음
    """
    ntu = raw_turbidity_ntu
    x = scaler.transform([[ntu, math.log(max(ntu, 0.1)), algae_extra]])

    water_type = clf.predict(x)[0]

    if water_type not in regressors:
        # F1: 해당 유형 모델 없음 → 안전 기본값
        return DOSE_SAFE_DEFAULT, water_type, 'F1_FALLBACK'

    dose = regressors[water_type].predict(x)[0]

    # F1: 출력 범위 검사
    flag = ''
    if dose < DOSE_MIN or dose > DOSE_MAX:
        dose_clipped = max(DOSE_MIN, min(DOSE_MAX, dose))
        print(f'[AI][F1] 범위 초과 클리핑: {dose:.2f} → {dose_clipped:.2f} mg/L')
        dose = dose_clipped
        flag = 'F1_CLIPPED'

    return round(dose, 4), water_type, flag


# ---------------------------------------------------------------------------
# AI 에이전트
# ---------------------------------------------------------------------------
class AiAgent(HMI):

    def pre_loop(self):
        self.scaler, self.clf, self.regressors = load_models()
        self.prev_turbidity = INITIAL_RAW_TURBIDITY
        self.current_dose = DOSE_SAFE_DEFAULT
        self.cycle_count = 0

    def main_loop(self):
        arguments = sys.argv[1:]
        if arguments == ['status']:
            self._status_once()
        else:
            self._control_loop()

    def _read_turbidity(self) -> float:
        """PLC1에서 원수 탁도를 읽는다. 실패하면 직전 값을 반환한다."""
        raw = self.receive(AIT101, PLC1_ADDRESS)
        return to_number(raw, self.prev_turbidity)

    def _read_sim_time(self) -> float:
        raw = self.receive(SIM_TIME_PLC1, PLC1_ADDRESS)
        return to_number(raw, 0.0)

    def _check_h2(self, turbidity: float) -> bool:
        """H2: 탁도 급변 감지. True이면 이상."""
        delta = abs(turbidity - self.prev_turbidity)
        if delta > TURBIDITY_SPIKE_THRESHOLD:
            print(f'[AI][H2] 탁도 급변 경보: {self.prev_turbidity:.1f} → {turbidity:.1f} NTU '
                  f'(변화량 {delta:.1f}, 임계값 {TURBIDITY_SPIKE_THRESHOLD})')
            return True
        if turbidity > TURBIDITY_MAX_VALID:
            print(f'[AI][H2] 탁도 센서 오류 의심: {turbidity:.1f} NTU (최대 허용 {TURBIDITY_MAX_VALID})')
            return True
        return False

    def _control_loop(self):
        print('[AI] 자동 제어 루프 시작')
        while True:
            cycle_start = time.time()
            self.cycle_count += 1

            turbidity = self._read_turbidity()
            sim_time = self._read_sim_time()

            # H2 이상 탐지 (탁도는 그대로 사용하되 경보만 출력)
            anomaly = self._check_h2(turbidity)

            # 추론
            dose, water_type, flag = predict_dose(
                turbidity, 0.0,  # algae_extra: 현재는 센서 없음 → 0으로 고정
                self.scaler, self.clf, self.regressors,
            )

            # PLC2에 전달
            self.send(DOSE_SETPOINT, dose, PLC2_ADDRESS)
            self.current_dose = dose
            self.prev_turbidity = turbidity

            # 로그 (10사이클마다)
            if self.cycle_count % 10 == 1:
                flags = []
                if anomaly:
                    flags.append('H2')
                if flag:
                    flags.append(flag)
                flag_str = ' [' + ','.join(flags) + ']' if flags else ''
                day = int(sim_time // 86400) + 1
                hhmm = int(sim_time % 86400) // 60
                print(f'[AI] {day}일 {hhmm//60:02d}:{hhmm%60:02d} '
                      f'탁도={turbidity:.1f} NTU 유형={water_type} '
                      f'설정={dose:.2f} mg/L{flag_str}')

            elapsed = time.time() - cycle_start
            sleep_time = max(0.0, DEVICE_SLEEP_SECONDS - elapsed)
            time.sleep(sleep_time)

    def _status_once(self):
        """현재 상태를 한 번 출력하고 종료한다."""
        turbidity = self._read_turbidity()
        dose, water_type, flag = predict_dose(
            turbidity, 0.0, self.scaler, self.clf, self.regressors,
        )
        print(f'탁도: {turbidity:.1f} NTU')
        print(f'유형: {water_type}')
        print(f'예측 주입률: {dose:.2f} mg/L')
        if flag:
            print(f'보안 플래그: {flag}')


if __name__ == '__main__':
    AiAgent(name='hmi', state=STATE, protocol=CLIENT_PROTOCOL)
