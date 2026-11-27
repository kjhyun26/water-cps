"""운영자 정책과 AI 정책을 같은 30일 원수 시나리오로 돌려 결과를 비교한다.

공정 수식은 build_dataset.py 와 같다 (MiniCPS 없이 빠르게 계산).
비교 항목:
    침전수 탁도가 1.0 NTU를 넘은 시간 비율과 최댓값
    PLC3 경보 횟수 (1.0 NTU를 공정 30분 넘게 연속 초과한 횟수)
    약품 사용량 (t)

AI 정책은 두 가지로 돌린다.
    AI (조류 정보 있음)  조류 보정값을 안다고 가정 (조류 센서가 있는 경우)
    AI (조류 정보 없음)  지금의 ai_agent.py 와 같다. 조류 보정값을 늘 0으로 넣는다

실행:
    cd ~/water_cps
    venv/bin/python dataset/evaluate_policy.py
"""

import collections
import os
import sys

WATER_CPS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WATER_CPS_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_model  # noqa: E402
from build_dataset import _required_dose, _settled_turbidity  # noqa: E402
from config import (  # noqa: E402
    CHEMICAL_PUMP_MAX, INITIAL_DOSE_SETPOINT, INITIAL_RAW_TURBIDITY, PROCESS_STEP_SECONDS,
    PRODUCTION_FLOW, SETTLING_DELAY_SECONDS, TURBIDITY_ALARM_MINUTES, TURBIDITY_LIMIT,
)
from operator_policy import OperatorPolicy  # noqa: E402
from scenarios import default_scenario, raw_conditions  # noqa: E402

DAYS = 30
QUEUE_LENGTH = SETTLING_DELAY_SECONDS // PROCESS_STEP_SECONDS


def _ai_setpoints(conditions, use_algae, scaler, clf, regressors):
    """걸음마다 AI 설정값. 입력이 같으면 출력도 같으므로 한 번에 묶어 예측한다."""
    rows = [ai_model.features(ntu, algae if use_algae else 0.0) for ntu, algae, _ in conditions]
    x = scaler.transform(rows)
    labels = [str(label) for label in clf.predict(x)]
    setpoints = [None] * len(rows)
    for label in set(labels):
        idx = [i for i, l in enumerate(labels) if l == label]
        pred = regressors[label].predict(x[idx])
        for i, p in zip(idx, pred):
            setpoints[i] = float(p) + ai_model.SAFETY_MARGIN
    return setpoints


def simulate(conditions, setpoints):
    alarm_steps = TURBIDITY_ALARM_MINUTES * 60 // PROCESS_STEP_SECONDS
    diff_queue = collections.deque(
        [INITIAL_DOSE_SETPOINT - _required_dose(INITIAL_RAW_TURBIDITY)] * QUEUE_LENGTH)
    over_steps = over_run = alarms = 0
    max_settled = 0.0
    chemical_kg = 0.0
    for (ntu, algae, _), setpoint in zip(conditions, setpoints):
        command = min(max(PRODUCTION_FLOW * setpoint / 1000, 0.0), CHEMICAL_PUMP_MAX)
        actual = command * 1000 / PRODUCTION_FLOW
        chemical_kg += command * PROCESS_STEP_SECONDS / 3600
        diff_queue.append(actual - (_required_dose(ntu) + algae))
        settled = _settled_turbidity(diff_queue.popleft())
        max_settled = max(max_settled, settled)
        if settled > TURBIDITY_LIMIT:
            over_steps += 1
            over_run += 1
            if over_run == alarm_steps + 1:
                alarms += 1
        else:
            over_run = 0
    total = len(conditions)
    return {'over_ratio': over_steps / total, 'max_settled': max_settled,
            'alarms': alarms, 'chemical_t': chemical_kg / 1000}


def main():
    scenario = default_scenario()
    steps = DAYS * 86400 // PROCESS_STEP_SECONDS
    conditions = [raw_conditions(i * PROCESS_STEP_SECONDS, scenario) for i in range(steps)]

    policy = OperatorPolicy(overdose=2.0, jar_test_hours=(9,))
    operator = [policy.setpoint_at(i * PROCESS_STEP_SECONDS, _required_dose(ntu) + algae, ntu)
                for i, (ntu, algae, _) in enumerate(conditions)]

    scaler, clf, regressors = ai_model.load_models()
    results = {
        '운영자 (하루 1회 자-테스트)': simulate(conditions, operator),
        'AI (조류 정보 있음)': simulate(conditions, _ai_setpoints(conditions, True, scaler, clf, regressors)),
        'AI (조류 정보 없음)': simulate(conditions, _ai_setpoints(conditions, False, scaler, clf, regressors)),
    }

    print(f"{DAYS}일 시나리오 (호우 3번, 조류 1번) 결과")
    print(f"{'정책':<22}{'1 NTU 초과 시간':>14}{'최대 침전수 탁도':>16}{'PLC3 경보':>10}{'약품 사용량':>12}")
    for name, r in results.items():
        print(f"{name:<22}{r['over_ratio']*100:>13.2f}%{r['max_settled']:>13.2f} NTU"
              f"{r['alarms']:>9}번{r['chemical_t']:>10.1f} t")


if __name__ == '__main__':
    main()
